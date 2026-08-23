# -*- coding: utf-8 -*-
"""input_media의 사진/동영상으로 9:16 숏폼(유튜브 쇼츠/릴스)을 자동 생성한다."""

from __future__ import annotations

import base64
import io
import math
import os
import random
import re
import shutil
import subprocess
import sys
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import requests
from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont
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
FFMPEG_TIMEOUT = int(os.getenv("FFMPEG_TIMEOUT", "300"))
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}

DEFAULT_VOICE_ID = "cgSgspJ2msm6clMCkdW9"
ELEVENLABS_MODEL = "eleven_multilingual_v2"
KENBURNS_W = 900
KENBURNS_H = 1600
ALLOWED_SPEEDS = (1.0, 1.2, 1.5)
DEFAULT_VOICE_TYPE = "bright_female"
DEFAULT_BGM_MOOD = "upbeat"

# Premade multilingual voices that speak Korean well on eleven_multilingual_v2.
# Override with ELEVENLABS_VOICE_<TYPE> in .env if needed.
VOICE_PRESETS = {
    "energetic_male": {
        "id": "iP95p4xoKVk53GoZ742B",  # Chris
        "stability": 0.28,
        "similarity_boost": 0.70,
        "style": 0.74,
    },
    "bright_female": {
        "id": "cgSgspJ2msm6clMCkdW9",  # Jessica
        "stability": 0.36,
        "similarity_boost": 0.78,
        "style": 0.58,
    },
    "calm_male": {
        "id": "onwK4e9ZLuTAKqWW03F9",  # Daniel
        "stability": 0.64,
        "similarity_boost": 0.82,
        "style": 0.18,
    },
    "story_female": {
        "id": "pFZP5JQG7iQjIQuC4Bku",  # Lily
        "stability": 0.48,
        "similarity_boost": 0.84,
        "style": 0.46,
    },
}
BGM_MOODS = ("upbeat", "emotional", "tense", "none")

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
        "bright_female": "bright_female",
        "female": "bright_female",
        "energetic_male": "energetic_male",
        "male": "energetic_male",
        "calm_male": "calm_male",
        "story_female": "story_female",
    }
    key = aliases.get(key, DEFAULT_VOICE_TYPE)
    preset = VOICE_PRESETS[key]
    env_name = "ELEVENLABS_VOICE_" + key.upper()
    voice_id = (os.getenv(env_name) or preset["id"] or DEFAULT_VOICE_ID).strip()
    return key, voice_id, preset


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
        "upbeat": "upbeat",
        "beat": "upbeat",
        "energetic": "upbeat",
        "emotional": "emotional",
        "vlog": "emotional",
        "soft": "emotional",
        "tense": "tense",
        "funk": "tense",
        "punk": "tense",
        "none": "none",
        "off": "none",
        "mute": "none",
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


def generate_script(settings, media_files, style_prompt=""):
    # type: (Settings, List[Path], str) -> Tuple[str, List[int]]
    print("1) OpenAI(gpt-4o-mini)로 숏폼 나레이션 대본 작성 중...")
    style = (style_prompt or "").strip() or "시선을 사로잡는 빠른 템포의 숏폼"
    numbered = ", ".join(
        "{}번 {}".format(i + 1, path.name) for i, path in enumerate(media_files)
    )
    prompt = (
        "첨부된 사진/영상 프레임을 보고, 유튜브 쇼츠/인스타 릴스용 "
        "한국어 나레이션 대본만 작성하세요.\n"
        "영상 스타일/분위기: {}\n"
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
    ).format(style, numbered)
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
        if attached >= 8:
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
    "26",
    "-threads",
    "2",
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


def split_script_cues(script, total_duration):
    # type: (str, float) -> List[Tuple[str, float, float]]
    pieces = [
        p.strip()
        for p in re.split(r"(?<=[.!?。…])\s+|(?<=요)\s+|(?<=다)\s+", script)
        if p.strip()
    ]
    if not pieces:
        pieces = [script]
    if len(pieces) == 1:
        chunks = re.findall(r".{8,28}(?:\s+|$)|.{1,28}$", script)
        chunks = [c.strip() for c in chunks if c.strip()]
        if chunks:
            pieces = chunks

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


