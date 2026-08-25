# -*- coding: utf-8 -*-
"""input_media의 사진/동영상으로 9:16 숏폼(유튜브 쇼츠/릴스)을 자동 생성한다."""

from __future__ import annotations

import asyncio
import base64
import gc
import io
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import requests
from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
from license_lock import require_license, verify_saved_license

try:
    from moviepy import (
        AudioFileClip,
        CompositeAudioClip,
        CompositeVideoClip,
        ImageClip,
        VideoFileClip,
        concatenate_audioclips,
        concatenate_videoclips,
    )
except ImportError:
    from moviepy.editor import (
        AudioFileClip,
        CompositeAudioClip,
        CompositeVideoClip,
        ImageClip,
        VideoFileClip,
        concatenate_audioclips,
        concatenate_videoclips,
    )


ROOT = Path(__file__).resolve().parent
INPUT_DIR = ROOT / "input_media"
OUTPUT_DIR = ROOT / "output"
FONTS_DIR = ROOT / "fonts"
BGM_DIR = ROOT / "bgm"
SFX_DIR = ROOT / "sfx"
VOICE_PATH = OUTPUT_DIR / "voice.mp3"
FINAL_PATH = OUTPUT_DIR / "final_shorts.mp4"

TARGET_W = 720
TARGET_H = 1280
FPS = 24
STILL_FPS = 12
XFADE_SEC = 0.4
BLUR_RADIUS = 26
BLUR_DIM = 0.38
FAL_I2V_PRIMARY = os.getenv("FAL_I2V_MODEL", "fal-ai/kling-video/v1/standard/image-to-video")
FAL_I2V_FALLBACK = "fal-ai/minimax/video-01/image-to-video"
FAL_I2V_FAST = os.getenv("FAL_I2V_FAST_MODEL", "fal-ai/stable-video")
FAL_LIPSYNC_MODEL = os.getenv("FAL_LIPSYNC_MODEL", "fal-ai/sync-lipsync/v3/image-to-video")
FAL_WAIT_TIMEOUT = 12.0
HARD_JOB_LIMIT_SEC = 30.0
BEFORE_AFTER_SEC = 1.5
BEFORE_SWIPE_SEC = 0.45
MOTION_INTENSITY_DEFAULT = 7
MOTION_BUCKET_ID = 180
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or "https://auto-shorts-maker.onrender.com").rstrip("/")
NATURAL_I2V_PROMPT = (
    "Keep the exact subject from the input photo. Add physically accurate motion that matches "
    "the subject: wheels spin, hair or fur moves, limbs shift, breathing, a slight weight transfer. "
    "Photorealistic, preserve identity, no morphing, no extra limbs, no slideshow, cinematic 9:16."
)
PARALLAX_I2V_PROMPT = (
    "Strong 3D parallax camera push-in: foreground subject and background planes move at different "
    "speeds for volumetric depth. Keep exact identity, photorealistic, cinematic 9:16, no morphing."
)
LIPSYNC_I2V_PROMPT = (
    "Talking portrait close-up: natural lip sync mouth shapes, subtle facial expression, blinking, "
    "micro head motion matching speech rhythm. Preserve exact face identity, photorealistic 9:16."
)
SPARK_MAX_CLIPS = 3
SPARK_CLIP_SEC = 5.0
I2V_QUALITY_LAYER = "Photorealistic physics, 4k 60fps, 35mm anamorphic lens, preserve exact identity, no morphing, no extra limbs"
MOTION_CLIP_EXTS = (".mp4", ".mov", ".webm", ".m4v")
PIPELINE_HARD_LIMIT = 90.0
FAST_BLUR_SEC = 3.0
HD_W = 1080
HD_H = 1920
DURATION_TARGETS = {
    15: {"min_chars": 130, "max_chars": 160, "label": "15초 쇼츠"},
    30: {"min_chars": 260, "max_chars": 300, "label": "30초 스토리텔링"},
    60: {"min_chars": 500, "max_chars": 580, "label": "60초 롱쇼츠"},
}
CAMERA_MOTIONS = {
    "zoom_in": "Cinematic Push-in, keep the subject centered, photorealistic.",
    "push_in": "Cinematic Push-in, keep the subject centered, photorealistic.",
    "drone": "Orbit 360, smooth aerial orbit keeping subject identity.",
    "orbit": "Orbit 360, smooth cinematic orbit around the subject.",
    "pan": "Dynamic Low-angle Pan across the scene with natural parallax.",
    "low_angle": "Dynamic Low-angle Pan, tracking along the ground plane.",
    "fpv": "FPV Tracking Shot, aggressive forward follow through the scene.",
}
CAMERA_LAYER = {
    "zoom_in": "Cinematic Push-in",
    "push_in": "Cinematic Push-in",
    "drone": "Orbit 360",
    "orbit": "Orbit 360",
    "pan": "Dynamic Low-angle Pan",
    "low_angle": "Dynamic Low-angle Pan",
    "fpv": "FPV Tracking Shot",
}
SHOT_SEQUENCE = (
    ("wide", "Opening wide establishing shot, full subject and environment visible, FPV tracking, natural parallax"),
    ("action", "Dynamic action shot, forceful subject physics, motion blur, dynamic low-angle tracking camera"),
    ("close", "Cinematic close-up, shallow depth of field, blinking, breathing, organic micro-motion"),
)
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}

DEFAULT_VOICE_ID = "cgSgspJ2msm6clMCkdW9"
ELEVENLABS_MODEL = "eleven_multilingual_v2"
ALLOWED_SPEEDS = (1.0, 1.2, 1.5)
DEFAULT_VOICE_TYPE = "vlog_female"
DEFAULT_BGM_MOOD = "pop"

# Premade multilingual voices that speak Korean well on eleven_multilingual_v2.
VOICE_PRESETS = {
    "variety_male": {
        "id": "iP95p4xoKVk53GoZ742B",  # Chris
        "stability": 0.26,
        "similarity_boost": 0.68,
        "style": 0.78,
    },
    "variety_female": {
        "id": "cgSgspJ2msm6clMCkdW9",  # Jessica
        "stability": 0.32,
        "similarity_boost": 0.76,
        "style": 0.70,
    },
    "vlog_female": {
        "id": "pFZP5JQG7iQjIQuC4Bku",  # Lily
        "stability": 0.46,
        "similarity_boost": 0.84,
        "style": 0.48,
    },
    "fast_story_male": {
        "id": "bIHbv24MWmeRgasZH58o",  # Will
        "stability": 0.30,
        "similarity_boost": 0.72,
        "style": 0.66,
    },
    "docu_male": {
        "id": "onwK4e9ZLuTAKqWW03F9",  # Daniel
        "stability": 0.66,
        "similarity_boost": 0.84,
        "style": 0.16,
    },
    "radio_female": {
        "id": "EXAVITQu4vr4xnSDxMaL",  # Sarah
        "stability": 0.50,
        "similarity_boost": 0.80,
        "style": 0.40,
    },
    "news_male": {
        "id": "nPczCjzI2devNBz1zQrb",  # Brian
        "stability": 0.62,
        "similarity_boost": 0.82,
        "style": 0.12,
    },
    "news_female": {
        "id": "FGY2WhTYpPnrIDTdsKH5",  # Laura
        "stability": 0.58,
        "similarity_boost": 0.80,
        "style": 0.18,
    },
}
BGM_MOODS = ("variety", "lofi", "phonk", "pop", "acoustic", "suspense", "cinematic", "none")

SUB_MAX_WIDTH = 680
SUB_FONT_SIZE = 58
SUB_STROKE = 8
SUB_LINE_GAP = 10
SUB_PAD_X = 28
SUB_PAD_Y = 20
SUB_BOTTOM_MARGIN = 220
POP_ANIM_SEC = 0.42
VOICE_GAIN = 1.05
BGM_GAIN_MIN = 0.12
BGM_GAIN_MAX = 0.26
POP_GAIN = 0.72
WHOOSH_GAIN = 0.58
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg"}


@dataclass(frozen=True)
class StyleDirection:
    tone: str
    script_guide: str
    fill: Tuple[int, int, int]
    stroke: Tuple[int, int, int]
    font_scale: float
    xfade: float
    speed: float
    sfx: str


def default_style_direction(style_prompt=""):
    text = (style_prompt or "").lower()
    fill, stroke = (255, 255, 255), (0, 0, 0)
    xfade, speed, sfx, tone = 0.4, 1.0, "soft", "자연스러운 구어체"
    if any(k in text for k in ("무한도전", "예능", "variety", "개그", "코미디")):
        fill, stroke = (255, 224, 64), (20, 20, 20)
        xfade, speed, sfx, tone = 0.18, 1.2, "poppy", "빠르고 장난기 있는 예능 톤"
    elif any(k in text for k in ("뉴스", "브리핑", "news")):
        fill, stroke = (245, 248, 255), (12, 28, 72)
        xfade, speed, sfx, tone = 0.22, 1.0, "none", "단정한 뉴스 앵커 톤"
    elif any(k in text for k in ("다큐", "시네마", "cinematic")):
        fill, stroke = (255, 236, 210), (40, 24, 8)
        xfade, speed, sfx, tone = 0.5, 1.0, "soft", "낮고 차분한 내레이션"
    elif any(k in text for k in ("브이로그", "vlog", "감성")):
        fill, stroke = (255, 255, 255), (30, 18, 40)
        xfade, speed, sfx, tone = 0.45, 1.0, "soft", "친근한 브이로그 말투"
    return StyleDirection(
        tone=tone,
        script_guide=tone + ". 지정 스타일의 컷 템포와 말맛에 맞출 것.",
        fill=fill,
        stroke=stroke,
        font_scale=1.08 if "예능" in text or "무한도전" in text else 1.0,
        xfade=xfade,
        speed=speed,
        sfx=sfx,
    )


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    elevenlabs_api_key: str
    fal_key: str
    elevenlabs_voice_id: str


