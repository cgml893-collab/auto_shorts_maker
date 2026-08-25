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
    normalize_instagram_payload,
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
    keep_names = {
        "status.json",
        "final_shorts.mp4",
        "voice.mp3",
        "bgm.wav",
        "subs.srt",
        "i2v_source.jpg",
        "instagram_caption.json",
        "voice.mp3",
    }
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


def _instagram_fields(payload=None, style_prompt="", script=""):
    ig = normalize_instagram_payload(payload or {}, style_prompt=style_prompt, script=script)
    return {
        "script": str(script or "").strip(),
        "instagram_caption": ig.get("caption") or "",
        "instagram_hashtags": list(ig.get("hashtags") or []),
        "instagram_copy": ig.get("copy_text") or "",
    }


def _write_instagram_artifact(job_dir, fields):
    try:
        job_dir = Path(job_dir)
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "instagram_caption.json").write_text(
            json.dumps(fields, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        print("[안내] 인스타 캡션 저장 실패: {}".format(exc))


def _load_instagram_artifact(job_dir):
    path = Path(job_dir) / "instagram_caption.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


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
    before_after_hook=False,
    ai_lipsync=False,
    parallax_3d=False,
):
    started = time.time()
    target_duration = normalize_target_duration(target_duration)
    budget = min(float(pipeline_time_budget(target_duration)), 28.0)
    deadline = started + budget
    finished = threading.Event()
    write_lock = threading.Lock()
    content_fields = _instagram_fields(style_prompt=prompt)
    viral_flags = {
        "before_after_hook": bool(before_after_hook),
        "ai_lipsync": bool(ai_lipsync),
        "parallax_3d": bool(parallax_3d),
    }

    def _mark_completed(final_output_path=None, extra=None):
        final_output_path = Path(final_output_path or out_file)
        if not _output_valid(final_output_path):
            return False
        payload = {
            "status": "completed",
            "percent": 100,
            "progress": 100,
            "stage": "completed",
            "output": str(final_output_path),
            "error": None,
            "elapsed_sec": round(time.time() - started, 1),
        }
        payload.update(content_fields)
        if isinstance(extra, dict):
            payload.update(extra)
        _update_job(job_id, **payload)
        _write_instagram_artifact(Path(final_output_path).parent, content_fields)
        return True

    def progress(percent, message):
        pct = max(0, min(100, int(percent)))
        msg = str(message or "")
        if pct >= 96 or msg.startswith("완료") or msg == "completed":
            if _mark_completed(out_file):
                return
        fields = {
            "status": "processing",
            "stage": message,
            "percent": pct,
            "progress": pct,
            "elapsed_sec": round(time.time() - started, 1),
        }
        fields.update(content_fields)
        _update_job(job_id, **fields)

    def _force_complete():
        if _mark_completed(out_file):
            return
        work = Path(out_file).parent / "_force_complete"
        os.makedirs(str(work), exist_ok=True)
        with write_lock:
            if _mark_completed(out_file):
                return
            progress(92, "30초 안전 완성 · 균등 슬라이드쇼")
            fast_blur_slideshow(media_files, out_file, work, duration=float(target_duration))
            if not _mark_completed(out_file):
                raise RuntimeError("안전 슬라이드쇼가 완성된 MP4를 만들지 못했습니다.")

    def _watchdog():
        # 무한 폴링 원천 차단: 30초 하드 캡
        if finished.wait(timeout=30.0):
            return
        print("[안내] 30초 하드 캡 도달 → 강제 완성", flush=True)
        try:
            _force_complete()
        except Exception as exc:
            print("[안내] 강제 완성 워치독 실패: {}".format(exc))

    watcher = threading.Thread(target=_watchdog, daemon=True)
    watcher.start()

    try:
        boot = {"stage": "대본 작성 중", "percent": 8, "progress": 8, "elapsed_sec": 0}
        boot.update(content_fields)
        _update_job(job_id, **boot)
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
                before_after_hook=viral_flags["before_after_hook"],
                ai_lipsync=viral_flags["ai_lipsync"],
                parallax_3d=viral_flags["parallax_3d"],
            )
            try:
                result = fut.result(timeout=28.0)
                script_text = ""
                ig_payload = {}
                if isinstance(result, tuple):
                    if len(result) >= 3:
                        script_text = result[1] or ""
                        ig_payload = result[2] if isinstance(result[2], dict) else {}
                    elif len(result) >= 2:
                        script_text = result[1] or ""
                content_fields.update(
                    _instagram_fields(ig_payload, style_prompt=prompt, script=script_text)
                )
            except TimeoutError:
                print("[안내] 파이프라인 28초 초과 → 폴백 완성", flush=True)
                try:
                    _force_complete()
                except Exception as exc:
                    print("[안내] 타임아웃 폴백 실패: {}".format(exc))
            except Exception as exc:
                traceback.print_exc()
                progress(80, "외부 API 대기열 · 30초 안전 슬라이드쇼로 전환")
                try:
                    _force_complete()
                except Exception:
                    print("[안내] 서버 안전장치 폴백 실패: {}".format(exc))
        finally:
            try:
                runner.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                runner.shutdown(wait=False)
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
            try:
                _force_complete()
            except Exception:
                fail = {
                    "status": "failed",
                    "stage": "실패",
                    "error": str(exc),
                    "elapsed_sec": round(time.time() - started, 1),
                }
                fail.update(content_fields)
                _update_job(job_id, **fail)
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


