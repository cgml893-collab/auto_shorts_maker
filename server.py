# -*- coding: utf-8 -*-
"""스마트폰 앱과 통신하는 FastAPI 모바일 서버."""

from __future__ import annotations

import os
import shutil
import threading
import traceback
import uuid
from pathlib import Path
from typing import List

os.environ.setdefault("FFMPEG_TIMEOUT", "300")

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from license_lock import mobile_hwid, verify_or_activate_mobile
from main import IMAGE_EXTS, OUTPUT_DIR, VIDEO_EXTS, run_pipeline

load_dotenv()

JOBS_DIR = OUTPUT_DIR / "mobile_jobs"
RENDER_LOCK = threading.Lock()
ALLOWED_EXTS = IMAGE_EXTS | VIDEO_EXTS

app = FastAPI(
    title="AI 숏폼 모바일 서버",
    description="사진/영상 업로드로 9:16 숏폼을 만들고, 스마트폰 1대 라이선스를 검증합니다.",
    version="1.0.0",
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


def _cleanup_job(job_dir):
    try:
        shutil.rmtree(job_dir, ignore_errors=True)
    except Exception:
        pass


@app.get("/")
def root():
    return {
        "ok": True,
        "service": "AI 숏폼 모바일 서버",
        "endpoints": ["/verify-license", "/create-video"],
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
):
    ok, message = verify_or_activate_mobile(license_key, device_id, platform)
    if not ok:
        raise HTTPException(status_code=403, detail=message)

    prompt = (style or style_prompt or "").strip()
    if not files:
        raise HTTPException(status_code=400, detail="사진 또는 동영상을 한 개 이상 업로드해 주세요.")

    job_id = uuid.uuid4().hex[:12]
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
        _cleanup_job(job_dir)
        raise
    except Exception as exc:
        _cleanup_job(job_dir)
        raise HTTPException(status_code=400, detail="업로드 처리 실패: {}".format(exc))

    if not RENDER_LOCK.acquire(blocking=False):
        _cleanup_job(job_dir)
        raise HTTPException(
            status_code=429,
            detail="다른 영상을 만드는 중입니다. 잠시 후 다시 시도해 주세요.",
        )

    try:
        run_pipeline(
            saved,
            style_prompt=prompt,
            output_path=out_file,
            check_license=False,
        )
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="영상 제작 실패: {}".format(exc))
    finally:
        RENDER_LOCK.release()

    if not out_file.is_file():
        _cleanup_job(job_dir)
        raise HTTPException(status_code=500, detail="완성된 영상 파일을 찾지 못했습니다.")

    return FileResponse(
        path=str(out_file),
        media_type="video/mp4",
        filename="final_shorts.mp4",
        background=BackgroundTask(_cleanup_job, job_dir),
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
