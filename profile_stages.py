# -*- coding: utf-8 -*-
"""파이프라인 단계별 소요 시간을 측정한다 (비용 0원)."""

import os
import sys
import time
from pathlib import Path

for key in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "FAL_KEY"):
    os.environ.pop(key, None)

import main  # noqa: E402
from smoke_pipeline import make_photos, WORK  # noqa: E402

import shutil

shutil.rmtree(WORK, ignore_errors=True)
photos = make_photos(3)
out_file = WORK / "final_shorts.mp4"

t0 = time.time()
marks = []


def progress(percent, message):
    marks.append((time.time() - t0, percent, message))


sys.stdout = open(os.devnull, "w", encoding="utf-8")
try:
    main.run_pipeline(
        photos,
        style_prompt="노을 지는 바다 산책",
        progress_cb=progress,
        output_path=out_file,
        check_license=False,
        target_duration=15,
        is_spark_cinema=False,
        bgm_mood="lofi",
    )
finally:
    sys.stdout.close()
    sys.stdout = sys.__stdout__

total = time.time() - t0
print("총 소요: {:.1f}초".format(total))
print("{:>7} {:>7}  {}".format("경과", "구간", "단계"))
prev = 0.0
for elapsed, percent, message in marks:
    print("{:7.1f} {:7.1f}  [{:3d}%] {}".format(elapsed, elapsed - prev, percent, message[:60]))
    prev = elapsed
print("{:7.1f} {:7.1f}  [렌더 종료 이후]".format(total, total - prev))
