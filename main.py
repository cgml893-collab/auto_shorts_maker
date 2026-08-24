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
XFADE_SEC = 0.4
BLUR_RADIUS = 26
BLUR_DIM = 0.38
FAL_I2V_PRIMARY = os.getenv("FAL_I2V_MODEL", "fal-ai/minimax/video-01/image-to-video")
FAL_I2V_FALLBACK = "fal-ai/kling-video/v1/standard/image-to-video"
FAL_WAIT_TIMEOUT = 25.0
SPARK_MAX_CLIPS = 3
SPARK_CLIP_SEC = 5.0
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
    "zoom_in": "[Zoom in] Cinematic slow push-in, natural subtle motion, keep the subject centered, photorealistic.",
    "drone": "[Pedestal up, Tracking shot] Smooth drone-style rise and gentle forward glide over the scene.",
    "pan": "[Pan left] Smooth cinematic pan across the scene with natural parallax.",
}
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
                run_ffmpeg(
                    ["-ss", "0.3", "-i", str(path), "-frames:v", "1", "-q:v", "6", str(preview)],
                    timeout=20,
                )
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
    key = (value or "zoom_in").strip().lower()
    aliases = {
        "zoom_in": "zoom_in",
        "zoom": "zoom_in",
        "줌인": "zoom_in",
        "drone": "drone",
        "drone_shot": "drone",
        "드론": "drone",
        "pan": "pan",
        "panning": "pan",
        "패닝": "pan",
    }
    return aliases.get(key, "zoom_in")


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
    return {15: 90.0, 30: 140.0, 60: 200.0}.get(int(duration), 90.0)


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
    style = normalize_visual_fx(style)
    if style == "zoom_punch":
        zoom = "1.07+0.11*abs(sin(2*PI*on/9))"
        scale_w, scale_h = w * 2, h * 2
    elif style == "cinematic":
        zoom = "min(1.04+on*0.00042,1.13)"
        scale_w, scale_h = int(w * 1.28), int(h * 1.28)
    else:
        zoom = "min(zoom+0.00115,1.16)"
        scale_w, scale_h = int(w * 1.24), int(h * 1.24)
    pan_x = "iw/2-(iw/zoom/2)+on*0.16" if style == "cinematic" else "iw/2-(iw/zoom/2)"
    grade = ",eq=contrast=1.05:saturation=0.93:gamma=0.97" if style == "cinematic" else ""
    return (
        "scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},"
        "zoompan=z='{z}':x='{x}':y='ih/2-(ih/zoom/2)':d=1:s={w}x{h}:fps={f}"
        "{grade},setsar=1,format=yuv420p"
    ).format(sw=scale_w, sh=scale_h, z=zoom, x=pan_x, w=w, h=h, f=f, grade=grade)


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
            "FontName={},FontSize=28,Bold=1,PrimaryColour=&H0000EAFF,"
            "OutlineColour=&H00000000,BackColour=&H00000000,BorderStyle=1,"
            "Outline=4,Shadow=0,Alignment=2,MarginV=54"
        ).format(name)
    return raw.replace(",", "\\,")


def ducking_audio_filter(speed=1.0):
    tempo = ""
    if abs(float(speed) - 1.0) > 0.001:
        tempo = "," + atempo_chain(speed)
    return (
        "[1:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo{tempo},asplit=2[voice][sc];"
        "[2:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo{tempo},volume=0.55[bgm];"
        "[bgm][sc]sidechaincompress=threshold=0.045:ratio=12:attack=20:release=260:makeup=1:knee=8[dk];"
        "[voice]volume=1.08[va];"
        "[va][dk]amix=inputs=2:duration=first:dropout_transition=0[a]"
    ).format(tempo=tempo)


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


