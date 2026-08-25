# -*- coding: utf-8 -*-
"""로컬 스모크 테스트: API 키 없이(비용 0원) 파이프라인 전체를 돌려 결과를 검증한다.

사용법: python smoke_pipeline.py [target_duration]
"""

import os
import shutil
import sys
from pathlib import Path

# 비용 0원 보장: 외부 API 키를 이 프로세스에서만 제거
for key in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "FAL_KEY"):
    os.environ.pop(key, None)

from PIL import Image, ImageDraw  # noqa: E402

import main  # noqa: E402

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "output" / "smoke"

# load_settings()는 .env를 다시 읽어 키를 되살리므로, 환경변수 제거만으로는 비용이 0원이 아니다.
# 스모크를 임포트하는 모든 스위트가 키 없는 경로만 타도록 여기서 한 번에 고정한다.
KEYLESS_SETTINGS = main.Settings(
    openai_api_key="",
    elevenlabs_api_key="",
    fal_key="",
    elevenlabs_voice_id=main.DEFAULT_VOICE_ID,
)
main.load_settings = lambda: KEYLESS_SETTINGS


def make_photos(count=3):
    WORK.mkdir(parents=True, exist_ok=True)
    media_dir = WORK / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    palette = [(196, 92, 74), (58, 122, 168), (96, 158, 108), (172, 132, 200)]
    paths = []
    for index in range(count):
        dest = media_dir / "sunset_beach_{:02d}.jpg".format(index + 1)
        img = Image.new("RGB", (1600, 1200), palette[index % len(palette)])
        draw = ImageDraw.Draw(img)
        draw.ellipse((300 + index * 90, 220, 900 + index * 90, 820), fill=(250, 236, 190))
        draw.rectangle((0, 900, 1600, 1200), fill=(38, 34, 48))
        img.save(str(dest), "JPEG", quality=92)
        paths.append(dest)
    return paths


def main_smoke():
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    shutil.rmtree(WORK, ignore_errors=True)
    photos = make_photos(3)
    out_file = WORK / "final_shorts.mp4"

    stages = []

    def progress(percent, message):
        stages.append((percent, message))
        print("   [{:3d}%] {}".format(int(percent), message), flush=True)

    out, script, instagram = main.run_pipeline(
        photos,
        style_prompt="노을 지는 바다 산책",
        progress_cb=progress,
        output_path=out_file,
        check_license=False,
        target_duration=target,
        is_spark_cinema=False,
        bgm_mood="lofi",
    )

    duration = main.probe_duration(out)
    size = main.probe_video_size(out)
    print("\n===== 결과 =====")
    print("파일      :", out, "({:,} bytes)".format(Path(out).stat().st_size))
    print("길이      : {:.2f}초 (목표 {}초)".format(duration, target))
    print("해상도    :", size)
    print("대본      :", (script or "")[:90].replace("\n", " "), "...")
    print("캡션      :", (instagram.get("caption") or "")[:70])
    print("해시태그  :", instagram.get("hashtags"))
    motion = [m for _p, m in stages if "단일 패스" in m or "켄 번스" in m]
    degraded = [m for _p, m in stages if "안전 슬라이드쇼" in m or "안전장치" in m]
    print("모션 경로 :", motion or "정지컷 렌더")
    print("폴백 여부 :", degraded or "없음")
    ok = (
        Path(out).is_file()
        and Path(out).stat().st_size > 50_000
        and abs(duration - target) < 1.5
        and size == (720, 1280)
        and bool(script)
        and bool(instagram.get("hashtags"))
        and bool(motion)
        and not degraded
    )
    print("판정      :", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main_smoke())
