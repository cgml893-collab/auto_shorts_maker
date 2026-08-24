# -*- coding: utf-8 -*-
"""스마트폰 앱과 통신하는 FastAPI 모바일 서버 (비동기 작업 큐)."""

from __future__ import annotations

import gc
import json
import os
import shutil
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List

os.environ.setdefault("FFMPEG_TIMEOUT", "180")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from license_lock import (
    MASTER_PRO_KEY,
    PAYMENT_MESSAGE,
    PAYMENT_REQUIRED,
    authorize_create_job,
    compact_license,
    consume_entitlement,
    is_master_pro_key,
    mobile_hwid,
    normalize_key,
    resolve_mobile_entitlement,
    verify_or_activate_mobile,
)
from main import (
    IMAGE_EXTS,
    OUTPUT_DIR,
    PIPELINE_HARD_LIMIT,
    VIDEO_EXTS,
    analyze_media_styles,
    canvas_size,
    compose_blur_fill_frame,
    diet_image_file,
    fast_blur_slideshow,
    load_settings,
    normalize_aspect_ratio,
    normalize_bgm_mood,
    normalize_camera_motion,
    normalize_caption_style,
    normalize_motion_intensity,
    normalize_speed,
    normalize_target_duration,
    normalize_visual_fx,
    parse_flag,
    pipeline_time_budget,
    resolve_voice,
    run_pipeline,
)

load_dotenv()

JOBS_DIR = OUTPUT_DIR / "mobile_jobs"
UPLOAD_CHUNK = 256 * 1024
MAX_UPLOAD_FILES = 8
MAX_SINGLE_UPLOAD = 80 * 1024 * 1024
ALLOWED_EXTS = IMAGE_EXTS | VIDEO_EXTS
JOBS: Dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
WORKER = ThreadPoolExecutor(max_workers=1)

app = FastAPI(
    title="ClipSpark AI 모바일 서버",
    description="⚡ 10초 쾌속 / ✨ 스파크 시네마 AI · 3단계 라이선스",
    version="4.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class VerifyLicenseBody(BaseModel):
    device_id: str = Field(..., description="Android ID 또는 iOS identifierForVendor")
    license_key: str = Field("", description="베이직/프로 라이선스 키")
    platform: str = Field("", description="android 또는 ios")


class LicenseStatusBody(BaseModel):
    device_id: str
    platform: str = ""
    license_key: str = ""


def _canonical_license(raw):
    text = (raw or "").strip()
    if is_master_pro_key(text):
        return MASTER_PRO_KEY
    return normalize_key(text) or compact_license(text) or text


def _safe_name(name):
    raw = Path(name or "media").name
    stem = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in Path(raw).stem) or "media"
    suffix = Path(raw).suffix.lower()
    if suffix not in ALLOWED_EXTS:
        return None
    return stem + suffix