def generate_script(settings, media_files, style_prompt="", direction=None, target_duration=15):
    # type: (Settings, List[Path], str) -> Tuple[str, List[int]]
    print("1) OpenAI(gpt-4o-mini)로 숏폼 나레이션 대본 작성 중...")
    style = (style_prompt or "").strip() or "시선을 사로잡는 빠른 템포의 숏폼"
    target_duration = normalize_target_duration(target_duration)
    spec = DURATION_TARGETS[target_duration]
    min_chars, max_chars = spec["min_chars"], spec["max_chars"]
    guide = ""
    if direction is not None:
        guide = "\n연출 지시: {} / {}".format(direction.tone, direction.script_guide)
    numbered = ", ".join(
        "{}번 {}".format(i + 1, path.name) for i, path in enumerate(media_files)
    )
    prompt = (
        "첨부된 사진/영상 프레임을 보고, 유튜브 쇼츠/인스타 릴스용 "
        "한국어 나레이션 대본만 작성하세요.\n"
        "영상 스타일/분위기: {}{}\n"
        "이미지 번호: {}\n"
        "목표 길이: {}초 ({})\n"
        "사진이 1~2장뿐이어도 선택한 길이에 맞는 완성형 3단 스토리텔링으로 작성하세요.\n"
        "규칙:\n"
        "- 말할 때 약 {}초 (공백 제외 {}~{}자. 짧으면 실패)\n"
        "- 구성: (1) 첫 3초를 잡는 훅 (2) 장면·감정·디테일을 펼치는 본문 (3) 여운 있는 마무리\n"
        "- 지정한 스타일에 맞게 톤과 템포를 맞출 것\n"
        "- 구어체, 짧은 문장을 이어 붙여 호흡 있게\n"
        "- 장면 지시, 이모지, 해시태그, #기호, 영어 태그, 따옴표, 제목 금지\n"
        "- 화면에 보이는 소재를 구체적으로 언급\n"
        "- 대본 본문만 먼저 쓰고, 마지막 줄에 사진 배치를 이렇게 적으세요:\n"
        "PHOTO_ORDER: 1,3,2\n"
        "- PHOTO_ORDER는 대본 흐름에 맞게 이미지 번호(1부터)를 의미 있는 순서로 나열. 반복 가능"
    ).format(style, guide, numbered, target_duration, spec["label"], target_duration, min_chars, max_chars)
    content = [{"type": "text", "text": prompt}]

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

    client = OpenAI(api_key=settings.openai_api_key)

    def _ask(extra=""):
        body = list(content)
        if extra:
            body = [dict(body[0])] + body[1:]
            body[0]["text"] = content[0]["text"] + extra
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": body}],
            temperature=0.85,
            max_tokens=900 if target_duration <= 15 else (1400 if target_duration <= 30 else 1800),
        )
        raw_text = (response.choices[0].message.content or "").strip()
        raw_text = re.sub(r"^대본\s*[:：]\s*", "", raw_text)
        raw_text, order_ids = parse_photo_order(raw_text, len(media_files))
        return sanitize_narration(raw_text), order_ids

    script, order = _ask()
    compact = re.sub(r"\s+", "", script)
    if len(compact) < min_chars:
        script, order = _ask(
            "\n이전 대본이 너무 짧습니다. 공백 제외 {}~{}자로 훅-본문-마무리를 다시 쓰세요.".format(
                min_chars, max_chars
            )
        )
        compact = re.sub(r"\s+", "", script)
    if len(compact) < min_chars:
        script, order = _ask(
            "\n{}자 미만입니다. 3단 스토리로 공백 제외 {}자 전후의 완성형 대본을 다시 쓰세요.".format(
                min_chars, (min_chars + max_chars) // 2
            )
        )
        compact = re.sub(r"\s+", "", script)
    if not script or len(compact) < 80:
        raise RuntimeError("대본 생성에 실패했습니다. OpenAI 응답이 비어 있거나 너무 짧습니다.")
    print("   대본 ({}자):\n   {}\n".format(len(compact), script))
    if order:
        print("   사진 배치: {}".format([i + 1 for i in order]))
    return script, order


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


FFMPEG_TIMEOUT = int(os.getenv("FFMPEG_TIMEOUT", "60"))
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
    "2",
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
    "2",
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


def wrap_caption_lines(text, width=14):
    text = sanitize_narration(text)
    lines = []
    current = ""
    for ch in text:
        current += ch
        if len(current) >= width:
            lines.append(current.strip())
            current = ""
            if len(lines) >= 4:
                break
    if current.strip() and len(lines) < 4:
        lines.append(current.strip())
    return "\n".join(lines) if lines else text


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


