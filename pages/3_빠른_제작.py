"""빠른 제작 — ChatGPT 답변 붙여넣기 + 곡 + 그림 5장 → 버튼 하나로 시험 렌더.

문제가 있을 때만 멈추고 물어본다. 수익화용 렌더(체크리스트)·업로드 초안은 '노래로 자동 제작'에서 같은 프로젝트로 이어서.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from shortsmaker import auto_pipeline as ap
from shortsmaker import manual_prompt as mp
from shortsmaker.models import PROJECTS_DIR
from shortsmaker.quick import run_quick
from shortsmaker.quick_parse import EXAMPLE, parse_quick, request_prompt
from shortsmaker.rank_extract import label

st.set_page_config(page_title="빠른 제작", layout="wide")
st.title("빠른 제작")
st.caption("① ChatGPT 답변 ② 곡 ③ 그림 5장 → '시험 렌더까지 만들기'. 문제가 있을 때만 멈춥니다. "
           "수익화용 렌더·업로드는 '노래로 자동 제작'에서 같은 프로젝트를 불러와 이어서 하세요.")
ROOT = Path(os.environ.get("SHORTSMAKER_PROJECTS_DIR", PROJECTS_DIR))
ss = st.session_state

with st.expander("ChatGPT 에 이 양식을 요청하는 프롬프트 (복사)"):
    topic = st.text_input("주제", "KBO 단일 시즌 홈런 TOP 5", key="q_topic")
    prompt = request_prompt(topic)
    st.code(prompt, language=None)
    if st.button("프롬프트 복사", key="q_cp"):
        mp.copy_to_clipboard(prompt)
        st.toast("복사했습니다")
    st.caption("⚠ ChatGPT 가 준 기록·출처는 반드시 직접 확인하세요. 확인 못 한 칸이 '확인 필요'로 남아 있으면 읽지 않습니다.")

# ---------------------------------------------------------------- ① 답변
st.header("① ChatGPT 답변 붙여넣기")
text = st.text_area("답변", "", height=230, key="q_text", placeholder=EXAMPLE)
data = parse_quick(text) if text.strip() else None
if data:
    if data.errors:
        for e in data.errors:
            st.error(e)
    else:
        st.success(f"읽음: 제목 '{data.title.replace(chr(10), ' / ')}' · 출처 {data.source_name} · 항목 {len(data.rows)}개 · 메모 {len(data.memos)}줄")
    for w in data.warnings:
        st.warning(w)
    if data.rows:
        st.dataframe(pd.DataFrame([{"순위": label(data.rows, r), "선수": r.player, "팀": r.team, "기록": r.record,
                                    "연도": r.year, "장면 설명": r.scene} for r in data.rows]), hide_index=True)
    if data.memos:
        st.caption("메모: " + " / ".join(data.memos))

# ---------------------------------------------------------------- ② 곡
st.header("② 곡 파일")
song = st.file_uploader("Suno 에서 받은 곡 (가사가 들어 있는 m4a)", type=["m4a", "mp3", "wav"], key="q_song")

# ---------------------------------------------------------------- ③ 그림
st.header("③ 그림 5장 (표 순서대로: 5위 → 1위)")
files = st.file_uploader("그림 (png·jpg·webp)", type=["png", "jpg", "jpeg", "webp"], accept_multiple_files=True,
                         key="q_imgs")
if files:
    names = [f.name for f in files]
    if ss.get("q_names") != names:  # 새로 올리면 올린 순서로 초기화
        ss.q_names, ss.q_order = names, list(range(len(files)))
    order = ss.q_order
    labels = [f"{label(data.rows, r)} {r.player}" for r in data.rows] if data and data.rows else \
             [f"{i + 1}번째" for i in range(len(files))]
    cols = st.columns(max(1, len(order)))
    for pos, idx in enumerate(order):
        with cols[pos]:
            st.markdown(f"**{labels[pos] if pos < len(labels) else f'{pos + 1}번째'}**")
            st.image(files[idx].getvalue(), use_container_width=True, caption=files[idx].name)
            to = st.selectbox("바꿀 자리", [None] + [p for p in range(len(order)) if p != pos],
                              format_func=lambda p: "—" if p is None else (labels[p] if p < len(labels) else f"{p + 1}번째"),
                              key=f"q_sw{pos}")
            if to is not None and st.button("바꾸기", key=f"q_swb{pos}"):
                order[pos], order[to] = order[to], order[pos]
                st.rerun()

# ---------------------------------------------------------------- 만들기
st.header("④ 만들기")
c1, c2 = st.columns(2)
tpl = c1.radio("영상 모드", list(ap.TEMPLATES), format_func=ap.TEMPLATES.get, horizontal=True, key="q_tpl")
default_slug = re.sub(r"[^\w가-힣]+", "_", (data.title.split("\n")[-1] if data and data.title else "quick")).strip("_")[:40]
slug = c2.text_input("프로젝트 이름", default_slug, key="q_slug")
ready = bool(data and data.ok and song and files and len(files) == len(data.rows))
if not ready:
    st.caption("답변 양식 오류가 없고, 곡과 그림(" + (str(len(data.rows)) if data and data.rows else "5") + "장)이 있으면 열립니다")


def _go(accept: bool) -> None:
    images = [files[i].getvalue() for i in ss.q_order]
    bar = st.progress(0.0)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / song.name
        path.write_bytes(song.getvalue())
        res = run_quick(data, path, images, slug=slug, template=tpl, root=ROOT, accept_mismatch=accept,
                        progress=lambda f, m: bar.progress(min(f, 1.0), m))
    ss.q_result = res


if st.button("시험 렌더까지 만들기", key="q_go", type="primary", disabled=not ready):
    _go(False)
res = ss.get("q_result")
if res:
    if res.status == "stopped":
        st.error(f"[{res.stage}] {res.message}")
        for i in res.issues:
            st.warning(i)
        if res.stage == "가사·표 대조":
            a, b = st.columns(2)
            if a.button("표대로 계속", key="q_accept", type="primary"):
                _go(True)
                st.rerun()
            b.caption("고치려면 답변을 수정한 뒤 다시 '시험 렌더까지 만들기'")
    else:
        st.success(f"시험 렌더 완료 → '노래로 자동 제작'에서 프로젝트 '{res.slug}'를 불러와 로고 확인·Suno 확인·수익화용 렌더를 이어서 하세요")
        if res.output and Path(res.output).exists():
            st.video(res.output)
            st.write(f"`{res.output}`")
        for w in res.issues:
            st.caption(w)
