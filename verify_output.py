# -*- coding: utf-8 -*-
"""완성된 영상이 실제로 사진 전부를 보여 주고, 움직이고, 소리가 있는지 검증한다."""

import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

import main

target = Path(sys.argv[1] if len(sys.argv) > 1 else main.OUTPUT_DIR / "smoke" / "final_shorts.mp4")
probe_dir = target.parent / "_verify"
probe_dir.mkdir(parents=True, exist_ok=True)


def grab(second):
    dest = probe_dir / "at_{:05.2f}.jpg".format(second)
    main.ffmpeg_extract_still(target, dest, ss="{:.2f}".format(second), qv=3, timeout=25)
    return Image.open(str(dest)).convert("RGB")


def diff_score(a, b):
    return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]


duration = main.probe_duration(target)
size = main.probe_video_size(target)
print("길이 {:.2f}초 / 해상도 {} / {:,} bytes".format(duration, size, target.stat().st_size))

# 오디오 스트림 확인
info = subprocess.run(
    [main.ffmpeg_bin(), "-hide_banner", "-i", str(target)], capture_output=True
).stderr.decode("utf-8", "replace")
has_audio = "Audio:" in info
print("오디오 스트림:", "있음" if has_audio else "없음")

marks = [0.6, duration * 0.25, duration * 0.5, duration * 0.75, duration - 0.6]
shots = [grab(m) for m in marks]

print("\n장면 변화 (연속 샘플 간 평균 픽셀 차이, 8 이상이면 다른 사진/컷):")
scene_changes = 0
for index in range(1, len(shots)):
    score = diff_score(shots[index - 1], shots[index])
    changed = score >= 8
    scene_changes += 1 if changed else 0
    print("  {:5.1f}s → {:5.1f}s : {:6.2f} {}".format(
        marks[index - 1], marks[index], score, "컷 변화" if changed else "동일 계열"
    ))

# 같은 컷 안에서의 미세 움직임(켄 번스) 확인
near = [grab(marks[1]), grab(marks[1] + 0.5)]
motion = diff_score(near[0], near[1])
print("\n같은 컷 내 0.5초 간 차이: {:.2f} ({})".format(motion, "움직임 있음" if motion > 0.6 else "정지"))

ok = has_audio and scene_changes >= 2 and motion > 0.6 and size == (720, 1280)
print("\n판정:", "PASS" if ok else "FAIL")
raise SystemExit(0 if ok else 1)