def fit_contain_on_blur(im, width, height):
    try:
        fixed = ImageOps.exif_transpose(im)
        if fixed is not None:
            im = fixed
    except Exception:
        pass
    src = im.convert("RGB")
    resampling = getattr(Image, "Resampling", Image)
    bg = fit_cover_rgb(src, width, height, fast=True)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=BLUR_RADIUS))
    bg = Image.blend(bg, Image.new("RGB", (width, height), (0, 0, 0)), BLUR_DIM)
    scale = min(width / float(src.width), height / float(src.height))
    nw = max(2, int(round(src.width * scale)))
    nh = max(2, int(round(src.height * scale)))
    nw -= nw % 2
    nh -= nh % 2
    fg = src.resize((nw, nh), resampling.BILINEAR)
    canvas = bg.copy()
    canvas.paste(fg, ((width - nw) // 2, (height - nh) // 2))
    return canvas


def still_from_media(path, dest_jpg, work_dir, width=None, height=None):
    width = int(width or TARGET_W)
    height = int(height or TARGET_H)
    dest_jpg = Path(dest_jpg)
    work_dir = Path(work_dir)
    os.makedirs(str(work_dir), exist_ok=True)
    os.makedirs(str(dest_jpg.parent), exist_ok=True)
    suffix = Path(path).suffix.lower()
    try:
        if suffix in IMAGE_EXTS:
            with open_image_upright(path) as im:
                save_image_verified(fit_contain_on_blur(im, width, height), dest_jpg, quality=85)
            diet_image_file(dest_jpg, dest=dest_jpg)
        else:
            tmp = work_dir / (dest_jpg.stem + "_grab.jpg")
            run_ffmpeg(
                ["-ss", "0.15", "-i", str(path), "-frames:v", "1", "-q:v", "4", str(tmp)],
                timeout=20,
            )
            with open_image_upright(tmp) as im:
                save_image_verified(fit_contain_on_blur(im, width, height), dest_jpg, quality=85)
    except Exception as exc:
        print("[안내] 스틸 추출 실패, 단색 폴백: {}".format(exc))
    return ensure_jpeg_on_disk(dest_jpg, (width, height))


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
    speed = normalize_speed(speed)
    if not frames:
        raise RuntimeError("렌더할 프레임이 없습니다.")
    work_dir = Path(work_dir or Path(frames[0]).parent)
    width = int(width or TARGET_W)
    height = int(height or TARGET_H)
    frames = [ensure_jpeg_on_disk(path, (width, height)) for path in frames]
    entries = build_slideshow_entries(
        frames, durations, work_dir, speed=speed, xfade_sec=xfade_sec
    )
    concat_path = work_dir / "slides.txt"
    write_concat_list(entries, concat_path)
    total = sum(dur for _png, dur in entries)
    hold = max(float(target_duration or total), float(total), 1.0)

    voice_mp3 = work_dir / "voice.mp3"
    if Path(voice_path).resolve() != voice_mp3.resolve():
        shutil.copy2(str(voice_path), str(voice_mp3))
    else:
        voice_mp3 = Path(voice_path)
    voice_fit = work_dir / "voice_fit.m4a"
    conform_audio_duration(voice_mp3, voice_fit, hold)

    bgm_mp3 = None
    if bgm_path:
        bgm_mp3 = work_dir / "bgm.mp3"
        src = Path(bgm_path)
        if src.suffix.lower() == ".mp3" and src.resolve() != bgm_mp3.resolve():
            shutil.copy2(str(src), str(bgm_mp3))
        elif src.suffix.lower() == ".mp3":
            bgm_mp3 = src
        else:
            bgm_mp3 = src

    if len(entries) == 1:
        args = [
            "-loop",
            "1",
            "-framerate",
            str(FPS),
            "-t",
            "{:.3f}".format(hold),
            "-i",
            str(entries[0][0]),
            "-i",
            str(voice_fit),
        ]
    else:
        args = ["-f", "concat", "-safe", "0", "-i", str(concat_path), "-i", str(voice_fit)]

    vf = visual_fx_filter(visual_fx, width, height, FPS)
    if cues and font_path:
        srt = write_cues_srt(cues, work_dir / "subs.srt")
        vf = "{},{}".format(vf, subtitles_vf(srt, font_path, caption_style))

    audio_map = ["-map", "0:v:0", "-map", "1:a:0"]
    extra = ["-filter_complex", "[0:v]{}[v]".format(vf)]
    audio_map = ["-map", "[v]", "-map", "1:a:0"]
    if bgm_mp3 is not None:
        args += ["-i", str(bgm_mp3)]
        if audio_ducking:
            af = ducking_audio_filter(speed)
        else:
            af = "[1:a]volume=1.05[va];[2:a]volume=0.16[ba];[va][ba]amix=inputs=2:duration=first:dropout_transition=0[a]"
            if abs(speed - 1.0) > 0.001:
                af = (
                    "[1:a]volume=1.05,{tempo}[va];[2:a]volume=0.16,{tempo}[ba];"
                    "[va][ba]amix=inputs=2:duration=first:dropout_transition=0[a]"
                ).format(tempo=atempo_chain(speed))
        extra = ["-filter_complex", "[0:v]{}[v];{}".format(vf, af)]
        audio_map = ["-map", "[v]", "-map", "[a]"]
    elif abs(speed - 1.0) > 0.001:
        extra = ["-filter_complex", "[0:v]{}[v];[1:a]{}[a]".format(vf, atempo_chain(speed))]
        audio_map = ["-map", "[v]", "-map", "[a]"]

    args += extra
    args += audio_map
    args += ["-t", "{:.3f}".format(hold), "-r", str(FPS)] + FFMPEG_ENCODE + [str(out_file)]
    run_ffmpeg(args, timeout=max(90, min(int(hold * 5 + 40), 240)))


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


def fal_image_to_video(image_path, prompt, dest_mp4):
    try:
        try:
            import fal_client
        except ImportError:
            raise RuntimeError("fal-client가 없습니다. pip install fal-client 후 다시 시도해 주세요.")
        try:
            slim = diet_image_file(image_path, dest=Path(image_path).with_name(Path(image_path).stem + "_fal.jpg"))
        except Exception:
            slim = image_path
        image_url = fal_client.upload_file(str(slim))
        arguments = {"prompt": prompt, "image_url": image_url, "prompt_optimizer": True}
        last_error = None
        for model in (FAL_I2V_PRIMARY, FAL_I2V_FALLBACK):
            try:
                print("   fal I2V: {} ← {}".format(model, Path(slim).name))
                payload = dict(arguments)
                if "kling" in model:
                    payload["duration"] = "5"
                result = fal_client.subscribe(model, arguments=payload, with_logs=False)
                url = _fal_video_url(result)
                if not url:
                    raise RuntimeError("fal 응답에 video url이 없습니다: {}".format(str(result)[:400]))
                download_http_file(url, dest_mp4, timeout=20)
                if Path(dest_mp4).is_file() and Path(dest_mp4).stat().st_size > 1000:
                    return Path(dest_mp4)
            except Exception as exc:
                last_error = exc
                print("[안내] {} 실패: {}".format(model, exc))
        raise RuntimeError("Image-to-Video 생성 실패: {}".format(last_error))
    except Exception as exc:
        print("[안내] fal.ai 전체 실패(프로세스 유지): {}".format(exc))
        raise RuntimeError("✨ 스파크 시네마 AI 호출 실패: {}".format(exc))


async def fal_image_to_video_timed(image_path, prompt, dest_mp4, timeout=FAL_WAIT_TIMEOUT):
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(fal_image_to_video, image_path, prompt, dest_mp4),
            timeout=float(timeout),
        )
    except asyncio.TimeoutError:
        print("[안내] fal.ai {}초 타임아웃 → 하위 엔진 폴백".format(timeout))
        raise RuntimeError("fal.ai 대기열 타임아웃 ({}s)".format(timeout))


VIP_I2V_TIMEOUT = 32.0
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
        "Photorealistic image-to-video. Keep the exact person, face, clothing, and body proportions. "
        "{angle}. Action: {action}. Physically plausible motion, cinematic camera move, 24fps, "
        "high detail, no morphing, no extra limbs. Style: {style}."
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
            timeout=VIP_I2V_TIMEOUT + 4,
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


def generate_spark_cinema_clips(
    media_files,
    style_prompt,
    camera_motion,
    work_dir,
    progress_cb=None,
    lock=None,
    width=None,
    height=None,
):
    motion = normalize_camera_motion(camera_motion)
    motion_prompt = CAMERA_MOTIONS[motion]
    prompt = "{} {}".format((style_prompt or "cinematic vertical short").strip(), motion_prompt)
    sources = list(media_files)[:SPARK_MAX_CLIPS]
    width = int(width or TARGET_W)
    height = int(height or TARGET_H)
    total = max(1, len(sources))
    _notify(progress_cb, 34, "✨ 스파크 시네마 AI 병렬 생성 중 ({}장)".format(total), lock)

    async def _one(index, src):
        try:
            frame = work_dir / ("i2v_src_{:02d}.jpg".format(index + 1))
            await asyncio.to_thread(still_from_media, src, frame, work_dir, width, height)
            await asyncio.to_thread(diet_image_file, frame, frame)
            clip = work_dir / ("i2v_{:02d}.mp4".format(index + 1))
            await fal_image_to_video_timed(frame, prompt, clip, timeout=FAL_WAIT_TIMEOUT)
            return clip
        except Exception as exc:
            print("[안내] 스파크 클립 {} 실패/타임아웃: {}".format(index + 1, exc))
            return exc

    async def _gather():
        tasks = [_one(index, src) for index, src in enumerate(sources)]
        return await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=FAL_WAIT_TIMEOUT,
        )

    try:
        results = asyncio.run(_gather())
    except asyncio.TimeoutError:
        print("[안내] 스파크 시네마 25초 대기열 초과 → 초고속 블러 전환")
        return []
    except Exception as exc:
        print("[안내] 스파크 시네마 병렬 호출 실패: {}".format(exc))
        return []
    clips = []
    errors = []
    for item in results:
        if isinstance(item, Exception):
            errors.append(item)
            print("[안내] 스파크 시네마 클립 실패: {}".format(item))
        elif item:
            clips.append(item)
    if not clips:
        print("[안내] 스파크 시네마 클립 없음 → 쾌속 블러 폴백")
        return []
    _notify(progress_cb, 62, "✨ 스파크 시네마 AI 클립 {}개 준비 완료".format(len(clips)), lock)
    return clips


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
    ).format(w=width, h=height, fps=FPS, subs=subtitles_vf(srt, font_path, "hormozi"))
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
    args += extra + maps + FFMPEG_ENCODE + ["-t", "{:.3f}".format(float(audio_duration)), str(out_file)]
    run_ffmpeg(args, timeout=50)


