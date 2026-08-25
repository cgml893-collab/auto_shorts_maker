# -*- coding: utf-8 -*-
"""FastAPI 서버 계약 스모크: 라이선스 → 분석 → 생성 → 폴링 → 다운로드 (비용 0원)."""

import os
import time
import uuid
from pathlib import Path

for key in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "FAL_KEY"):
    os.environ.pop(key, None)

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import server  # noqa: E402
from smoke_pipeline import KEYLESS_SETTINGS, make_photos  # noqa: E402

# server는 load_settings를 직접 임포트했으므로 그 참조도 같이 눌러야 비용이 0원으로 유지된다
server.load_settings = lambda: KEYLESS_SETTINGS

client = TestClient(server.app)
# 무료 플랜은 기기당 1회이므로 매 실행마다 새 기기로 검증한다
DEVICE = "smoke-{}".format(uuid.uuid4().hex[:12])
failures = []


def check(label, condition, detail=""):
    print("{} {}{}".format("PASS" if condition else "FAIL", label, "  " + str(detail)[:120] if detail else ""))
    if not condition:
        failures.append(label)


res = client.get("/health")
check("GET /health", res.status_code == 200 and res.json().get("ok") is True)

res = client.get("/")
check("GET / (엔드포인트 목록)", res.status_code == 200 and "/job-audio/{job_id}" in res.json()["endpoints"])

res = client.request("HEAD", "/health")
check("HEAD /health", res.status_code == 200)

res = client.post("/license-status", json={"device_id": DEVICE, "platform": "android"})
body = res.json() if res.status_code == 200 else {}
check(
    "POST /license-status",
    res.status_code == 200 and body.get("plan") == "free" and body.get("free_remaining") == 1,
    "plan={} free={}".format(body.get("plan"), body.get("free_remaining")),
)

photos = make_photos(3)


def upload_files():
    return [("files", (p.name, p.read_bytes(), "image/jpeg")) for p in photos]


res = client.post(
    "/analyze-media",
    files=upload_files()[:2],
    data={"device_id": DEVICE, "platform": "android", "license_key": ""},
)
styles = res.json().get("styles") if res.status_code == 200 else None
check(
    "POST /analyze-media (키 없이 로컬 폴백)",
    res.status_code == 200 and isinstance(styles, list) and len(styles) >= 3 and "prompt" in styles[0],
    "code={} styles={}".format(res.status_code, len(styles or [])),
)

form = {
    "style": "노을 지는 바다 산책",
    "device_id": DEVICE,
    "platform": "android",
    "license_key": "",
    "voice_type": "vlog_female",
    "speed_multiplier": "1.0",
    "bgm_type": "lofi",
    "is_spark_cinema": "false",
    "is_vip_mode": "false",
    "camera_motion": "zoom_in",
    "output_height": "720",
    "target_duration": "15",
    "caption_style": "hormozi",
    "visual_fx": "ken_burns",
    "aspect_ratio": "9:16",
    "audio_ducking": "true",
    "motion_intensity": "7",
    # 무료 플랜이 유료 연출을 뚫으려 시도 → 서버가 꺼야 한다
    "before_after_hook": "true",
    "ai_lipsync": "true",
    "parallax_3d": "true",
}
res = client.post("/create-video", files=upload_files(), data=form)
created = res.json() if res.status_code == 200 else {}
job_id = created.get("job_id")
check("POST /create-video", res.status_code == 200 and bool(job_id), "status={} body={}".format(res.status_code, str(created)[:160]))

if res.status_code == 200:
    check(
        "무료 플랜 바이럴 연출 차단",
        created.get("before_after_hook") is False
        and created.get("ai_lipsync") is False
        and created.get("parallax_3d") is False,
        "ba={} lip={} par={}".format(
            created.get("before_after_hook"), created.get("ai_lipsync"), created.get("parallax_3d")
        ),
    )
    check("제로코스트 모드 표시", created.get("zero_cost_mode") is True)

if job_id:
    deadline = time.time() + 240
    status = {}
    seen_stages = []
    while time.time() < deadline:
        res = client.get("/job-status/{}".format(job_id))
        if res.status_code != 200:
            break
        status = res.json()
        if status.get("stage") not in seen_stages:
            seen_stages.append(status.get("stage"))
        if status.get("status") in ("completed", "failed"):
            break
        time.sleep(1.0)

    check(
        "GET /job-status 완료 도달",
        status.get("status") == "completed",
        "status={} stage={} {:.0f}s".format(status.get("status"), status.get("stage"), status.get("elapsed_sec", 0)),
    )
    check("job-status 진행률 100", status.get("percent") == 100 and status.get("progress") == 100)
    check(
        "인스타 캡션 계약",
        bool(status.get("instagram_copy")) and isinstance(status.get("instagram_hashtags"), list)
        and len(status.get("instagram_hashtags") or []) >= 3,
        "hashtags={}".format(status.get("instagram_hashtags")),
    )
    check("대본 반환", bool(status.get("script")))

    res = client.request("HEAD", "/i2v-image/{}".format(job_id))
    check(
        "HEAD /i2v-image (fal 사전검증)",
        res.status_code == 200 and res.headers.get("content-type") == "image/jpeg",
        "code={} ct={}".format(res.status_code, res.headers.get("content-type")),
    )
    res = client.get("/i2v-image/{}".format(job_id))
    check("GET /i2v-image", res.status_code == 200 and len(res.content) > 1000)

    res = client.get("/download/{}".format(job_id))
    check(
        "GET /download",
        res.status_code == 200 and res.headers.get("content-type") == "video/mp4" and len(res.content) > 50_000,
        "code={} bytes={}".format(res.status_code, len(res.content)),
    )
    if res.status_code == 200:
        dest = Path(server.OUTPUT_DIR) / "smoke_server_download.mp4"
        dest.write_bytes(res.content)
        check("다운로드 영상 길이 15초", abs(main.probe_duration(dest) - 15) < 1.6, main.probe_duration(dest))
        check("다운로드 영상 9:16", main.probe_video_size(dest) == (720, 1280), main.probe_video_size(dest))

    # 무료 1회를 소진했으므로 같은 기기의 두 번째 요청은 결제 안내로 막혀야 한다
    res = client.post("/license-status", json={"device_id": DEVICE, "platform": "android"})
    body = res.json() if res.status_code == 200 else {}
    check(
        "무료 소진 후 결제 안내",
        body.get("free_remaining") == 0 and body.get("payment_required") is True,
        "free={} pay={}".format(body.get("free_remaining"), body.get("payment_required")),
    )
    res = client.post("/create-video", files=upload_files(), data=form)
    check("무료 소진 후 create-video 402", res.status_code == 402, "code={}".format(res.status_code))

res = client.get("/job-status/does-not-exist")
check("없는 작업 404", res.status_code == 404)

print("\n실패 {}건: {}".format(len(failures), failures or "없음"))
raise SystemExit(0 if not failures else 1)
