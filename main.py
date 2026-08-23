# -*- coding: utf-8 -*-
"""input_media의 사진/동영상으로 9:16 숏폼(유튜브 쇼츠/릴스)을 자동 생성한다."""

from __future__ import annotations

import base64
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import requests
from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFilter, ImageFont
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
RUNWAY_CLIP_SEC = 5.0
RUNWAY_MAX_CLIPS = 3
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

SUB_MAX_WIDTH = 640
SUB_FONT_SIZE = 48
SUB_STROKE = 6
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
    if not fal_key:
        missing.append("FAL_KEY")
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


def _pil_to_jpeg_b64(image, max_side=1024):
    # type: (Image.Image, int) -> str
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
            with Image.open(path) as im:
                return _pil_to_jpeg_b64(im)
        if suffix in VIDEO_EXTS:
            preview = OUTPUT_DIR / ("_preview_{}.jpg".format(path.stem))
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            try:
                run_ffmpeg(
                    ["-ss", "0.3", "-i", str(path), "-frames:v", "1", "-q:v", "6", str(preview)],
                    timeout=20,
                )
                with Image.open(preview) as im:
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


def generate_script(settings, media_files, style_prompt="", direction=None):
    # type: (Settings, List[Path], str) -> Tuple[str, List[int]]
    print("1) OpenAI(gpt-4o-mini)로 숏폼 나레이션 대본 작성 중...")
    style = (style_prompt or "").strip() or "시선을 사로잡는 빠른 템포의 숏폼"
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
        "규칙:\n"
        "- 말할 때 20~30초 (대략 90~160자, 너무 길지 않게)\n"
        "- 첫 문장은 시선을 사로잡는 훅\n"
        "- 지정한 스타일에 맞게 톤과 템포를 맞출 것\n"
        "- 구어체, 짧은 문장. 사람이 실제로 말하는 대사만\n"
        "- 장면 지시, 이모지, 해시태그, #기호, 영어 태그, 따옴표, 제목 금지\n"
        "- 화면에 보이는 소재를 구체적으로 언급\n"
        "- 대본 본문만 먼저 쓰고, 마지막 줄에 사진 배치를 이렇게 적으세요:\n"
        "PHOTO_ORDER: 1,3,2\n"
        "- PHOTO_ORDER는 대본 흐름에 맞게 이미지 번호(1부터)를 의미 있는 순서로 나열. 반복 가능"
    ).format(style, guide, numbered)
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
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": content}],
        temperature=0.85,
        max_tokens=420,
    )
    raw = (response.choices[0].message.content or "").strip()
    raw = re.sub(r"^대본\s*[:：]\s*", "", raw)
    raw, order = parse_photo_order(raw, len(media_files))
    script = sanitize_narration(raw)
    if not script:
        raise RuntimeError("대본 생성에 실패했습니다. OpenAI 응답이 비어 있습니다.")
    print("   대본:\n   {}\n".format(script))
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


FFMPEG_PRESET = [
    "-c:v",
    "libx264",
    "-tune",
    "stillimage",
    "-preset",
    "ultrafast",
    "-crf",
    "23",
    "-pix_fmt",
    "yuv420p",
]

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


def run_ffmpeg(args, timeout=None):
    if timeout is None:
        timeout = FFMPEG_TIMEOUT
    cmd = [ffmpeg_bin(), "-hide_banner", "-y"] + [str(a) for a in args]
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
        err = (exc.stderr or b"").decode("utf-8", errors="replace")
        print("[ffmpeg timeout stderr]\n{}".format(err), flush=True)
        raise RuntimeError("FFmpeg 시간 초과 ({}s): {}".format(timeout, err[-1500:]))
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
    if proc.returncode != 0:
        print("[ffmpeg exit {}]".format(proc.returncode), flush=True)
        print("[ffmpeg stderr]\n{}".format(stderr), flush=True)
        if stdout.strip():
            print("[ffmpeg stdout]\n{}".format(stdout), flush=True)
        raise RuntimeError(
            "FFmpeg 실패 (code={}): {}".format(
                proc.returncode, (stderr or stdout or "stderr 비어 있음")[-2500:]
            )
        )
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
            "-movflags",
            "+faststart",
            str(out_file),
        ],
        timeout=FFMPEG_TIMEOUT,
    )