@app.api_route("/i2v-image/{job_id}", methods=["GET", "HEAD"])
def i2v_image(job_id: str):
    """fal.ai가 사전 HEAD 검증 후 GET으로 이미지를 가져간다."""
    job_dir = JOBS_DIR / job_id
    path = None
    for name in ("i2v_source.jpg", "frame_0.jpg"):
        candidate = job_dir / name
        if candidate.is_file() and candidate.stat().st_size >= 32:
            path = candidate
            break
    if path is None:
        media_dir = job_dir / "media"
        if media_dir.is_dir():
            for child in sorted(media_dir.iterdir()):
                if child.suffix.lower() in IMAGE_EXTS and child.stat().st_size >= 32:
                    path = child
                    break
    if path is None:
        raise HTTPException(status_code=404, detail="I2V 소스 이미지를 찾지 못했습니다.")
    headers = {
        "Content-Type": "image/jpeg",
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, max-age=120",
    }
    return FileResponse(
        path=str(path),
        media_type="image/jpeg",
        headers=headers,
    )


@app.api_route("/job-audio/{job_id}", methods=["GET", "HEAD"])
def job_audio(job_id: str):
    """립싱크용 공개 음성 URL (fal.ai가 fetch)."""
    job_dir = JOBS_DIR / job_id
    for name in ("voice.mp3", "voice.wav", "voice.m4a"):
        path = job_dir / name
        if path.is_file() and path.stat().st_size >= 64:
            media = "audio/mpeg" if path.suffix.lower() == ".mp3" else "audio/wav"
            return FileResponse(path=str(path), media_type=media)
    raise HTTPException(status_code=404, detail="작업 음성을 찾지 못했습니다.")


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
            "/job-audio/{job_id}",
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
    before_after_hook: str = Form("false", description="📸 비포/애프터 셔터 전환"),
    ai_lipsync: str = Form("false", description="🗣️ AI 페이스 립싱크"),
    parallax_3d: str = Form("false", description="🌌 3D 공간 입체 무빙"),
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
    ba_hook = parse_flag(before_after_hook)
    lipsync_on = parse_flag(ai_lipsync)
    parallax_on = parse_flag(parallax_3d)
    # 바이럴 토글만으로 fal/스파크를 강제하지 않음 — 명시적 스파크 PRO일 때만 유료 I2V
    # lipsync fal은 스파크 ON일 때만 파이프라인에서 허용
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
            "before_after_hook": ba_hook,
            "ai_lipsync": lipsync_on,
            "parallax_3d": parallax_on,
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
        spark,
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
        ba_hook,
        lipsync_on,
        parallax_on,
    )
    return {
        "job_id": job_id,
        "status": "processing",
        "voice_type": voice_key,
        "speed_multiplier": speed,
        "bgm_type": mood,
        "bgm_mood": mood,
        "is_spark_cinema": spark,
        "is_runway_mode": spark,
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
        "before_after_hook": ba_hook,
        "ai_lipsync": lipsync_on,
        "parallax_3d": parallax_on,
        "zero_cost_mode": not spark,
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
    artifact = _load_instagram_artifact(JOBS_DIR / job_id)
    caption = str(job.get("instagram_caption") or artifact.get("instagram_caption") or "")
    hashtags = job.get("instagram_hashtags") or artifact.get("instagram_hashtags") or []
    if not isinstance(hashtags, list):
        hashtags = []
    copy_text = str(job.get("instagram_copy") or artifact.get("instagram_copy") or "")
    if not copy_text and (caption or hashtags):
        copy_text = "{}\n\n{}".format(caption, " ".join(str(t) for t in hashtags)).strip()
    script = str(job.get("script") or artifact.get("script") or "")
    return {
        "job_id": job["job_id"],
        "status": job.get("status") or "processing",
        "stage": stage or job.get("stage") or "processing",
        "percent": percent,
        "progress": percent,
        "elapsed_sec": float(job.get("elapsed_sec") or 0),
        "error": job.get("error"),
        "script": script,
        "instagram_caption": caption,
        "instagram_hashtags": hashtags,
        "instagram_copy": copy_text,
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
