# -*- coding: utf-8 -*-
"""스마트폰 앱과 통신하는 FastAPI 모바일 서버 (비동기 작업 큐)."""

from __future__ import annotations

import os
import shutil
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List

os.environ.setdefault("FFMPEG_TIMEOUT", "300")

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from license_lock import mobile_hwid, verify_or_activate_mobile
from main import IMAGE_EXTS, OUTPUT_DIR, VIDEO_EXTS, normalize_bgm_mood, normalize_speed, resolve_voice, run_pipeline

load_dotenv()

JOBS_DIR = OUTPUT_DIR / "mobile_jobs"
ALLOWED_EXTS = IMAGE_EXTS | VIDEO_EXTS
JOBS: Dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
WORKER = ThreadPoolExecutor(max_workers=1)

app = FastAPI(
    title="AI 숏폼 모바일 서버",
    description="비동기 작업 큐 + Pillow/FFmpeg 초고속 릴스 엔진",
    version="2.0.0",
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
    license_key: str = Field(..., description="이 스마트폰용 라이선스 키")
    platform: str = Field("", description="android 또는 ios")


def _safe_name(name):
    raw = Path(name or "media").name
    stem = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in Path(raw).stem) or "media"
    suffix = Path(raw).suffix.lower()
    if suffix not in ALLOWED_EXTS:
        return None
    return stem + suffix


def _job_snapshot(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return None
        return dict(job)


def _update_job(job_id, **fields):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(fields)


def _run_job(job_id, media_files, prompt, out_file, voice_type, speed_multiplier, bgm_mood):
    def progress(percent, message):
        _update_job(
            job_id,
            status="processing",
            stage=message,
            percent=max(0, min(100, int(percent))),
        )

    try:
        _update_job(job_id, stage="대본 작성 중", percent=8)
        run_pipeline(
            media_files,
            style_prompt=prompt,
            progress_cb=progress,
            output_path=out_file,
            check_license=False,
            voice_type=voice_type,
            speed_multiplier=speed_multiplier,
            bgm_mood=bgm_mood,
        )
        if not Path(out_file).is_file():
            raise RuntimeError("완성된 영상 파일을 찾지 못했습니다.")
        _update_job(
            job_id,
            status="completed",
            stage="완료",
            percent=100,
            error=None,
        )
    except Exception as exc:
        traceback.print_exc()
        _update_job(
            job_id,
            status="failed",
            stage="실패",
            error=str(exc),
        )


@app.get("/")
def root():
    return {
        "ok": True,
        "service": "AI 숏폼 모바일 서버",
        "endpoints": [
            "/verify-license",
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
    ok, message = verify_or_activate_mobile(
        body.license_key, body.device_id, body.platform
    )
    hwid = mobile_hwid(body.device_id, body.platform)
    short = "-".join(hwid[i : i + 4] for i in range(0, 16, 4))
    if not ok:
        raise HTTPException(status_code=403, detail=message)
    return {
        "ok": True,
        "message": message,
        "device_bound": True,
        "machine_code": short,
        "platform": (body.platform or "").strip().lower(),
    }


@app.post("/create-video")
async def create_video(
    files: List[UploadFile] = File(..., description="사진/동영상 (여러 개 가능)"),
    style: str = Form("", description="스타일 프롬프트"),
    style_prompt: str = Form("", description="style 별칭"),
    license_key: str = Form(..., description="라이선스 키"),
    device_id: str = Form(..., description="Android ID / iOS Vendor ID"),
    platform: str = Form("", description="android 또는 ios"),
    voice_type: str = Form("bright_female", description="energetic_male/bright_female/calm_male/story_female"),
    speed_multiplier: str = Form("1.0", description="1.0, 1.2, 1.5"),
    bgm_mood: str = Form("upbeat", description="upbeat/emotional/tense/none"),
):
    ok, message = verify_or_activate_mobile(license_key, device_id, platform)
    if not ok:
        raise HTTPException(status_code=403, detail=message)

    prompt = (style or style_prompt or "").strip()
    if not files:
        raise HTTPException(status_code=400, detail="사진 또는 동영상을 한 개 이상 업로드해 주세요.")

    voice_key, _vid, _preset = resolve_voice(voice_type)
    speed = normalize_speed(speed_multiplier)
    mood = normalize_bgm_mood(bgm_mood)

    job_id = uuid.uuid4().hex[:16]
    job_dir = JOBS_DIR / job_id
    media_dir = job_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    out_file = job_dir / "final_shorts.mp4"

    saved = []
    try:
        for index, item in enumerate(files):
            filename = _safe_name(item.filename)
            if not filename:
                raise HTTPException(
                    status_code=400,
                    detail="지원하지 않는 파일입니다: {}".format(item.filename),
                )
            dest = media_dir / "{:03d}_{}".format(index + 1, filename)
            data = await item.read()
            if not data:
                raise HTTPException(status_code=400, detail="빈 파일입니다: {}".format(item.filename))
            dest.write_bytes(data)
            saved.append(dest)
            await item.close()
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
            "error": None,
            "output": str(out_file),
        }

    WORKER.submit(_run_job, job_id, saved, prompt, out_file, voice_key, speed, mood)
    return {
        "job_id": job_id,
        "status": "processing",
        "voice_type": voice_key,
        "speed_multiplier": speed,
        "bgm_mood": mood,
    }


@app.get("/job-status/{job_id}")
def job_status(job_id: str):
    job = _job_snapshot(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "stage": job.get("stage") or "processing",
        "percent": int(job.get("percent") or 0),
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
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
