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

os.environ.setdefault("FFMPEG_TIMEOUT", "60")
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
    diet_image_file,
    fast_blur_slideshow,
    load_settings,
    normalize_bgm_mood,
    normalize_camera_motion,
    normalize_speed,
    parse_flag,
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
    keep_names = {"status.json", "final_shorts.mp4"}
    if keep_file:
        try:
            keep = Path(keep_file).resolve()
        except OSError:
            keep = None
    for child in list(job_dir.iterdir()):
        try:
            if keep is not None and child.resolve() == keep:
                continue
            if child.name in keep_names:
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


def _update_job(job_id, **fields):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(fields)
    _persist_job(job_id)


def _payment_response(message=PAYMENT_MESSAGE):
    return JSONResponse(
        status_code=402,
        content={"error": PAYMENT_REQUIRED, "message": message},
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
):
    started = time.time()
    deadline = started + PIPELINE_HARD_LIMIT
    finished = threading.Event()
    write_lock = threading.Lock()

    def progress(percent, message):
        _update_job(
            job_id,
            status="processing",
            stage=message,
            percent=max(0, min(100, int(percent))),
            progress=max(0, min(100, int(percent))),
            elapsed_sec=round(time.time() - started, 1),
        )

    def _force_blur():
        if Path(out_file).is_file():
            return
        work = Path(out_file).parent / "_force_blur"
        work.mkdir(parents=True, exist_ok=True)
        with write_lock:
            if Path(out_file).is_file():
                return
            progress(92, "30초 강제 완성 · 초고속 3초 블러 슬라이드쇼")
            fast_blur_slideshow(media_files, out_file, work, duration=3.0)

    def _watchdog():
        if finished.wait(timeout=max(1.0, PIPELINE_HARD_LIMIT - 5.0)):
            return
        try:
            _force_blur()
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
            )
            try:
                fut.result(timeout=max(4.0, PIPELINE_HARD_LIMIT - 4.0))
            except Exception as exc:
                if not isinstance(exc, TimeoutError):
                    traceback.print_exc()
                progress(80, "외부 API 대기열 · 초고속 3초 블러 슬라이드쇼로 전환")
                try:
                    _force_blur()
                except Exception:
                    print("[안내] 서버 안전장치 폴백 실패: {}".format(exc))
        finally:
            runner.shutdown(wait=False)
        if not Path(out_file).is_file():
            _force_blur()
        if not Path(out_file).is_file():
            raise RuntimeError("완성된 영상 파일을 찾지 못했습니다.")
        consume_entitlement(features)
        _update_job(
            job_id,
            status="completed",
            stage="완료",
            percent=100,
            progress=100,
            error=None,
            elapsed_sec=round(time.time() - started, 1),
        )
    except Exception as exc:
        traceback.print_exc()
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


@app.get("/")
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
        ],
    }


@app.get("/health")
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
    camera_motion: str = Form("zoom_in", description="zoom_in/drone/pan"),
    output_height: str = Form("720", description="720 또는 1080 (프로)"),
):
    prompt = (style or style_prompt or "").strip()
    if not files:
        raise HTTPException(status_code=400, detail="사진 또는 동영상을 한 개 이상 업로드해 주세요.")

    voice_key, _vid, _preset = resolve_voice(voice_type)
    speed = normalize_speed(speed_multiplier)
    mood = normalize_bgm_mood(bgm_type or bgm_mood)
    spark = parse_flag(is_spark_cinema) or parse_flag(is_runway_mode)
    motion = normalize_camera_motion(camera_motion)
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
    )
    if not allowed:
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
        spark,
        motion,
        height,
        features,
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
        "camera_motion": motion,
        "output_height": height,
        "plan": features.get("plan"),
        "error_code": err_code,
    }


@app.get("/job-status/{job_id}")
def job_status(job_id: str):
    job = _job_snapshot(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    percent = int(job.get("percent") or job.get("progress") or 0)
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "stage": job.get("stage") or "processing",
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
    if job["status"] == "processing":
        raise HTTPException(status_code=409, detail="아직 영상이 준비되지 않았습니다.")
    if job["status"] != "completed":
        raise HTTPException(status_code=500, detail=job.get("error") or "영상 제작에 실패했습니다.")
    out_file = Path(job["output"])
    if not out_file.is_file():
        raise HTTPException(status_code=404, detail="완성된 영상 파일을 찾지 못했습니다.")
    return FileResponse(
        path=str(out_file),
        media_type="video/mp4",
        filename="final_shorts.mp4",
        background=BackgroundTask(_cleanup_after_download, str(out_file.parent)),
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
