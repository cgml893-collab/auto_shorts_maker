# -*- coding: utf-8 -*-
"""AI 숏폼 & 릴스 원클릭 자동 제작기 — Streamlit UI."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from license_lock import (
    activate_license,
    formatted_hwid,
    machine_code,
    require_license,
    verify_saved_license,
)
from main import (
    BGM_DIR,
    IMAGE_EXTS,
    OUTPUT_DIR,
    SFX_DIR,
    VIDEO_EXTS,
    find_named_sfx,
    list_audio_files,
    run_pipeline,
)

ROOT = Path(__file__).resolve().parent
UPLOAD_DIR = OUTPUT_DIR / "uploads"

PRESETS = [
    "신나는 브이로그",
    "감동적인 일상",
    "빠른 템포의 유머 숏폼",
    "감성 힐링 여행",
    "정보성 꿀팁 릴스",
]


def inject_css():
    st.markdown(
        """
<style>
@import url('https://fonts.googleapis.com/css2?family=Pretendard:wght@400;600;800&display=swap');

html, body, [class*="css"] {
  font-family: Pretendard, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
}

.stApp {
  background:
    radial-gradient(1200px 600px at 10% -10%, rgba(255, 77, 141, 0.28), transparent 50%),
    radial-gradient(900px 500px at 110% 0%, rgba(124, 77, 255, 0.32), transparent 46%),
    linear-gradient(180deg, #0b0714 0%, #140c24 48%, #0b0714 100%);
  color: #f6f3ff;
}

.block-container {
  max-width: 880px;
  padding-top: 2.2rem;
  padding-bottom: 3.5rem;
}

.hero {
  text-align: center;
  margin-bottom: 1.6rem;
}
.hero-badge {
  display: inline-block;
  padding: 0.35rem 0.85rem;
  border-radius: 999px;
  background: rgba(255,255,255,0.08);
  border: 1px solid rgba(255,255,255,0.12);
  color: #ffd3ea;
  font-size: 0.82rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  margin-bottom: 0.85rem;
}
.hero h1 {
  font-size: 2.15rem;
  font-weight: 800;
  line-height: 1.25;
  margin: 0 0 0.55rem 0;
  background: linear-gradient(90deg, #fff 10%, #ffb3d4 45%, #c5b6ff 90%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.hero p {
  margin: 0 auto;
  max-width: 560px;
  color: rgba(246,243,255,0.72);
  font-size: 1.02rem;
}

.card {
  background: rgba(255,255,255,0.06);
  border: 1px solid rgba(255,255,255,0.10);
  box-shadow: 0 18px 50px rgba(0,0,0,0.28);
  border-radius: 22px;
  padding: 1.15rem 1.2rem 0.3rem 1.2rem;
  margin-bottom: 1rem;
}
.card-label {
  font-weight: 700;
  font-size: 1.02rem;
  margin-bottom: 0.15rem;
}
.card-help {
  color: rgba(246,243,255,0.55);
  font-size: 0.88rem;
  margin-bottom: 0.7rem;
}

div[data-testid="stFileUploader"] section {
  background: rgba(12, 8, 24, 0.45);
  border: 1.5px dashed rgba(255,179,212,0.45);
  border-radius: 16px;
}

.stTextArea textarea {
  background: rgba(12, 8, 24, 0.45) !important;
  color: #fff !important;
  border-radius: 14px !important;
}

div.stButton > button[kind="primary"] {
  background: linear-gradient(90deg, #ff4d8d 0%, #7c4dff 100%);
  color: white;
  border: 0;
  height: 3.55rem;
  font-size: 1.18rem;
  font-weight: 800;
  border-radius: 16px;
  letter-spacing: -0.02em;
  box-shadow: 0 12px 30px rgba(124, 77, 255, 0.35);
}
div.stButton > button[kind="primary"]:hover {
  filter: brightness(1.08);
  transform: translateY(-1px);
}
div.stButton > button[kind="secondary"] {
  background: rgba(255,255,255,0.08);
  color: #f6f3ff;
  border: 1px solid rgba(255,255,255,0.12);
  border-radius: 999px;
}

.stDownloadButton > button {
  width: 100%;
  height: 3rem;
  font-weight: 700;
  border-radius: 14px;
  background: #fff;
  color: #1a1028;
  border: 0;
}

.login-box {
  background: rgba(255,255,255,0.07);
  border: 1px solid rgba(255,255,255,0.12);
  border-radius: 24px;
  padding: 1.6rem 1.4rem 1.2rem 1.4rem;
  box-shadow: 0 22px 60px rgba(0,0,0,0.35);
  margin-top: 0.4rem;
}
.hwid-chip {
  font-family: Consolas, "Courier New", monospace;
  font-size: 0.86rem;
  word-break: break-all;
  background: rgba(0,0,0,0.28);
  border: 1px solid rgba(255,255,255,0.1);
  border-radius: 12px;
  padding: 0.7rem 0.8rem;
  color: #ffd3ea;
}

.result-box {
  background: rgba(255,255,255,0.06);
  border: 1px solid rgba(255,255,255,0.10);
  border-radius: 22px;
  padding: 1.15rem 1.2rem 1.3rem 1.2rem;
  margin-top: 0.6rem;
}

footer { visibility: hidden; }
</style>
        """,
        unsafe_allow_html=True,
    )


def render_license_gate():
    ok, _message = verify_saved_license()
    if ok:
        st.session_state["licensed"] = True
        return True
    if st.session_state.get("licensed"):
        return True

    st.markdown(
        """
<div class="hero">
  <div class="hero-badge">LICENSE LOCK · 1 PC</div>
  <h1>라이선스 인증</h1>
  <p>이 프로그램은 발급된 라이선스 키와, 최초 등록된 1대의 컴퓨터에서만 실행됩니다.</p>
</div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown('<div class="login-box">', unsafe_allow_html=True)
    st.markdown("**머신 코드**")
    st.markdown('<div class="hwid-chip">{}</div>'.format(machine_code()), unsafe_allow_html=True)
    st.caption("전체 HWID")
    st.markdown('<div class="hwid-chip">{}</div>'.format(formatted_hwid()), unsafe_allow_html=True)
    st.write("")
    key = st.text_input(
        "License Key",
        type="password",
        placeholder="ASM-XXXX-XXXX-XXXX-XXXX",
    )
    unlock = st.button("라이선스 인증하고 시작하기", type="primary", use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)
    if unlock:
        success, message = activate_license(key)
        if success:
            st.session_state["licensed"] = True
            st.success(message)
            st.rerun()
        else:
            st.error("실행이 차단되었습니다. " + message)
            st.stop()
    st.stop()
    return False


def save_uploads(uploaded_files):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    saved = []
    for i, item in enumerate(uploaded_files):
        suffix = Path(item.name).suffix.lower()
        stem = Path(item.name).stem
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in stem) or "media"
        dest = UPLOAD_DIR / "{:03d}_{}{}".format(i + 1, safe, suffix)
        dest.write_bytes(item.getbuffer())
        saved.append(dest)
    return saved


def main():
    st.set_page_config(
        page_title="AI 숏폼 & 릴스 원클릭 자동 제작기",
        page_icon="🎬",
        layout="centered",
    )
    inject_css()
    render_license_gate()

    if "style_prompt" not in st.session_state:
        st.session_state.style_prompt = "신나는 브이로그"
    if "video_path" not in st.session_state:
        st.session_state.video_path = None
    if "script_text" not in st.session_state:
        st.session_state.script_text = ""

    st.markdown(
        """
<div class="hero">
  <div class="hero-badge">YouTube Shorts · Instagram Reels · 9:16</div>
  <h1>AI 숏폼 &amp; 릴스 원클릭 자동 제작기</h1>
  <p>사진과 영상을 올리면 대본, 한국어 보이스, 팝 자막, BGM·효과음까지 캡컷 감성 세로 숏폼으로 만들어 줍니다.</p>
</div>
        """,
        unsafe_allow_html=True,
    )

    bgm_n = len(list_audio_files(BGM_DIR))
    pop_ok = find_named_sfx("pop") is not None
    whoosh_ok = find_named_sfx("whoosh") is not None
    bits = []
    if bgm_n:
        bits.append("BGM {}곡".format(bgm_n))
    else:
        bits.append("BGM 없음 (bgm 폴더에 mp3 추가)")
    bits.append("팝 SFX " + ("준비됨" if pop_ok else "자동 생성"))
    bits.append("후시 " + ("준비됨" if whoosh_ok else "자동 생성"))
    st.caption(" · ".join(bits) + "  ·  자막은 중앙에서 통통 튀며 등장합니다")

    st.markdown(
        '<div class="card"><div class="card-label">1. 사진 / 동영상 업로드</div>'
        '<div class="card-help">여러 파일을 한꺼번에 드래그 앤 드롭할 수 있어요.</div></div>',
        unsafe_allow_html=True,
    )
    accept = sorted(IMAGE_EXTS | VIDEO_EXTS)
    uploaded = st.file_uploader(
        "미디어 업로드",
        type=[ext.lstrip(".") for ext in accept],
        accept_multiple_files=True,
        label_visibility="collapsed",
    )

    st.markdown(
        '<div class="card"><div class="card-label">2. 스타일 프롬프트</div>'
        '<div class="card-help">원하는 분위기만 적으면 나레이션 톤이 맞춰집니다.</div></div>',
        unsafe_allow_html=True,
    )

    cols = st.columns(len(PRESETS))
    for col, preset in zip(cols, PRESETS):
        if col.button(preset, use_container_width=True):
            st.session_state.style_prompt = preset

    style_prompt = st.text_area(
        "스타일 프롬프트",
        key="style_prompt",
        height=90,
        placeholder="예: 신나는 브이로그, 감동적인 일상, 빠른 템포의 유머 숏폼",
        label_visibility="collapsed",
    )

    st.write("")
    clicked = st.button(
        "원클릭 영상 제작하기",
        type="primary",
        use_container_width=True,
    )

    if clicked:
        if not uploaded:
            st.error("사진 또는 동영상을 한 개 이상 업로드해 주세요.")
        elif not (style_prompt or "").strip():
            st.error("스타일 프롬프트를 입력해 주세요.")
        else:
            progress = st.progress(0, text="준비 중...")
            status = st.empty()
            with st.spinner("캡컷 스타일로 숏폼을 만들고 있어요. BGM·효과음 믹싱과 렌더에 1~3분 걸릴 수 있습니다."):
                try:
                    media_files = save_uploads(uploaded)

                    def on_progress(percent, message):
                        progress.progress(min(100, max(0, int(percent))), text=message)
                        status.caption(message)

                    out_path, script = run_pipeline(
                        media_files,
                        style_prompt=style_prompt.strip(),
                        progress_cb=on_progress,
                        output_path=OUTPUT_DIR / "final_shorts.mp4",
                    )
                    st.session_state.video_path = str(out_path)
                    st.session_state.script_text = script
                    progress.progress(100, text="완료!")
                    status.empty()
                    st.success("영상이 완성되었습니다.")
                except Exception as exc:
                    progress.empty()
                    st.error("제작 중 오류가 발생했습니다.\n\n{}".format(exc))

    video_path = st.session_state.video_path
    if video_path and Path(video_path).is_file():
        st.markdown('<div class="result-box">', unsafe_allow_html=True)
        st.subheader("완성된 숏폼")
        if st.session_state.script_text:
            with st.expander("생성된 나레이션 대본", expanded=False):
                st.write(st.session_state.script_text)
        st.video(video_path)
        with open(video_path, "rb") as handle:
            st.download_button(
                label="다운로드",
                data=handle,
                file_name="final_shorts.mp4",
                mime="video/mp4",
                use_container_width=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)


if __name__ == "__main__":
    main()