def render_subtitle_png(text, font_path, out_path):
    # type: (str, str, Path) -> Path
    text = sanitize_narration(text)
    if not text:
        Image.new("RGBA", (8, 8), (0, 0, 0, 0)).save(out_path, "PNG")
        return out_path
    font = _load_font(font_path, SUB_FONT_SIZE)
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

    y = SUB_PAD_Y
    for line, (lw, lh, bbox) in zip(lines, sizes):
        x = int((width - lw) / 2.0) - bbox[0]
        draw.text(
            (x, y - bbox[1]),
            line,
            font=font,
            fill=(255, 255, 255, 255),
            stroke_width=SUB_STROKE,
            stroke_fill=(0, 0, 0, 255),
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
        "upbeat": ("upbeat", "beat", "energetic", "happy", "fun", "신나"),
        "emotional": ("emotional", "vlog", "soft", "chill", "calm", "감성"),
        "tense": ("tense", "funk", "punk", "pulse", "dark", "긴박"),
    }.get(mood, ())
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
    if mood == "emotional":
        wave_data = (
            0.11 * np.sin(2 * np.pi * 196 * t)
            + 0.08 * np.sin(2 * np.pi * 247 * t)
            + 0.05 * np.sin(2 * np.pi * 294 * t)
        ) * (0.55 + 0.45 * np.sin(2 * np.pi * 0.12 * t))
    elif mood == "tense":
        pulse = (np.sin(2 * np.pi * 2.4 * t) > 0).astype(np.float64)
        wave_data = 0.14 * np.sin(2 * np.pi * 98 * t) * pulse + 0.04 * np.sin(
            2 * np.pi * 392 * t
        )
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


