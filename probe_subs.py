# -*- coding: utf-8 -*-
"""subtitles 필터의 경로/이스케이프 조합 중 어떤 것이 실제로 통하는지 실험한다."""

import subprocess
from pathlib import Path

import main

WORK = Path(main.OUTPUT_DIR) / "probe_subs"
WORK.mkdir(parents=True, exist_ok=True)
srt = WORK / "subs.srt"
srt.write_text("\ufeff1\n00:00:00,000 --> 00:00:01,000\n테스트 자막\n", encoding="utf-8")
font = main.find_korean_font()
out = WORK / "probe.mp4"


def attempt(label, subs_expr, cwd=None):
    cmd = [
        main.ffmpeg_bin(), "-hide_banner", "-y",
        "-f", "lavfi", "-i", "color=c=navy:s=360x640:d=1",
        "-filter_complex", "[0:v]fps=24,format=yuv420p,{}[v]".format(subs_expr),
        "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", "-t", "1", str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, cwd=str(cwd) if cwd else None)
    err = (proc.stderr or b"").decode("utf-8", "replace")
    tag = "OK  " if proc.returncode == 0 else "FAIL"
    print("{} {}".format(tag, label))
    if proc.returncode != 0:
        line = [l for l in err.splitlines() if "rror" in l or "No such" in l]
        print("      -> {}".format((line or err.splitlines()[-1:])[0][:150]))


style = main.caption_force_style("hormozi", font)
esc = main._ffmpeg_filter_path(srt)
fontsdir = main._ffmpeg_filter_path(Path(font).resolve().parent)

attempt("current: escaped abs path + fontsdir + force_style",
        "subtitles={}:fontsdir={}:force_style='{}'".format(esc, fontsdir, style))
attempt("escaped abs path only", "subtitles={}".format(esc))
attempt("escaped abs path + force_style", "subtitles={}:force_style='{}'".format(esc, style))
attempt("filename only (cwd)", "subtitles=subs.srt", cwd=WORK)
attempt("filename + fontsdir + force_style (cwd)",
        "subtitles=subs.srt:fontsdir={}:force_style='{}'".format(fontsdir, style), cwd=WORK)
attempt("filename + force_style, no fontsdir (cwd)",
        "subtitles=subs.srt:force_style='{}'".format(style), cwd=WORK)
plain_srt = Path(srt).resolve().as_posix()
plain_fonts = Path(font).resolve().parent.as_posix()
attempt("quoted filename= + quoted fontsdir + force_style",
        "subtitles=filename='{}':fontsdir='{}':force_style='{}'".format(plain_srt, plain_fonts, style))
attempt("quoted filename= only", "subtitles=filename='{}'".format(plain_srt))
attempt("quoted filename= + force_style", "subtitles=filename='{}':force_style='{}'".format(plain_srt, style))
attempt("cwd + quoted fontsdir + force_style",
        "subtitles=subs.srt:fontsdir='{}':force_style='{}'".format(plain_fonts, style), cwd=WORK)
import shutil
shutil.copy2(str(font), str(WORK / Path(font).name))
attempt("cwd + fontsdir=. + force_style (제안)",
        "subtitles=subs.srt:fontsdir=.:force_style='{}'".format(style), cwd=WORK)
attempt("cwd + fontsdir=. only", "subtitles=subs.srt:fontsdir=.", cwd=WORK)
print("\nforce_style =", style)
print("escaped srt =", esc)
print("fontsdir    =", fontsdir)
