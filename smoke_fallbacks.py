# -*- coding: utf-8 -*-
"""폴백 경로 검증: 스파크(fal 키 없음), 모션 실패 → 정지컷 렌더, 렌더 실패 → 안전 슬라이드쇼."""

import os
import shutil
import sys
import time
from pathlib import Path

for key in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "FAL_KEY"):
    os.environ.pop(key, None)

import main  # noqa: E402
from smoke_pipeline import make_photos  # noqa: E402

BASE = Path(main.OUTPUT_DIR) / "fallbacks"
failures = []


def run(label, patches, **kwargs):
    work = BASE / label.replace(" ", "_").replace("/", "_")
    shutil.rmtree(work, ignore_errors=True)
    (work / "media").mkdir(parents=True, exist_ok=True)
    staged = []
    for src in make_photos(3):
        dest = work / "media" / Path(src).name
        shutil.copy2(str(src), str(dest))
        staged.append(dest)
    out_file = work / "final_shorts.mp4"
    marks = []
    saved = {name: getattr(main, name) for name in patches}
    for name, value in patches.items():
        setattr(main, name, value)
    devnull = open(os.devnull, "w", encoding="utf-8")
    real = sys.stdout
    sys.stdout = devnull
    started = time.time()
    error = None
    try:
        main.run_pipeline(
            staged,
            style_prompt="노을 지는 바다 산책",
            progress_cb=lambda p, m: marks.append(m),
            output_path=out_file,
            check_license=False,
            target_duration=15,
            **kwargs,
        )
    except Exception as exc:  # noqa: BLE001
        error = exc
    finally:
        sys.stdout = real
        devnull.close()
        for name, value in saved.items():
            setattr(main, name, value)

    elapsed = time.time() - started
    ok = out_file.is_file() and out_file.stat().st_size > 30_000
    length = main.probe_duration(out_file) if ok else 0
    # 진행 알림은 시도 직전에 찍히므로 마지막에 실제로 쓰인 경로를 본다
    if any("안전 슬라이드쇼" in m for m in marks):
        path_used = "안전슬라이드쇼"
    elif any("전문 편집 렌더링" in m for m in marks):
        path_used = "정지컷"
    elif any("단일 패스" in m for m in marks):
        path_used = "모션"
    else:
        path_used = "미확인"
    good = ok and abs(length - 15) < 1.6 and error is None
    print("{} {:26s} {:6.1f}s  길이 {:5.2f}  경로={:8s} {}".format(
        "PASS" if good else "FAIL", label, elapsed, length, path_used,
        "err={}".format(str(error)[:70]) if error else "",
    ))
    if not good:
        failures.append(label)
    return path_used


def boom(*_args, **_kwargs):
    raise RuntimeError("의도적 실패 (폴백 검증)")


print("케이스별 폴백 동작 (모두 완성 영상이 나와야 PASS)\n")

p = run("스파크 ON · FAL 키 없음", {}, is_spark_cinema=True)
if p != "모션":
    failures.append("스파크 폴백이 모션 경로가 아님")
    print("     -> 기대: 제로코스트 모션, 실제: {}".format(p))

p = run("모션 클립 생성 전부 실패", {"ffmpeg_ken_burns_clip": lambda *a, **k: None,
                                "ffmpeg_parallax_clip": boom})
if p != "정지컷":
    print("     -> 참고: 실제 경로 {}".format(p))

p = run("모션 합성(spark_pass) 실패", {"ffmpeg_spark_pass": boom})
if p != "정지컷":
    failures.append("spark_pass 실패 시 정지컷 렌더로 안 넘어감")
    print("     -> 기대: 정지컷, 실제: {}".format(p))

p = run("모션+정지컷 렌더 둘 다 실패", {"ffmpeg_spark_pass": boom, "ffmpeg_single_pass": boom})
if p != "안전슬라이드쇼":
    failures.append("최종 안전 슬라이드쇼로 안 떨어짐")
    print("     -> 기대: 안전슬라이드쇼, 실제: {}".format(p))

p = run("TTS/음성 트랙 실패", {"generate_voice": boom})

print("\n실패 {}건: {}".format(len(failures), failures or "없음"))
raise SystemExit(0 if not failures else 1)