def fit_cover_rgb(im, width, height):
    img = im.convert("RGB")
    scale = max(width / float(img.width), height / float(img.height))
    nw = max(width, int(round(img.width * scale)))
    nh = max(height, int(round(img.height * scale)))
    resample = getattr(Image, "Resampling", Image).LANCZOS
    img = img.resize((nw, nh), resample)
    left = max(0, (nw - width) // 2)
    top = max(0, (nh - height) // 2)
    return img.crop((left, top, left + width, top + height))


def still_from_media(path, dest_jpg, work_dir, width=None, height=None):
    width = int(width or TARGET_W)
    height = int(height or TARGET_H)
    suffix = Path(path).suffix.lower()
    if suffix in IMAGE_EXTS:
        with Image.open(path) as im:
            fit_cover_rgb(im, width, height).save(dest_jpg, "JPEG", quality=88)
        return dest_jpg
    tmp = work_dir / (dest_jpg.stem + "_grab.jpg")
    run_ffmpeg(
        ["-ss", "0.15", "-i", str(path), "-frames:v", "1", "-q:v", "4", str(tmp)],
        timeout=20,
    )
    with Image.open(tmp) as im:
        fit_cover_rgb(im, width, height).save(dest_jpg, "JPEG", quality=88)
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


def kenburns_zoom_expr(index):
    if index % 2 == 0:
        return "min(1+0.00135*on,1.14)"
    return "if(eq(on,0),1.14,max(1.14-0.00135*on,1.0))"


def ffmpeg_motion_pass(scenes, voice_path, out_file, speed=1.0, bgm_path=None):
    speed = normalize_speed(speed)
    args = []
    n = len(scenes)
    for still, _cap, _dur in scenes:
        args += ["-loop", "1", "-i", str(still)]
    for _still, cap, _dur in scenes:
        args += ["-loop", "1", "-i", str(cap)]
    args += ["-i", str(voice_path)]
    if bgm_path:
        args += ["-i", str(bgm_path)]

    filters = []
    labels = []
    for i, (_still, _cap, dur) in enumerate(scenes):
        frames = max(int(round(max(0.2, float(dur)) * FPS)), 10)
        cap_i = n + i
        filters.append(
            "[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
            "crop={w}:{h},zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            "d={d}:s={tw}x{th}:fps={fps},format=yuv420p[z{i}]".format(
                i=i,
                w=KENBURNS_W,
                h=KENBURNS_H,
                z=kenburns_zoom_expr(i),
                d=frames,
                tw=TARGET_W,
                th=TARGET_H,
                fps=FPS,
            )
        )
        filters.append(
            "[z{i}][{c}:v]overlay=(W-w)/2:H-h-64[v{i}]".format(i=i, c=cap_i)
        )
        labels.append("[v{}]".format(i))
    filters.append("".join(labels) + "concat=n={}:v=1:a=0[vcat]".format(n))
    if abs(speed - 1.0) > 0.001:
        filters.append("[vcat]setpts=PTS/{:.4f},fps={},format=yuv420p[v]".format(speed, FPS))
        audio_fx = atempo_chain(speed)
    else:
        filters.append("[vcat]fps={},format=yuv420p,setsar=1[v]".format(FPS))
        audio_fx = "anull"

    voice_i = 2 * n
    filters.append("[{}:a]{}[va]".format(voice_i, audio_fx))
    if bgm_path:
        bgm_i = voice_i + 1
        filters.append(
            "[{}:a]aformat=sample_fmts=fltp:channel_layouts=stereo,volume=0.16,{}[ba]".format(
                bgm_i, audio_fx
            )
        )
        filters.append("[va][ba]amix=inputs=2:duration=first:dropout_transition=2[a]")
        a_map = "[a]"
    else:
        a_map = "[va]"

    args += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[v]",
        "-map",
        a_map,
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "24",
        "-threads",
        "2",
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
    run_ffmpeg(args)


def render_stillimage_reel(
    media_files,
    script,
    voice_path,
    duration,
    font_path,
    work_dir,
    out_file,
    photo_order=None,
    speed_multiplier=1.0,
    bgm_mood=DEFAULT_BGM_MOOD,
):
    cues = split_script_cues(sanitize_narration(script), duration)
    if not cues:
        cues = [(sanitize_narration(script), 0.0, duration)]
    assigned = arrange_media_for_cues(media_files, cues, photo_order or [])
    scenes = []
    for i, ((text, start, end), src) in enumerate(zip(cues, assigned)):
        still = work_dir / ("still_{:03d}.jpg".format(i))
        still_from_media(src, still, work_dir, KENBURNS_W, KENBURNS_H)
        cap = work_dir / ("cap_{:03d}.png".format(i))
        render_subtitle_png(text, font_path, cap)
        scenes.append((still, cap, max(0.2, float(end) - float(start))))

    mood = normalize_bgm_mood(bgm_mood)
    bgm_path = None
    if mood != "none":
        bgm_path = pick_bgm_for_mood(mood)
        if bgm_path is None:
            bgm_path = synthesize_bgm(mood, duration, work_dir / "bgm_mood.wav")
            print("   합성 BGM: {}".format(mood))
        else:
            print("   BGM 파일: {}".format(bgm_path.name))

    ffmpeg_motion_pass(
        scenes,
        voice_path,
        out_file,
        speed=speed_multiplier,
        bgm_path=bgm_path,
    )


def _notify(progress_cb, percent, message):
    print(message)
    if progress_cb is not None:
        progress_cb(percent, message)


def run_pipeline(
    media_files,
    style_prompt="",
    progress_cb=None,
    output_path=None,
    check_license=True,
    voice_type=DEFAULT_VOICE_TYPE,
    speed_multiplier=1.0,
    bgm_mood=DEFAULT_BGM_MOOD,
):
    # type: (List[Path], str, object, Optional[Path], bool, str, float, str) -> Tuple[Path, str]
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
    voice_key, _voice_id, _preset = resolve_voice(voice_type)

    _notify(
        progress_cb,
        5,
        "미디어 {}개 준비: {}".format(
            len(media_files), ", ".join(p.name for p in media_files)
        ),
    )
    _notify(progress_cb, 12, "대본 작성 중")
    script, photo_order = generate_script(settings, media_files, style_prompt=style_prompt)
    script = sanitize_narration(script)

    _notify(progress_cb, 32, "음성 생성 중")
    voice_file = out_file.parent / "voice.mp3"
    generate_voice(settings, script, output_path=voice_file, voice_type=voice_key)

    audio_duration = probe_duration(voice_file)
    if audio_duration < 1:
        raise RuntimeError("생성된 음성이 너무 짧습니다. 대본/TTS를 확인하세요.")
    print("   음성 길이: {:.2f}초 / 배속 {}x / BGM {}".format(audio_duration, speed, mood))

    work_dir = out_file.parent / ("_ffwork_{}".format(uuid.uuid4().hex[:8]))
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        _notify(progress_cb, 70, "영상 렌더링 중")
        render_stillimage_reel(
            media_files,
            script,
            voice_file,
            audio_duration,
            font_path,
            work_dir,
            out_file,
            photo_order=photo_order,
            speed_multiplier=speed,
            bgm_mood=mood,
        )
        _notify(progress_cb, 95, "완료 처리 중")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    cleanup_temp_files()
    _notify(progress_cb, 100, "완료: {}".format(out_file))
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