def overlay_subtitles(video_path, sub_assets, out_file):
    if not sub_assets:
        shutil.copy2(str(video_path), str(out_file))
        return
    args = ["-i", str(video_path)]
    for png, _s, _e, _w, _h in sub_assets:
        args += ["-i", str(png)]
    filters = []
    last = "0:v"
    for i, (_png, start, end, _w, h) in enumerate(sub_assets):
        out_v = "v{}".format(i)
        y = max(0, TARGET_H - int(h) - SUB_BOTTOM_MARGIN)
        filters.append(
            "[{last}][{si}:v]overlay=x=(W-w)/2:y={y}:enable='between(t,{start:.3f},{end:.3f})'[{out}]".format(
                last=last,
                si=i + 1,
                y=y,
                start=start,
                end=end,
                out=out_v,
            )
        )
        last = out_v
    filters[-1] = filters[-1].rsplit("[", 1)[0] + "[vout]"
    args += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[vout]",
        "-map",
        "0:a:0",
        "-t",
        str(probe_duration(video_path)),
    ]
    args += FFMPEG_PRESET
    args += ["-c:a", "copy", "-movflags", "+faststart", str(out_file)]
    run_ffmpeg(args)


def build_subtitle_assets(script, duration, font_path, work_dir):
    cues = split_script_cues(script, duration)
    assets = []
    for i, (text, start, end) in enumerate(cues):
        png_path = work_dir / ("sub_{:03d}.png".format(i))
        render_subtitle_png(text, font_path, png_path)
        with Image.open(png_path) as im:
            w, h = im.size
        assets.append((png_path, float(start), float(end), w, h))
    return assets


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
        with Image.open(path) as im:
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
    suffix = Path(path).suffix.lower()
    if suffix in IMAGE_EXTS:
        with Image.open(path) as im:
            fit_contain_on_blur(im, width, height).save(dest_jpg, "JPEG", quality=85)
        return dest_jpg
    tmp = work_dir / (dest_jpg.stem + "_grab.jpg")
    run_ffmpeg(
        ["-ss", "0.15", "-i", str(path), "-frames:v", "1", "-q:v", "4", str(tmp)],
        timeout=20,
    )
    with Image.open(tmp) as im:
        fit_contain_on_blur(im, width, height).save(dest_jpg, "JPEG", quality=85)
    return dest_jpg


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
    suffix = Path(src).suffix.lower()
    if suffix in IMAGE_EXTS:
        with Image.open(src) as im:
            canvas = fit_contain_on_blur(im, TARGET_W, TARGET_H).convert("RGBA")
    else:
        tmp = work_dir / (dest_png.stem + "_grab.jpg")
        run_ffmpeg(
            ["-ss", "0.12", "-i", str(src), "-frames:v", "1", "-q:v", "6", str(tmp)],
            timeout=12,
        )
        with Image.open(tmp) as im:
            canvas = fit_contain_on_blur(im, TARGET_W, TARGET_H).convert("RGBA")
    text = sanitize_narration(caption)
    if text:
        cap_path = dest_png.with_name(dest_png.stem + "_cap.png")
        render_subtitle_png(
            text,
            font_path,
            cap_path,
            fill=(direction.fill if direction else (255, 255, 255)),
            stroke=(direction.stroke if direction else (0, 0, 0)),
            font_scale=(direction.font_scale if direction else 1.0),
        )
        overlay = Image.open(cap_path).convert("RGBA")
        x = max(0, (TARGET_W - overlay.width) // 2)
        y = max(0, TARGET_H - overlay.height - 64)
        canvas.paste(overlay, (x, y), overlay)
    canvas.convert("RGB").save(dest_png, "PNG", compress_level=1)


def prepare_captioned_frames(media_files, pieces, photo_order, font_path, work_dir, direction=None):
    dummy_cues = [(text, 0.0, 1.0) for text in pieces]
    assigned = arrange_media_for_cues(media_files, dummy_cues, photo_order or [])
    frames = [None] * len(pieces)

    def _one(index):
        framed = work_dir / ("frame_{:03d}.png".format(index))
        compose_captioned_png(
            assigned[index], pieces[index], font_path, framed, work_dir, direction=direction
        )
        return index, framed

    workers = min(2, max(1, len(pieces)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_one, i) for i in range(len(pieces))]
        for fut in as_completed(futures):
            index, framed = fut.result()
            frames[index] = framed
    return frames


def _concat_file_line(path):
    return "file '{}'".format(Path(path).resolve().as_posix().replace("'", r"'\''"))


def write_concat_list(entries, list_path):
    lines = ["ffconcat version 1.0"]
    last = entries[-1][0]
    for png, dur in entries:
        last = png
        lines.append(_concat_file_line(png))
        lines.append("duration {:.4f}".format(max(0.04, float(dur))))
    lines.append(_concat_file_line(last))
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return list_path


def build_slideshow_entries(frames, durations, work_dir, speed=1.0, xfade_sec=XFADE_SEC):
    speed = max(0.5, float(speed))
    xfade_sec = max(0.12, min(0.6, float(xfade_sec or XFADE_SEC)))
    scaled = [max(0.2, float(dur) / speed) for dur in durations]
    if len(frames) == 1:
        return [(frames[0], scaled[0])]

    opened = [Image.open(path).convert("RGB") for path in frames]
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
                mix.save(out, "JPEG", quality=82)
                entries.append((out, step))
    finally:
        for img in opened:
            img.close()
    return entries


def ffmpeg_single_pass(frames, durations, voice_path, bgm_path, out_file, speed=1.0, work_dir=None, xfade_sec=XFADE_SEC):
    speed = normalize_speed(speed)
    if not frames:
        raise RuntimeError("렌더할 프레임이 없습니다.")
    work_dir = Path(work_dir or Path(frames[0]).parent)
    entries = build_slideshow_entries(
        frames, durations, work_dir, speed=speed, xfade_sec=xfade_sec
    )
    concat_path = work_dir / "slides.txt"
    write_concat_list(entries, concat_path)
    total = sum(dur for _png, dur in entries)

    voice_mp3 = work_dir / "voice.mp3"
    if Path(voice_path).resolve() != voice_mp3.resolve():
        shutil.copy2(str(voice_path), str(voice_mp3))
    else:
        voice_mp3 = Path(voice_path)

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
            "{:.3f}".format(total),
            "-i",
            str(entries[0][0]),
            "-i",
            str(voice_mp3),
        ]
    else:
        args = ["-f", "concat", "-safe", "0", "-i", str(concat_path), "-i", str(voice_mp3)]
    audio_map = ["-map", "0:v:0", "-map", "1:a:0"]
    extra = []
    if bgm_mp3 is not None:
        args += ["-i", str(bgm_mp3)]
        af = "[1:a]volume=1.05[va];[2:a]volume=0.16[ba];[va][ba]amix=inputs=2:duration=first:dropout_transition=0[a]"
        if abs(speed - 1.0) > 0.001:
            af = (
                "[1:a]volume=1.05,{tempo}[va];[2:a]volume=0.16,{tempo}[ba];"
                "[va][ba]amix=inputs=2:duration=first:dropout_transition=0[a]"
            ).format(tempo=atempo_chain(speed))
        extra = ["-filter_complex", af]
        audio_map = ["-map", "0:v:0", "-map", "[a]"]
    elif abs(speed - 1.0) > 0.001:
        extra = ["-af", atempo_chain(speed)]

    args += extra
    args += audio_map
    args += [
        "-r",
        str(FPS),
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
        "-shortest",
        "-movflags",
        "+faststart",
        str(out_file),
    ]
    run_ffmpeg(args, timeout=min(60, FFMPEG_TIMEOUT))


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
        import fal_client
    except ImportError:
        raise RuntimeError("fal-client가 없습니다. pip install fal-client 후 다시 시도해 주세요.")
    image_url = fal_client.upload_file(str(image_path))
    arguments = {"prompt": prompt, "image_url": image_url, "prompt_optimizer": True}
    last_error = None
    for model in (FAL_I2V_PRIMARY, FAL_I2V_FALLBACK):
        try:
            print("   fal I2V: {} ← {}".format(model, Path(image_path).name))
            payload = dict(arguments)
            if "kling" in model:
                payload["duration"] = "5"
            result = fal_client.subscribe(model, arguments=payload, with_logs=False)
            url = _fal_video_url(result)
            if not url:
                raise RuntimeError("fal 응답에 video url이 없습니다: {}".format(str(result)[:400]))
            download_http_file(url, dest_mp4)
            if Path(dest_mp4).is_file() and Path(dest_mp4).stat().st_size > 1000:
                return Path(dest_mp4)
        except Exception as exc:
            last_error = exc
            print("[안내] {} 실패: {}".format(model, exc))
    raise RuntimeError("Image-to-Video 생성 실패: {}".format(last_error))