async def _persist_upload(item, dest):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with dest.open("wb") as handle:
        while True:
            chunk = await item.read(UPLOAD_CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_SINGLE_UPLOAD:
                raise HTTPException(status_code=413, detail="파일이 너무 큽니다. 사진을 줄여 다시 올려 주세요.")
            handle.write(chunk)
            del chunk
    await item.close()
    if size <= 0:
        raise HTTPException(status_code=400, detail="빈 파일입니다: {}".format(item.filename))
    suffix = dest.suffix.lower()
    if suffix in IMAGE_EXTS:
        slim = dest.with_suffix(".jpg")
        dest = diet_image_file(dest, dest=slim)
    _release_memory()
    return dest


def _job_snapshot(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job:
            return dict(job)
    status_file = JOBS_DIR / job_id / "status.json"
    if status_file.is_file():
        try:
            data = json.loads(status_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                with JOBS_LOCK:
                    JOBS[job_id] = data
                return dict(data)
        except Exception:
            return None
    return None


def _persist_job(job_id):
    snap = _job_snapshot(job_id)
    if not snap:
        return
    job_dir = Path(snap.get("output") or (JOBS_DIR / job_id / "final_shorts.mp4")).parent
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "status.json").write_text(json.dumps(snap, ensure_ascii=True), encoding="utf-8")


def _release_memory():
    gc.collect()
    gc.collect()


def _purge_path(path):
    target = Path(path)
    try:
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.is_file() or target.is_symlink():
            target.unlink()
    except OSError:
        pass


def _purge_job_temps(job_dir, keep_file=None):
    job_dir = Path(job_dir)
    if not job_dir.exists():
        return
    keep = None
    keep_names = {"status.json", "final_shorts.mp4", "voice.mp3", "bgm.wav", "subs.srt", "i2v_source.jpg"}
    if keep_file:
        try:
            keep = Path(keep_file).resolve()
        except OSError:
            keep = None
    for child in list(job_dir.iterdir()):
        try:
            if keep is not None and child.resolve() == keep:
                continue
            if child.name in keep_names or child.name.startswith("frame_"):
                continue
        except OSError:
            pass
        _purge_path(child)


def _finish_job_cleanup(job_dir, keep_file=None):
    _purge_job_temps(job_dir, keep_file=keep_file)
    _release_memory()


def _cleanup_after_download(job_dir):
    _purge_path(job_dir)
    _release_memory()


def _output_valid(path):
    try:
        target = Path(path)
        return target.is_file() and target.stat().st_size >= 32
    except OSError:
        return False


def _job_mp4_path(job):
    candidates = []
    raw = (job or {}).get("output")
    if raw:
        candidates.append(Path(raw))
    job_id = (job or {}).get("job_id")
    if job_id:
        candidates.append(JOBS_DIR / str(job_id) / "final_shorts.mp4")
    for path in candidates:
        if _output_valid(path):
            return path
    return candidates[0] if candidates else None


def _update_job(job_id, **fields):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            JOBS[job_id] = {"job_id": job_id}
            job = JOBS[job_id]
        if job.get("status") == "completed" and fields.get("status") == "processing":
            return
        job.update(fields)
    _persist_job(job_id)


def _payment_response(message=PAYMENT_MESSAGE, status_code=402):
    return JSONResponse(
        status_code=status_code,
        content={"error": PAYMENT_REQUIRED, "message": message},
    )


def _vip_forbidden_response(message="👑 VIP 시네마 스튜디오는 프로 VIP 전용입니다."):
    return JSONResponse(
        status_code=403,
        content={
            "error": PAYMENT_REQUIRED,
            "code": "VIP_PRO_REQUIRED",
            "message": message,
        },
    )


def _run_job(
    job_id,
    media_files,
    prompt,
    out_file,
    voice_type,
    speed_multiplier,
    bgm_mood,
    spark_cinema,
    camera_motion,
    output_height,
    features,
    target_duration=15,
    caption_style="hormozi",
    visual_fx="ken_burns",
    aspect_ratio="9:16",
    audio_ducking=True,
    is_vip_mode=False,
    action_motion_enabled=False,
    action_style="",
    action_preset="",
    motion_intensity=7,
):
    started = time.time()
    target_duration = normalize_target_duration(target_duration)
    budget = pipeline_time_budget(target_duration)
    deadline = started + budget
    finished = threading.Event()
    write_lock = threading.Lock()

    def _mark_completed(final_output_path=None):
        final_output_path = Path(final_output_path or out_file)
        if not _output_valid(final_output_path):
            return False
        _update_job(
            job_id,
            status="completed",
            percent=100,
            progress=100,
            stage="completed",
            output=str(final_output_path),
            error=None,
            elapsed_sec=round(time.time() - started, 1),
        )
        return True

    def progress(percent, message):
        pct = max(0, min(100, int(percent)))
        msg = str(message or "")
        if pct >= 96 or msg.startswith("완료") or msg == "completed":
            if _mark_completed(out_file):
                return
        _update_job(
            job_id,
            status="processing",
            stage=message,
            percent=pct,
            progress=pct,
            elapsed_sec=round(time.time() - started, 1),
        )

    def _force_complete():
        if _mark_completed(out_file):
            return
        work = Path(out_file).parent / "_force_complete"
        os.makedirs(str(work), exist_ok=True)
        with write_lock:
            if _mark_completed(out_file):
                return
            progress(92, "{}초 안전 완성 · 균등 슬라이드쇼".format(int(target_duration)))
            fast_blur_slideshow(media_files, out_file, work, duration=float(target_duration))
            if not _mark_completed(out_file):
                raise RuntimeError("안전 슬라이드쇼가 완성된 MP4를 만들지 못했습니다.")

    def _watchdog():
        if finished.wait(timeout=max(30.0, budget + 45.0)):
            return
        try:
            _force_complete()
        except Exception as exc:
            print("[안내] 강제 완성 워치독 실패: {}".format(exc))

    watcher = threading.Thread(target=_watchdog, daemon=True)
    watcher.start()

    try:
        _update_job(job_id, stage="대본 작성 중", percent=8, progress=8, elapsed_sec=0)
        runner = ThreadPoolExecutor(max_workers=1)
        try:
            fut = runner.submit(
                run_pipeline,
                media_files,
                style_prompt=prompt,
                progress_cb=progress,
                output_path=out_file,
                check_license=False,
                voice_type=voice_type,
                speed_multiplier=speed_multiplier,
                bgm_mood=bgm_mood,
                is_runway_mode=spark_cinema,
                is_spark_cinema=spark_cinema,
                camera_motion=camera_motion,
                output_height=output_height,
                fast_mode=not spark_cinema,
                deadline_ts=deadline,
                target_duration=target_duration,
                caption_style=caption_style,
                visual_fx=visual_fx,
                aspect_ratio=aspect_ratio,
                audio_ducking=audio_ducking,
                is_vip_mode=is_vip_mode,
                action_motion_enabled=action_motion_enabled,
                action_style=action_style,
                action_preset=action_preset,
                motion_intensity=motion_intensity,
            )
            try:
                fut.result(timeout=max(90.0, budget))
            except TimeoutError:
                print("[안내] 파이프라인 대기 시간 초과, 완성 파일 확인", flush=True)
            except Exception as exc:
                traceback.print_exc()
                progress(80, "외부 API 대기열 · {}초 안전 슬라이드쇼로 전환".format(int(target_duration)))
                try:
                    _force_complete()
                except Exception:
                    print("[안내] 서버 안전장치 폴백 실패: {}".format(exc))
        finally:
            runner.shutdown(wait=True)
        if not _output_valid(out_file):
            _force_complete()
        if not _mark_completed(out_file):
            raise RuntimeError("완성된 영상 파일을 찾지 못했습니다.")
        try:
            consume_entitlement(features)
        except Exception as exc:
            print("[안내] 이용권 차감 실패, 완성 파일은 유지: {}".format(exc))
    except Exception as exc:
        traceback.print_exc()
        if _mark_completed(out_file):
            print("[안내] 예외 후에도 완성 MP4가 있어 completed 유지: {}".format(exc))
        else:
            _update_job(
                job_id,
                status="failed",
                stage="실패",
                error=str(exc),
                elapsed_sec=round(time.time() - started, 1),
            )
    finally:
        finished.set()
        job_dir = Path(out_file).parent
        keep = out_file if Path(out_file).is_file() else None
        if isinstance(media_files, list):
            media_files.clear()
        if keep is None:
            _purge_path(job_dir)
            _release_memory()
        else:
            _finish_job_cleanup(job_dir, keep_file=keep)


@app.get("/i2v-image/{job_id}")
def i2v_image(job_id: str):
    job_dir = JOBS_DIR / job_id
    for name in ("i2v_source.jpg", "frame_0.jpg"):
        path = job_dir / name
        if path.is_file() and path.stat().st_size >= 32:
            return FileResponse(path=str(path), media_type="image/jpeg")
    media_dir = job_dir / "media"
    if media_dir.is_dir():
        for child in sorted(media_dir.iterdir()):
            if child.suffix.lower() in IMAGE_EXTS and child.stat().st_size >= 32:
                return FileResponse(path=str(child), media_type="image/jpeg")
    raise HTTPException(status_code=404, detail="I2V 소스 이미지를 찾지 못했습니다.")


@app.api_route("/", methods=["GET", "HEAD"])
def root():
    return {
        "ok": True,
        "service": "ClipSpark AI 모바일 서버",
        "branding": "✨ 스파크 시네마 AI",
        "endpoints": [
            "/verify-license",
            "/license-status",
            "/analyze-media",
            "/create-video",
            "/job-status/{job_id}",
            "/download/{job_id}",
            "/i2v-image/{job_id}",
        ],
    }


@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {"ok": True}


@app.post("/verify-license")
def verify_license(body: VerifyLicenseBody):
    license_key = _canonical_license(body.license_key)
    ok, message = verify_or_activate_mobile(license_key, body.device_id, body.platform)
    hwid = mobile_hwid(body.device_id, body.platform)
    short = "-".join(hwid[i : i + 4] for i in range(0, 16, 4))
    if not ok and not is_master_pro_key(license_key):
        raise HTTPException(status_code=403, detail=message)
    if is_master_pro_key(license_key):
        ok = True
    _ok, _msg, features = resolve_mobile_entitlement(body.device_id, body.platform, license_key)
    return {
        "ok": True,
        "message": message if ok else "마스터 키로 프로 VIP가 활성화되었습니다.",
        "device_bound": not bool(features.get("master")),
        "machine_code": short,
        "platform": (body.platform or "").strip().lower(),
        "plan": features.get("plan"),
        "plan_label": features.get("label"),
        "status_bar": features.get("status_bar"),
        "features": features,
    }


@app.post("/license-status")
def license_status(body: LicenseStatusBody):
    license_key = _canonical_license(body.license_key)
    ok, message, features = resolve_mobile_entitlement(body.device_id, body.platform, license_key)
    return {
        "ok": ok or features.get("plan") in ("basic", "pro"),
        "message": message,
        "plan": features.get("plan"),
        "plan_label": features.get("label"),
        "status_bar": features.get("status_bar"),
        "free_remaining": features.get("free_remaining", 0),
        "features": features,
        "payment_required": (not ok) and features.get("plan") == "free",
    }


@app.post("/analyze-media")
async def analyze_media(
    files: List[UploadFile] = File(..., description="사진/동영상"),
    license_key: str = Form("", description="라이선스 키 (무료 체험은 생략 가능)"),
    device_id: str = Form(..., description="Android ID / iOS Vendor ID"),
    platform: str = Form("", description="android 또는 ios"),
):
    if not files:
        raise HTTPException(status_code=400, detail="분석할 미디어를 올려 주세요.")

    job_id = uuid.uuid4().hex[:12]
    tmp_dir = JOBS_DIR / ("analyze_{}".format(job_id))
    tmp_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    try:
        for index, item in enumerate(files[:3]):
            filename = _safe_name(item.filename)
            if not filename:
                continue
            dest = tmp_dir / "{:03d}_{}".format(index + 1, filename)
            dest = await _persist_upload(item, dest)
            saved.append(dest)
        if not saved:
            raise HTTPException(status_code=400, detail="지원하는 미디어가 없습니다.")
        result = analyze_media_styles(load_settings(), saved)
        result["ok"] = True
        return result
    except HTTPException:
        raise
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="미디어 분석 실패: {}".format(exc))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        saved.clear()
        _release_memory()


@app.post("/create-video")
async def create_video(
    files: List[UploadFile] = File(..., description="사진/동영상 (EXIF 회전 자동 보정)"),
    style: str = Form("", description="스타일 프롬프트"),
    style_prompt: str = Form("", description="style 별칭"),
    license_key: str = Form("", description="라이선스 키 (무료 체험은 생략 가능)"),
    device_id: str = Form(..., description="Android ID / iOS Vendor ID"),
    platform: str = Form("", description="android 또는 ios"),
    voice_type: str = Form("vlog_female", description="한국어 성우"),
    speed_multiplier: str = Form("1.0", description="1.0 / 1.2 / 1.5"),
    bgm_mood: str = Form("lofi", description="구버전 별칭"),
    bgm_type: str = Form("lofi", description="BGM 분위기"),
    is_runway_mode: str = Form("false", description="하위 호환"),
    is_spark_cinema: str = Form("false", description="✨ 스파크 시네마 AI"),
    camera_motion: str = Form("zoom_in", description="push_in/fpv/orbit/low_angle"),
    output_height: str = Form("720", description="720 또는 1080 (프로)"),
    target_duration: str = Form("15", description="15 / 30 / 60초"),
    caption_style: str = Form("hormozi", description="hormozi / neon / minimal / variety"),
    visual_fx: str = Form("ken_burns", description="ken_burns / zoom_punch / cinematic"),
    aspect_ratio: str = Form("9:16", description="9:16 / 16:9 / 1:1"),
    audio_ducking: str = Form("true", description="보이스 구간에 BGM 20% 더킹"),
    is_vip_mode: str = Form("false", description="👑 VIP 시네마 스튜디오"),
    action_motion_enabled: str = Form("false", description="다이내믹 액션 모션"),
    action_style: str = Form("", description="액션 직접 입력"),
    action_preset: str = Form("", description="bike_stunt/dance/dynamic/sprint"),
    subject_motion: str = Form("", description="피사체 동작 지정"),
    motion_intensity: str = Form("7", description="모션 강도 6~8"),
):
    prompt = (style or style_prompt or "").strip()
    action_style = (action_style or subject_motion or "").strip()
    if not files:
        raise HTTPException(status_code=400, detail="사진 또는 동영상을 한 개 이상 업로드해 주세요.")

    voice_key, _vid, _preset = resolve_voice(voice_type)
    speed = normalize_speed(speed_multiplier)
    mood = normalize_bgm_mood(bgm_type or bgm_mood)
    spark = parse_flag(is_spark_cinema) or parse_flag(is_runway_mode)
    motion = normalize_camera_motion(camera_motion)
    duration = normalize_target_duration(target_duration)
    captions = normalize_caption_style(caption_style)
    fx = normalize_visual_fx(visual_fx or motion)
    ratio = normalize_aspect_ratio(aspect_ratio)
    ducking = parse_flag(audio_ducking) if str(audio_ducking or "").strip() else True
    vip = parse_flag(is_vip_mode)
    action_on = parse_flag(action_motion_enabled)
    intensity = normalize_motion_intensity(motion_intensity)
    try:
        height = int(float(output_height or 720))
    except (TypeError, ValueError):
        height = 720
    if height >= 1080:
        height = 1080
    else:
        height = 720

    license_key = _canonical_license(license_key)
    allowed, err_code, err_msg, features = authorize_create_job(
        device_id,
        platform,
        license_key,
        spark,
        voice_key,
        mood,
        speed,
        height,
        style_prompt=prompt,
        is_vip_mode=vip,
    )
    if not allowed:
        if vip and (features or {}).get("plan") != "pro":
            return _vip_forbidden_response(err_msg or "👑 VIP 시네마 스튜디오는 프로 VIP 전용입니다.")
        return _payment_response(err_msg or PAYMENT_MESSAGE)

    job_id = uuid.uuid4().hex[:16]
    job_dir = JOBS_DIR / job_id
    media_dir = job_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    out_file = job_dir / "final_shorts.mp4"

    saved = []
    try:
        for index, item in enumerate(files[:MAX_UPLOAD_FILES]):
            filename = _safe_name(item.filename)
            if not filename:
                raise HTTPException(
                    status_code=400,
                    detail="지원하지 않는 파일입니다: {}".format(item.filename),
                )
            dest = media_dir / "{:03d}_{}".format(index + 1, filename)
            dest = await _persist_upload(item, dest)
            saved.append(dest)
            try:
                canvas_w, canvas_h = canvas_size(ratio, height)
                frame_dest = job_dir / "frame_{}.jpg".format(index)
                compose_blur_fill_frame(
                    dest,
                    frame_dest,
                    canvas_w,
                    canvas_h,
                )
                if index == 0:
                    shutil.copy2(str(frame_dest), str(job_dir / "i2v_source.jpg"))
            except Exception as exc:
                print("[안내] 업로드 직후 9:16 프레임 합성 실패: {}".format(exc), flush=True)
            _release_memory()
    except HTTPException:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="업로드 처리 실패: {}".format(exc))

    with JOBS_LOCK:
        JOBS[job_id] = {
            "job_id": job_id,
            "status": "processing",
            "stage": "대기 중",
            "percent": 1,
            "progress": 1,
            "error": None,
            "elapsed_sec": 0,
            "output": str(out_file),
        }
    _persist_job(job_id)

    WORKER.submit(
        _run_job,
        job_id,
        saved,
        prompt,
        out_file,
        voice_key,
        speed,
        mood,
        spark or vip,
        motion,
        height,
        features,
        duration,
        captions,
        fx,
        ratio,
        ducking,
        vip,
        action_on,
        action_style,
        action_preset,
        intensity,
    )
    return {
        "job_id": job_id,
        "status": "processing",
        "voice_type": voice_key,
        "speed_multiplier": speed,
        "bgm_type": mood,
        "bgm_mood": mood,
        "is_spark_cinema": spark or vip,
        "is_runway_mode": spark or vip,
        "is_vip_mode": vip,
        "action_motion_enabled": action_on,
        "action_style": action_style,
        "action_preset": action_preset,
        "camera_motion": motion,
        "motion_intensity": intensity,
        "output_height": height,
        "target_duration": duration,
        "caption_style": captions,
        "visual_fx": fx,
        "aspect_ratio": ratio,
        "audio_ducking": ducking,
        "plan": features.get("plan"),
        "error_code": err_code,
    }


