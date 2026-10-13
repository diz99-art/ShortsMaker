"""가사 만들기 — 주제·확정 데이터 → 반전·해설·비교가 들어간 Suno 가사 3가지 → 검사 통과 → suno_brief.md.

기본은 Claude Code 헤드리스(`claude -p`, 사용자 구독 안에서), 실패하면 프롬프트 복사 수동 모드.
결과(projects/_briefs/<이름>/)는 곡이 나온 뒤 '노래로 자동 제작'에서 연결하면 데이터·출처를 이어받는다.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pandas as pd
import streamlit as st

from shortsmaker import manual_prompt as mp
from shortsmaker import production_log as plog
from shortsmaker.lyrics_check import check_lyrics_text, history_lyrics
from shortsmaker.lyrics_gen import (
    DEFAULT_STYLE,
    Draft,
    LyricsGenError,
    auto_subtitles,
    build_prompt,
    claude_available,
    generate_with_claude,
    pair_problems,
    parse_response,
    save_brief,
)
from shortsmaker.models import PROJECTS_DIR
from shortsmaker.rank_extract import RankRow, label, rows_complete

st.set_page_config(page_title="가사 만들기", layout="wide")
st.title("가사 만들기 (Suno 브리프)")

ROOT = Path(os.environ.get("SHORTSMAKER_PROJECTS_DIR", PROJECTS_DIR))
BRIEFS = ROOT / "_briefs"
ss = st.session_state
COLS = ["순위", "선수", "팀", "기록", "연도", "메모", "기록 줄(빨간)"]


def _slug(t: str) -> str:
    return re.sub(r"[^\w가-힣]+", "_", t).strip("_")[:50] or "brief"


def load(name: str) -> dict:
    return json.loads((BRIEFS / name / "brief.json").read_text(encoding="utf-8"))


def save(name: str, b: dict) -> None:
    (BRIEFS / name).mkdir(parents=True, exist_ok=True)
    (BRIEFS / name / "brief.json").write_text(json.dumps(b, ensure_ascii=False, indent=2), encoding="utf-8")


with st.sidebar:
    st.caption("생성: " + ("`claude -p` 사용 가능 (Claude 구독 안에서, 추가 API 비용 없음)" if claude_available()
                          else "claude 명령 없음 → 수동 모드(프롬프트 복사)"))

# ---------------------------------------------------------------- 브리프
existing = sorted(p.parent.name for p in BRIEFS.glob("*/brief.json")) if BRIEFS.exists() else []
c1, c2 = st.columns(2)
with c1:
    with st.form("new_brief"):
        topic = st.text_input("주제", "KBO 역대 FA 계약 총액 TOP 5")
        go = st.form_submit_button("새 브리프")
    if go and topic.strip():
        name = _slug(topic)
        if (BRIEFS / name).exists():
            st.error("같은 이름의 브리프가 있습니다. '불러오기'를 쓰세요")
        else:
            save(name, {"topic": topic.strip(), "title": "", "source_name": "", "source_url": "", "rows": [],
                        "data_confirmed": False, "draft": None, "chosen": None, "chosen_lyrics": "", "log": []})
            ss.brief = name
with c2:
    if existing:
        pick = st.selectbox("불러오기", existing, key="pick_brief")
        if st.button("불러오기", key="load_brief"):
            ss.brief = pick
if not ss.get("brief"):
    st.stop()
NAME = ss.brief
b = load(NAME)


def log(event: str, **data) -> None:
    b["log"].append({"at": plog.now(), "event": event, **data})
    save(NAME, b)


st.info(f"브리프 **{NAME}** · 주제: {b['topic']}")

# ---------------------------------------------------------------- 1. 확정 데이터
st.header("1. 확정 데이터 (가사에는 이 데이터만 씁니다)")
with st.expander("ShortsData 내보내기에서 불러오기 (exports/<이름>/project.json)"):
    path = st.text_input("project.json 경로", "", key="sd_path")
    if st.button("불러오기", key="sd_load") and path:
        try:
            pj = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
            meta_p = Path(path).expanduser().parent / "source.meta.json"
            meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {}
            b["rows"] = [{"rank": it.get("display_rank") or it["rank"], "player": it["name"], "team": "",
                          "record": it.get("value", ""), "year": "", "notes": it.get("description", ""),
                          "stat_line": it.get("stat_line", "")} for it in pj["items"]]
            b["title"] = b["title"] or pj.get("title", "")
            b["source_name"] = meta.get("source_name", b["source_name"])
            b["source_url"] = meta.get("source_url", b["source_url"])
            b["data_confirmed"] = False
            log("data_imported", path=path)
            st.rerun()
        except (OSError, ValueError, KeyError) as e:
            st.error(f"불러오기 실패: {e}")
title = st.text_area("영상 제목 (2줄)", b["title"] or "", height=70, key="b_title")
s1, s2 = st.columns(2)
src_name = s1.text_input("데이터 출처 이름", b["source_name"], key="b_src")
src_url = s2.text_input("데이터 출처 URL", b["source_url"], key="b_url")
df = pd.DataFrame(
    [
        {
            "순위": r["rank"],
            "선수": r.get("player", ""),
            "팀": r.get("team", ""),
            "기록": r.get("record", ""),
            "연도": r.get("year", ""),
            "메모": r.get("notes", ""),
            "기록 줄(빨간)": r.get("stat_line", ""),
        }
        for r in b["rows"]
    ]
    or [{c: "" for c in COLS} | {"순위": r} for r in (5, 4, 3, 2, 1)]
)
edited = st.data_editor(df, hide_index=True, num_rows="dynamic", key="b_rows",
                        column_config={"순위": st.column_config.NumberColumn(min_value=1, max_value=5, step=1)})
if st.button("데이터 확정", key="b_confirm", type="primary"):
    rows = [{"rank": int(x["순위"]), "player": str(x["선수"] or "").strip(), "team": str(x["팀"] or "").strip(),
             "record": str(x["기록"] or "").strip(), "year": str(x["연도"] or "").strip(),
             "notes": str(x["메모"] or "").strip(), "stat_line": str(x["기록 줄(빨간)"] or "").strip()}
            for x in edited.to_dict("records") if x.get("순위") not in (None, "") and not pd.isna(x["순위"])]
    problems = rows_complete([RankRow(**{k: v for k, v in r.items() if k != "notes"}) for r in rows])
    if not (src_name.strip() and src_url.strip()):
        problems.append("데이터 출처 이름과 URL 이 필요합니다")
    if problems:
        st.error("확정할 수 없습니다: " + "; ".join(problems))
    else:
        b.update(rows=rows, title=title.strip(), source_name=src_name.strip(), source_url=src_url.strip(),
                 data_confirmed=True, draft=None, chosen=None)
        log("data_confirmed", rows=rows, source=src_name.strip())
        st.rerun()
if b["data_confirmed"]:
    rr = [RankRow(**{k: v for k, v in r.items() if k != "notes"}) for r in b["rows"]]
    st.success("데이터 확정됨: " + " / ".join(f"{label(rr, x)} {x.player or x.team} {x.record}" for x in rr))

# ---------------------------------------------------------------- 2. 생성
st.header("2. 가사 3가지 버전 만들기")
if not b["data_confirmed"]:
    st.caption("1단계 데이터를 확정하면 열립니다")
    st.stop()
hist = history_lyrics(ROOT, exclude=NAME)
# 이전 영상 가사의 해설 줄(숫자 없는 줄)은 피하라고 알려 준다. 생성 뒤에도 검사기가 겹침을 따로 경고
avoid = list(dict.fromkeys(l for _, lines in hist for l in lines if not re.search(r"\d|위", l)))[:30]
prompt = build_prompt(b["topic"], b["title"], b["rows"], f"{b['source_name']} {b['source_url']}", avoid=avoid)
g1, g2 = st.columns(2)
if g1.button("Claude 로 3가지 만들기 (claude -p)", key="gen_claude", type="primary", disabled=not claude_available()):
    with st.spinner("claude -p 로 가사 생성 중 (1분 안팎)"):
        try:
            d = generate_with_claude(prompt, rows=b["rows"])
            b["draft"] = d.model_dump()
            log("lyrics_generated", via="claude -p", versions=len(d.versions))
            st.rerun()
        except LyricsGenError as e:
            st.error(f"{e} → 아래 수동 모드를 쓰세요")
with st.expander(
    "수동 모드: 프롬프트 복사 → claude.ai·ChatGPT 에 붙여 넣기 → 답 붙여 넣기", expanded=not claude_available()
):
    st.code(prompt, language=None)
    if st.button("프롬프트 복사", key="cp_prompt"):
        mp.copy_to_clipboard(prompt)
        st.toast("복사했습니다")
    pasted = st.text_area("받은 답(JSON) 붙여 넣기", "", height=160, key="pasted")
    if st.button("붙여 넣은 답 읽기", key="parse") and pasted.strip():
        try:
            d = parse_response(pasted, b["rows"])
            b["draft"] = d.model_dump()
            log("lyrics_generated", via="manual", versions=len(d.versions))
            st.rerun()
        except LyricsGenError as e:
            st.error(str(e))

# ---------------------------------------------------------------- 3. 고르고 검사
st.header("3. 버전 고르기·고치기 → 검사 통과 → Suno 브리프")
if not b["draft"]:
    st.caption("2단계에서 가사를 만들면 열립니다")
    st.stop()
draft = Draft.model_validate(b["draft"])
names = [f"{i + 1}. {v.name}" for i, v in enumerate(draft.versions)]
choice = st.radio("버전", list(range(len(names))), format_func=lambda i: names[i], horizontal=True, key="ver",
                  index=b["chosen"] or 0)
v = draft.versions[choice]
st.caption(f"관점: {v.angle}")
sname = st.text_input("Suno 곡 제목", draft.title, key=f"st{choice}")
style = st.text_input("Style", draft.style or DEFAULT_STYLE, key=f"sy{choice}")
la, lb = st.columns(2)
text = la.text_area("노래용 가사 (Suno 에 넣음 · 숫자는 한글로 읽는 대로)", v.lyrics, height=420, key=f"ly{choice}")
sub_key = f"sub{choice}"
if sub_key not in ss:
    ss[sub_key] = v.subtitles or auto_subtitles(v.lyrics, b["rows"])
if lb.button("숫자 자동 변환 (노래용 → 자막용)", key=f"auto{choice}"):
    ss[sub_key] = auto_subtitles(text, b["rows"])
subs = lb.text_area("자막용 가사 (영상 자막 · 같은 줄, 숫자만 아라비아)", height=420, key=sub_key)
pair_errs = pair_problems(text, subs, b["rows"])
for e in pair_errs:
    lb.error(e)
report = check_lyrics_text(text, b["rows"], history=hist)
cA, cB = st.columns(2)
with cA:
    if report.passed:
        st.success("검사 통과: 나열형 아님 · 반전·해설·비교 있음 · 숫자·이름이 데이터와 일치")
    for e in report.errors:
        st.error(e)
with cB:
    for w in report.warnings:
        st.warning(w)
    st.caption(f"통계: {report.stats}")
ack = st.checkbox("이전 영상과 겹치는 표현을 확인했습니다", key=f"ack{choice}") if report.overlaps else True
if st.button("Suno 용으로 저장", key="save_brief", type="primary",
             disabled=not (report.passed and ack and not pair_errs)):
    try:
        path = save_brief(BRIEFS / NAME, sname, style, text, report, topic=b["topic"],
                          source=f"{b['source_name']} {b['source_url']}", overlaps_confirmed=bool(ack),
                          subtitles=subs, rows=b["rows"])
        edited_flag = text.strip() != v.lyrics.strip()
        b.update(chosen=choice, chosen_lyrics=text, chosen_subtitles=subs, suno_title=sname, suno_style=style)
        log("lyrics_chosen", version=v.name, edited_by_human=edited_flag, report=report.as_dict())
        st.success(f"저장: {path}")
    except LyricsGenError as e:
        st.error(str(e))
brief_md = BRIEFS / NAME / "suno_brief.md"
if brief_md.exists():
    st.subheader("suno_brief.md")
    st.markdown(brief_md.read_text(encoding="utf-8"))
    k1, k2, k3 = st.columns(3)
    if k1.button("제목 복사", key="cp_t"):
        mp.copy_to_clipboard(b.get("suno_title", ""))
    if k2.button("Style 복사", key="cp_s"):
        mp.copy_to_clipboard(b.get("suno_style", ""))
    if k3.button("노래용 가사 복사 (Suno)", key="cp_l"):
        mp.copy_to_clipboard(b.get("chosen_lyrics", ""))
    st.caption("Suno 유료 플랜으로 곡을 만든 뒤, '노래로 자동 제작'에서 곡을 올리고 이 브리프를 연결하세요. "
               "브리프를 연결하면 자막은 자막용 가사(아라비아 숫자)로 나옵니다.")