def generate_runway_clips(media_files, style_prompt, camera_motion, work_dir, progress_cb=None, lock=None):
    motion = normalize_camera_motion(camera_motion)
    motion_prompt = CAMERA_MOTIONS[motion]
    prompt = "{} {}".format((style_prompt or "cinematic vertical short").strip(), motion_prompt)
    sources = list(media_files)[:RUNWAY_MAX_CLIPS]
    clips = []
    total = max(1, len(sources))
    for index, src in enumerate(sources):
        _notify(
            progress_cb,
            34 + int(28 * index / total),
            "런웨이 AI 비디오 생성 중 ({}/{})...".format(index + 1, total),
            lock,
        )
        frame = work_dir / ("i2v_src_{:02d}.jpg".format(index + 1))
        still_from_media(src, frame, work_dir, TARGET_W, TARGET_H)
        clip = work_dir / ("i2v_{:02d}.mp4".format(index + 1))
        fal_image_to_video(frame, prompt, clip)
        clips.append(clip)
    return clips


def fit_runway_clip(src, dest, duration):
    run_ffmpeg(
        [
            "-stream_loop",
            "-1",
            "-i",
            str(src),
            "-t",
            "{:.3f}".format(max(0.4, float(duration))),
            "-vf",
            "scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps={fps},format=yuv420p".format(
                w=TARGET_W, h=TARGET_H, fps=FPS
            ),
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
        timeout=40,
    )
    return dest


def overlay_cues_on_video(video_path, cues, font_path, direction, work_dir, dest):
    args = ["-i", str(video_path)]
    filters = []
    last = "0:v"
    valid = 0
    for i, (text, start, end) in enumerate(cues):
        png = work_dir / ("runway_sub_{:03d}.png".format(i))
        render_subtitle_png(
            text,
            font_path,
            png,
            fill=(direction.fill if direction else (255, 255, 255)),
            stroke=(direction.stroke if direction else (0, 0, 0)),
            font_scale=(direction.font_scale if direction else 1.0),
        )
        if not png.is_file():
            continue
        args += ["-loop", "1", "-i", str(png)]
        inp = valid + 1
        out = "ov{}".format(valid)
        filters.append(
            "[{src}][{inp}:v]overlay=(W-w)/2:H-h-64:enable='between(t,{start:.3f},{end:.3f})'[{out}]".format(
                src=last,
                inp=inp,
                start=float(start),
                end=float(end),
                out=out,
            )
        )
        last = out
        valid += 1
    if not filters:
        shutil.copy2(str(video_path), str(dest))
        return dest
    duration = max(0.4, probe_duration(video_path))
    args += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[{}]".format(last),
        "-an",
        "-t",
        "{:.3f}".format(duration),
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        str(dest),
    ]
    run_ffmpeg(args, timeout=50)
    return dest


