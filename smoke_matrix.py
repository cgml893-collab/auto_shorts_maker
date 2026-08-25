# -*- coding: utf-8 -*-
"""주요 조합을 한 번에 돌려 회귀를 잡는다 (전부 비용 0원 · 외부 API 미사용)."""

import os
import shutil
import sys
import time
from pathlib import Path

for key in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "FAL_KEY"):
    os.environ.pop(key, None)

import main  # noqa: E402
from smoke_pipeline import make_photos  # noqa: E402

BASE = Path(main.OUTPUT_DIR) / "matrix"

CASES = [
    ("15초 기본 (제로코스트)", 15, 1, {}),
    ("15초 사진 1장", 15, 1, {}),
    ("30초 사진 5장", 30, 5, {}),
    ("60초 사진 3장", 60, 3, {}),
    ("비포/애프터 셔터 훅", 15, 3, {"before_after_hook": True}),
    ("3D 파라랙스", 15, 3, {"parallax_3d": True}),
    ("훅 + 파라랙스 동시", 15, 3, {"before_after_hook": True, "parallax_3d": True}),
    ("BGM 없음 + 1.5배속", 15, 3, {"bgm_mood": "none", "speed_multiplier": 1.5}),
    ("16:9 가로", 15, 3, {"aspect_ratio": "16:9"}),
    ("1:1 정방", 15, 2, {"aspect_ratio": "1:1"}),
    ("1080p 세로", 15, 2, {"output_height": 1080}),
    ("neon 자막", 15, 2, {"caption_style": "neon"}),
]


def run_case(index, label, duration, photos, extra):
    work = BASE / "case_{:02d}".format(index)
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    media_dir = work / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    srcs = make_photos(photos)
    staged = []
    for src in srcs:
        dest = media_dir / Path(src).name
        shutil.copy2(str(src), str(dest))
        staged.append(dest)
    out_file = work / "final_shorts.mp4"
    marks = []
    started = time.time()
    devnull = open(os.devnull, "w", encoding="utf-8")
    real = sys.stdout
    sys.stdout = devnull
    error = None
    try:
        main.run_pipeline(
            staged,
            style_prompt="노을 지는 바다 산책",
            progress_cb=lambda p, m: marks.append(m),
            output_path=out_file,
            check_license=False,
            target_duration=duration,
            is_spark_cinema=False,
            bgm_mood=extra.pop("bgm_mood", "lofi"),
            **extra,
        )
    except Exception as exc:  # noqa: BLE001
        error = exc
    finally:
        sys.stdout = real
        devnull.close()

    elapsed = time.time() - started
    ok = out_file.is_file() and out_file.stat().st_size > 50_000
    actual = size = None
    if ok:
        try:
            actual = main.probe_duration(out_file)
            size = main.probe_video_size(out_file)
        except Exception:
            ok = False
    degraded = any("안전 슬라이드쇼" in m or "안전장치" in m for m in marks)
    motion = any("단일 패스" in m for m in marks)
    length_ok = ok and abs(actual - duration) < 1.6
    verdict = "PASS" if (ok and length_ok and not degraded and error is None) else "FAIL"
    print(
        "{:22s} {:>5s} {:6.1f}s  길이 {:>6s}  {:>10s}  모션 {:3s}  폴백 {:3s}  {}".format(
            label[:22],
            verdict,
            elapsed,
            "{:.2f}".format(actual) if actual else "-",
            "{}x{}".format(*size) if size else "-",
            "O" if motion else "X",
            "O" if degraded else "X",
            "err={}".format(str(error)[:60]) if error else "",
        )
    )
    return verdict == "PASS"


def main_matrix():
    shutil.rmtree(BASE, ignore_errors=True)
    print("{:22s} {:>5s} {:>7s}  {:>11s}  {:>10s}".format("케이스", "판정", "소요", "길이", "해상도"))
    results = []
    for index, (label, duration, photos, extra) in enumerate(CASES, start=1):
        results.append(run_case(index, label, duration, photos, dict(extra)))
    print("\n총 {}건 중 {}건 PASS".format(len(results), sum(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main_matrix())