def ffmpeg_runway_pass(*args, **kwargs):
    return ffmpeg_spark_pass(*args, **kwargs)


def fallback_script(style_prompt="", target_duration=15):
    style = sanitize_narration(style_prompt) or "이 장면"
    target_duration = normalize_target_duration(target_duration)
    hook = "지금 이 장면, 그냥 스치듯 넘기지 마세요. {}의 빛과 공기가 한꺼번에 마음을 붙잡습니다.".format(style[:20])
    body15 = (
        "가까이 다가갈수록 디테일이 살아나고 짧은 숨이 길게 남아요. "
        "오늘은 이 순간을 기록하고 내일의 나에게 따뜻한 여운으로 건넵니다."
    )
    body30 = (
        "가까이 다가갈수록 색과 결이 또렷해지고, 잠깐의 침묵이 이야기를 밀어 올립니다. "
        "시선이 머무는 자리마다 작은 감정이 쌓이고, 그 감정이 다음 장면을 자연스럽게 엽니다. "
        "우리는 이 하루를 서둘러 소비하지 않고, 한 컷 한 컷에 이름을 붙여 기억합니다. "
        "오늘은 이 순간을 기록하고, 내일의 나에게 따뜻한 여운과 선명한 잔상으로 건넵니다."
    )
    body60 = (
        "가까이 다가갈수록 색과 결이 또렷해지고, 작은 흔들림조차 이야기의 호흡이 됩니다. "
        "시선이 머무는 자리마다 감정이 쌓이고, 그 감정이 다음 장면을 조용히 엽니다. "
        "서둘러 넘겨 버리기엔 너무 선명한 하루라서, 우리는 한 컷 한 컷에 이름을 붙입니다. "
        "빛은 잠깐 머물고, 그림자는 더 오래 남고, 그 사이 공간이 사람의 마음을 담습니다. "
        "멀리서 보면 풍경이고 가까이서 보면 온기입니다. 그 온기가 오늘의 이유를 설명합니다. "
        "그래서 이 영상은 자랑이 아니라 기록입니다. 지나간 시간을 붙잡는 짧은 편지입니다. "
        "마지막 컷이 닫혀도 여운은 남습니다. 내일의 나에게, 오늘의 온기를 그대로 전합니다."
    )
    if target_duration >= 60:
        text = hook + " " + body60
    elif target_duration >= 30:
        text = hook + " " + body30
    else:
        text = hook + " " + body15
    return sanitize_narration(text), []


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
    frame = work_dir / "fast_blur.jpg"
    try:
        if src is not None and src.suffix.lower() in IMAGE_EXTS:
            diet_image_file(src, dest=frame)
        elif src is not None:
            still_from_media(src, frame, work_dir, TARGET_W, TARGET_H)
        else:
            raise RuntimeError("no media")
    except Exception as exc:
        print("[안내] fast_blur.jpg 준비 실패, 단색 폴백: {}".format(exc))
        Image.new("RGB", (TARGET_W, TARGET_H), (18, 10, 28)).save(str(frame), "JPEG", quality=80)
    ensure_jpeg_on_disk(frame, (TARGET_W, TARGET_H))
    require_image_for_ffmpeg(frame)
    args = [
        "-loop",
        "1",
        "-framerate",
        str(FPS),
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
    ] + FFMPEG_ENCODE + [str(out_file)]
    run_ffmpeg(args, timeout=max(25, min(int(duration * 5 + 20), 240)))
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
    spark = spark or vip
    motion = normalize_camera_motion(camera_motion)
    target_duration = normalize_target_duration(target_duration)
    caption_style = normalize_caption_style(caption_style)
    visual_fx = normalize_visual_fx(visual_fx or motion)
    aspect_ratio = normalize_aspect_ratio(aspect_ratio)
    width, height = canvas_size(aspect_ratio, output_height)
    action_enabled = bool(action_motion_enabled) or bool((action_style or action_preset or "").strip())
    if vip and action_enabled:
        action_style = resolve_action_style(action_preset, action_style)
    elif vip:
        action_style = resolve_action_style(action_preset or "dynamic", action_style)
    else:
        action_style = ""
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
        _notify(progress_cb, 8, "사진 초경량 압축 및 하이라이트 추출", progress_lock)
        slim = []
        for path in media_files:
            prepared = smart_prepare_media(path, work_dir)
            if Path(prepared).suffix.lower() in IMAGE_EXTS:
                slim.append(diet_image_file(prepared))
            else:
                slim.append(prepared)
        media_files = slim
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
            script, photo_order = generate_script(
                settings,
                media_files,
                style_prompt=style_prompt,
                direction=direction,
                target_duration=target_duration,
            )
        except Exception as exc:
            print("[안내] 대본 API 실패, 로컬 스토리로 폴백: {}".format(exc))
            script, photo_order = fallback_script(style_prompt, target_duration=target_duration)
            used_fallback = True
        script = sanitize_narration(script)
        pieces = split_script_pieces(script)
        _notify(progress_cb, 24, "대본 완료 · 음성 합성과 사진 보정을 동시에 시작", progress_lock)

        def _hold_frames():
            ordered = list(media_files)
            if photo_order:
                cycle = [media_files[i] for i in photo_order if 0 <= i < len(media_files)]
                if cycle:
                    ordered = cycle
            stills = []
            for index, src in enumerate(ordered):
                dest = work_dir / "hold_{:03d}.jpg".format(index)
                still_from_media(src, dest, work_dir, width, height)
                stills.append(ensure_jpeg_on_disk(dest, (width, height)))
            return stills or prepare_captioned_frames(
                media_files, pieces or [script], photo_order, font_path, work_dir,
                direction=direction, width=width, height=height,
            )

        def _voice_job():
            _notify(progress_cb, 28, "ElevenLabs 음성 합성 중", progress_lock)
            path = ensure_voice_track(settings, script, voice_file, voice_key, duration=float(target_duration))
            _notify(progress_cb, 56, "음성 생성 완료", progress_lock)
            return path

        def _frame_job():
            if vip and _left() > 10:
                try:
                    clips = generate_vip_action_clips(
                        media_files,
                        settings,
                        action_style,
                        style_prompt,
                        work_dir,
                        progress_cb=progress_cb,
                        lock=progress_lock,
                        width=width,
                        height=height,
                    )
                    if clips:
                        return clips
                    print("[안내] VIP I2V 빈 결과 → 쾌속 모션 엔진")
                except Exception as exc:
                    print("[안내] VIP I2V 실패 → 쾌속 모션: {}".format(exc))
            if spark and _left() > 8:
                try:
                    return generate_spark_cinema_clips(
                        media_files,
                        style_prompt,
                        motion,
                        work_dir,
                        progress_cb=progress_cb,
                        lock=progress_lock,
                        width=width,
                        height=height,
                    )
                except Exception as exc:
                    print("[안내] 쾌속 모션 실패, 블러/홀드 렌더로 전환: {}".format(exc))
                    return None
            _notify(progress_cb, 30, "사진 노출 균등 배분 및 리사이즈 중", progress_lock)
            frames = _hold_frames()
            _notify(progress_cb, 52, "장면 프레임 준비 완료", progress_lock)
            return frames

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
                generated = frame_fut.result(timeout=max(6.0, min(FAL_WAIT_TIMEOUT + 1, _left() - 6)))
            except Exception as exc:
                print("[안내] 영상 생성 대기 중단: {}".format(exc))
                generated = None

        if not generated:
            generated = _hold_frames()
        voice_fit = work_dir / "voice_target.m4a"
        try:
            voice_path = conform_audio_duration(voice_path or voice_file, voice_fit, target_duration)
        except Exception as exc:
            print("[안내] 음성 길이 보정 실패: {}".format(exc))
        audio_duration = float(target_duration)
        cues = split_script_cues(script, audio_duration)
        hold_frames = generated if generated else _hold_frames()
        if spark and generated and str(generated[0]).lower().endswith((".mp4", ".mov", ".webm", ".m4v")):
            durations = even_scene_durations(len(generated), audio_duration)
        else:
            hold_frames = _hold_frames()
            durations = even_scene_durations(len(hold_frames), audio_duration)
        _notify(progress_cb, 68, "BGM 준비 중", progress_lock)
        try:
            bgm_path = resolve_bgm(mood, audio_duration, work_dir / "bgm.wav")
        except Exception:
            bgm_path = None

        spark_clips = generated if spark and generated and str(generated[0]).lower().endswith(".mp4") else None
        try:
            if spark_clips and _left() >= 8:
                if mood == "none":
                    bgm_path = resolve_bgm("lofi", audio_duration, work_dir / "bgm.wav")
                _notify(progress_cb, 78, "✨ 스파크 시네마 · 음성·BGM·자막 단일 패스 합성", progress_lock)
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
        if vip and Path(out_file).is_file():
            try:
                _notify(progress_cb, 88, "👑 키네틱 자막 · 스튜디오 오디오 마스터링", progress_lock)
                captioned = work_dir / "vip_kinetic.mp4"
                burn_kinetic_captions(
                    out_file,
                    script,
                    audio_duration,
                    font_path,
                    captioned,
                    work_dir,
                    width,
                    height,
                    caption_style,
                )
                mixed = work_dir / "vip_master.mp4"
                scene_starts = []
                acc = 0.0
                for dur in durations:
                    scene_starts.append(acc)
                    acc += float(dur)
                mix_vip_sfx(
                    captioned if Path(captioned).is_file() else out_file,
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
            script, _order = fallback_script(style_prompt, target_duration=target_duration)
            script = sanitize_narration(script)
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
    return out_file, script


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