def ffmpeg_runway_pass(clips, durations, cues, font_path, direction, voice_path, bgm_path, out_file, work_dir):
    if not clips:
        raise RuntimeError("런웨이 비디오 클립이 없습니다.")
    fitted = []
    n = len(clips)
    for i, dur in enumerate(durations):
        src = clips[i % n]
        dest = work_dir / ("runway_fit_{:03d}.mp4".format(i))
        fit_runway_clip(src, dest, dur)
        fitted.append(dest)
    concat_path = work_dir / "runway_concat.txt"
    lines = ["ffconcat version 1.0"]
    for clip in fitted:
        lines.append("file '{}'".format(Path(clip).resolve().as_posix().replace("'", r"'\''")))
    concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    silent_video = work_dir / "runway_body.mp4"
    run_ffmpeg(
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(silent_video),
        ],
        timeout=50,
    )
    captioned = work_dir / "runway_captioned.mp4"
    overlay_cues_on_video(silent_video, cues, font_path, direction, work_dir, captioned)

    args = ["-i", str(captioned), "-i", str(voice_path)]
    maps = ["-map", "0:v:0", "-map", "1:a:0"]
    extra = []
    if bgm_path:
        args += ["-i", str(bgm_path)]
        extra = [
            "-filter_complex",
            "[1:a]volume=1.05[va];[2:a]volume=0.16[ba];[va][ba]amix=inputs=2:duration=first:dropout_transition=0[a]",
        ]
        maps = ["-map", "0:v:0", "-map", "[a]"]
    args += extra + maps + [
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
        "-shortest",
        "-movflags",
        "+faststart",
        str(out_file),
    ]
    run_ffmpeg(args, timeout=50)


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
    camera_motion="zoom_in",
):
    # type: (List[Path], str, object, Optional[Path], bool, str, float, str, bool, str) -> Tuple[Path, str]
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
    runway = bool(is_runway_mode)
    motion = normalize_camera_motion(camera_motion)
    voice_key, _voice_id, _preset = resolve_voice(voice_type)
    progress_lock = threading.Lock()

    work_dir = out_file.parent / ("_ffwork_{}".format(uuid.uuid4().hex[:8]))
    work_dir.mkdir(parents=True, exist_ok=True)
    voice_file = out_file.parent / "voice.mp3"

    _notify(
        progress_cb,
        4,
        "미디어 {}개 준비: {}".format(
            len(media_files), ", ".join(p.name for p in media_files)
        ),
        progress_lock,
    )
    _notify(progress_cb, 7, "긴 영상 하이라이트 추출 중", progress_lock)
    media_files = [smart_prepare_media(path, work_dir) for path in media_files]
    _notify(progress_cb, 11, "스타일 연출 해석 중", progress_lock)
    direction = interpret_style_direction(settings, style_prompt)
    _notify(progress_cb, 16, "대본 작성 중", progress_lock)
    script, photo_order = generate_script(
        settings, media_files, style_prompt=style_prompt, direction=direction
    )
    script = sanitize_narration(script)
    pieces = split_script_pieces(script)
    _notify(progress_cb, 22, "대본 완료 · 음성/이미지 병렬 처리 시작", progress_lock)

    try:
        def _voice_job():
            _notify(progress_cb, 28, "음성 생성 중", progress_lock)
            path = generate_voice(settings, script, output_path=voice_file, voice_type=voice_key)
            _notify(progress_cb, 58, "음성 생성 완료", progress_lock)
            return path

        def _frame_job():
            if runway:
                return generate_runway_clips(
                    media_files,
                    style_prompt,
                    motion,
                    work_dir,
                    progress_cb=progress_cb,
                    lock=progress_lock,
                )
            _notify(progress_cb, 30, "이미지·자막 병렬 합성 중", progress_lock)
            frames = prepare_captioned_frames(
                media_files, pieces, photo_order, font_path, work_dir, direction=direction
            )
            _notify(progress_cb, 52, "이미지·자막 합성 완료", progress_lock)
            return frames

        with ThreadPoolExecutor(max_workers=2) as pool:
            voice_fut = pool.submit(_voice_job)
            frame_fut = pool.submit(_frame_job)
            voice_path = voice_fut.result()
            generated = frame_fut.result()

        audio_duration = probe_duration(voice_path)
        if audio_duration < 1:
            raise RuntimeError("생성된 음성이 너무 짧습니다. 대본/TTS를 확인하세요.")
        cues = split_script_cues(script, audio_duration)
        durations = [max(0.2, float(end) - float(start)) for _text, start, end in cues]
        _notify(progress_cb, 68, "BGM 준비 중", progress_lock)
        bgm_path = resolve_bgm(mood, audio_duration, work_dir / "bgm.wav")

        if runway:
            clips = generated
            if not clips:
                raise RuntimeError("런웨이 비디오를 만들지 못했습니다.")
            if len(durations) != len(clips):
                # match cue count to available I2V clips by grouping
                if len(durations) > len(clips):
                    durations = durations[: len(clips)]
                    cues = cues[: len(clips)]
                else:
                    durations = durations + [max(0.4, audio_duration / max(1, len(clips)))] * (
                        len(clips) - len(durations)
                    )
            _notify(progress_cb, 78, "런웨이 클립·자막 합성 중", progress_lock)
            ffmpeg_runway_pass(
                clips,
                durations,
                cues,
                font_path,
                direction,
                voice_path,
                bgm_path,
                out_file,
                work_dir,
            )
        else:
            frames = generated
            if len(durations) != len(frames):
                n = min(len(durations), len(frames))
                frames = frames[:n]
                durations = durations[:n]
            print("   음성 길이: {:.2f}초 / 배속 {}x / BGM {} / xfade {:.2f}".format(
                audio_duration, speed, mood, direction.xfade
            ))
            _notify(progress_cb, 76, "영상 렌더링 중 (단일 패스)", progress_lock)
            ffmpeg_single_pass(
                frames,
                durations,
                voice_path,
                bgm_path,
                out_file,
                speed=speed,
                work_dir=work_dir,
                xfade_sec=direction.xfade,
            )
        _notify(progress_cb, 96, "출력 파일 정리 중", progress_lock)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    cleanup_temp_files()
    _notify(progress_cb, 100, "완료: {}".format(out_file), progress_lock)
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