@app.get("/job-status/{job_id}")
def job_status(job_id: str):
    job = _job_snapshot(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    out_file = _job_mp4_path(job)
    stage = str(job.get("stage") or "")
    percent = int(job.get("percent") or job.get("progress") or 0)
    if out_file is not None and _output_valid(out_file) and (
        job.get("status") == "completed"
        or percent >= 96
        or stage.startswith("완료")
        or stage == "completed"
        or "출력 정리" in stage
    ):
        _update_job(
            job_id,
            status="completed",
            percent=100,
            progress=100,
            stage="completed",
            output=str(out_file),
            error=None,
        )
        job = _job_snapshot(job_id) or job
        percent = 100
        stage = "completed"
    return {
        "job_id": job["job_id"],
        "status": job.get("status") or "processing",
        "stage": stage or job.get("stage") or "processing",
        "percent": percent,
        "progress": percent,
        "elapsed_sec": float(job.get("elapsed_sec") or 0),
        "error": job.get("error"),
    }


@app.get("/download/{job_id}")
def download_job(job_id: str):
    job = _job_snapshot(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    out_file = _job_mp4_path(job)
    if out_file is not None and _output_valid(out_file):
        _update_job(
            job_id,
            status="completed",
            percent=100,
            progress=100,
            stage="completed",
            output=str(out_file),
            error=None,
        )
        return FileResponse(
            path=str(out_file),
            media_type="video/mp4",
            filename="final_shorts.mp4",
            headers={"Accept-Ranges": "bytes"},
            background=BackgroundTask(_cleanup_after_download, str(out_file.parent)),
        )
    if job.get("status") == "processing":
        raise HTTPException(status_code=409, detail="아직 영상이 준비되지 않았습니다.")
    raise HTTPException(status_code=404, detail="완성된 영상 파일을 찾지 못했습니다.")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