def load_settings():
    # type: () -> Settings
    load_dotenv(ROOT / ".env")
    openai_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    eleven_key = (os.getenv("ELEVENLABS_API_KEY") or "").strip()
    fal_key = (os.getenv("FAL_KEY") or "").strip()
    voice_id = (os.getenv("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE_ID).strip()

    missing = []
    if not openai_key:
        missing.append("OPENAI_API_KEY")
    if not eleven_key:
        missing.append("ELEVENLABS_API_KEY")
    if missing:
        raise RuntimeError(
            ".env에 다음 키가 없습니다: "
            + ", ".join(missing)
            + "\n프로젝트 루트에 .env 파일을 만들고 값을 넣어 주세요."
        )

    os.environ["FAL_KEY"] = fal_key
    if fal_key:
        os.environ.setdefault("FAL_KEY_ID", fal_key.split(":")[0] if ":" in fal_key else fal_key)
    return Settings(
        openai_api_key=openai_key,
        elevenlabs_api_key=eleven_key,
        fal_key=fal_key,
        elevenlabs_voice_id=voice_id,
    )


def collect_media():
    # type: () -> List[Path]
    if not INPUT_DIR.exists():
        raise RuntimeError("input_media 폴더가 없습니다: {}".format(INPUT_DIR))

    files = []
    for path in sorted(INPUT_DIR.iterdir(), key=lambda x: x.name.lower()):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTS | VIDEO_EXTS:
            files.append(path)
    if not files:
        raise RuntimeError("input_media 폴더에 사진 또는 동영상이 없습니다.")
    return files


def find_korean_font():
    # type: () -> str
    candidates = []
    if FONTS_DIR.exists():
        candidates.extend(sorted(FONTS_DIR.glob("*.ttf")))
        candidates.extend(sorted(FONTS_DIR.glob("*.otf")))
        candidates.extend(sorted(FONTS_DIR.glob("*.ttc")))
    candidates.extend(
        [
            Path(r"C:\Windows\Fonts\malgunbd.ttf"),
            Path(r"C:\Windows\Fonts\malgun.ttf"),
            Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
            Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
            Path("/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
            Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.otf"),
            Path("/usr/share/fonts/truetype/noto-cjk/NotoSansCJK-Bold.ttc"),
            Path("/usr/share/fonts/opentype/noto-cjk/NotoSansCJK-Bold.ttc"),
            Path("/System/Library/Fonts/AppleSDGothicNeo.ttc"),
        ]
    )
    for path in candidates:
        if path.is_file():
            return str(path)
    raise RuntimeError(
        "한글 폰트를 찾지 못했습니다. fonts 폴더에 .ttf 파일을 넣거나 "
        "Windows 맑은 고딕(malgunbd.ttf)을 설치해 주세요."
    )


def _load_font(font_path, size):
    try:
        return ImageFont.truetype(font_path, size)
    except OSError:
        return ImageFont.truetype(font_path, size, index=0)


Image.MAX_IMAGE_PIXELS = 25_000_000
DIET_MAX_W = 720
DIET_MAX_H = 1280
DIET_MAX_BYTES = 500 * 1024


def open_image_upright(path):
    img = Image.open(path)
    try:
        fixed = ImageOps.exif_transpose(img)
        if fixed is not None and fixed is not img:
            img.close()
            img = fixed
        elif fixed is not None:
            img = fixed
    except Exception:
        pass
    return img


def diet_image_file(path, dest=None, max_w=None, max_h=None, max_bytes=None, quality=85):
    path = Path(path)
    if path.suffix.lower() not in IMAGE_EXTS:
        return path
    dest = Path(dest) if dest else path.with_name(path.stem + "_diet.jpg")
    max_w = int(max_w or DIET_MAX_W)
    max_h = int(max_h or DIET_MAX_H)
    max_bytes = int(max_bytes or DIET_MAX_BYTES)
    resampling = getattr(Image, "Resampling", Image)
    try:
        with Image.open(path) as raw:
            try:
                raw.draft("RGB", (max_w, max_h))
            except Exception:
                pass
            try:
                fixed = ImageOps.exif_transpose(raw)
                img = fixed if fixed is not None else raw
            except Exception:
                img = raw
            rgb = img.convert("RGB")
        rgb.thumbnail((max_w, max_h), resampling.BILINEAR)
        payload = None
        q = int(quality)
        while q >= 40:
            buf = io.BytesIO()
            rgb.save(buf, format="JPEG", quality=q, optimize=True)
            payload = buf.getvalue()
            if len(payload) <= max_bytes:
                break
            q -= 10
            nw = max(2, int(rgb.width * 0.82))
            nh = max(2, int(rgb.height * 0.82))
            rgb = rgb.resize((nw, nh), resampling.BILINEAR)
        rgb.close()
        if not payload:
            return path
        os.makedirs(str(dest.parent), exist_ok=True)
        dest.write_bytes(payload)
        if not os.path.exists(str(dest)) or os.path.getsize(str(dest)) < 32:
            raise RuntimeError("이미지 저장 실패: {}".format(dest))
        if dest.resolve() != path.resolve():
            try:
                path.unlink()
            except OSError:
                pass
        gc.collect()
        return dest
    except Exception as exc:
        print("[안내] 이미지 초경량 압축 실패, 폴백 저장: {}".format(exc))
        try:
            os.makedirs(str(dest.parent), exist_ok=True)
            with Image.open(path) as raw:
                raw.convert("RGB").save(str(dest), "JPEG", quality=80)
            if os.path.exists(str(dest)) and os.path.getsize(str(dest)) >= 32:
                gc.collect()
                return dest
        except Exception:
            pass
        gc.collect()
        return path


def _pil_to_jpeg_b64(image, max_side=1024):
    # type: (Image.Image, int) -> str
    try:
        transposed = ImageOps.exif_transpose(image)
        if transposed is not None:
            image = transposed
    except Exception:
        pass
    img = image.convert("RGB")
    width, height = img.size
    scale = min(1.0, float(max_side) / float(max(width, height)))
    if scale < 1.0:
        resample = getattr(Image, "Resampling", Image).LANCZOS
        img = img.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            resample,
        )
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def media_to_preview_b64(path):
    # type: (Path) -> Optional[str]
    suffix = path.suffix.lower()
    try:
        if suffix in IMAGE_EXTS:
            with open_image_upright(path) as im:
                return _pil_to_jpeg_b64(im)
        if suffix in VIDEO_EXTS:
            preview = OUTPUT_DIR / ("_preview_{}.jpg".format(path.stem))
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            try:
                ffmpeg_extract_still(path, preview, ss="0.3", qv=6, timeout=20)
                with open_image_upright(preview) as im:
                    return _pil_to_jpeg_b64(im)
            except Exception:
                clip = VideoFileClip(str(path))
                try:
                    t = min(0.5, max(0.0, float(clip.duration or 1) * 0.1))
                    frame = clip.get_frame(t)
                finally:
                    clip.close()
                return _pil_to_jpeg_b64(Image.fromarray(frame.astype("uint8")))
    except Exception as exc:
        print("[경고] 미리보기 추출 실패 ({}): {}".format(path.name, exc))
    return None


def sanitize_narration(text):
    # type: (str) -> str
    cleaned = (text or "").replace("\r", "\n")
    cleaned = re.sub(r"(?i)https?://\S+", " ", cleaned)
    cleaned = re.sub(r"[#＃][0-9A-Za-z가-힣_]+", " ", cleaned)
    cleaned = cleaned.replace("#", " ").replace("＃", " ")
    cleaned = re.sub(r"[^\S\n]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"^[\s\-•·]+", "", cleaned, flags=re.M)
    return cleaned.strip(" \"'`")


def resolve_voice(voice_type):
    key = (voice_type or DEFAULT_VOICE_TYPE).strip().lower()
    aliases = {
        "variety_male": "variety_male",
        "variety_female": "variety_female",
        "vlog_female": "vlog_female",
        "fast_story_male": "fast_story_male",
        "docu_male": "docu_male",
        "radio_female": "radio_female",
        "news_male": "news_male",
        "news_female": "news_female",
        "bright_female": "variety_female",
        "energetic_male": "variety_male",
        "calm_male": "docu_male",
        "story_female": "vlog_female",
        "female": "vlog_female",
        "male": "variety_male",
    }
    key = aliases.get(key, DEFAULT_VOICE_TYPE)
    preset = VOICE_PRESETS[key]
    env_name = "ELEVENLABS_VOICE_" + key.upper()
    voice_id = (os.getenv(env_name) or preset["id"] or DEFAULT_VOICE_ID).strip()
    return key, voice_id, preset


def parse_flag(value):
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def normalize_camera_motion(value):
    key = (value or "zoom_in").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "zoom_in": "zoom_in",
        "zoom": "zoom_in",
        "push_in": "push_in",
        "pushin": "push_in",
        "줌인": "zoom_in",
        "푸시인": "push_in",
        "drone": "orbit",
        "drone_shot": "orbit",
        "orbit": "orbit",
        "orbit_360": "orbit",
        "드론": "orbit",
        "오빗": "orbit",
        "pan": "low_angle",
        "panning": "low_angle",
        "low_angle": "low_angle",
        "lowangle": "low_angle",
        "패닝": "low_angle",
        "로우앵글": "low_angle",
        "fpv": "fpv",
        "tracking": "fpv",
        "트래킹": "fpv",
    }
    return aliases.get(key, "zoom_in")


def normalize_motion_intensity(value):
    try:
        intensity = int(round(float(value)))
    except (TypeError, ValueError):
        intensity = MOTION_INTENSITY_DEFAULT
    return max(6, min(8, intensity))


def normalize_speed(value):
    try:
        speed = float(value)
    except (TypeError, ValueError):
        return 1.0
    closest = min(ALLOWED_SPEEDS, key=lambda item: abs(item - speed))
    return float(closest)


def normalize_bgm_mood(value):
    mood = (value or DEFAULT_BGM_MOOD).strip().lower()
    aliases = {
        "variety": "variety",
        "lofi": "lofi",
        "phonk": "phonk",
        "pop": "pop",
        "acoustic": "acoustic",
        "suspense": "suspense",
        "cinematic": "cinematic",
        "none": "none",
        "off": "none",
        "mute": "none",
        "upbeat": "pop",
        "beat": "pop",
        "emotional": "lofi",
        "vlog": "lofi",
        "tense": "suspense",
        "funk": "phonk",
        "punk": "phonk",
    }
    return aliases.get(mood, DEFAULT_BGM_MOOD)


def normalize_target_duration(value):
    try:
        seconds = int(round(float(value)))
    except (TypeError, ValueError):
        seconds = 15
    if seconds >= 50:
        return 60
    if seconds >= 22:
        return 30
    return 15


def pipeline_time_budget(duration):
    # 모바일 무한 폴링 방지: 어떤 길이여도 하드 캡 30초 안에 폴백 완성
    del duration
    return float(HARD_JOB_LIMIT_SEC)


def ffmpeg_extract_still(src, dest, ss="0.15", qv=2, timeout=20):
    """단일 프레임 JPEG 추출. FFmpeg image2 시퀀스 경고 방지용 -update 1 포함."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-ss",
            str(ss),
            "-i",
            str(src),
            "-frames:v",
            "1",
            "-update",
            "1",
            "-q:v",
            str(qv),
            str(dest),
        ],
        timeout=timeout,
    )
    return dest


def normalize_caption_style(value):
    key = (value or "hormozi").strip().lower().replace("-", "_").replace(" ", "")
    aliases = {
        "hormozi": "hormozi",
        "호모지": "hormozi",
        "호르모지": "hormozi",
        "neon": "neon",
        "neonpop": "neon",
        "네온": "neon",
        "네온팝": "neon",
        "minimal": "minimal",
        "미니멀": "minimal",
        "variety": "variety",
        "예능": "variety",
        "예능볼드": "variety",
    }
    return aliases.get(key, "hormozi")


def normalize_visual_fx(value):
    key = (value or "ken_burns").strip().lower().replace("-", "_").replace(" ", "")
    aliases = {
        "ken_burns": "ken_burns",
        "kenburns": "ken_burns",
        "켄번스": "ken_burns",
        "켄번스무빙": "ken_burns",
        "zoom_in": "ken_burns",
        "zoom_punch": "zoom_punch",
        "zoompunch": "zoom_punch",
        "다이내믹줌": "zoom_punch",
        "dynamiczoom": "zoom_punch",
        "cinematic": "cinematic",
        "시네마틱": "cinematic",
        "drone": "cinematic",
        "pan": "cinematic",
    }
    return aliases.get(key, "ken_burns")


def normalize_aspect_ratio(value):
    key = (value or "9:16").strip().lower().replace(" ", "")
    aliases = {
        "9:16": "9:16",
        "9x16": "9:16",
        "vertical": "9:16",
        "portrait": "9:16",
        "세로": "9:16",
        "16:9": "16:9",
        "16x9": "16:9",
        "horizontal": "16:9",
        "landscape": "16:9",
        "가로": "16:9",
        "1:1": "1:1",
        "1x1": "1:1",
        "square": "1:1",
        "정사각": "1:1",
    }
    return aliases.get(key, "9:16")


def canvas_size(aspect_ratio, output_height=720):
    aspect = normalize_aspect_ratio(aspect_ratio)
    hd = int(output_height or 720) >= 1080
    if aspect == "16:9":
        return (1920, 1080) if hd else (1280, 720)
    if aspect == "1:1":
        return (1080, 1080) if hd else (720, 720)
    return (1080, 1920) if hd else (720, 1280)


def even_scene_durations(count, total):
    n = max(1, int(count or 1))
    total = max(1.0, float(total))
    each = total / float(n)
    durations = [each] * n
    durations[-1] = max(0.2, total - sum(durations[:-1]))
    return durations


def visual_fx_filter(style, width, height, fps):
    w, h, f = int(width), int(height), int(fps or FPS)
    del style
    return (
        "scale={w}:{h}:force_original_aspect_ratio=disable,setsar=1,fps={f},format=yuv420p"
    ).format(w=w, h=h, f=f)


def caption_force_style(style, font_path):
    name = subtitle_font_name(font_path)
    style = normalize_caption_style(style)
    if style == "neon":
        raw = (
            "FontName={},FontSize=26,Bold=1,PrimaryColour=&H00FF66FF,"
            "OutlineColour=&H00FF0099,BackColour=&H80000000,BorderStyle=1,"
            "Outline=3,Shadow=2,Alignment=2,MarginV=56"
        ).format(name)
    elif style == "minimal":
        raw = (
            "FontName={},FontSize=22,Bold=0,PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00000000,BackColour=&H90000000,BorderStyle=4,"
            "Outline=0,Shadow=0,Alignment=2,MarginV=52,MarginL=40,MarginR=40"
        ).format(name)
    elif style == "variety":
        raw = (
            "FontName={},FontSize=30,Bold=1,PrimaryColour=&H00F0F0FF,"
            "OutlineColour=&H00000000,BackColour=&H00000000,BorderStyle=1,"
            "Outline=5,Shadow=1,Alignment=2,MarginV=50"
        ).format(name)
    else:
        raw = (
            "FontName={},FontSize=24,Bold=1,PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00000000,BackColour=&H00000000,BorderStyle=1,"
            "Outline=3,Shadow=0,Alignment=2,MarginV=72,MarginL=48,MarginR=48"
        ).format(name)
    return raw.replace(",", "\\,")


def ducking_audio_filter(speed=1.0, voice_idx=1, bgm_idx=2):
    tempo = ""
    if abs(float(speed) - 1.0) > 0.001:
        tempo = "," + atempo_chain(speed)
    return (
        "[{v}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo{tempo},asplit=2[voice][sc];"
        "[{b}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo{tempo},volume=0.55[bgm];"
        "[bgm][sc]sidechaincompress=threshold=0.045:ratio=12:attack=20:release=260:makeup=1:knee=8[dk];"
        "[voice]volume=1.08[va];"
        "[va][dk]amix=inputs=2:duration=first:dropout_transition=0[a]"
    ).format(v=int(voice_idx), b=int(bgm_idx), tempo=tempo)


def conform_audio_duration(src, dest, seconds):
    seconds = max(1.0, float(seconds))
    dest = Path(dest)
    run_ffmpeg(
        [
            "-i",
            str(src),
            "-af",
            "aresample=44100,apad=pad_dur=240,atrim=0:{:.3f},asetpts=PTS-STARTPTS".format(seconds),
            "-t",
            "{:.3f}".format(seconds),
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(dest),
        ],
        timeout=40,
    )
    return dest


def atempo_chain(speed):
    remaining = float(speed)
    parts = []
    while remaining > 2.0001:
        parts.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        parts.append("atempo=0.5")
        remaining /= 0.5
    parts.append("atempo={:.4f}".format(remaining))
    return ",".join(parts)


def parse_photo_order(raw, media_count):
    # type: (str, int) -> Tuple[str, List[int]]
    text = (raw or "").strip()
    order = []
    match = re.search(r"(?:PHOTO_ORDER|사진순서|순서)\s*[:：]\s*([0-9,\s\-]+)", text, re.I)
    if match:
        order = [int(num) - 1 for num in re.findall(r"\d+", match.group(1))]
        text = (text[: match.start()] + text[match.end() :]).strip()
    order = [idx for idx in order if 0 <= idx < media_count]
    return text, order


def _extract_json(text):
    raw = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.S)
    if fence:
        raw = fence.group(1)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def interpret_style_direction(settings, style_prompt=""):
    base = default_style_direction(style_prompt)
    style = (style_prompt or "").strip() or "감성 브이로그"
    try:
        client = OpenAI(api_key=settings.openai_api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": (
                        "한국어 숏폼 연출 디렉션을 JSON만 반환하세요. 스타일: {}\n"
                        '{{"tone":"대본 어조","script_guide":"대본 작성 지시 한 줄",'
                        '"fill":[R,G,B],"stroke":[R,G,B],"font_scale":1.0,'
                        '"xfade":0.4,"speed":1.0,"sfx":"poppy|soft|none",'
                        '"cut_tempo":"fast|normal|slow"}}'
                    ).format(style),
                }
            ],
            temperature=0.4,
            max_tokens=280,
        )
        data = _extract_json(response.choices[0].message.content or "")
        fill = tuple(int(x) for x in (data.get("fill") or base.fill)[:3])
        stroke = tuple(int(x) for x in (data.get("stroke") or base.stroke)[:3])
        xfade = float(data.get("xfade") or base.xfade)
        tempo = (data.get("cut_tempo") or "").lower()
        if tempo == "fast":
            xfade = min(xfade, 0.2)
        elif tempo == "slow":
            xfade = max(xfade, 0.45)
        return StyleDirection(
            tone=(data.get("tone") or base.tone).strip(),
            script_guide=(data.get("script_guide") or base.script_guide).strip(),
            fill=(max(0, min(255, fill[0])), max(0, min(255, fill[1])), max(0, min(255, fill[2]))),
            stroke=(max(0, min(255, stroke[0])), max(0, min(255, stroke[1])), max(0, min(255, stroke[2]))),
            font_scale=max(0.85, min(1.25, float(data.get("font_scale") or base.font_scale))),
            xfade=max(0.12, min(0.6, xfade)),
            speed=normalize_speed(data.get("speed") or base.speed),
            sfx=(data.get("sfx") or base.sfx).strip() or "soft",
        )
    except Exception as exc:
        print("[안내] 스타일 해석 폴백: {}".format(exc))
        return base


def analyze_media_styles(settings, media_files):
    if not media_files:
        raise RuntimeError("분석할 미디어가 없습니다.")
    b64 = media_to_preview_b64(media_files[0])
    prompt = (
        "사진/영상 첫 프레임을 보고 장소, 분위기, 행동을 파악한 뒤 "
        "한국어 JSON만 반환하세요.\n"
        '{"place":"","mood":"","action":"","theme":"",'
        '"styles":[{"label":"짧은 칩 제목","prompt":"실제 입력용 스타일 프롬프트"}]}'
        "\nstyles는 3~4개. prompt는 예능/브이로그/뉴스 등 연출이 드러나게."
    )
    content = [{"type": "text", "text": prompt}]
    if b64:
        content.append(
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}}
        )
    client = OpenAI(api_key=settings.openai_api_key)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": content}],
        temperature=0.7,
        max_tokens=420,
    )
    data = _extract_json(response.choices[0].message.content or "")
    styles = []
    for item in data.get("styles") or []:
        if isinstance(item, dict):
            label = (item.get("label") or "").strip()
            prompt_text = (item.get("prompt") or label).strip()
            if prompt_text:
                styles.append({"label": label or prompt_text, "prompt": prompt_text})
        elif isinstance(item, str) and item.strip():
            styles.append({"label": item.strip(), "prompt": item.strip()})
    if len(styles) < 3:
        styles = [
            {"label": "감성 브이로그", "prompt": "감성 브이로그"},
            {"label": "무한도전 스타일", "prompt": "무한도전 스타일"},
            {"label": "뉴스 브리핑", "prompt": "뉴스 브리핑"},
            {"label": "시네마틱 하이라이트", "prompt": "시네마틱 하이라이트"},
        ]
    return {
        "place": (data.get("place") or "").strip(),
        "mood": (data.get("mood") or "").strip(),
        "action": (data.get("action") or "").strip(),
        "theme": (data.get("theme") or "").strip(),
        "styles": styles[:4],
    }


def smart_prepare_media(path, work_dir):
    path = Path(path)
    if path.suffix.lower() not in VIDEO_EXTS:
        return path
    duration = probe_duration(path)
    if duration <= 60:
        return path
    start = _peak_window_start(path, duration, window=25.0)
    dest = work_dir / ("highlight_{}.mp4".format(path.stem[:24]))
    print("   긴 영상 {:.1f}초 → 하이라이트 {:.1f}초부터 25초 추출".format(duration, start))
    run_ffmpeg(
        [
            "-ss",
            "{:.3f}".format(start),
            "-t",
            "25",
            "-i",
            str(path),
            "-vf",
            "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            str(dest),
        ],
        timeout=45,
    )
    return dest if dest.is_file() else path


def _peak_window_start(path, duration, window=25.0):
    window = min(30.0, max(20.0, float(window)))
    if duration <= window + 0.5:
        return 0.0
    tmp = Path(path).parent / ("_rms_{}.wav".format(uuid.uuid4().hex[:8]))
    try:
        run_ffmpeg(
            [
                "-i",
                str(path),
                "-t",
                "180",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "8000",
                str(tmp),
            ],
            timeout=35,
        )
        with wave.open(str(tmp), "rb") as wav_file:
            sr = wav_file.getframerate() or 8000
            frames = wav_file.readframes(wav_file.getnframes())
        samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
        if samples.size < sr:
            return min(duration * 0.12, max(0.0, duration - window))
        hop = max(1, sr // 2)
        win = max(hop, int(sr * 0.5))
        scores = []
        for i in range(0, samples.size - win, hop):
            chunk = samples[i : i + win]
            scores.append(float(np.sqrt(np.mean(np.square(chunk)))))
        if not scores:
            return 0.0
        span = max(1, int(round(window / 0.5)))
        best_i, best = 0, -1.0
        for i in range(0, len(scores)):
            val = float(np.mean(scores[i : i + span]))
            if val > best:
                best, best_i = val, i
        start = best_i * 0.5
        return max(0.0, min(start, max(0.0, duration - window)))
    except Exception as exc:
        print("[안내] 하이라이트 탐색 폴백: {}".format(exc))
        return min(duration * 0.15, max(0.0, duration - window))
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


VIRAL_REELS_SYSTEM_PROMPT = """\
<role>당신은 인스타그램 릴스 100만 조회수를 만드는 전문 숏폼 디렉터입니다.</role>
<structure>
  - 0~3초: 첫 문장에서 시청자를 멈추게 하는 강력한 패턴 브레이크 후킹 (Hook)
  - 3~15초: 불필요한 서론 없이 리듬감 넘치는 짧은 구어체 본론 (Body)
  - 15~30초: 여운 있는 마무리 및 저장/공유를 유도하는 CTA (Outro)
</structure>
<rules>
  - 나레이션 음성에는 해시태그나 이모지를 읽지 않는다.
  - 문장은 10자 내외의 짧은 호흡으로 끊어 자막 가독성을 극대화한다.
  - 장면 지시, 제목, 따옴표, 영어 해시태그를 script에 넣지 않는다.
  - 화면에 보이는 소재를 구체적으로 언급한다.
</rules>
"""


def normalize_instagram_payload(raw, style_prompt="", script=""):
    # type: (object, str, str) -> dict
    data = raw if isinstance(raw, dict) else {}
    caption = sanitize_narration(str(data.get("caption") or data.get("body") or "")).strip()
    tags_raw = data.get("hashtags") or data.get("tags") or []
    tags = []
    if isinstance(tags_raw, str):
        tags_raw = re.findall(r"[#＃]?[0-9A-Za-z가-힣_]+", tags_raw)
    if isinstance(tags_raw, list):
        for item in tags_raw:
            token = re.sub(r"^[#＃]+", "", str(item or "").strip())
            token = re.sub(r"[^0-9A-Za-z가-힣_]", "", token)
            if not token:
                continue
            tag = "#" + token
            if tag not in tags:
                tags.append(tag)
            if len(tags) >= 5:
                break
    if not caption:
        hook = sanitize_narration(script or "").split(".")[0].strip()
        style = sanitize_narration(style_prompt) or "오늘의 순간"
        caption = (
            (hook + ".")
            if hook
            else "지금 이 장면, 그냥 넘기지 마세요."
        )
        caption = "{} {} 저장해 두고 나중에 다시 보세요.".format(caption, style[:24]).strip()
    while len(tags) < 5:
        defaults = ["#릴스", "#숏폼", "#인스타그램", "#일상", "#감성"]
        for d in defaults:
            if d not in tags:
                tags.append(d)
            if len(tags) >= 5:
                break
        break
    tags = tags[:5]
    copy_text = "{}\n\n{}".format(caption.strip(), " ".join(tags)).strip()
    return {
        "caption": caption.strip(),
        "hashtags": tags,
        "copy_text": copy_text,
    }


def parse_script_json_response(raw_text, media_count):
    # type: (str, int) -> Tuple[str, List[int], dict]
    text = (raw_text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    data = None
    try:
        data = json.loads(text)
    except Exception:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                data = json.loads(match.group(0))
            except Exception:
                data = None
    if isinstance(data, dict):
        script = sanitize_narration(str(data.get("script") or data.get("narration") or ""))
        order_src = data.get("photo_order") or data.get("PHOTO_ORDER") or []
        order_ids = []
        if isinstance(order_src, str):
            _, order_ids = parse_photo_order("PHOTO_ORDER: " + order_src, media_count)
        elif isinstance(order_src, list):
            for n in order_src:
                try:
                    idx = int(n) - 1
                except Exception:
                    continue
                if 0 <= idx < media_count:
                    order_ids.append(idx)
        ig = normalize_instagram_payload(
            data.get("instagram") or data.get("instagram_caption") or {},
            script=script,
        )
        if script:
            return script, order_ids, ig
    script, order_ids = parse_photo_order(text, media_count)
    script = sanitize_narration(script)
    return script, order_ids, normalize_instagram_payload({}, script=script)


def generate_script(settings, media_files, style_prompt="", direction=None, target_duration=15):
    # type: (Settings, List[Path], str) -> Tuple[str, List[int], dict]
    print("1) OpenAI(gpt-4o-mini) 클로드식 XML 릴스 대본 작성 중...")
    style = (style_prompt or "").strip() or "시선을 사로잡는 빠른 템포의 숏폼"
    target_duration = normalize_target_duration(target_duration)
    spec = DURATION_TARGETS[target_duration]
    min_chars, max_chars = spec["min_chars"], spec["max_chars"]
    vision_hint = _vision_tags_from_media(media_files, style)
    guide = ""
    if direction is not None:
        guide = "\n연출 지시: {} / {}".format(direction.tone, direction.script_guide)
    numbered = ", ".join(
        "{}번 {}".format(i + 1, path.name) for i, path in enumerate(media_files)
    )
    user_prompt = (
        "첨부된 사진/영상 프레임을 보고 인스타그램 릴스/유튜브 쇼츠용 콘텐츠를 JSON으로만 작성하세요.\n"
        "영상 스타일/분위기: {}{}\n"
        "비전 힌트: {}\n"
        "이미지 번호: {}\n"
        "목표 길이: {}초 ({}) · 나레이션은 말할 때 약 {}초 (공백 제외 {}~{}자)\n"
        "사진이 1~2장뿐이어도 선택한 길이에 맞는 완성형 Hook-Body-Outro로 작성하세요.\n"
        "목표 길이가 15초면 Body를 압축하고, 30초 이상이면 Outro CTA를 분명히 넣으세요.\n"
        "응답 JSON 스키마:\n"
        '{{'
        '"script":"나레이션 대본(해시태그·이모지 금지, 10자 내외 짧은 문장)",'
        '"photo_order":[1,3,2],'
        '"instagram":{{"caption":"업로드용 본문 글","hashtags":["#태그1","#태그2","#태그3","#태그4","#태그5"]}}'
        '}}\n'
        "- photo_order는 대본 흐름에 맞는 이미지 번호(1부터). 반복 가능\n"
        "- instagram.caption은 저장/공유를 유도하는 업로드용 본문(이모지 소량 허용)\n"
        "- hashtags는 핵심 한글/영문 해시태그 정확히 5개"
    ).format(
        style,
        guide,
        vision_hint,
        numbered,
        target_duration,
        spec["label"],
        target_duration,
        min_chars,
        max_chars,
    )
    content = [{"type": "text", "text": user_prompt}]

    attached = 0
    for path in media_files:
        b64 = media_to_preview_b64(path)
        if not b64:
            continue
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64," + b64},
            }
        )
        attached += 1
        if attached >= 4:
            break

    if attached == 0:
        content[0]["text"] += "\n미디어 파일명 힌트: {}".format(numbered)

    def _instant_fallback(reason):
        print("[안내] 대본 즉시 로컬 폴백 ({}): {}".format(reason, vision_hint), flush=True)
        return fallback_script(style_prompt or vision_hint, target_duration=target_duration, vision_tags=vision_hint)

    if not (settings.openai_api_key or "").strip():
        return _instant_fallback("OPENAI_API_KEY 없음")

    try:
        client = OpenAI(api_key=settings.openai_api_key, timeout=12.0)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": VIRAL_REELS_SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            temperature=0.85,
            response_format={"type": "json_object"},
            max_tokens=900 if target_duration <= 15 else (1400 if target_duration <= 30 else 1800),
        )
        raw_text = (response.choices[0].message.content or "").strip()
        if not raw_text:
            return _instant_fallback("빈 응답")
        script, order, ig_payload = parse_script_json_response(raw_text, len(media_files))
        compact = re.sub(r"\s+", "", script or "")
        if not script or len(compact) < 80:
            return _instant_fallback("응답 짧음/파싱 실패")
        ig_payload = normalize_instagram_payload(ig_payload, style_prompt=style, script=script)
        print("   대본 ({}자):\n   {}\n".format(len(compact), script))
        print(
            "   인스타 캡션: {}\n   태그: {}".format(
                ig_payload.get("caption"), " ".join(ig_payload.get("hashtags") or [])
            )
        )
        if order:
            print("   사진 배치: {}".format([i + 1 for i in order]))
        return script, order, ig_payload
    except Exception as exc:
        return _instant_fallback(str(exc)[:160])


def _vision_tags_from_media(media_files, style_prompt=""):
    bits = []
    style = sanitize_narration(style_prompt or "")
    if style:
        bits.append(style[:40])
    for path in list(media_files or [])[:4]:
        stem = re.sub(r"[_\-]+", " ", Path(path).stem)
        stem = re.sub(r"\d{4}.*", "", stem).strip()
        stem = re.sub(r"\s+", " ", stem)
        if stem and stem.lower() not in {"image", "video", "img", "photo", "media"}:
            bits.append(stem[:28])
    joined = " · ".join(bits[:3]).strip(" ·")
    return joined or "일상 순간"


def generate_voice(settings, script, output_path=None, voice_type=DEFAULT_VOICE_TYPE):
    # type: (Settings, str, Optional[Path], str) -> Path
    key, voice_id, preset = resolve_voice(voice_type)
    spoken = sanitize_narration(script)
    print("2) ElevenLabs({}) 한국어 네이티브 음성 생성 중... voice={}".format(ELEVENLABS_MODEL, key))
    dest = Path(output_path) if output_path else VOICE_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = "https://api.elevenlabs.io/v1/text-to-speech/{}".format(voice_id)
    headers = {
        "xi-api-key": settings.elevenlabs_api_key,
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
    }
    payload = {
        "text": spoken,
        "model_id": ELEVENLABS_MODEL,
        "voice_settings": {
            "stability": preset["stability"],
            "similarity_boost": preset["similarity_boost"],
            "style": preset["style"],
            "use_speaker_boost": True,
        },
    }
    response = requests.post(url, json=payload, headers=headers, timeout=180)
    if response.status_code >= 400:
        raise RuntimeError(
            "ElevenLabs TTS 실패 ({}): {}".format(
                response.status_code, response.text[:500]
            )
        )
    dest.write_bytes(response.content)
    print("   저장: {}".format(dest))
    return dest


FFMPEG_TIMEOUT = int(os.getenv("FFMPEG_TIMEOUT", "180"))
FFMPEG_RUN_LOCK = threading.Lock()
FFMPEG_ENCODE = [
    "-c:v",
    "libx264",
    "-preset",
    "ultrafast",
    "-tune",
    "stillimage",
    "-crf",
    "23",
    "-pix_fmt",
    "yuv420p",
    "-c:a",
    "aac",
    "-b:a",
    "128k",
    "-threads",
    "0",
    "-movflags",
    "+faststart",
]
FFMPEG_MOTION_ENCODE = [
    "-c:v",
    "libx264",
    "-preset",
    "ultrafast",
    "-crf",
    "23",
    "-pix_fmt",
    "yuv420p",
    "-c:a",
    "aac",
    "-b:a",
    "128k",
    "-threads",
    "0",
    "-movflags",
    "+faststart",
]
FFMPEG_PRESET = [
    "-c:v",
    "libx264",
    "-preset",
    "ultrafast",
    "-tune",
    "stillimage",
    "-crf",
    "23",
    "-pix_fmt",
    "yuv420p",
    "-threads",
    "0",
    "-movflags",
    "+faststart",
]
FFMPEG_LIGHT = ["-threads", "2"]

SCALE_PAD_VF = (
    "scale={w}:{h}:force_original_aspect_ratio=decrease,"
    "pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,"
    "setsar=1,fps={fps},format=yuv420p"
).format(w=TARGET_W, h=TARGET_H, fps=FPS)


def ffmpeg_bin():
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).is_file():
            return exe
    except Exception:
        pass
    raise RuntimeError("FFmpeg를 찾을 수 없습니다. 시스템에 ffmpeg를 설치해 주세요.")


def mp4_file_ready(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size < 32:
        return False
    try:
        with path.open("rb") as fh:
            head = fh.read(12)
        return len(head) >= 12 and head[4:8] == b"ftyp"
    except OSError:
        return False


def save_image_verified(image, dest, quality=85):
    dest = Path(dest)
    os.makedirs(str(dest.parent), exist_ok=True)
    image.convert("RGB").save(str(dest), "JPEG", quality=int(quality))
    if not os.path.exists(str(dest)) or os.path.getsize(str(dest)) < 32:
        raise RuntimeError("이미지 저장 실패: {}".format(dest))
    return dest


def ensure_jpeg_on_disk(path, size=None, fill=(18, 10, 28)):
    path = Path(path)
    os.makedirs(str(path.parent), exist_ok=True)
    width, height = size or (TARGET_W, TARGET_H)
    width = max(2, int(width) - int(width) % 2)
    height = max(2, int(height) - int(height) % 2)
    if os.path.exists(str(path)) and os.path.getsize(str(path)) >= 32:
        return path
    Image.new("RGB", (width, height), fill).save(str(path), "JPEG", quality=80)
    if not os.path.exists(str(path)) or os.path.getsize(str(path)) < 32:
        raise RuntimeError("폴백 이미지 저장 실패: {}".format(path))
    return path


def require_image_for_ffmpeg(path):
    path = Path(path)
    if not os.path.exists(str(path)) or os.path.getsize(str(path)) < 32:
        raise RuntimeError("FFmpeg 입력 이미지가 없습니다: {}".format(path))
    return path


def _atomic_mp4_args(args):
    if not args:
        return None, None, list(args)
    last = Path(str(args[-1]))
    if last.suffix.lower() != ".mp4":
        return None, None, [str(a) for a in args]
    os.makedirs(str(last.parent), exist_ok=True)
    if last.name == "final_shorts.mp4":
        temp = last.parent / "temp_render.mp4"
    else:
        temp = last.parent / "temp_render_{}.mp4".format(last.stem)
    if temp.resolve() == last.resolve():
        temp = last.parent / "temp_render_{}.mp4".format(uuid.uuid4().hex[:8])
    return temp, last, [str(a) for a in args[:-1]] + [str(temp)]


def _discard_temp_mp4(temp):
    if temp is None:
        return
    try:
        Path(temp).unlink(missing_ok=True)
    except TypeError:
        try:
            if Path(temp).exists():
                Path(temp).unlink()
        except OSError:
            pass
    except OSError:
        pass


def _publish_temp_mp4(temp, dest):
    temp = Path(temp)
    dest = Path(dest)
    if not temp.is_file() or temp.stat().st_size < 32:
        _discard_temp_mp4(temp)
        raise RuntimeError("FFmpeg가 빈 MP4를 남겼습니다: {}".format(temp))
    if not mp4_file_ready(temp):
        _discard_temp_mp4(temp)
        raise RuntimeError("MP4 ftyp 검증 실패 (미완성 moov 차단): {}".format(temp))
    os.replace(str(temp), str(dest))
    if not mp4_file_ready(dest):
        raise RuntimeError("최종 MP4 게시 실패: {}".format(dest))
    return dest


def run_ffmpeg(args, timeout=None):
    if timeout is None:
        timeout = FFMPEG_TIMEOUT
    with FFMPEG_RUN_LOCK:
        return _run_ffmpeg_locked(args, timeout)


def _run_ffmpeg_locked(args, timeout):
    temp, dest, argv = _atomic_mp4_args(args)
    if temp is not None and temp.exists():
        _discard_temp_mp4(temp)
    cmd = [ffmpeg_bin(), "-hide_banner", "-y"] + argv
    print("[ffmpeg cmd] {}".format(" ".join(cmd)), flush=True)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            creationflags=flags,
        )
    except subprocess.TimeoutExpired as exc:
        _discard_temp_mp4(temp)
        err = (exc.stderr or b"").decode("utf-8", errors="replace")
        print("[ffmpeg timeout stderr]\n{}".format(err), flush=True)
        raise RuntimeError("FFmpeg 시간 초과 ({}s): {}".format(timeout, err[-1500:]))
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
    if proc.returncode != 0:
        _discard_temp_mp4(temp)
        print("[ffmpeg exit {}]".format(proc.returncode), flush=True)
        print("[ffmpeg stderr]\n{}".format(stderr), flush=True)
        if stdout.strip():
            print("[ffmpeg stdout]\n{}".format(stdout), flush=True)
        raise RuntimeError(
            "FFmpeg 실패 (code={}): {}".format(
                proc.returncode, (stderr or stdout or "stderr 비어 있음")[-2500:]
            )
        )
    if temp is not None and dest is not None:
        _publish_temp_mp4(temp, dest)
    if stderr.strip():
        print("[ffmpeg log]\n{}".format(stderr[-800:]), flush=True)
    return proc


def probe_duration(path):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    proc = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        timeout=30,
        creationflags=flags,
    )
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
    if not match:
        print("[ffmpeg probe stderr]\n{}".format(stderr), flush=True)
        raise RuntimeError("미디어 길이를 읽지 못했습니다: {}\n{}".format(path, stderr[-800:]))
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _safe_src_copy(src, work_dir, index):
    suffix = Path(src).suffix.lower() or ".jpg"
    dest = work_dir / ("src_{:03d}{}".format(index, suffix))
    shutil.copy2(str(src), str(dest))
    return dest


def make_scene_clip(src, dest, duration):
    duration = max(0.2, float(duration))
    suffix = Path(src).suffix.lower()
    if suffix in IMAGE_EXTS:
        require_image_for_ffmpeg(src)
    args = []
    if suffix in IMAGE_EXTS:
        args += [
            "-loop",
            "1",
            "-framerate",
            str(FPS),
            "-t",
            "{:.3f}".format(duration),
            "-i",
            str(src),
        ]
    else:
        args += [
            "-stream_loop",
            "-1",
            "-t",
            "{:.3f}".format(duration),
            "-i",
            str(src),
        ]
    args += ["-vf", SCALE_PAD_VF, "-an"] + FFMPEG_PRESET + [str(dest)]
    run_ffmpeg(args)


def concat_scene_clips(clips, dest):
    if len(clips) == 1:
        shutil.copy2(str(clips[0]), str(dest))
        return
    args = []
    for clip in clips:
        args += ["-i", str(clip)]
    parts = []
    for i in range(len(clips)):
        parts.append("[{}:v]setsar=1,format=yuv420p[v{}]".format(i, i))
    concat_in = "".join("[v{}]".format(i) for i in range(len(clips)))
    parts.append("{}concat=n={}:v=1:a=0[vout]".format(concat_in, len(clips)))
    args += [
        "-filter_complex",
        ";".join(parts),
        "-map",
        "[vout]",
        "-an",
    ] + FFMPEG_PRESET + [str(dest)]
    run_ffmpeg(args)


def mux_voice(video_path, voice_path, duration, out_file):
    run_ffmpeg(
        [
            "-i",
            str(video_path),
            "-i",
            str(voice_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-t",
            "{:.3f}".format(duration),
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            str(out_file),
        ],
        timeout=FFMPEG_TIMEOUT,
    )


def overlay_subtitles(video_path, cues, font_path, out_file, work_dir, caption_style="hormozi"):
    if not cues:
        shutil.copy2(str(video_path), str(out_file))
        return out_file
    srt = write_cues_srt(cues, Path(work_dir) / "subs.srt")
    run_ffmpeg(
        [
            "-i",
            str(video_path),
            "-vf",
            subtitles_vf(srt, font_path, caption_style),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
        ]
        + FFMPEG_LIGHT
        + [str(out_file)],
        timeout=min(50, FFMPEG_TIMEOUT),
    )
    return out_file


def build_subtitle_assets(script, duration, font_path, work_dir):
    return split_script_cues(script, duration)


def _call(clip, names, *args, **kwargs):
    for name in names:
        method = getattr(clip, name, None)
        if callable(method):
            return method(*args, **kwargs)
    raise AttributeError("clip has none of: {}".format(names))


def to_vertical(clip):
    width, height = clip.size
    scale = max(TARGET_W / float(width), TARGET_H / float(height))
    clip = _call(clip, ("resized", "resize"), scale)
    cw, ch = clip.size
    x1 = max(0, int(round((cw - TARGET_W) / 2.0)))
    y1 = max(0, int(round((ch - TARGET_H) / 2.0)))
    clip = _call(
        clip,
        ("cropped", "crop"),
        x1=x1,
        y1=y1,
        width=TARGET_W,
        height=TARGET_H,
    )
    if tuple(clip.size) != (TARGET_W, TARGET_H):
        clip = _call(clip, ("resized", "resize"), (TARGET_W, TARGET_H))
    return clip


def load_visual_clip(path):
    # type: (Path) -> object
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTS:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        tmp = OUTPUT_DIR / ("_frame_{}.jpg".format(path.stem))
        with open_image_upright(path) as im:
            im.convert("RGB").save(tmp, quality=95)
        clip = ImageClip(str(tmp))
        clip = to_vertical(clip)
        if hasattr(clip, "without_audio"):
            clip = clip.without_audio()
        elif hasattr(clip, "withoutaudio"):
            clip = clip.withoutaudio()
        return clip

    clip = VideoFileClip(str(path))
    if hasattr(clip, "without_audio"):
        clip = clip.without_audio()
    elif hasattr(clip, "withoutaudio"):
        clip = clip.withoutaudio()
    return to_vertical(clip)


def _subclip(clip, start, end):
    if hasattr(clip, "subclipped"):
        return clip.subclipped(start, end)
    return clip.subclip(start, end)


def fit_duration(clip, duration):
    duration = max(0.2, float(duration))
    src = getattr(clip, "duration", None)
    if src in (None, 0):
        return _call(clip, ("with_duration", "set_duration"), duration)

    src = float(src)
    if src >= duration:
        return _subclip(clip, 0, duration)

    loops = int(duration / src) + 1
    looped = concatenate_videoclips([clip] * loops, method="compose")
    return _subclip(looped, 0, duration)


def build_visuals(media_files, audio_duration):
    # type: (List[Path], float) -> Tuple[object, List[float]]
    print("3) 미디어를 9:16 (720x1280)으로 맞추고 이어붙이는 중...")
    per = audio_duration / float(len(media_files))
    clips = []
    scene_starts = []
    t = 0.0
    for path in media_files:
        scene_starts.append(t)
        raw = load_visual_clip(path)
        fitted = fit_duration(raw, per)
        fitted = _call(fitted, ("with_fps", "set_fps"), FPS)
        clips.append(fitted)
        t += per
    video = concatenate_videoclips(clips, method="compose")
    video = _subclip(video, 0, audio_duration)
    return _call(video, ("with_fps", "set_fps"), FPS), scene_starts


def split_script_pieces(script):
    # type: (str) -> List[str]
    text = sanitize_narration(script)
    pieces = [
        p.strip()
        for p in re.split(r"(?<=[.!?。…])\s+|(?<=요)\s+|(?<=다)\s+", text)
        if p.strip()
    ]
    if not pieces:
        pieces = [text]
    if len(pieces) == 1:
        chunks = re.findall(r".{8,32}(?:\s+|$)|.{1,32}$", text)
        chunks = [c.strip() for c in chunks if c.strip()]
        if chunks:
            pieces = chunks
    return pieces or [text]


def split_script_cues(script, total_duration):
    # type: (str, float) -> List[Tuple[str, float, float]]
    pieces = split_script_pieces(script)
    weights = [max(1, len(p)) for p in pieces]
    total_w = float(sum(weights))
    cues = []
    t = 0.0
    for i, text in enumerate(pieces):
        dur = total_duration * (weights[i] / total_w)
        if i == len(pieces) - 1:
            dur = max(0.15, total_duration - t)
        start = t
        end = min(total_duration, t + dur)
        cues.append((text, start, end))
        t = end
    return cues


def _srt_clock(seconds):
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    whole = int(seconds % 60)
    millis = int(round((seconds - math.floor(seconds)) * 1000.0))
    if millis >= 1000:
        whole += 1
        millis = 0
    if whole >= 60:
        minutes += 1
        whole = 0
    return "{:02d}:{:02d}:{:02d},{:03d}".format(hours, minutes, whole, millis)


def wrap_caption_lines(text, width=16):
    text = sanitize_narration(text).replace("\n", " ").strip()
    if not text:
        return ""
    width = max(8, int(width or 16))
    if len(text) <= width:
        return text
    cut = text[:width]
    space = cut.rfind(" ")
    if space >= 8:
        cut = cut[:space]
    return cut.strip()


def write_cues_srt(cues, dest):
    blocks = []
    index = 1
    for text, start, end in cues:
        body = wrap_caption_lines(text)
        if not body:
            continue
        stop = max(float(start) + 0.2, float(end))
        blocks.append(
            "{}\n{} --> {}\n{}".format(
                index, _srt_clock(start), _srt_clock(stop), body
            )
        )
        index += 1
    Path(dest).write_text("\ufeff" + "\n\n".join(blocks) + "\n", encoding="utf-8")
    return Path(dest)


def _ffmpeg_filter_path(path):
    return Path(path).resolve().as_posix().replace("\\", "/").replace(":", "\\:").replace("'", r"\'")


def subtitle_font_name(font_path):
    name = Path(font_path).name.lower()
    if "nanum" in name:
        return "NanumGothic"
    if "malgun" in name:
        return "Malgun Gothic"
    if "noto" in name:
        return "Noto Sans CJK KR"
    if "apple" in name or "gothicneo" in name:
        return "Apple SD Gothic Neo"
    return "NanumGothic"


def subtitles_vf(srt_path, font_path, caption_style="hormozi"):
    srt = _ffmpeg_filter_path(srt_path)
    fontsdir = _ffmpeg_filter_path(Path(font_path).resolve().parent)
    style = caption_force_style(caption_style, font_path)
    return "subtitles={}:fontsdir={}:force_style='{}'".format(srt, fontsdir, style)


def _text_size(draw, text, font, stroke_width):
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    return bbox[2] - bbox[0], bbox[3] - bbox[1], bbox


def wrap_subtitle_lines(text, font, max_width, stroke_width):
    probe = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    draw = ImageDraw.Draw(probe)
    lines = []
    for paragraph in text.replace("\r", "").split("\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        current = ""
        for ch in paragraph:
            test = current + ch
            width, _h, _b = _text_size(draw, test, font, stroke_width)
            if width <= max_width or not current:
                current = test
            else:
                lines.append(current)
                current = ch
        if current:
            lines.append(current)
    return lines or [text]


def render_subtitle_png(text, font_path, out_path, fill=(255, 255, 255), stroke=(0, 0, 0), font_scale=1.0):
    # type: (str, str, Path, Tuple[int, int, int], Tuple[int, int, int], float) -> Path
    text = sanitize_narration(text)
    if not text:
        Image.new("RGBA", (8, 8), (0, 0, 0, 0)).save(out_path, "PNG")
        return out_path
    size = max(32, int(round(SUB_FONT_SIZE * float(font_scale or 1.0))))
    font = _load_font(font_path, size)
    inner_width = SUB_MAX_WIDTH - SUB_PAD_X * 2
    lines = wrap_subtitle_lines(text, font, inner_width, SUB_STROKE)

    probe = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    draw = ImageDraw.Draw(probe)
    sizes = [_text_size(draw, line, font, SUB_STROKE) for line in lines]
    content_w = max(s[0] for s in sizes)
    content_h = sum(s[1] for s in sizes) + SUB_LINE_GAP * (len(lines) - 1)

    width = min(TARGET_W, max(1, content_w + SUB_PAD_X * 2))
    height = max(1, content_h + SUB_PAD_Y * 2)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    fill_rgba = (int(fill[0]), int(fill[1]), int(fill[2]), 255)
    stroke_rgba = (int(stroke[0]), int(stroke[1]), int(stroke[2]), 255)

    y = SUB_PAD_Y
    for line, (lw, lh, bbox) in zip(lines, sizes):
        x = int((width - lw) / 2.0) - bbox[0]
        draw.text(
            (x, y - bbox[1]),
            line,
            font=font,
            fill=fill_rgba,
            stroke_width=SUB_STROKE,
            stroke_fill=stroke_rgba,
        )
        y += lh + SUB_LINE_GAP

    img.save(out_path, "PNG")
    return out_path


def bounce_scale(t):
    if t <= 0:
        return 0.38
    if t < 0.09:
        return 0.38 + (1.26 - 0.38) * (t / 0.09)
    if t < 0.18:
        return 1.26 + (0.90 - 1.26) * ((t - 0.09) / 0.09)
    if t < 0.28:
        return 0.90 + (1.08 - 0.90) * ((t - 0.18) / 0.10)
    if t < POP_ANIM_SEC:
        return 1.08 + (1.00 - 1.08) * ((t - 0.28) / max(0.01, POP_ANIM_SEC - 0.28))
    return 1.0


def bounce_y(t, img_h):
    scale = bounce_scale(t)
    h = img_h * scale
    center_y = (TARGET_H - h) / 2.0
    dest_y = TARGET_H - h - SUB_BOTTOM_MARGIN
    if t >= POP_ANIM_SEC:
        return dest_y
    p = min(1.0, t / POP_ANIM_SEC)
    c1 = 1.70158
    c3 = c1 + 1.0
    ease = 1.0 + c3 * math.pow(p - 1.0, 3) + c1 * math.pow(p - 1.0, 2)
    ease = max(0.0, min(1.12, ease))
    y = center_y + (dest_y - center_y) * min(1.0, ease)
    y -= 70.0 * math.sin(math.pi * p) * (1.0 - p)
    return y


def apply_pop_animation(clip, img_w, img_h):
    try:
        clip = _call(clip, ("resized", "resize"), bounce_scale)
    except Exception:
        pass

    def pos_at(t):
        return ("center", bounce_y(t, img_h))

    return _call(clip, ("with_position", "set_position"), pos_at)


def imageclip_from_rgba(img, duration):
    rgba = np.array(img)
    if rgba.ndim != 3 or rgba.shape[2] < 4:
        clip = ImageClip(rgba[:, :, :3] if rgba.ndim == 3 else rgba)
        return _call(clip, ("with_duration", "set_duration"), duration)

    rgb = rgba[:, :, :3]
    alpha = rgba[:, :, 3].astype("float64") / 255.0
    clip = ImageClip(rgb)
    clip = _call(clip, ("with_duration", "set_duration"), duration)

    try:
        mask = ImageClip(alpha, is_mask=True)
    except TypeError:
        mask = ImageClip(alpha, ismask=True)
    mask = _call(mask, ("with_duration", "set_duration"), duration)

    if hasattr(clip, "with_mask"):
        return clip.with_mask(mask)
    return clip.set_mask(mask)


def make_subtitle_clips(script, duration, font_path):
    # type: (str, float, str) -> Tuple[list, List[float]]
    print("4) Pillow로 자막 이미지 + 팝 애니메이션 생성 중...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cues = split_script_cues(script, duration)
    clips = []
    cue_starts = []
    for i, (text, start, end) in enumerate(cues):
        dur = max(0.12, end - start)
        png_path = OUTPUT_DIR / ("_sub_{:03d}.png".format(i))
        render_subtitle_png(text, font_path, png_path)
        with Image.open(png_path) as im:
            overlay = im.convert("RGBA").copy()
        clip = imageclip_from_rgba(overlay, dur)
        clip = apply_pop_animation(clip, overlay.size[0], overlay.size[1])
        clip = _call(clip, ("with_start", "set_start"), start)
        clips.append(clip)
        cue_starts.append(start)
    return clips, cue_starts


def cleanup_temp_files():
    patterns = ("_frame_*.jpg", "_sub_*.png")
    for pattern in patterns:
        for tmp in OUTPUT_DIR.glob(pattern):
            try:
                tmp.unlink()
            except OSError:
                pass


def list_audio_files(folder):
    if not folder.exists():
        return []
    files = [
        p
        for p in sorted(folder.iterdir())
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS and not p.name.startswith(".")
    ]
    return files


def pick_bgm_file():
    files = list_audio_files(BGM_DIR)
    if not files:
        return None
    return random.choice(files)


def pick_bgm_for_mood(mood):
    mood = normalize_bgm_mood(mood)
    if mood == "none":
        return None
    keywords = {
        "variety": ("variety", "fun", "comedy", "예능", "gag"),
        "lofi": ("lofi", "chill", "soft", "감성", "vlog"),
        "phonk": ("phonk", "funk", "drift", "dark"),
        "pop": ("pop", "upbeat", "happy", "beat"),
        "acoustic": ("acoustic", "guitar", "folk", "warm"),
        "suspense": ("suspense", "tense", "thriller", "pulse"),
        "cinematic": ("cinematic", "epic", "score", "film"),
    }.get(mood, (mood,))
    files = list_audio_files(BGM_DIR)
    matched = [
        path
        for path in files
        if any(word in path.stem.lower() for word in keywords)
    ]
    pool = matched or files
    if pool:
        return random.choice(pool)
    return None


def synthesize_bgm(mood, duration, dest):
    mood = normalize_bgm_mood(mood)
    if mood == "none":
        return None
    sr = 22050
    n = max(sr, int(sr * max(1.0, float(duration) + 0.4)))
    t = np.arange(n, dtype=np.float64) / float(sr)
    if mood in ("lofi", "acoustic"):
        wave_data = (
            0.10 * np.sin(2 * np.pi * 196 * t)
            + 0.07 * np.sin(2 * np.pi * 247 * t)
            + 0.04 * np.sin(2 * np.pi * 294 * t)
        ) * (0.55 + 0.45 * np.sin(2 * np.pi * 0.12 * t))
    elif mood in ("suspense", "phonk"):
        pulse = (np.sin(2 * np.pi * 2.4 * t) > 0).astype(np.float64)
        wave_data = 0.14 * np.sin(2 * np.pi * 98 * t) * pulse + 0.04 * np.sin(
            2 * np.pi * 392 * t
        )
    elif mood == "cinematic":
        wave_data = 0.09 * np.sin(2 * np.pi * 130 * t) + 0.06 * np.sin(2 * np.pi * 196 * t)
    elif mood == "variety":
        kick = np.exp(-((t % 0.4) * 16)) * np.sin(2 * np.pi * 80 * t)
        stab = ((t % 0.4) < 0.07).astype(np.float64) * np.sin(2 * np.pi * 660 * t)
        wave_data = 0.15 * kick + 0.05 * stab
    else:
        kick = np.exp(-((t % 0.5) * 18)) * np.sin(2 * np.pi * 70 * t)
        stab = ((t % 0.5) < 0.08).astype(np.float64) * np.sin(2 * np.pi * 523 * t)
        wave_data = 0.16 * kick + 0.05 * stab
    fade = np.minimum(1.0, np.minimum(t / 0.12, (t[-1] - t) / 0.25))
    samples = np.clip(wave_data * fade, -0.35, 0.35)
    pcm = (samples * 32767.0).astype(np.int16)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dest), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sr)
        wav_file.writeframes(pcm.tobytes())
    return dest


def find_named_sfx(name):
    preferred = [
        SFX_DIR / "{}.mp3".format(name),
        SFX_DIR / "{}.wav".format(name),
        SFX_DIR / "{}.MP3".format(name),
    ]
    for path in preferred:
        if path.is_file():
            return path
    for path in list_audio_files(SFX_DIR):
        if name.lower() in path.stem.lower():
            return path
    return None


def _write_wav_mono(path, samples, sr=44100):
    samples = np.clip(np.asarray(samples, dtype=np.float64), -1.0, 1.0)
    pcm = (samples * 32767.0).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def ensure_pop_sfx():
    found = find_named_sfx("pop")
    if found:
        return found
    path = SFX_DIR / "pop.wav"
    sr = 44100
    n = int(sr * 0.12)
    t = np.arange(n, dtype=np.float64) / sr
    tone = np.sin(2 * np.pi * 1046 * t) + 0.35 * np.sin(2 * np.pi * 1568 * t)
    env = np.exp(-t * 40)
    click = np.random.default_rng(7).normal(0, 1, n) * 0.12 * np.exp(-t * 70)
    _write_wav_mono(path, 0.8 * tone * env + click, sr)
    print("   sfx/pop 파일이 없어 짧은 팝 효과음을 생성했습니다: {}".format(path))
    return path


def ensure_whoosh_sfx():
    found = find_named_sfx("whoosh")
    if found:
        return found
    path = SFX_DIR / "whoosh.wav"
    sr = 44100
    dur = 0.36
    n = int(sr * dur)
    t = np.arange(n, dtype=np.float64) / sr
    rng = np.random.default_rng(21)
    noise = rng.normal(0, 1, n)
    env = np.sin(np.pi * t / dur) ** 1.15
    chirp = np.sin(2 * np.pi * (160.0 + 1500.0 * (t / dur)) * t) * 0.25
    _write_wav_mono(path, (0.5 * noise + chirp) * env, sr)
    print("   sfx/whoosh 파일이 없어 전환 효과음을 생성했습니다: {}".format(path))
    return path


def ensure_shutter_sfx():
    found = find_named_sfx("shutter")
    if found:
        return found
    path = SFX_DIR / "shutter.wav"
    sr = 44100
    n = int(sr * 0.18)
    t = np.arange(n, dtype=np.float64) / sr
    rng = np.random.default_rng(42)
    click = rng.normal(0, 1, n) * np.exp(-t * 55)
    mechan = np.sin(2 * np.pi * 2200 * t) * np.exp(-t * 48) * 0.55
    thud = np.sin(2 * np.pi * 140 * t) * np.exp(-t * 28) * 0.35
    _write_wav_mono(path, 0.9 * click + mechan + thud, sr)
    print("   sfx/shutter 파일이 없어 셔터음을 생성했습니다: {}".format(path))
    return path


def _escape_drawtext(text):
    return (
        str(text or "")
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
    )


def build_before_still_clip(still_jpg, dest_mp4, width, height, duration=BEFORE_AFTER_SEC):
    width = int(width or TARGET_W)
    height = int(height or TARGET_H)
    width -= width % 2
    height -= height % 2
    duration = max(1.0, float(duration))
    shutter = ensure_shutter_sfx()
    font = find_korean_font()
    font_opt = ""
    if font and Path(font).is_file():
        font_path = str(Path(font)).replace("\\", "/").replace(":", "\\:")
        font_opt = "fontfile={}:".format(font_path)
    label = _escape_drawtext("Before")
    vf = (
        "scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,"
        "drawtext={font}text='{label}':fontsize={fs}:fontcolor=white:borderw=5:bordercolor=black@0.75:"
        "x=(w-text_w)/2:y=h*0.10,"
        "format=yuv420p,fps={fps}"
    ).format(w=width, h=height, font=font_opt, label=label, fs=max(42, height // 18), fps=FPS)
    dest = Path(dest_mp4)
    run_ffmpeg(
        [
            "-loop",
            "1",
            "-t",
            "{:.3f}".format(duration),
            "-i",
            str(still_jpg),
            "-i",
            str(shutter),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "22",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(dest),
        ],
        timeout=30,
    )
    if not dest.is_file() or dest.stat().st_size < 1000:
        raise RuntimeError("Before 클립 생성 실패")
    return dest


def apply_before_after_hook(still_jpg, motion_mp4, dest_mp4, width=None, height=None, work_dir=None):
    """첫 1.5초 Before + 셔터음 → 스와이프 → AI 모션 클립."""
    width = int(width or TARGET_W)
    height = int(height or TARGET_H)
    work = Path(work_dir) if work_dir else Path(dest_mp4).parent
    work.mkdir(parents=True, exist_ok=True)
    before = work / "before_hook.mp4"
    after_norm = work / "after_norm.mp4"
    build_before_still_clip(still_jpg, before, width, height, BEFORE_AFTER_SEC)
    run_ffmpeg(
        [
            "-i",
            str(motion_mp4),
            "-vf",
            "scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps={fps},format=yuv420p".format(
                w=width, h=height, fps=FPS
            ),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "22",
            "-movflags",
            "+faststart",
            str(after_norm),
        ],
        timeout=40,
    )
    if not after_norm.is_file():
        shutil.copy2(str(motion_mp4), str(dest_mp4))
        return Path(dest_mp4)
    offset = max(0.4, BEFORE_AFTER_SEC - BEFORE_SWIPE_SEC)
    dest = Path(dest_mp4)
    try:
        run_ffmpeg(
            [
                "-i",
                str(before),
                "-i",
                str(after_norm),
                "-filter_complex",
                (
                    "[0:v][1:v]xfade=transition=slideleft:duration={fade}:offset={off}[v];"
                    "[0:a]aformat=sample_rates=44100:channel_layouts=stereo,apad=pad_dur=0.35[a0];"
                    "[a0]anull[a]"
                ).format(fade=BEFORE_SWIPE_SEC, off=offset),
                "-map",
                "[v]",
                "-map",
                "[a]",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "22",
                "-c:a",
                "aac",
                "-shortest",
                "-movflags",
                "+faststart",
                str(dest),
            ],
            timeout=45,
        )
    except Exception as exc:
        print("[안내] xfade 스와이프 실패, concat 폴백: {}".format(exc), flush=True)
        list_path = work / "ba_concat.txt"
        list_path.write_text(
            "ffconcat version 1.0\nfile '{}'\nfile '{}'\n".format(
                str(before).replace("'", "'\\''"),
                str(after_norm).replace("'", "'\\''"),
            ),
            encoding="utf-8",
        )
        run_ffmpeg(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "22",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(dest),
            ],
            timeout=40,
        )
    if dest.is_file() and dest.stat().st_size > 1000:
        return dest
    shutil.copy2(str(motion_mp4), str(dest))
    return Path(dest)


def ffmpeg_parallax_clip(src_jpg, dest_mp4, duration=5.0, width=None, height=None, intensity=7):
    """피사체/배경 속도차를 흉내 낸 3D 전진 파라랙스 클립."""
    width = int(width or TARGET_W)
    height = int(height or TARGET_H)
    width -= width % 2
    height -= height % 2
    duration = max(2.5, float(duration))
    intensity = normalize_motion_intensity(intensity)
    zoom_bg = 1.18 + 0.03 * (intensity - 6)
    zoom_fg = 1.28 + 0.04 * (intensity - 6)
    frames = max(36, int(round(duration * FPS)))
    dest = Path(dest_mp4)
    # 배경: 강한 블러 + 느린 푸시인 / 전경: 선명 크롭 + 더 빠른 전진
    fc = (
        "[0:v]scale={bw}:{bh}:flags=bicubic,boxblur=18:2,"
        "zoompan=z='min(zoom+0.0012\\,{zbg})':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n}:s={w}x{h}:fps={fps}[bg];"
        "[0:v]scale={fw}:{fh}:flags=bicubic,"
        "zoompan=z='min(zoom+0.0022\\,{zfg})':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)-on*0.35':d={n}:s={w}x{h}:fps={fps},"
        "format=rgba,colorchannelmixer=aa=0.92[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2:format=auto,format=yuv420p,setsar=1[v]"
    ).format(
        bw=int(width * zoom_bg) + int(width * zoom_bg) % 2,
        bh=int(height * zoom_bg) + int(height * zoom_bg) % 2,
        fw=int(width * zoom_fg) + int(width * zoom_fg) % 2,
        fh=int(height * zoom_fg) + int(height * zoom_fg) % 2,
        zbg="{:.3f}".format(zoom_bg),
        zfg="{:.3f}".format(zoom_fg),
        n=frames,
        w=width,
        h=height,
        fps=FPS,
    )
    try:
        run_ffmpeg(
            [
                "-loop",
                "1",
                "-t",
                "{:.3f}".format(duration),
                "-i",
                str(src_jpg),
                "-filter_complex",
                fc,
                "-map",
                "[v]",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "22",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(dest),
            ],
            timeout=35,
        )
    except Exception as exc:
        print("[안내] 파라랙스 합성 실패, 켄 번스 폴백: {}".format(exc), flush=True)
        clips = ffmpeg_ken_burns_sequence(
            src_jpg, Path(dest).parent, count=1, duration=duration, width=width, height=height, intensity=intensity
        )
        if clips:
            shutil.copy2(str(clips[0]), str(dest))
    if dest.is_file() and dest.stat().st_size > 1000:
        return dest
    raise RuntimeError("3D 파라랙스 클립 생성 실패")


def _public_job_audio_url(audio_path):
    job_dir = _job_dir_for_media(audio_path)
    dest = job_dir / "voice.mp3"
    src = Path(audio_path)
    if src.is_file() and src.resolve() != dest.resolve():
        try:
            shutil.copy2(str(src), str(dest))
        except Exception:
            pass
    if not dest.is_file():
        raise RuntimeError("공개할 음성 파일이 없습니다.")
    return "{}/job-audio/{}".format(PUBLIC_BASE_URL, job_dir.name)


def _fal_upload_or_url(path, kind="image"):
    path = Path(path)
    try:
        import fal_client

        url = fal_client.upload_file(str(path))
        if url:
            return url
    except Exception as exc:
        print("[안내] fal upload 실패({}): {}".format(kind, exc), flush=True)
    if kind == "image":
        try:
            return _public_i2v_image_url(path)
        except Exception:
            return _fal_data_uri(path)
    return _public_job_audio_url(path)


def fal_lipsync_image_to_video(image_path, audio_path, dest_mp4, timeout=45.0):
    """인물/반려동물 사진 + 나레이션 음성 → 립싱크 모션 (fal sync-lipsync / I2V 폴백)."""
    if _fal_billing_dead():
        raise RuntimeError("fal 잔액 부족 · 립싱크 폴백")
    key = (os.getenv("FAL_KEY") or "").strip()
    if not key:
        raise RuntimeError("FAL_KEY가 없습니다.")
    os.environ["FAL_KEY"] = key
    image_url = _fal_upload_or_url(image_path, kind="image")
    audio_url = _fal_upload_or_url(audio_path, kind="audio")
    model = FAL_LIPSYNC_MODEL
    payload = {"image_url": image_url, "audio_url": audio_url}
    print("   fal 립싱크: {} ← img/audio".format(model), flush=True)
    result = None
    try:
        import fal_client

        result = fal_client.subscribe(model, arguments=payload, with_logs=False)
    except Exception as exc:
        print("[안내] fal lipsync subscribe 실패, queue 재시도: {}".format(exc), flush=True)
        if _fal_is_billing_error(exc):
            _mark_fal_billing_dead()
            raise RuntimeError("fal 잔액 부족: {}".format(exc))
        result = _fal_queue_subscribe(model, payload, timeout=timeout)
    url = _fal_video_url(result)
    if not url:
        raise RuntimeError("립싱크 응답에 video url이 없습니다")
    download_http_file(url, dest_mp4, timeout=min(30, max(10, int(timeout))))
    if Path(dest_mp4).is_file() and Path(dest_mp4).stat().st_size > 1000:
        return Path(dest_mp4)
    raise RuntimeError("립싱크 다운로드 실패")


def generate_viral_motion_clips(
    media_files,
    style_prompt,
    camera_motion,
    work_dir,
    progress_cb=None,
    lock=None,
    width=None,
    height=None,
    settings=None,
    user_action="",
    job_dir=None,
    target_duration=15,
    motion_intensity=None,
    voice_path=None,
    before_after=False,
    ai_lipsync=False,
    parallax_3d=False,
):
    """인스타 바이럴 특수 연출: 립싱크 / 파라랙스 / 비포-애프터 훅."""
    width = int(width or TARGET_W)
    height = int(height or TARGET_H)
    motion_intensity = normalize_motion_intensity(motion_intensity)
    job_dir = Path(job_dir) if job_dir else Path(work_dir).parent
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    hero = i2v_hero_source(media_files, job_dir)
    if hero is None:
        return []
    frame = job_dir / "i2v_source.jpg"
    try:
        prepare_i2v_still(hero, frame, width, height)
    except Exception as exc:
        print("[안내] 바이럴 I2V 캔버스 실패: {}".format(exc), flush=True)
        return []

    clips = []
    if ai_lipsync and voice_path and Path(voice_path).is_file():
        _notify(progress_cb, 34, "🎙️ AI 페이스 립싱크 생성 중", lock)
        lipsync_out = work_dir / "lipsync_01.mp4"
        try:
            fal_lipsync_image_to_video(frame, voice_path, lipsync_out, timeout=min(50.0, FAL_WAIT_TIMEOUT + 25))
            if lipsync_out.is_file():
                clips.append(lipsync_out)
        except Exception as exc:
            print("[안내] 립싱크 API 실패, 토킹 I2V 폴백: {}".format(exc), flush=True)
            try:
                talking = work_dir / "lipsync_i2v.mp4"
                fal_image_to_video(
                    frame,
                    LIPSYNC_I2V_PROMPT + " " + (style_prompt or "")[:60],
                    talking,
                    timeout=FAL_WAIT_TIMEOUT,
                    motion_intensity=motion_intensity,
                )
                if Path(talking).is_file():
                    clips.append(talking)
            except Exception as inner:
                print("[안내] 토킹 I2V도 실패: {}".format(inner), flush=True)

    if parallax_3d and len(clips) < SPARK_MAX_CLIPS:
        _notify(progress_cb, 38, "🌌 3D 공간 입체 무빙 합성", lock)
        try:
            # fal I2V에 파라랙스 프롬프트 우선
            para_ai = work_dir / "parallax_i2v.mp4"
            fal_image_to_video(
                frame,
                PARALLAX_I2V_PROMPT + " " + (CAMERA_MOTIONS.get(normalize_camera_motion(camera_motion), "")),
                para_ai,
                timeout=FAL_WAIT_TIMEOUT,
                motion_intensity=max(motion_intensity, 7),
            )
            if Path(para_ai).is_file():
                clips.append(para_ai)
        except Exception as exc:
            print("[안내] 파라랙스 I2V 실패, 로컬 합성: {}".format(exc), flush=True)
        if len(clips) < 1 or (parallax_3d and not any("parallax" in Path(c).stem for c in clips)):
            try:
                local = work_dir / "parallax_local.mp4"
                ffmpeg_parallax_clip(
                    frame,
                    local,
                    duration=SPARK_CLIP_SEC,
                    width=width,
                    height=height,
                    intensity=motion_intensity,
                )
                clips.append(local)
            except Exception as exc:
                print("[안내] 로컬 파라랙스 실패: {}".format(exc), flush=True)

    if not clips:
        # 바이럴 토글이 있어도 기본 스파크 I2V로 채움
        clips = generate_spark_cinema_clips(
            media_files,
            style_prompt,
            camera_motion,
            work_dir,
            progress_cb=progress_cb,
            lock=lock,
            width=width,
            height=height,
            settings=settings,
            user_action=user_action,
            job_dir=job_dir,
            target_duration=target_duration,
            motion_intensity=motion_intensity,
        )

    if before_after and clips:
        _notify(progress_cb, 58, "📸 비포➔애프터 셔터 전환 적용", lock)
        hooked = []
        for index, clip in enumerate(clips[:1]):
            dest = work_dir / ("before_after_{:02d}.mp4".format(index + 1))
            try:
                apply_before_after_hook(frame, clip, dest, width=width, height=height, work_dir=work_dir)
                hooked.append(dest)
            except Exception as exc:
                print("[안내] 비포/애프터 훅 실패: {}".format(exc), flush=True)
                hooked.append(clip)
        hooked.extend(clips[1:])
        clips = hooked

    unique = []
    seen = set()
    for clip in clips:
        key = str(Path(clip).resolve())
        if key not in seen and Path(clip).is_file():
            seen.add(key)
            unique.append(Path(clip))
    return unique[:SPARK_MAX_CLIPS]


def scale_volume(clip, factor):
    if hasattr(clip, "with_volume_scaled"):
        return clip.with_volume_scaled(factor)
    if hasattr(clip, "volumex"):
        return clip.volumex(factor)
    return clip


def audio_start(clip, t):
    return _call(clip, ("with_start", "set_start"), t)


def fade_audio(clip, fade_in=0.0, fade_out=0.0):
    result = clip
    if fade_in and fade_in > 0:
        if hasattr(result, "audio_fadein"):
            result = result.audio_fadein(fade_in)
        elif hasattr(result, "with_effects"):
            try:
                from moviepy.audio.fx import AudioFadeIn

                result = result.with_effects([AudioFadeIn(fade_in)])
            except Exception:
                pass
    if fade_out and fade_out > 0:
        if hasattr(result, "audio_fadeout"):
            result = result.audio_fadeout(fade_out)
        elif hasattr(result, "with_effects"):
            try:
                from moviepy.audio.fx import AudioFadeOut

                result = result.with_effects([AudioFadeOut(fade_out)])
            except Exception:
                pass
    return result


def loop_or_trim_audio(clip, duration):
    duration = max(0.05, float(duration))
    src = float(clip.duration or 0)
    if src <= 0:
        return clip
    if src >= duration:
        return _subclip(clip, 0, duration)
    loops = int(duration / src) + 1
    looped = concatenate_audioclips([clip] * loops)
    return _subclip(looped, 0, duration)


def voice_rms(clip, samples=20):
    dur = float(clip.duration or 0)
    if dur <= 0:
        return 0.1
    vals = []
    for i in range(samples):
        t = min(dur - 1e-3, (i + 0.5) / samples * dur)
        try:
            frame = np.asarray(clip.get_frame(t), dtype=np.float64)
            vals.append(float(np.sqrt(np.mean(np.square(frame)))))
        except Exception:
            continue
    if not vals:
        return 0.1
    return max(1e-4, float(np.mean(vals)))


def trim_sfx(clip, max_dur):
    src = float(clip.duration or 0)
    if src <= 0:
        return clip
    if src > max_dur:
        return _subclip(clip, 0, max_dur)
    return clip


def mix_soundtrack(voice, duration, cue_starts, scene_starts):
    voice = scale_volume(voice, VOICE_GAIN)
    parts = [voice]
    rms = voice_rms(voice)
    bgm_gain = float(np.clip(0.018 / rms, BGM_GAIN_MIN, BGM_GAIN_MAX))

    bgm_path = pick_bgm_file()
    if bgm_path:
        print("   BGM: {} (볼륨 {:.2f}, 보이스 RMS {:.3f})".format(bgm_path.name, bgm_gain, rms))
        bgm = AudioFileClip(str(bgm_path))
        bgm = loop_or_trim_audio(bgm, duration)
        bgm = scale_volume(bgm, bgm_gain)
        bgm = fade_audio(bgm, fade_in=0.25, fade_out=0.55)
        parts.append(bgm)
    else:
        print("   [안내] bgm 폴더에 .mp3가 없어 배경음악 없이 진행합니다.")

    pop_path = ensure_pop_sfx()
    pop_src = trim_sfx(AudioFileClip(str(pop_path)), 0.28)
    for start in cue_starts:
        one = pop_src.copy() if hasattr(pop_src, "copy") else trim_sfx(AudioFileClip(str(pop_path)), 0.28)
        one = scale_volume(one, POP_GAIN)
        one = audio_start(one, float(start))
        parts.append(one)

    whoosh_path = ensure_whoosh_sfx()
    whoosh_src = trim_sfx(AudioFileClip(str(whoosh_path)), 0.42)
    for i, start in enumerate(scene_starts):
        if i == 0:
            continue
        one = whoosh_src.copy() if hasattr(whoosh_src, "copy") else trim_sfx(AudioFileClip(str(whoosh_path)), 0.42)
        one = scale_volume(one, WHOOSH_GAIN)
        one = audio_start(one, max(0.0, float(start) - 0.04))
        parts.append(one)

    mixed = CompositeAudioClip(parts)
    mixed = _call(mixed, ("with_duration", "set_duration"), duration)
    return mixed


def fit_cover_rgb(im, width, height, fast=True):
    img = im.convert("RGB")
    scale = max(width / float(img.width), height / float(img.height))
    nw = max(width, int(round(img.width * scale)))
    nh = max(height, int(round(img.height * scale)))
    resampling = getattr(Image, "Resampling", Image)
    resample = resampling.BILINEAR if fast else resampling.LANCZOS
    img = img.resize((nw, nh), resample)
    left = max(0, (nw - width) // 2)
    top = max(0, (nh - height) // 2)
    return img.crop((left, top, left + width, top + height))


def _lanczos():
    resampling = getattr(Image, "Resampling", Image)
    return getattr(resampling, "LANCZOS", getattr(Image, "LANCZOS", Image.BICUBIC))


def fit_contain_on_blur(im, width, height):
    try:
        fixed = ImageOps.exif_transpose(im)
        if fixed is not None:
            im = fixed
    except Exception:
        pass
    src = im.convert("RGB")
    lanczos = _lanczos()
    width = max(2, int(width) - int(width) % 2)
    height = max(2, int(height) - int(height) % 2)
    bg = ImageOps.fit(src, (width, height), method=lanczos, centering=(0.5, 0.5))
    bg = bg.filter(ImageFilter.GaussianBlur(radius=25))
    if hasattr(ImageOps, "contain"):
        fg = ImageOps.contain(src, (width, height), method=lanczos)
    else:
        scale = min(width / float(src.width), height / float(src.height))
        nw = max(1, int(round(src.width * scale)))
        nh = max(1, int(round(src.height * scale)))
        fg = src.resize((nw, nh), lanczos)
    canvas = Image.new("RGB", (width, height))
    canvas.paste(bg, (0, 0))
    canvas.paste(fg, ((width - fg.size[0]) // 2, (height - fg.size[1]) // 2))
    return canvas


def compose_blur_fill_frame(src_path, dest_path, width=None, height=None):
    width = int(width or TARGET_W)
    height = int(height or TARGET_H)
    width = max(2, width - width % 2)
    height = max(2, height - height % 2)
    dest_path = Path(dest_path)
    os.makedirs(str(dest_path.parent), exist_ok=True)
    src = Path(src_path)
    grab = None
    if src.suffix.lower() in VIDEO_EXTS:
        grab = dest_path.with_name(dest_path.stem + "_grab.jpg")
        ffmpeg_extract_still(src, grab, ss="0.15", qv=2, timeout=20)
        src = grab
    with Image.open(src) as raw:
        composed = fit_contain_on_blur(raw, width, height)
    composed.save(str(dest_path), "JPEG", quality=88, subsampling=2)
    composed.close()
    if grab is not None:
        try:
            grab.unlink()
        except OSError:
            pass
    if not os.path.exists(str(dest_path)) or os.path.getsize(str(dest_path)) < 32:
        raise RuntimeError("9:16 프레임 저장 실패: {}".format(dest_path))
    print("   9:16 프레임 저장: {}".format(dest_path), flush=True)
    return dest_path


def prepare_job_frames(media_files, job_dir, width=None, height=None):
    width = int(width or TARGET_W)
    height = int(height or TARGET_H)
    job_dir = Path(job_dir)
    os.makedirs(str(job_dir), exist_ok=True)
    frames = []
    sources = list(media_files or [])
    if not sources:
        dest = job_dir / "frame_0.jpg"
        Image.new("RGB", (width, height), (32, 28, 36)).save(str(dest), "JPEG", quality=85)
        return [dest]
    for index, src in enumerate(sources):
        dest = job_dir / "frame_{}.jpg".format(index)
        try:
            compose_blur_fill_frame(src, dest, width, height)
        except Exception as exc:
            print("[안내] frame_{}.jpg 합성 실패, 재시도: {}".format(index, exc), flush=True)
            still_from_media(src, dest, job_dir, width, height)
        if os.path.exists(str(dest)) and os.path.getsize(str(dest)) >= 32:
            frames.append(dest)
    if not frames:
        raise RuntimeError("9:16 프레임을 만들지 못했습니다.")
    return frames


def _is_motion_clip(path):
    return str(path).lower().endswith(MOTION_CLIP_EXTS)


def i2v_hero_source(media_files, job_dir):
    media_dir = Path(job_dir) / "media"
    candidates = []
    if media_dir.is_dir():
        for child in sorted(media_dir.iterdir()):
            if child.suffix.lower() in IMAGE_EXTS or child.suffix.lower() in VIDEO_EXTS:
                candidates.append(child)
    if not candidates:
        candidates = [Path(p) for p in (media_files or [])]
    return candidates[0] if candidates else None


def prepare_i2v_still(path, dest_jpg, width=None, height=None):
    """업로드 사진을 9:16으로 아웃페인팅(블러 확장)해 잘림·왜곡 없이 I2V에 넣는다."""
    return compose_blur_fill_frame(path, dest_jpg, width, height)


def i2v_source_paths(media_files, job_dir):
    hero = i2v_hero_source(media_files, job_dir)
    return [hero] if hero is not None else []


def still_from_media(path, dest_jpg, work_dir, width=None, height=None):
    width = int(width or TARGET_W)
    height = int(height or TARGET_H)
    dest_jpg = Path(dest_jpg)
    os.makedirs(str(Path(work_dir)), exist_ok=True)
    os.makedirs(str(dest_jpg.parent), exist_ok=True)
    try:
        return compose_blur_fill_frame(path, dest_jpg, width, height)
    except Exception as exc:
        print("[안내] 스틸 추출 실패, 단색 폴백: {}".format(exc))
    return ensure_jpeg_on_disk(dest_jpg, (width, height))


def burn_caption_on_jpeg(frame_path, text, dest_path, font_path, width, height):
    dest_path = Path(dest_path)
    os.makedirs(str(dest_path.parent), exist_ok=True)
    im = Image.open(frame_path).convert("RGB")
    if im.size != (width, height):
        im = im.resize((width, height), _lanczos())
    draw = ImageDraw.Draw(im)
    font = _load_font(font_path, max(40, int(round(height * 0.036))))
    line = wrap_caption_lines(text, width=16)
    if not line:
        shutil.copy2(str(frame_path), str(dest_path))
        return dest_path
    stroke = max(4, int(round(height * 0.0045)))
    bbox = draw.textbbox((0, 0), line, font=font, stroke_width=stroke)
    lw, lh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = max(16, (width - lw) // 2 - bbox[0])
    y = height - max(110, int(height * 0.14)) - lh - bbox[1]
    draw.text(
        (x, y),
        line,
        font=font,
        fill=(255, 255, 255),
        stroke_width=stroke,
        stroke_fill=(0, 0, 0),
    )
    im.save(str(dest_path), "JPEG", quality=88, subsampling=2)
    im.close()
    if not os.path.exists(str(dest_path)) or os.path.getsize(str(dest_path)) < 32:
        raise RuntimeError("자막 프레임 저장 실패: {}".format(dest_path))
    return dest_path


def arrange_media_for_cues(media_files, cues, photo_order):
    n = max(1, len(media_files))
    if photo_order:
        cycle = [media_files[i] for i in photo_order if 0 <= i < n]
    else:
        cycle = list(media_files)
    if not cycle:
        cycle = list(media_files)
    return [cycle[i % len(cycle)] for i in range(len(cues))]


def compose_captioned_png(src, caption, font_path, dest_png, work_dir, direction=None):
    dest_jpg = Path(dest_png).with_suffix(".jpg")
    still_from_media(src, dest_jpg, work_dir, TARGET_W, TARGET_H)
    if dest_jpg.resolve() != Path(dest_png).resolve():
        shutil.copy2(str(dest_jpg), str(dest_png))
    return Path(dest_png)


def prepare_captioned_frames(
    media_files,
    pieces,
    photo_order,
    font_path,
    work_dir,
    direction=None,
    width=None,
    height=None,
):
    dummy_cues = [(text, 0.0, 1.0) for text in pieces]
    assigned = arrange_media_for_cues(media_files, dummy_cues, photo_order or [])
    width = int(width or TARGET_W)
    height = int(height or TARGET_H)
    jobs = []
    for index, src in enumerate(assigned):
        framed = work_dir / ("frame_{:03d}.jpg".format(index))
        jobs.append((src, framed))

    def _one(item):
        src, dest = item
        still_from_media(src, dest, work_dir, width, height)
        return ensure_jpeg_on_disk(dest, (width, height))

    workers = min(4, max(1, len(jobs)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        frames = list(pool.map(_one, jobs))
    return frames


def _concat_file_line(path):
    return "file '{}'".format(Path(path).resolve().as_posix().replace("'", r"'\''"))


def write_concat_list(entries, list_path):
    lines = ["ffconcat version 1.0"]
    last = None
    for png, dur in entries:
        if not os.path.exists(str(png)) or os.path.getsize(str(png)) < 32:
            continue
        last = png
        lines.append(_concat_file_line(png))
        lines.append("duration {:.4f}".format(max(0.04, float(dur))))
    if last is None:
        raise RuntimeError("concat에 사용할 이미지가 없습니다.")
    lines.append(_concat_file_line(last))
    os.makedirs(str(Path(list_path).parent), exist_ok=True)
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return list_path


def build_slideshow_entries(frames, durations, work_dir, speed=1.0, xfade_sec=XFADE_SEC):
    speed = max(0.5, float(speed))
    xfade_sec = max(0.12, min(0.6, float(xfade_sec or XFADE_SEC)))
    scaled = [max(0.2, float(dur) / speed) for dur in durations]
    if len(frames) == 1:
        return [(frames[0], scaled[0])]

    opened = [open_image_upright(path).convert("RGB") for path in frames]
    entries = []
    try:
        for i, png in enumerate(frames):
            last = i == len(frames) - 1
            fade = 0.0
            if not last:
                fade = min(xfade_sec, scaled[i] * 0.45, scaled[i + 1] * 0.45)
            hold = scaled[i] if last else max(0.12, scaled[i] - fade)
            entries.append((png, hold))
            if last or fade < 0.12:
                continue
            count = max(6, int(round(FPS * fade)))
            step = fade / float(count)
            src_a = opened[i]
            src_b = opened[i + 1]
            for k in range(count):
                alpha = (k + 1) / float(count)
                mix = Image.blend(src_a, src_b, alpha)
                out = work_dir / "xfade_{:03d}_{:02d}.jpg".format(i, k)
                save_image_verified(mix, out, quality=82)
                entries.append((out, step))
    finally:
        for img in opened:
            img.close()
    return entries


def ffmpeg_single_pass(
    frames,
    durations,
    voice_path,
    bgm_path,
    out_file,
    speed=1.0,
    work_dir=None,
    xfade_sec=XFADE_SEC,
    cues=None,
    font_path=None,
    width=None,
    height=None,
    visual_fx="ken_burns",
    caption_style="hormozi",
    audio_ducking=True,
    target_duration=None,
):
    del xfade_sec, visual_fx, caption_style, audio_ducking
    speed = normalize_speed(speed)
    out_file = Path(out_file)
    job_dir = out_file.parent
    os.makedirs(str(job_dir), exist_ok=True)
    width = int(width or TARGET_W)
    height = int(height or TARGET_H)
    work_dir = Path(work_dir or job_dir)
    os.makedirs(str(work_dir), exist_ok=True)

    disk_frames = []
    found = list(job_dir.glob("frame_*.jpg"))

    def _frame_key(path):
        try:
            return int(Path(path).stem.split("_")[-1])
        except (TypeError, ValueError):
            return 0

    for path in sorted(found, key=_frame_key):
        if os.path.exists(str(path)) and os.path.getsize(str(path)) >= 32:
            disk_frames.append(path)
    if not disk_frames:
        sources = list(frames or [])
        disk_frames = prepare_job_frames(sources, job_dir, width, height)
    if not disk_frames:
        raise RuntimeError("렌더할 프레임이 없습니다.")
    for frame in disk_frames:
        require_image_for_ffmpeg(frame)

    n = len(disk_frames)
    hold = max(float(target_duration or 0), 1.0)
    if durations and len(durations) == n:
        scaled = [max(0.2, float(d) / max(0.5, speed)) for d in durations]
        total = sum(scaled)
        if total > 0.2:
            scaled = [hold * (d / total) for d in scaled]
        else:
            scaled = even_scene_durations(n, hold)
    else:
        scaled = even_scene_durations(n, hold)
    scaled[-1] = max(0.2, hold - sum(scaled[:-1]))

    if cues and font_path:
        captioned = []
        cue_durs = []
        for i, (text, start, end) in enumerate(cues):
            src = disk_frames[i % len(disk_frames)]
            dest = job_dir / "caption_{}.jpg".format(i)
            try:
                burn_caption_on_jpeg(src, text, dest, font_path, width, height)
            except Exception as exc:
                print("[안내] 자막 JPEG 합성 실패, 원본 프레임 사용: {}".format(exc), flush=True)
                dest = src
            captioned.append(dest)
            cue_durs.append(max(0.35, float(end) - float(start)))
        if captioned:
            disk_frames = captioned
            total = sum(cue_durs)
            if total > 0.2:
                scaled = [hold * (d / total) for d in cue_durs]
                scaled[-1] = max(0.2, hold - sum(scaled[:-1]))
            n = len(disk_frames)

    voice_mp3 = job_dir / "voice.mp3"
    src_voice = Path(voice_path)
    audio_in = src_voice if src_voice.is_file() else voice_mp3
    if not audio_in.is_file():
        raise RuntimeError("voice.mp3가 없습니다.")
    if audio_in.resolve() != voice_mp3.resolve() and src_voice.suffix.lower() == ".mp3":
        if not voice_mp3.is_file():
            shutil.copy2(str(src_voice), str(voice_mp3))

    bgm_wav = None
    if bgm_path and Path(bgm_path).is_file():
        src_bgm = Path(bgm_path)
        bgm_wav = job_dir / "bgm.wav"
        if src_bgm.resolve() == bgm_wav.resolve():
            pass
        else:
            try:
                run_ffmpeg(
                    [
                        "-stream_loop",
                        "-1",
                        "-i",
                        str(src_bgm),
                        "-t",
                        "{:.3f}".format(hold),
                        "-ac",
                        "2",
                        "-ar",
                        "44100",
                        str(bgm_wav),
                    ],
                    timeout=25,
                )
            except Exception as exc:
                print("[안내] bgm.wav 변환 실패: {}".format(exc), flush=True)
                bgm_wav = src_bgm if src_bgm.suffix.lower() == ".wav" else None
        if bgm_wav is not None and (not os.path.exists(str(bgm_wav)) or os.path.getsize(str(bgm_wav)) < 32):
            bgm_wav = None

    args = []
    for frame, dur in zip(disk_frames, scaled):
        require_image_for_ffmpeg(frame)
        args += [
            "-loop",
            "1",
            "-framerate",
            str(STILL_FPS),
            "-t",
            "{:.3f}".format(max(0.2, float(dur))),
            "-i",
            str(frame),
        ]
    args += ["-i", str(audio_in)]
    voice_idx = n
    bgm_idx = None
    if bgm_wav is not None:
        args += ["-i", str(bgm_wav)]
        bgm_idx = n + 1

    vf_parts = []
    for i in range(n):
        vf_parts.append(
            "[{i}:v]fps={fps},format=yuv420p,setsar=1[v{i}]".format(i=i, fps=STILL_FPS)
        )
    if n == 1:
        vstream = "v0"
    else:
        vf_parts.append(
            "{}concat=n={}:v=1:a=0[vcat]".format(
                "".join("[v{}]".format(i) for i in range(n)), n
            )
        )
        vstream = "vcat"

    trim = "{:.3f}".format(hold)
    if bgm_idx is not None:
        audio_filters = [
            "[{v}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,volume=1.08,apad,atrim=0:{t}[va];"
            "[{b}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,volume=0.18,atrim=0:{t}[ba];"
            "[va][ba]amix=inputs=2:duration=first:dropout_transition=0[a]".format(
                v=voice_idx, b=bgm_idx, t=trim
            )
        ]
    else:
        audio_filters = [
            "[{v}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
            "volume=1.05,apad,atrim=0:{t},asetpts=PTS-STARTPTS[a]".format(
                v=voice_idx, t=trim
            )
        ]
    a_map = "[a]"
    fc = ";".join(vf_parts + audio_filters)
    encode = [
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-tune",
        "stillimage",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-threads",
        "0",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-shortest",
        "-movflags",
        "+faststart",
    ]
    cmd = args + [
        "-filter_complex",
        fc,
        "-map",
        "[{}]".format(vstream),
        "-map",
        a_map,
        "-t",
        trim,
        "-r",
        str(STILL_FPS),
    ] + encode + [str(out_file)]
    run_ffmpeg(cmd, timeout=max(180, min(int(hold * 12 + 60), 300)))


def resolve_bgm(mood, duration, dest):
    mood = normalize_bgm_mood(mood)
    if mood == "none":
        return None
    bgm_path = pick_bgm_for_mood(mood)
    if bgm_path is not None:
        print("   BGM 파일: {}".format(bgm_path.name))
        return bgm_path
    made = synthesize_bgm(mood, duration, dest)
    print("   합성 BGM: {}".format(mood))
    return made


def _notify(progress_cb, percent, message, lock=None):
    print(message)
    if progress_cb is None:
        return
    if lock is not None:
        with lock:
            progress_cb(percent, message)
    else:
        progress_cb(percent, message)


def download_http_file(url, dest, timeout=180):
    response = requests.get(url, timeout=timeout)
    if response.status_code >= 400:
        raise RuntimeError("파일 다운로드 실패 ({}): {}".format(response.status_code, url[:120]))
    Path(dest).write_bytes(response.content)
    return Path(dest)


def _fal_video_url(result):
    if not isinstance(result, dict):
        return None
    video = result.get("video")
    if isinstance(video, dict):
        return video.get("url")
    if isinstance(video, str) and video.startswith("http"):
        return video
    for key in ("url", "video_url"):
        value = result.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
    return None


def natural_i2v_prompt(style_prompt=""):
    extra = sanitize_narration(style_prompt or "")
    if extra:
        return "{} Cinematic mood: {}.".format(NATURAL_I2V_PROMPT, extra[:80])
    return NATURAL_I2V_PROMPT


def _user_motion_english_hint(user_action):
    text = sanitize_narration(user_action or "")
    if not text:
        return ""
    mapping = (
        ("질주", "the subject accelerates forward with physically accurate motion"),
        ("앞으로", "the subject moves forward toward the camera"),
        ("손 흔", "the person waves a hand toward the camera"),
        ("흔들", "the subject waves or sways naturally"),
        ("바라보", "the subject turns and looks at the camera"),
        ("카메라", "the subject looks into the camera lens"),
        ("고개", "the subject turns its head slightly"),
        ("꼬리", "the animal wags its tail"),
        ("바퀴", "wheels rotate and the vehicle rolls forward"),
        ("오토바이", "the motorcycle rider accelerates forcefully, wheels spinning, suspension reacting"),
        ("바이크", "the motorcycle rider accelerates forcefully, wheels spinning, suspension reacting"),
        ("스윙", "the swing moves back and forth with pendulum physics"),
        ("그네", "the swing moves back and forth with pendulum physics"),
        ("jump", "the subject jumps with realistic weight and landing"),
        ("wave", "the person waves a hand toward the camera"),
        ("run", "the subject runs forward with natural gait"),
    )
    lowered = text.lower()
    for key, hint in mapping:
        if key.lower() in lowered:
            return "{} ({})".format(hint, text)
    return text


def _default_i2v_layers(user_action="", style_prompt="", camera_motion=""):
    hint = _user_motion_english_hint(user_action)
    cam_key = normalize_camera_motion(camera_motion)
    subject_action = hint or (
        "The subject in the photo moves naturally, blinking, breathing, turning head smoothly with organic physics"
    )
    environment = (
        "asphalt reflections, motion blur, smoke and dust particles, refractive lighting, "
        "soft depth of field, cinematic night or daylight matching the photo"
    )
    if any(k in (hint + " " + (style_prompt or "")).lower() for k in ("bike", "motorcycle", "바이크", "오토바이")):
        subject_action = (
            "The motorcycle rider in the photo accelerates forcefully along the city asphalt, "
            "wheels spinning with realistic motion blur, suspension rebound, headlights beaming forward"
        )
        environment = "cinematic night reflections on wet asphalt, tire smoke, dust particles, motion blur"
    return {
        "subject": "the exact subject from the input photo",
        "subject_action": subject_action,
        "camera": CAMERA_LAYER.get(cam_key, "Cinematic Push-in"),
        "environment": environment,
        "quality": I2V_QUALITY_LAYER,
    }


def vision_four_layer_analysis(settings, image_path, user_action="", style_prompt="", camera_motion=""):
    layers = _default_i2v_layers(user_action, style_prompt, camera_motion)
    api_key = getattr(settings, "openai_api_key", "") or os.getenv("OPENAI_API_KEY") or ""
    b64 = media_to_preview_b64(Path(image_path)) if image_path else None
    if not api_key or not b64:
        return layers
    try:
        client = OpenAI(api_key=api_key)
        body = [
            {
                "type": "text",
                "text": (
                    "Analyze this photo. Detect the main subject (person, motorcycle, animal, background). "
                    "Return JSON only with keys: subject, subject_action, camera, environment, quality. "
                    "subject_action: concrete physics (wheels spinning, suspension rebound, gaze shift, tail wag, breathing). "
                    "camera: one of FPV Tracking Shot, Dynamic Low-angle Pan, Cinematic Push-in, Orbit 360. "
                    "Preferred camera: {cam}. "
                    "environment: asphalt reflections, motion blur, smoke/dust particles, lighting refraction matching the photo. "
                    "quality: always '{quality}'. "
                    "User action: {action}. Style: {style}. "
                    "Preserve identity. English values only."
                ).format(
                    cam=CAMERA_LAYER.get(normalize_camera_motion(camera_motion), "Cinematic Push-in"),
                    quality=I2V_QUALITY_LAYER,
                    action=_user_motion_english_hint(user_action) or "(infer natural physics)",
                    style=sanitize_narration(style_prompt) or "cinematic short-form",
                ),
            },
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
        ]
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": body}],
            temperature=0.35,
            max_tokens=280,
            response_format={"type": "json_object"},
        )
        raw = (response.choices[0].message.content or "").strip()
        data = json.loads(raw)
        for key in ("subject", "subject_action", "camera", "environment", "quality"):
            value = sanitize_narration(str(data.get(key) or "")).replace("\n", " ")
            if value:
                layers[key] = value
        print("   Vision 4레이어: {subject} | {action} | {cam}".format(
            subject=layers["subject"][:48],
            action=layers["subject_action"][:72],
            cam=layers["camera"][:40],
        ), flush=True)
        return layers
    except Exception as exc:
        print("[안내] Vision 4레이어 분석 실패, 로컬 레이어 사용: {}".format(exc), flush=True)
        return layers


def compose_layered_i2v_prompt(layers, shot_prompt, user_action=""):
    hint = _user_motion_english_hint(user_action)
    extra = (" User-requested action: {}.".format(hint) if hint else "")
    return (
        "{action}. Camera: {camera}. Shot: {shot}. Environment: {env}. {quality}. "
        "Keep the exact subject from the input photo.{extra}"
    ).format(
        action=layers.get("subject_action") or NATURAL_I2V_PROMPT,
        camera=layers.get("camera") or "Cinematic Push-in",
        shot=shot_prompt,
        env=layers.get("environment") or "motion blur, cinematic lighting",
        quality=layers.get("quality") or I2V_QUALITY_LAYER,
        extra=extra,
    )


def build_multishot_i2v_prompts(layers, target_duration, user_action=""):
    shots = list(SHOT_SEQUENCE) if int(target_duration or 15) >= 15 else [SHOT_SEQUENCE[1]]
    return [(key, compose_layered_i2v_prompt(layers, prompt, user_action)) for key, prompt in shots]


def vision_subject_motion_prompt(settings, image_path, user_action="", style_prompt="", camera_motion=""):
    layers = vision_four_layer_analysis(settings, image_path, user_action, style_prompt, camera_motion)
    return compose_layered_i2v_prompt(layers, SHOT_SEQUENCE[1][1], user_action)


def _job_dir_for_media(path):
    path = Path(path).resolve()
    for parent in path.parents:
        if parent.parent.name == "mobile_jobs":
            return parent
    return path.parent


def _public_i2v_image_url(image_path):
    job_dir = _job_dir_for_media(image_path)
    dest = job_dir / "i2v_source.jpg"
    src = Path(image_path)
    if src.suffix.lower() in IMAGE_EXTS and src.is_file():
        shutil.copy2(str(src), str(dest))
    elif dest.is_file():
        pass
    else:
        raise RuntimeError("I2V 소스 이미지가 없습니다.")
    return "{}/i2v-image/{}".format(PUBLIC_BASE_URL, job_dir.name)


def _fal_data_uri(image_path):
    raw = Path(image_path).read_bytes()
    if len(raw) > 3_000_000:
        slim = diet_image_file(image_path, dest=Path(image_path).with_name(Path(image_path).stem + "_uri.jpg"))
        raw = Path(slim).read_bytes()
    return "data:image/jpeg;base64,{}".format(base64.b64encode(raw).decode("ascii"))


_FAL_BILLING_LOCK = threading.Lock()
_FAL_BILLING_DEAD = False


def reset_fal_billing_flag():
    global _FAL_BILLING_DEAD
    with _FAL_BILLING_LOCK:
        _FAL_BILLING_DEAD = False


def _fal_billing_dead():
    with _FAL_BILLING_LOCK:
        return _FAL_BILLING_DEAD


def _mark_fal_billing_dead():
    global _FAL_BILLING_DEAD
    with _FAL_BILLING_LOCK:
        _FAL_BILLING_DEAD = True


def _fal_is_billing_error(exc):
    text = str(exc or "").lower()
    return any(
        token in text
        for token in (
            "exhausted balance",
            "user is locked",
            "top up your balance",
            "payment required",
            "insufficient credits",
        )
    )


def _fal_queue_subscribe(model, payload, timeout=90):
    key = (os.getenv("FAL_KEY") or "").strip()
    if not key:
        raise RuntimeError("FAL_KEY가 없습니다.")
    headers = {"Authorization": "Key {}".format(key), "Content-Type": "application/json"}
    submit = requests.post(
        "https://queue.fal.run/{}".format(model),
        headers=headers,
        json=payload,
        timeout=45,
    )
    if submit.status_code >= 400:
        raise RuntimeError("fal queue {} HTTP {}: {}".format(model, submit.status_code, submit.text[:400]))
    data = submit.json()
    status_url = data.get("status_url")
    response_url = data.get("response_url")
    request_id = data.get("request_id")
    if not status_url and request_id:
        status_url = "https://queue.fal.run/{}/requests/{}/status".format(model, request_id)
        response_url = "https://queue.fal.run/{}/requests/{}".format(model, request_id)
    deadline = time.time() + float(timeout)
    while time.time() < deadline:
        st = requests.get(status_url, headers=headers, timeout=20)
        js = st.json() if st.content else {}
        status = str(js.get("status") or "").upper()
        if status in ("COMPLETED", "OK"):
            resp = requests.get(response_url, headers=headers, timeout=30)
            resp.raise_for_status()
            return resp.json()
        if status in ("FAILED", "ERROR"):
            raise RuntimeError("fal 작업 실패: {}".format(str(js)[:400]))
        time.sleep(1.0)
    raise RuntimeError("fal queue 대기 시간 초과 ({}s)".format(int(timeout)))


def fal_image_to_video(image_path, prompt, dest_mp4, timeout=None, models=None, motion_intensity=None):
    timeout = float(timeout or FAL_WAIT_TIMEOUT)
    motion_intensity = normalize_motion_intensity(motion_intensity)
    models = tuple(models or (FAL_I2V_PRIMARY, FAL_I2V_FALLBACK, FAL_I2V_FAST))
    deadline = time.time() + timeout
    if _fal_billing_dead():
        raise RuntimeError("fal 잔액 부족 · 켄 번스 폴백")
    try:
        try:
            import fal_client
        except ImportError:
            fal_client = None
        key = (os.getenv("FAL_KEY") or "").strip()
        if key:
            os.environ["FAL_KEY"] = key
        try:
            slim_dest = Path(image_path).with_name(Path(image_path).stem + "_fal.jpg")
            shutil.copy2(str(image_path), str(slim_dest))
            slim = diet_image_file(slim_dest, dest=slim_dest)
        except Exception:
            slim = image_path
        prompt = (prompt or NATURAL_I2V_PROMPT).strip()
        image_urls = []
        try:
            image_urls.append(_public_i2v_image_url(slim))
        except Exception as exc:
            print("[안내] 공개 I2V URL 생성 실패: {}".format(exc), flush=True)
        try:
            image_urls.append(_fal_data_uri(slim))
        except Exception:
            pass
        if not image_urls:
            raise RuntimeError("I2V에 넘길 이미지 URL이 없습니다.")
        last_error = None
        for model in models:
            left = deadline - time.time()
            if left < 3.5:
                break
            image_url = image_urls[0]
            try:
                print("   fal I2V: {} ← {}".format(model, str(image_url)[:88]), flush=True)
                payload = {
                    "prompt": prompt,
                    "image_url": image_url,
                    "motion_bucket_id": MOTION_BUCKET_ID,
                    "motion_intensity": motion_intensity,
                }
                if "kling" in model:
                    payload["duration"] = "5"
                    payload["cfg_scale"] = 0.5
                elif "stable-video" in model or "svd" in model:
                    payload.pop("prompt", None)
                    payload["prompt"] = prompt
                else:
                    payload["prompt_optimizer"] = True
                    result = None
                if fal_client is not None and left > 8 and not _fal_billing_dead():
                    try:
                        result = fal_client.subscribe(model, arguments=payload, with_logs=False)
                    except Exception as exc:
                        print("[안내] fal subscribe 실패, queue API 재시도: {}".format(exc), flush=True)
                        if _fal_is_billing_error(exc):
                            _mark_fal_billing_dead()
                            raise RuntimeError("fal 잔액 부족: {}".format(exc))
                if result is None:
                    result = _fal_queue_subscribe(model, payload, timeout=max(4.0, left - 2.0))
                url = _fal_video_url(result)
                if not url:
                    raise RuntimeError("fal 응답에 video url이 없습니다: {}".format(str(result)[:400]))
                download_http_file(url, dest_mp4, timeout=min(20, max(8, int(deadline - time.time()))))
                if Path(dest_mp4).is_file() and Path(dest_mp4).stat().st_size > 1000:
                    return Path(dest_mp4)
            except Exception as exc:
                last_error = exc
                print("[안내] {} 실패: {}".format(model, exc), flush=True)
                if _fal_is_billing_error(exc):
                    _mark_fal_billing_dead()
                    raise RuntimeError("fal 잔액 부족: {}".format(exc))
        raise RuntimeError("Image-to-Video 생성 실패: {}".format(last_error))
    except Exception as exc:
        print("[안내] fal.ai 전체 실패(프로세스 유지): {}".format(exc))
        raise RuntimeError("✨ 스파크 시네마 AI 호출 실패: {}".format(exc))


async def fal_image_to_video_timed(image_path, prompt, dest_mp4, timeout=FAL_WAIT_TIMEOUT, models=None, motion_intensity=None):
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                fal_image_to_video,
                image_path,
                prompt,
                dest_mp4,
                timeout,
                models,
                motion_intensity,
            ),
            timeout=float(timeout),
        )
    except asyncio.TimeoutError:
        print("[안내] fal.ai {}초 타임아웃 → 하위 엔진 폴백".format(timeout))
        raise RuntimeError("fal.ai 대기열 타임아웃 ({}s)".format(timeout))


VIP_I2V_TIMEOUT = 25.0
ACTION_PRESETS = {
    "bike_stunt": "오토바이 앞바퀴를 들고 묘기 부리며 질주하는 장면, 엔진 배기음과 타이어 연기",
    "dance": "비트에 맞춘 역동적인 댄스, 강한 제스처와 카메라 펀치 인",
    "dynamic": "폭발적인 다이내믹 액션, 파편과 에너지, 빠른 카메라 푸시",
    "sprint": "전력 질주하는 추적 샷, 바람과 먼지가 흩날리는 장면",
}
MULTI_ANGLES = (
    ("wide", "wide cinematic establishing shot, full body visible in the environment, natural parallax"),
    ("close", "tight close-up preserving the exact face and outfit, shallow depth of field, micro motion"),
    ("drone", "high cinematic drone aerial tracking shot, smooth orbit, keep subject identity"),
)


def normalize_action_preset(value):
    key = (value or "").strip().lower().replace("-", "_").replace(" ", "")
    aliases = {
        "bike_stunt": "bike_stunt",
        "bike": "bike_stunt",
        "바이크": "bike_stunt",
        "오토바이": "bike_stunt",
        "dance": "dance",
        "댄스": "dance",
        "dynamic": "dynamic",
        "다이내믹": "dynamic",
        "sprint": "sprint",
        "질주": "sprint",
        "run": "sprint",
    }
    return aliases.get(key, "")


def resolve_action_style(preset="", custom=""):
    preset_key = normalize_action_preset(preset)
    custom = (custom or "").strip()
    parts = []
    if preset_key:
        parts.append(ACTION_PRESETS[preset_key])
    if custom:
        parts.append(custom)
    return " / ".join(parts) or ACTION_PRESETS["dynamic"]


def expand_action_i2v_prompt(settings, action_style, angle_prompt, style_prompt=""):
    fallback = (
        "Keep the exact subject from the input photo, including face, fur, clothing, and proportions. "
        "{angle}. Natural motion first: blinking, breathing, slight head turn, tail or hair movement. "
        "Then action: {action}. Photorealistic, no morphing, no extra limbs. Style: {style}."
    ).format(
        angle=angle_prompt,
        action=action_style or "dynamic cinematic motion",
        style=style_prompt or "cinematic short-form",
    )
    api_key = getattr(settings, "openai_api_key", "") or os.getenv("OPENAI_API_KEY") or ""
    if not api_key:
        return fallback
    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You write a single English image-to-video prompt. Preserve identity. "
                        "Include real-world physics and a camera move. No quotes, no lists."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Action (Korean/English): {}\nCamera/angle: {}\nStyle: {}\n"
                        "Write one dense I2V prompt."
                    ).format(action_style, angle_prompt, style_prompt or "cinematic"),
                },
            ],
            temperature=0.7,
            max_tokens=220,
        )
        text = (response.choices[0].message.content or "").strip().replace("\n", " ")
        return text or fallback
    except Exception as exc:
        print("[안내] 액션 프롬프트 확장 실패, 로컬 프롬프트 사용: {}".format(exc))
        return fallback


def action_sfx_kinds(action_style):
    text = (action_style or "").lower()
    if any(k in text for k in ("바이크", "오토바이", "bike", "wheelie", "엔진", "질주", "타이어")):
        return ("engine", "tire", "wind")
    if any(k in text for k in ("댄스", "dance", "춤")):
        return ("whoosh", "pop", "boom")
    if any(k in text for k in ("폭발", "파열", "boom", "임팩트")):
        return ("boom", "whoosh", "wind")
    return ("whoosh", "wind", "boom")


def ensure_engine_sfx():
    found = find_named_sfx("engine")
    if found:
        return found
    path = SFX_DIR / "engine.wav"
    sr = 44100
    n = int(sr * 1.8)
    t = np.arange(n, dtype=np.float64) / sr
    rumble = 0.22 * np.sin(2 * np.pi * 72 * t) + 0.12 * np.sin(2 * np.pi * 36 * t)
    pops = ((t * 18) % 1.0 < 0.08).astype(np.float64) * 0.18 * np.sin(2 * np.pi * 220 * t)
    env = np.minimum(1.0, np.minimum(t / 0.05, (t[-1] - t) / 0.12))
    _write_wav_mono(path, (rumble + pops) * env, sr)
    return path


def ensure_tire_sfx():
    found = find_named_sfx("tire") or find_named_sfx("skid")
    if found:
        return found
    path = SFX_DIR / "tire.wav"
    sr = 44100
    n = int(sr * 0.7)
    t = np.arange(n, dtype=np.float64) / sr
    noise = np.random.default_rng(21).normal(0, 1, n)
    hiss = noise * np.exp(-t * 3.2) * (0.35 + 0.2 * np.sin(2 * np.pi * 18 * t))
    _write_wav_mono(path, hiss, sr)
    return path


def ensure_boom_sfx():
    found = find_named_sfx("boom") or find_named_sfx("impact")
    if found:
        return found
    path = SFX_DIR / "boom.wav"
    sr = 44100
    n = int(sr * 0.9)
    t = np.arange(n, dtype=np.float64) / sr
    boom = np.sin(2 * np.pi * (48 + t * 20) * t) * np.exp(-t * 5.5)
    noise = np.random.default_rng(3).normal(0, 1, n) * np.exp(-t * 9) * 0.22
    _write_wav_mono(path, 0.85 * boom + noise, sr)
    return path


def ensure_wind_sfx():
    found = find_named_sfx("wind") or find_named_sfx("air")
    if found:
        return found
    path = SFX_DIR / "wind.wav"
    sr = 44100
    n = int(sr * 1.4)
    t = np.arange(n, dtype=np.float64) / sr
    noise = np.random.default_rng(11).normal(0, 1, n)
    wind = noise * (0.18 + 0.12 * np.sin(2 * np.pi * 0.7 * t)) * np.minimum(1.0, t / 0.08)
    _write_wav_mono(path, wind, sr)
    return path


def resolve_sfx_file(kind):
    if kind == "engine":
        return ensure_engine_sfx()
    if kind == "tire":
        return ensure_tire_sfx()
    if kind == "boom":
        return ensure_boom_sfx()
    if kind == "wind":
        return ensure_wind_sfx()
    if kind == "pop":
        return ensure_pop_sfx()
    return ensure_whoosh_sfx()


def split_words_timed(script, duration):
    words = [w for w in re.findall(r"\S+", sanitize_narration(script) or "") if w]
    duration = max(1.0, float(duration))
    if not words:
        return []
    weights = [max(1, len(re.sub(r"\W+", "", w, flags=re.U)) or 1) for w in words]
    total = float(sum(weights))
    cursor = 0.0
    out = []
    for word, weight in zip(words, weights):
        span = duration * (weight / total)
        out.append((word, cursor, min(duration, cursor + max(0.08, span))))
        cursor += span
    if out:
        out[-1] = (out[-1][0], out[-1][1], duration)
    return out


def _ass_clock(seconds):
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds - hours * 3600 - minutes * 60
    return "{:d}:{:02d}:{:05.2f}".format(hours, minutes, secs)


def write_kinetic_ass(words, dest, play_w=720, play_h=1280):
    dest = Path(dest)
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: {}".format(int(play_w)),
        "PlayResY: {}".format(int(play_h)),
        "WrapStyle: 2",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Kinetic,NanumGothic,28,&H00FFFFFF,&H000000FF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,4,0,2,36,36,72,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for index, (word, start, end) in enumerate(words):
        left = " ".join(w for w, _s, _e in words[max(0, index - 2) : index])
        right = " ".join(w for w, _s, _e in words[index + 1 : index + 3])
        text = r"{{\c&H00FFFFFF&}}" + left
        if left:
            text += " "
        text += r"{{\c&H0000EAFF&\b1}}" + word + r"{{\c&H00FFFFFF&\b0}}"
        if right:
            text += " " + right
        lines.append(
            "Dialogue: 0,{},{},Kinetic,,0,0,0,,{}".format(
                _ass_clock(start), _ass_clock(end), text.replace("\n", " ")
            )
        )
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest


def generate_vip_action_clips(
    media_files,
    settings,
    action_style,
    style_prompt,
    work_dir,
    progress_cb=None,
    lock=None,
    width=None,
    height=None,
):
    sources = list(media_files)[:SPARK_MAX_CLIPS]
    width = int(width or TARGET_W)
    height = int(height or TARGET_H)
    _notify(progress_cb, 32, "👑 VIP 액션 모션 · Kling/Minimax I2V 합성", lock)
    clips = []

    async def _one(index, src):
        angle_key, angle_prompt = MULTI_ANGLES[index % len(MULTI_ANGLES)]
        prompt = expand_action_i2v_prompt(settings, action_style, angle_prompt, style_prompt)
        print("   VIP I2V [{}]: {}".format(angle_key, prompt[:160]))
        frame = work_dir / ("vip_src_{:02d}.jpg".format(index + 1))
        await asyncio.to_thread(still_from_media, src, frame, work_dir, width, height)
        clip = work_dir / ("vip_{:02d}.mp4".format(index + 1))
        await fal_image_to_video_timed(frame, prompt, clip, timeout=VIP_I2V_TIMEOUT)
        return clip

    async def _gather():
        return await asyncio.wait_for(
            asyncio.gather(*[_one(i, src) for i, src in enumerate(sources)], return_exceptions=True),
            timeout=VIP_I2V_TIMEOUT + 20,
        )

    try:
        results = asyncio.run(_gather())
    except Exception as exc:
        print("[안내] VIP I2V 병렬 실패: {}".format(exc))
        return []
    for item in results:
        if isinstance(item, Exception):
            print("[안내] VIP 클립 실패: {}".format(item))
        elif item and Path(item).is_file():
            clips.append(item)
    return clips


def mix_vip_sfx(video_path, out_file, duration, action_style, cue_starts, scene_starts, work_dir):
    kinds = action_sfx_kinds(action_style)
    inputs = ["-i", str(video_path)]
    filters = []
    mix_labels = ["[0:a]"]
    index = 1
    climax = max(0.6, float(duration) * 0.72)

    def _add(kind, at, volume=0.42):
        nonlocal index
        path = resolve_sfx_file(kind)
        inputs.extend(["-i", str(path)])
        delay_ms = max(0, int(round(float(at) * 1000)))
        lab = "s{}".format(index)
        filters.append("[{}:a]adelay={}|{},volume={:.2f},apad=pad_dur=2[{}]".format(index, delay_ms, delay_ms, volume, lab))
        mix_labels.append("[{}]".format(lab))
        index += 1

    for kind in kinds[:2]:
        _add(kind, 0.15 if kind != "boom" else climax, 0.38 if kind != "engine" else 0.28)
    _add("whoosh", 0.04, 0.34)
    for start in list(scene_starts or [])[1:4]:
        _add("whoosh", max(0.0, float(start) - 0.05), 0.32)
    for start in list(cue_starts or [])[:6]:
        _add("pop", float(start), 0.28)
    _add("boom", climax, 0.55)
    n = len(mix_labels)
    filters.append("{}amix=inputs={}:duration=first:dropout_transition=0,volume=1.05[aout]".format("".join(mix_labels), n))
    args = inputs + [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "0:v:0",
        "-map",
        "[aout]",
        "-t",
        "{:.3f}".format(float(duration)),
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        str(out_file),
    ]
    run_ffmpeg(args, timeout=40)
    return Path(out_file)


def burn_kinetic_captions(video_path, script, duration, font_path, out_file, work_dir, width, height, caption_style="hormozi"):
    words = split_words_timed(script, duration)
    ass = write_kinetic_ass(words, Path(work_dir) / "kinetic.ass", play_w=width, play_h=height)
    srt = write_cues_srt([(w, s, e) for w, s, e in words], Path(work_dir) / "kinetic.srt")
    vf = "ass={}".format(_ffmpeg_filter_path(ass))
    try:
        run_ffmpeg(
            [
                "-i",
                str(video_path),
                "-vf",
                vf,
                "-c:a",
                "copy",
            ]
            + FFMPEG_PRESET
            + [str(out_file)],
            timeout=50,
        )
        return Path(out_file)
    except Exception as exc:
        print("[안내] ASS 키네틱 자막 실패, SRT 폴백: {}".format(exc))
        run_ffmpeg(
            [
                "-i",
                str(video_path),
                "-vf",
                subtitles_vf(srt, font_path, caption_style),
                "-c:a",
                "copy",
            ]
            + FFMPEG_PRESET
            + [str(out_file)],
            timeout=50,
        )
        return Path(out_file)


def salvage_motion_clips(work_dir):
    work_dir = Path(work_dir)
    found = []
    for pattern in ("i2v_*.mp4", "kb_*.mp4", "temp_render_kb_*.mp4", "temp_render_i2v_*.mp4"):
        found.extend(sorted(work_dir.glob(pattern)))
    clips = []
    seen = set()
    for path in found:
        if "temp_render_final" in path.name:
            continue
        if not path.is_file() or path.stat().st_size < 1000:
            continue
        key = path.resolve()
        if key in seen:
            continue
        if not _is_motion_clip(path):
            continue
        seen.add(key)
        clips.append(path)
    return clips


def ffmpeg_ken_burns_sequence(src_jpg, work_dir, count=3, duration=5.0, width=None, height=None, intensity=7):
    """정지 컷을 zoompan/crop(n)으로 움직이게 만든다. crop의 잘못된 'on' 변수는 사용하지 않는다."""
    width = int(width or TARGET_W)
    height = int(height or TARGET_H)
    width -= width % 2
    height -= height % 2
    intensity = normalize_motion_intensity(intensity)
    count = max(1, int(count or 1))
    duration = max(2.0, float(duration))
    fps = STILL_FPS
    frames = max(24, int(round(duration * fps)))
    n_last = max(1, frames - 1)
    zoom = 1.12 + 0.04 * (intensity - 6)
    sw = int(round(width * zoom))
    sh = int(round(height * zoom))
    sw += sw % 2
    sh += sh % 2
    # crop 표현식은 프레임 번호 n (zoompan의 on 아님)
    crops = (
        "(iw-ow)*n/{n}:(ih-oh)*n/{n}*0.45",
        "(iw-ow)*(1-n/{n}):(ih-oh)*0.42",
        "(iw-ow)*0.5:(ih-oh)*(1-n/{n})",
    )
    zooms = (
        "min(zoom+0.0015\\,{z})",
        "min(zoom+0.0018\\,{z})",
        "min(zoom+0.0012\\,{z})",
    )
    clips = []
    src = Path(src_jpg)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        dest = work_dir / ("kb_{:02d}.mp4".format(index + 1))
        xy = crops[index % len(crops)].format(n=n_last)
        zexpr = zooms[index % len(zooms)].format(z="{:.3f}".format(zoom))
        # zoompan이 crop(n)보다 안정적이라 1순위, 실패 시 crop(n) 폴백
        vf_zoom = (
            "scale={sw}:{sh}:flags=fast_bilinear,"
            "zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={fps},"
            "setsar=1,format=yuv420p"
        ).format(sw=sw, sh=sh, z=zexpr, frames=frames, w=width, h=height, fps=fps)
        vf_crop = (
            "scale={sw}:{sh}:flags=fast_bilinear,"
            "crop={w}:{h}:{xy},"
            "setsar=1,fps={fps},format=yuv420p"
        ).format(sw=sw, sh=sh, w=width, h=height, xy=xy, fps=fps)
        ok = False
        for vf in (vf_zoom, vf_crop):
            try:
                run_ffmpeg(
                    [
                        "-loop",
                        "1",
                        "-framerate",
                        str(fps),
                        "-t",
                        "{:.3f}".format(duration),
                        "-i",
                        str(src),
                        "-an",
                        "-vf",
                        vf,
                        "-c:v",
                        "libx264",
                        "-preset",
                        "ultrafast",
                        "-crf",
                        "23",
                        "-pix_fmt",
                        "yuv420p",
                        "-movflags",
                        "+faststart",
                        str(dest),
                    ],
                    timeout=25,
                )
                if dest.is_file() and dest.stat().st_size > 1000:
                    clips.append(dest)
                    ok = True
                    break
            except Exception as exc:
                print("[안내] 켄 번스 시도 실패: {}".format(exc), flush=True)
        if not ok:
            print("[안내] 켄 번스 클립 생성 실패: {}".format(dest), flush=True)
    return clips


def pillow_ken_burns_sequence(src_jpg, work_dir, count=3, duration=5.0, width=None, height=None, intensity=7):
    return ffmpeg_ken_burns_sequence(
        src_jpg,
        work_dir,
        count=count,
        duration=duration,
        width=width,
        height=height,
        intensity=intensity,
    )


def generate_spark_cinema_clips(
    media_files,
    style_prompt,
    camera_motion,
    work_dir,
    progress_cb=None,
    lock=None,
    width=None,
    height=None,
    settings=None,
    user_action="",
    job_dir=None,
    target_duration=15,
    motion_intensity=None,
):
    width = int(width or TARGET_W)
    height = int(height or TARGET_H)
    motion_intensity = normalize_motion_intensity(motion_intensity)
    reset_fal_billing_flag()
    job_dir = Path(job_dir) if job_dir else Path(work_dir).parent
    hero = i2v_hero_source(media_files, job_dir)
    if hero is None:
        print("[안내] I2V 원본 미디어가 없습니다")
        return []
    _notify(progress_cb, 30, "9:16 네이티브 캔버스 아웃페인팅", lock)
    frame = job_dir / "i2v_source.jpg"
    try:
        prepare_i2v_still(hero, frame, width, height)
    except Exception as exc:
        print("[안내] I2V 9:16 캔버스 실패: {}".format(exc), flush=True)
        return []
    _notify(progress_cb, 33, "Vision 4레이어 프롬프트 엔진", lock)
    layers = vision_four_layer_analysis(
        settings or load_settings(),
        frame,
        user_action=user_action,
        style_prompt=style_prompt,
        camera_motion=camera_motion,
    )
    shots = build_multishot_i2v_prompts(layers, target_duration, user_action)
    for key, prompt in shots:
        print("   I2V [{}]: {}".format(key, prompt[:200]), flush=True)
    _notify(progress_cb, 36, "✨ 멀티숏 I2V 병렬 생성 ({}숏, {}초 제한)".format(len(shots), int(FAL_WAIT_TIMEOUT)), lock)

    async def _one(index, shot_key, prompt, models):
        clip = work_dir / ("i2v_{:02d}_{}.mp4".format(index + 1, shot_key))
        await fal_image_to_video_timed(
            frame,
            prompt,
            clip,
            timeout=FAL_WAIT_TIMEOUT,
            models=models,
            motion_intensity=motion_intensity,
        )
        return clip

    async def _wave(models):
        tasks = [_one(i, key, prompt, models) for i, (key, prompt) in enumerate(shots)]
        return await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=FAL_WAIT_TIMEOUT,
        )

    clips = []
    try:
        results = asyncio.run(_wave((FAL_I2V_PRIMARY, FAL_I2V_FALLBACK, FAL_I2V_FAST)))
    except Exception as exc:
        print("[안내] I2V 병렬 25초 제한 초과/실패: {}".format(exc), flush=True)
        results = []
    for item in results:
        if isinstance(item, Exception):
            print("[안내] I2V 클립 실패: {}".format(item), flush=True)
        elif item and Path(item).is_file() and Path(item).stat().st_size > 1000:
            clips.append(item)

    unique = []
    seen = set()
    for clip in clips:
        key = str(Path(clip).resolve())
        if key not in seen:
            seen.add(key)
            unique.append(clip)
    clips = unique
    if clips:
        _notify(progress_cb, 62, "✨ I2V 모션 클립 {}개 준비 완료".format(len(clips)), lock)
        return clips[:SPARK_MAX_CLIPS]

    _notify(progress_cb, 58, "3순위 Pillow 켄 번스 다이내믹 무빙", lock)
    print("[안내] I2V 25초 초과/실패 → 켄 번스 모션 블러 폴백", flush=True)
    kb = pillow_ken_burns_sequence(
        frame,
        work_dir,
        count=len(shots),
        duration=SPARK_CLIP_SEC,
        width=width,
        height=height,
        intensity=motion_intensity,
    )
    return kb


def generate_runway_clips(*args, **kwargs):
    return generate_spark_cinema_clips(*args, **kwargs)


def normalize_spark_clip(src, dest, width=None, height=None):
    width = int(width or TARGET_W)
    height = int(height or TARGET_H)
    run_ffmpeg(
        [
            "-i",
            str(src),
            "-an",
            "-vf",
            "scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps={fps},format=yuv420p".format(
                w=width, h=height, fps=FPS
            ),
        ]
        + FFMPEG_PRESET
        + [str(dest)],
        timeout=30,
    )
    return dest


def normalize_runway_clip(src, dest):
    return normalize_spark_clip(src, dest)


def concat_loop_copy(clips, dest, duration, work_dir):
    duration = max(1.0, float(duration))
    try:
        if len(clips) == 1:
            run_ffmpeg(
                [
                    "-fflags",
                    "+genpts",
                    "-stream_loop",
                    "-1",
                    "-i",
                    str(clips[0]),
                    "-t",
                    "{:.3f}".format(duration),
                    "-an",
                    "-c",
                    "copy",
                    str(dest),
                ],
                timeout=20,
            )
            return dest
        durs = [max(0.4, probe_duration(path)) for path in clips]
        list_path = work_dir / "rw_loop.txt"
        lines = ["ffconcat version 1.0"]
        elapsed = 0.0
        index = 0
        while elapsed < duration + 0.05 and index < 24:
            path = clips[index % len(clips)]
            lines.append(_concat_file_line(path))
            elapsed += durs[index % len(clips)]
            index += 1
        list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        run_ffmpeg(
            [
                "-fflags",
                "+genpts",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                "-t",
                "{:.3f}".format(duration),
                "-an",
                "-c",
                "copy",
                str(dest),
            ],
            timeout=20,
        )
        return dest
    except RuntimeError:
        args = ["-stream_loop", "-1", "-i", str(clips[0]), "-t", "{:.3f}".format(duration), "-an"]
        if len(clips) > 1:
            args = ["-f", "concat", "-safe", "0", "-i", str(work_dir / "rw_loop.txt"), "-t", "{:.3f}".format(duration), "-an"]
        run_ffmpeg(
            args
            + [
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
            ]
            + FFMPEG_LIGHT
            + [str(dest)],
            timeout=30,
        )
        return dest


def overlay_cues_on_video(video_path, cues, font_path, direction, work_dir, dest):
    return overlay_subtitles(video_path, cues, font_path, dest, work_dir)


def ffmpeg_spark_pass(
    clips,
    durations,
    cues,
    font_path,
    direction,
    voice_path,
    bgm_path,
    out_file,
    work_dir,
    audio_duration,
    width=None,
    height=None,
    caption_style="hormozi",
):
    if not clips:
        raise RuntimeError("✨ 스파크 시네마 AI 비디오 클립이 없습니다.")
    width = int(width or TARGET_W)
    height = int(height or TARGET_H)
    target = max(float(audio_duration), 1.0)
    durs = []
    for path in clips:
        try:
            durs.append(max(0.4, probe_duration(path)))
        except Exception:
            durs.append(SPARK_CLIP_SEC)
    list_path = work_dir / "spark_loop.txt"
    lines = ["ffconcat version 1.0"]
    elapsed = 0.0
    index = 0
    while elapsed < target + 0.05 and index < 24:
        path = clips[index % len(clips)]
        lines.append(_concat_file_line(path))
        elapsed += durs[index % len(clips)]
        index += 1
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    srt = write_cues_srt(cues, work_dir / "subs.srt")
    vf = (
        "scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps={fps},format=yuv420p,{subs}"
    ).format(w=width, h=height, fps=FPS, subs=subtitles_vf(srt, font_path, caption_style or "hormozi"))
    args = ["-f", "concat", "-safe", "0", "-i", str(list_path), "-i", str(voice_path)]
    maps = ["-map", "[v]", "-map", "1:a:0"]
    extra = ["-filter_complex", "[0:v]{}[v]".format(vf)]
    if bgm_path:
        args += ["-i", str(bgm_path)]
        extra = [
            "-filter_complex",
            "[0:v]{}[v];{}".format(vf, ducking_audio_filter(1.0)),
        ]
        maps = ["-map", "[v]", "-map", "[a]"]
    args += extra + maps + FFMPEG_MOTION_ENCODE + ["-t", "{:.3f}".format(float(audio_duration)), str(out_file)]
    run_ffmpeg(args, timeout=50)


def ffmpeg_runway_pass(*args, **kwargs):
    return ffmpeg_spark_pass(*args, **kwargs)


def fallback_script(style_prompt="", target_duration=15, vision_tags=""):
    style = sanitize_narration(style_prompt) or sanitize_narration(vision_tags) or "이 장면"
    tags = sanitize_narration(vision_tags) or style
    target_duration = normalize_target_duration(target_duration)
    hook = "지금 이 장면. 그냥 넘기지 마세요."
    body15 = (
        "{}이 눈에 들어와요. "
        "가까이 갈수록 디테일이 살아나고. "
        "짧은 숨이 길게 남아요. "
        "오늘은 이 순간을 기록해요. "
        "저장해 두고 다시 보세요."
    ).format(tags[:28])
    body30 = (
        "{}의 빛과 공기가 한꺼번에 마음을 붙잡아요. "
        "가까이 다가갈수록 색과 결이 또렷해져요. "
        "잠깐의 침묵이 이야기를 밀어 올립니다. "
        "시선이 머무는 자리마다 감정이 쌓여요. "
        "그 감정이 다음 장면을 자연스럽게 엽니다. "
        "우리는 이 하루를 서둘러 소비하지 않아요. "
        "한 컷 한 컷에 이름을 붙여 기억합니다. "
        "오늘은 이 순간을 기록해요. "
        "저장하고 공유해 주세요."
    ).format(tags[:28])
    body60 = (
        "{}의 빛과 공기가 한꺼번에 마음을 붙잡아요. "
        "가까이 다가갈수록 색과 결이 또렷해져요. "
        "작은 흔들림조차 이야기의 호흡이 됩니다. "
        "시선이 머무는 자리마다 감정이 쌓여요. "
        "그 감정이 다음 장면을 조용히 엽니다. "
        "서둘러 넘겨 버리기엔 너무 선명한 하루예요. "
        "한 컷 한 컷에 이름을 붙입니다. "
        "빛은 잠깐 머물고 그림자는 더 오래 남아요. "
        "멀리서 보면 풍경이고 가까이서 보면 온기입니다. "
        "그래서 이 영상은 자랑이 아니라 기록입니다. "
        "마지막 컷이 닫혀도 여운은 남아요. "
        "저장하고 친구에게 공유해 주세요."
    ).format(tags[:28])
    if target_duration >= 60:
        text = hook + " " + body60
    elif target_duration >= 30:
        text = hook + " " + body30
    else:
        text = hook + " " + body15
    script = sanitize_narration(text)
    ig = normalize_instagram_payload(
        {
            "caption": "지금 이 순간을 릴스로 담아봤어요. {}의 온기를 저장해 두고 다시 보세요.".format(style[:24]),
            "hashtags": ["#릴스", "#숏폼", "#일상", "#감성", "#인스타추천"],
        },
        style_prompt=style,
        script=script,
    )
    return script, [], ig


def ensure_voice_track(settings, script, dest, voice_key, duration=18.0):
    try:
        path = generate_voice(settings, script, output_path=dest, voice_type=voice_key)
        if Path(path).is_file() and Path(path).stat().st_size > 500:
            return Path(path)
    except Exception as exc:
        print("[안내] TTS 실패, 무음 트랙으로 폴백: {}".format(exc))
    silent = Path(dest).with_suffix(".wav")
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo",
            "-t",
            "{:.3f}".format(max(8.0, float(duration))),
            "-c:a",
            "pcm_s16le",
            str(silent),
        ],
        timeout=15,
    )
    return silent


def fast_blur_slideshow(media_files, out_file, work_dir, voice_path=None, duration=None):
    duration = float(duration or FAST_BLUR_SEC)
    work_dir = Path(work_dir)
    os.makedirs(str(work_dir), exist_ok=True)
    out_file = Path(out_file)
    os.makedirs(str(out_file.parent), exist_ok=True)
    src = Path(media_files[0]) if media_files else None
    preset = Path(out_file).parent / "frame_0.jpg"
    frame = work_dir / "fast_blur.jpg"
    try:
        if os.path.exists(str(preset)) and os.path.getsize(str(preset)) >= 32:
            shutil.copy2(str(preset), str(frame))
        elif src is not None:
            compose_blur_fill_frame(src, frame, TARGET_W, TARGET_H)
        else:
            raise RuntimeError("no media")
    except Exception as exc:
        print("[안내] fast_blur.jpg 준비 실패, 단색 폴백: {}".format(exc))
        Image.new("RGB", (TARGET_W, TARGET_H), (32, 28, 36)).save(str(frame), "JPEG", quality=80)
    ensure_jpeg_on_disk(frame, (TARGET_W, TARGET_H))
    require_image_for_ffmpeg(frame)
    args = [
        "-loop",
        "1",
        "-framerate",
        str(STILL_FPS),
        "-t",
        "{:.3f}".format(duration),
        "-i",
        str(frame),
    ]
    if voice_path and Path(voice_path).is_file():
        args += ["-i", str(voice_path)]
    else:
        args += [
            "-f",
            "lavfi",
            "-t",
            "{:.3f}".format(duration),
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=44100",
        ]
    args += [
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-t",
        "{:.3f}".format(duration),
        "-r",
        str(STILL_FPS),
        "-vf",
        "fps={},format=yuv420p".format(STILL_FPS),
    ] + FFMPEG_ENCODE + [str(out_file)]
    run_ffmpeg(args, timeout=max(180, min(int(duration * 12 + 60), 300)))
    gc.collect()
    return out_file


def blur_fallback_render(media_files, script, voice_path, bgm_path, out_file, work_dir, font_path, speed, width, height):
    try:
        return fast_blur_slideshow(media_files, out_file, work_dir, voice_path=voice_path, duration=FAST_BLUR_SEC)
    except Exception as exc:
        print("[안내] 초고속 블러 엔진 1차 실패, 단일 패스 재시도: {}".format(exc))
    pieces = split_script_pieces(script) or [script]
    frames = prepare_captioned_frames(
        media_files, pieces, [], font_path, work_dir, width=width, height=height
    )
    try:
        audio_duration = probe_duration(voice_path)
    except Exception:
        audio_duration = FAST_BLUR_SEC
    cues = split_script_cues(script, min(float(audio_duration), FAST_BLUR_SEC))
    durations = [max(0.2, float(end) - float(start)) for _text, start, end in cues]
    if len(durations) != len(frames):
        n = min(len(durations), len(frames)) or 1
        if not frames:
            raise RuntimeError("폴백 프레임이 없습니다.")
        frames = (frames * n)[:n]
        durations = (durations or [FAST_BLUR_SEC])[:n]
    ffmpeg_single_pass(
        frames,
        durations,
        voice_path,
        bgm_path,
        out_file,
        speed=speed,
        work_dir=work_dir,
        xfade_sec=0.12,
        cues=cues,
        font_path=font_path,
    )
    return out_file


def run_pipeline(
    media_files,
    style_prompt="",
    progress_cb=None,
    output_path=None,
    check_license=True,
    voice_type=DEFAULT_VOICE_TYPE,
    speed_multiplier=1.0,
    bgm_mood=DEFAULT_BGM_MOOD,
    is_runway_mode=False,
    is_spark_cinema=None,
    camera_motion="zoom_in",
    output_height=720,
    fast_mode=True,
    deadline_ts=None,
    target_duration=15,
    caption_style="hormozi",
    visual_fx="ken_burns",
    aspect_ratio="9:16",
    audio_ducking=True,
    is_vip_mode=False,
    action_motion_enabled=False,
    action_style="",
    action_preset="",
    motion_intensity=None,
    before_after_hook=False,
    ai_lipsync=False,
    parallax_3d=False,
):
    if check_license:
        ok, message = verify_saved_license()
        if not ok:
            raise RuntimeError("라이선스 인증이 필요합니다. " + message)
    if not media_files:
        raise RuntimeError("사진 또는 동영상을 한 개 이상 넣어 주세요.")

    settings = load_settings()
    font_path = find_korean_font()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = Path(output_path) if output_path else FINAL_PATH
    out_file.parent.mkdir(parents=True, exist_ok=True)
    speed = normalize_speed(speed_multiplier)
    mood = normalize_bgm_mood(bgm_mood)
    vip = bool(is_vip_mode)
    spark = bool(is_spark_cinema) if is_spark_cinema is not None else bool(is_runway_mode)
    before_after_hook = bool(before_after_hook)
    ai_lipsync = bool(ai_lipsync)
    parallax_3d = bool(parallax_3d)
    viral_on = before_after_hook or ai_lipsync or parallax_3d
    spark = spark or vip or viral_on
    motion = normalize_camera_motion(camera_motion)
    target_duration = normalize_target_duration(target_duration)
    caption_style = normalize_caption_style(caption_style)
    visual_fx = normalize_visual_fx(visual_fx or motion)
    if parallax_3d:
        visual_fx = "cinematic"
        motion = "push_in" if motion in ("zoom_in", "push_in") else motion
    aspect_ratio = normalize_aspect_ratio(aspect_ratio)
    width, height = canvas_size(aspect_ratio, output_height)
    motion_intensity = normalize_motion_intensity(motion_intensity)
    user_motion = (action_style or "").strip()
    sfx_on = bool(action_motion_enabled)
    action_enabled = sfx_on or bool((user_motion or action_preset or "").strip())
    if vip and action_enabled:
        action_style = resolve_action_style(action_preset, user_motion)
    else:
        action_style = user_motion
        if not action_style and normalize_action_preset(action_preset):
            action_style = ACTION_PRESETS.get(normalize_action_preset(action_preset), "")
    if vip and action_enabled:
        visual_fx = "zoom_punch"
    if spark and mood == "none":
        mood = "lofi"
    voice_key, _voice_id, _preset = resolve_voice(voice_type)
    progress_lock = threading.Lock()
    used_fallback = False
    if deadline_ts is None:
        deadline_ts = time.time() + pipeline_time_budget(target_duration)

    def _left():
        return float(deadline_ts) - time.time()

    work_dir = out_file.parent / ("_ffwork_{}".format(uuid.uuid4().hex[:8]))
    work_dir.mkdir(parents=True, exist_ok=True)
    voice_file = out_file.parent / "voice.mp3"

    script = ""
    photo_order = []
    instagram = normalize_instagram_payload({}, style_prompt=style_prompt)
    voice_path = None
    bgm_path = None
    direction = default_style_direction(style_prompt)

    try:
        _notify(
            progress_cb,
            4,
            "미디어 {}개 준비: {}".format(len(media_files), ", ".join(p.name for p in media_files)),
            progress_lock,
        )
        _notify(progress_cb, 8, "9:16 블러 배경 프레임 사전 합성", progress_lock)
        slim = []
        for path in media_files:
            prepared = smart_prepare_media(path, work_dir)
            if Path(prepared).suffix.lower() in IMAGE_EXTS:
                slim.append(diet_image_file(prepared))
            else:
                slim.append(prepared)
        media_files = slim
        job_dir = out_file.parent
        try:
            prepare_job_frames(media_files, job_dir, width, height)
        except Exception as exc:
            print("[안내] 사전 프레임 합성 실패: {}".format(exc), flush=True)
        gc.collect()

        if spark or not fast_mode:
            _notify(progress_cb, 12, "스타일 연출 해석 중", progress_lock)
            try:
                direction = interpret_style_direction(settings, style_prompt)
            except Exception as exc:
                print("[안내] 스타일 해석 실패, 기본 연출 사용: {}".format(exc))
                direction = default_style_direction(style_prompt)
        else:
            direction = default_style_direction(style_prompt)
            _notify(progress_cb, 12, "⚡ 10초 쾌속 모드 · 기본 연출 적용", progress_lock)

        _notify(progress_cb, 16, "대본 작성 중", progress_lock)
        try:
            if _left() < 14:
                raise RuntimeError("잔여시간 부족")
            script, photo_order, instagram = generate_script(
                settings,
                media_files,
                style_prompt=style_prompt,
                direction=direction,
                target_duration=target_duration,
            )
        except Exception as exc:
            print("[안내] 대본 API 실패, 로컬 스토리로 폴백: {}".format(exc))
            script, photo_order, instagram = fallback_script(style_prompt, target_duration=target_duration)
            used_fallback = True
        script = sanitize_narration(script)
        instagram = normalize_instagram_payload(instagram, style_prompt=style_prompt, script=script)
        pieces = split_script_pieces(script)
        _notify(progress_cb, 24, "대본 완료 · 음성 합성과 사진 보정을 동시에 시작", progress_lock)

        def _hold_frames():
            ordered = list(media_files)
            if photo_order:
                cycle = [media_files[i] for i in photo_order if 0 <= i < len(media_files)]
                if cycle:
                    ordered = cycle
            return prepare_job_frames(ordered, out_file.parent, width, height)

        def _voice_job():
            _notify(progress_cb, 28, "ElevenLabs 음성 합성 중", progress_lock)
            path = ensure_voice_track(settings, script, voice_file, voice_key, duration=float(target_duration))
            _notify(progress_cb, 56, "음성 생성 완료", progress_lock)
            return path

        def _frame_job(voice_for_lipsync=None):
            if viral_on and _left() > 12:
                try:
                    clips = generate_viral_motion_clips(
                        media_files,
                        style_prompt,
                        motion,
                        work_dir,
                        progress_cb=progress_cb,
                        lock=progress_lock,
                        width=width,
                        height=height,
                        settings=settings,
                        user_action=action_style,
                        job_dir=out_file.parent,
                        target_duration=target_duration,
                        motion_intensity=motion_intensity,
                        voice_path=voice_for_lipsync,
                        before_after=before_after_hook,
                        ai_lipsync=ai_lipsync,
                        parallax_3d=parallax_3d,
                    )
                    if clips:
                        return clips
                    print("[안내] 바이럴 연출 빈 결과 → 스파크/홀드 폴백")
                except Exception as exc:
                    print("[안내] 바이럴 연출 실패: {}".format(exc))
            if spark and _left() > 12:
                try:
                    clips = generate_spark_cinema_clips(
                        media_files,
                        style_prompt,
                        motion,
                        work_dir,
                        progress_cb=progress_cb,
                        lock=progress_lock,
                        width=width,
                        height=height,
                        settings=settings,
                        user_action=action_style,
                        job_dir=out_file.parent,
                        target_duration=target_duration,
                        motion_intensity=motion_intensity,
                    )
                    if clips:
                        if before_after_hook:
                            src = out_file.parent / "i2v_source.jpg"
                            if src.is_file() and clips:
                                try:
                                    hooked = work_dir / "before_after_spark.mp4"
                                    apply_before_after_hook(
                                        src, clips[0], hooked, width=width, height=height, work_dir=work_dir
                                    )
                                    clips = [hooked] + list(clips[1:])
                                except Exception as exc:
                                    print("[안내] 스파크 비포/애프터 훅 실패: {}".format(exc))
                        return clips
                    print("[안내] 스파크 I2V/켄번스 빈 결과 → 홀드 렌더")
                except Exception as exc:
                    print("[안내] 스파크 I2V 실패: {}".format(exc))
            _notify(progress_cb, 30, "사진 노출 균등 배분 및 리사이즈 중", progress_lock)
            frames = _hold_frames()
            _notify(progress_cb, 52, "장면 프레임 준비 완료", progress_lock)
            return frames

        if ai_lipsync:
            # 립싱크는 음성 파형이 필요하므로 음성 먼저 생성
            try:
                voice_path = _voice_job()
            except Exception as exc:
                print("[안내] 립싱크용 음성 생성 실패: {}".format(exc))
                voice_path = ensure_voice_track(
                    settings, script, voice_file, voice_key, duration=float(target_duration)
                )
            try:
                generated = _frame_job(voice_for_lipsync=voice_path)
            except Exception as exc:
                print("[안내] 립싱크 프레임 실패: {}".format(exc), flush=True)
                generated = salvage_motion_clips(work_dir)
        else:
            with ThreadPoolExecutor(max_workers=2) as pool:
                voice_fut = pool.submit(_voice_job)
                frame_fut = pool.submit(_frame_job)
                try:
                    voice_path = voice_fut.result(timeout=max(8.0, min(40.0, _left() - 8)))
                except Exception as exc:
                    print("[안내] 음성 대기 중단: {}".format(exc))
                    voice_path = ensure_voice_track(
                        settings, script, voice_file, voice_key, duration=float(target_duration)
                    )
                try:
                    i2v_wait = 14.0 if (vip or spark or viral_on) else 10.0
                    generated = frame_fut.result(timeout=max(8.0, min(i2v_wait, max(8.0, _left() - 3))))
                except Exception as exc:
                    print("[안내] 영상 생성 대기 중단: {}".format(exc), flush=True)
                    generated = salvage_motion_clips(work_dir)

        if spark:
            motion_ready = []
            if generated and _is_motion_clip(generated[0]):
                motion_ready = [Path(p) for p in generated if _is_motion_clip(p)]
            if not motion_ready:
                motion_ready = salvage_motion_clips(work_dir)
            if not motion_ready:
                src = out_file.parent / "i2v_source.jpg"
                if not src.is_file():
                    frames = _hold_frames()
                    src = frames[0] if frames else None
                if src is not None:
                    print("[안내] 스파크 모션 클립 복구 · FFmpeg 켄 번스", flush=True)
                    motion_ready = ffmpeg_ken_burns_sequence(
                        src,
                        work_dir,
                        count=3 if int(target_duration) >= 15 else 1,
                        duration=SPARK_CLIP_SEC,
                        width=width,
                        height=height,
                        intensity=motion_intensity,
                    )
            generated = motion_ready or generated
        if not generated:
            generated = _hold_frames()
        voice_fit = work_dir / "voice_target.m4a"
        try:
            voice_path = conform_audio_duration(voice_path or voice_file, voice_fit, target_duration)
        except Exception as exc:
            print("[안내] 음성 길이 보정 실패: {}".format(exc))
        audio_duration = float(target_duration)
        cues = split_script_cues(script, audio_duration)
        spark_clips = None
        if generated and _is_motion_clip(generated[0]):
            spark_clips = generated
            hold_frames = generated
            durations = even_scene_durations(len(generated), audio_duration)
        else:
            hold_frames = _hold_frames()
            durations = even_scene_durations(len(hold_frames), audio_duration)
        _notify(progress_cb, 68, "BGM 준비 중", progress_lock)
        try:
            bgm_path = resolve_bgm(mood, audio_duration, work_dir / "bgm.wav")
        except Exception:
            bgm_path = None

        spark_clips = spark_clips if spark else None
        try:
            if spark_clips:
                if mood == "none":
                    bgm_path = resolve_bgm("lofi", audio_duration, work_dir / "bgm.wav")
                _notify(progress_cb, 78, "✨ 스파크 시네마 · 음성·BGM 단일 패스 합성", progress_lock)
                ffmpeg_spark_pass(
                    spark_clips,
                    durations,
                    cues,
                    font_path,
                    direction,
                    voice_path,
                    bgm_path,
                    out_file,
                    work_dir,
                    audio_duration,
                    width=width,
                    height=height,
                    caption_style=caption_style,
                )
            else:
                print("   목표 길이: {:.2f}초 / 사진 {}장 균등 배분 / 배속 {}x / BGM {}".format(
                    audio_duration, len(hold_frames), speed, mood
                ))
                _notify(progress_cb, 76, "{}초 전문 편집 렌더링 중".format(int(target_duration)), progress_lock)
                ffmpeg_single_pass(
                    hold_frames,
                    durations,
                    voice_path,
                    bgm_path,
                    out_file,
                    speed=speed,
                    work_dir=work_dir,
                    xfade_sec=0.12 if fast_mode and not spark else direction.xfade,
                    cues=cues,
                    font_path=font_path,
                    width=width,
                    height=height,
                    visual_fx=visual_fx,
                    caption_style=caption_style,
                    audio_ducking=bool(audio_ducking),
                    target_duration=audio_duration,
                )
        except Exception as exc:
            print("[안내] 렌더 실패, 목표 길이 슬라이드 폴백: {}".format(exc))
            used_fallback = True
            _notify(progress_cb, 80, "{}초 안전 슬라이드쇼로 전환".format(int(target_duration)), progress_lock)
            fast_blur_slideshow(
                media_files, out_file, work_dir, voice_path=voice_path, duration=float(target_duration)
            )

        if not Path(out_file).is_file():
            raise RuntimeError("완성된 영상 파일을 찾지 못했습니다.")
        if vip and sfx_on and spark_clips and Path(out_file).is_file():
            try:
                _notify(progress_cb, 88, "스튜디오 오디오 마스터링", progress_lock)
                mixed = work_dir / "vip_master.mp4"
                scene_starts = []
                acc = 0.0
                for dur in durations:
                    scene_starts.append(acc)
                    acc += float(dur)
                mix_vip_sfx(
                    out_file,
                    mixed,
                    audio_duration,
                    action_style,
                    [start for _t, start, _e in cues],
                    scene_starts,
                    work_dir,
                )
                if mp4_file_ready(mixed):
                    os.replace(str(mixed), str(out_file))
            except Exception as exc:
                print("[안내] VIP 마스터링 생략: {}".format(exc))
        _notify(
            progress_cb,
            96,
            "출력 정리 및 메모리 회수 중" + (" (안전장치 적용)" if used_fallback else ""),
            progress_lock,
        )
    except Exception as exc:
        print("[안내] 파이프라인 예외, 최종 블러 폴백(프로세스 유지): {}".format(exc))
        used_fallback = True
        if not script:
            script, _order, instagram = fallback_script(style_prompt, target_duration=target_duration)
            script = sanitize_narration(script)
            instagram = normalize_instagram_payload(instagram, style_prompt=style_prompt, script=script)
        try:
            fast_blur_slideshow(media_files, out_file, work_dir, voice_path=voice_path, duration=float(target_duration))
        except Exception as inner:
            print("[안내] 최종 폴백 실패: {}".format(inner))
            raise
        if not Path(out_file).is_file():
            raise RuntimeError("완성된 영상 파일을 찾지 못했습니다.")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        cleanup_temp_files()
        gc.collect()

    _notify(progress_cb, 100, "완료: {}".format(out_file), progress_lock)
    gc.collect()
    return out_file, script, normalize_instagram_payload(instagram, style_prompt=style_prompt, script=script)


def main():
    try:
        require_license(interactive=True)
        media_files = collect_media()
        run_pipeline(media_files)
    except RuntimeError as exc:
        raise SystemExit(str(exc))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
