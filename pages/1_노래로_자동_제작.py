"""노래로 자동 제작 — 곡 하나로 yagu_v2 영상 + CapCut 초안.

단계마다 앞 단계가 끝나야 다음 버튼이 열린다. 진행 상태는 projects/<이름>/auto.json 에 저장된다.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from shortsmaker import auto_pipeline as ap
from shortsmaker.models import PROJECTS_DIR, load_project
from shortsmaker.player_assets import MockEditor, OpenAIEditor
from shortsmaker.rank_extract import RankRow, label

st.set_page_config(page_title="노래로 자동 제작", layout="wide")
st.title("노래로 자동 제작 (yagu_v2)")

ROOT = Path(os.environ.get("SHORTSMAKER_PROJECTS_DIR", PROJECTS_DIR))
has_key = bool(os.environ.get("OPENAI_API_KEY"))
ss = st.session_state

# ---------------------------------------------------------------- 모드
with st.sidebar:
    st.subheader("캐리커처 생성 모드")
    if has_key:
        st.success("OPENAI_API_KEY 설정됨 (값은 표시하지 않음)")
    else:
        st.warning("OPENAI_API_KEY 없음 → Mock 모드만 가능")
        st.caption("설정: 터미널에서 `~/.zshrc` 에 `export OPENAI_API_KEY=\"sk-...\"` 를 넣고 새 터미널에서 Streamlit 을 다시 실행")
    st.caption("Mock 모드: 비용 없음. 결과 카드에 'MOCK 시험용'이 찍히고 출력 이름에 _MOCK 이 붙습니다.")


def _slugify(text: str) -> str:
    return re.sub(r"[^\w가-힣]+", "_", text).strip("_")[:50] or "auto_project"


def _show_error(e: Exception) -> None:
    st.error(f"{e}")


# ---------------------------------------------------------------- 프로젝트 선택
existing = sorted(p.parent.name for p in ROOT.glob(f"*/{ap.STATE_FILE}")) if ROOT.exists() else []
tab_new, tab_load = st.tabs(["새로 만들기", "이어서 하기"])
with tab_new:
    with st.form("new"):
        song = st.file_uploader("곡 파일 (가사가 들어 있는 m4a·mp3)", type=["m4a", "mp3", "wav"], key="song")
        title = st.text_area("영상 제목 (2줄: 줄바꿈으로 나눔)", "한 번만 이겼어도…\nKBO 역대 최장 연패 TOP 5", height=80)
        slug = st.text_input("프로젝트 이름 (영문·한글·숫자)", "")
        real = st.checkbox("실제 캐리커처 생성(유료) 모드", value=False, disabled=not has_key,
                           help="API 키가 있을 때만 선택할 수 있습니다. 끄면 Mock 모드")
        go = st.form_submit_button("시작", disabled=False)
    if go:
        if song is None:
            st.error("곡 파일을 올려 주세요")
        else:
            name = _slugify(slug or Path(song.name).stem)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / song.name
                path.write_bytes(song.getvalue())
                try:
                    state, d = ap.start_from_song(path, name, title=title.strip(), mock=not real, root=ROOT)
                    ss.auto_slug = name
                    st.success(f"프로젝트 생성: {d}")
                except ap.FlowError as e:
                    _show_error(e)
with tab_load:
    if existing:
        pick = st.selectbox("프로젝트", existing, key="pick")
        if st.button("불러오기", key="load"):
            ss.auto_slug = pick
    else:
        st.caption("아직 자동 제작 프로젝트가 없습니다")

if not ss.get("auto_slug"):
    st.stop()

D = ROOT / ss.auto_slug
state = ap.load_state(D)
st.info(f"프로젝트: **{state.slug}** · 길이 {state.duration:g}초 · "
        + ("Mock 모드 (비용 없음)" if state.mock else "실제 생성 모드 (유료)"))

# ---------------------------------------------------------------- 1. 가사
st.header("1. 가사 불러오기 → 자동 맞추기")
if st.button("가사 자동 맞추기 실행" if not state.aligned else "다시 맞추기", key="align"):
    bar = st.progress(0.0)
    try:
        with st.spinner("보컬 분리·가사 정렬 중 (처음엔 모델 다운로드로 오래 걸림)"):
            ap.align(state, D, progress=lambda f, m: bar.progress(min(f, 1.0), m))
        st.rerun()
    except Exception as e:  # noqa: BLE001
        _show_error(e)
if state.aligned:
    lyr = load_project(D / "project.json")[0].lyrics
    st.dataframe(pd.DataFrame([{"시작": l.start, "끝": l.end, "가사": l.text} for l in lyr]), hide_index=True,
                 height=200)

# ---------------------------------------------------------------- 2. 표
st.header("2. 순위 표 확인·수정")
if not state.aligned:
    st.caption("1단계를 먼저 끝내세요")
else:
    st.caption("가사에서 뽑은 초안입니다. 선수 이름·연도처럼 가사에 없는 값은 직접 채우세요. 확정해야 다음 단계가 열립니다. "
               "공동 순위는 같은 순위 숫자를 두 행에 적으면 '공동 2위'로 표시되고, 그다음 순위(예: 3위)는 비워 둡니다.")
    df = pd.DataFrame([{"순위": r.rank, "표시": label(state.rows, r), "팀": r.team, "선수": r.player,
                        "기록": r.record, "연도": r.year, "근거 가사": r.lyric} for r in state.rows])
    edited = st.data_editor(df, hide_index=True, disabled=["표시", "근거 가사"], key="table", num_rows="dynamic",
                            column_config={
                                "순위": st.column_config.NumberColumn(min_value=1, max_value=5, step=1, required=True),
                                "표시": st.column_config.TextColumn(help="같은 순위가 둘 이상이면 '공동'"),
                                "기록": st.column_config.TextColumn(help="카드에 들어갈 표기 (예: 16연패)")})
    title2 = st.text_area("영상 제목", state.title, key="title2", height=80)
    merge = st.checkbox("자막: 'N위, 팀' 줄과 기록 줄을 한 자막으로 합치기", value=state.merge_record_lines, key="merge")
    if st.button("표 확정", key="confirm", type="primary"):
        starts = {r.lyric: r.start for r in state.rows if r.lyric}

        def _s(v) -> str:
            return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()

        rows = [RankRow(rank=int(x["순위"]), team=_s(x["팀"]), player=_s(x["선수"]), record=_s(x["기록"]),
                        year=_s(x["연도"]), lyric=_s(x["근거 가사"]), start=starts.get(_s(x["근거 가사"])))
                for x in edited.to_dict("records") if x.get("순위") is not None and not pd.isna(x["순위"])]
        try:
            ap.confirm_table(state, D, rows, title=title2.strip(), merge_record_lines=merge)
            st.rerun()
        except ap.FlowError as e:
            _show_error(e)
    if state.table_confirmed:
        st.success("표 확정됨 (수정 후 다시 '표 확정'을 누르면 갱신)")

# ---------------------------------------------------------------- 3. 사진
st.header("3. 선수 사진 (위키미디어 공용)")
if not state.table_confirmed:
    st.caption("2단계 표를 확정하면 열립니다")
else:
    for r in state.rows:
        k = r.slot
        ps = state.players[k]
        with st.expander(f"{label(state.rows, r)} {r.player} ({r.team})" + (" ✅" if ps.folder else ""),
                         expanded=not ps.folder):
            if ps.folder:
                meta_dir = D / ps.folder
                orig = next(meta_dir.glob("original.*"), None)
                if orig:
                    st.image(str(orig), width=180, caption=f"사용 중 ({ps.source})")
                continue
            q = st.text_input("검색어 (직접 고칠 수 있음, 자동으로 넓히지 않음)", ps.query, key=f"q{k}")
            if st.button("Commons 검색", key=f"s{k}"):
                try:
                    ap.search(state, D, k, q)
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    _show_error(e)
            if ps.photos:
                cols = st.columns(4)
                for i, ph in enumerate(ps.photos[:8]):
                    with cols[i % 4]:
                        st.image(ph.thumbnail, use_container_width=True)
                        st.caption(f"{ph.license} · {ph.author[:30]}" + (" · 상업 이용 후보" if ph.commercial_candidate else " · ⚠ 권한 불명"))
                        st.caption(ph.warning)
                        idc = st.checkbox("동일인 확인", key=f"id{k}_{i}")
                        rgt = st.checkbox("이용·변형 권한 확인", key=f"rg{k}_{i}")
                        if st.button("이 사진 사용", key=f"use{k}_{i}", disabled=not (idc and rgt)):
                            try:
                                ap.choose_photo(state, D, k, i, identity_confirmed=idc, rights_confirmed=rgt)
                                st.rerun()
                            except Exception as e:  # noqa: BLE001
                                _show_error(e)
            elif ps.query:
                st.info("검색 결과가 없거나 아직 검색하지 않았습니다. 사진이 없으면 아래에 이미지를 올려 주세요.")
            up = st.file_uploader("직접 이미지 올리기 (사진이 없을 때)", type=["png", "jpg", "jpeg"], key=f"up{k}")
            ok = st.checkbox("올린 이미지의 이용·변형 권한을 확인했습니다", key=f"upok{k}")
            if st.button("올린 이미지 사용", key=f"upuse{k}", disabled=up is None or not ok):
                try:
                    ap.upload_photo(state, D, k, up.getvalue(), rights_confirmed=ok)
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    _show_error(e)
            if state.mock and st.button("Mock 자리표시 실루엣 사용 (시험용)", key=f"ph{k}"):
                ap.placeholder_photo(state, D, k)
                st.rerun()

# ---------------------------------------------------------------- 4. 캐리커처
st.header("4. AI 캐리커처 (생성 전 비용 확인·승인)")
ready = state.table_confirmed and all(ps.folder for ps in state.players.values())
if not ready:
    st.caption("5명 모두 사진이 준비되면 열립니다")
else:
    if not state.mock:
        q = st.selectbox("품질", ["low", "medium", "high"], index=["low", "medium", "high"].index(state.style.quality),
                         key="quality")
        if q != state.style.quality:
            state.style = state.style.model_copy(update={"quality": q})
            ap.save_state(state, D)
    est = ap.cost_estimate(state, D)
    if est["plans"]:
        rows = [{"순위": ap.row_label(state, r), "선수": ap._row(state, r).player, "모델": est["model"], "품질": est["quality"],
                 "예상(출력)": "Mock $0" if state.mock else f"${p['estimated_output_usd']:.3f}",
                 "예상 범위": "Mock $0" if state.mock else
                 f"${p['estimated_total_usd_range'][0]:.3f} ~ ${p['estimated_total_usd_range'][1]:.3f}"}
                for r, p in sorted(est["plans"].items(), reverse=True)]
        st.dataframe(pd.DataFrame(rows), hide_index=True)
        if state.mock:
            st.info("Mock 모드: 비용 0원, OpenAI로 아무것도 보내지 않습니다.")
            agree_label = "Mock 생성(비용 없음)을 승인합니다"
        else:
            st.warning(f"예상 합계 **${est['total_usd']:.3f}** (최대 약 ${est['total_usd_high']:.3f}) · "
                       f"가격 기준 {est['pricing_checked_at']} {est['pricing_url']} · {est['note']}")
            agree_label = (f"예상 비용 ${est['total_usd']:.3f}(최대 약 ${est['total_usd_high']:.3f})를 확인했고, "
                     f"선수 사진 {len(est['plans'])}장을 OpenAI로 보내 캐리커처를 만드는 것을 승인합니다")
        agree = st.checkbox(agree_label, key="cost_ok")
        if st.button("승인하고 생성", key="gen", disabled=not agree, type="primary"):
            try:
                ap.approve_cost(state, D, est, approved=agree)
                editor = MockEditor() if state.mock else OpenAIEditor()
                bar = st.progress(0.0)
                ap.generate_caricatures(state, D, editor, progress=lambda f, m: bar.progress(f, m))
                st.rerun()
            except Exception as e:  # noqa: BLE001
                _show_error(e)
    cols = st.columns(5)
    for i, r in enumerate(state.rows):
        ps = state.players[r.slot]
        with cols[i]:
            st.markdown(f"**{label(state.rows, r)} {r.player}**")
            if ps.generations:
                st.image(str(D / ps.generations[-1] / "caricature.png"), use_container_width=True)
                if ps.approved:
                    st.success("승인됨")
                elif st.button("이 캐리커처 승인", key=f"ap{r.slot}"):
                    try:
                        ap.approve_caricature(state, D, r.slot, confirmed=True)
                        st.rerun()
                    except Exception as e:  # noqa: BLE001
                        _show_error(e)
            else:
                st.caption("아직 생성 전")

# ---------------------------------------------------------------- 5. 카드
st.header("5. 카드 합성 (구단 로고 없이 팀 색·이름)")
if not all(ps.approved for ps in state.players.values()):
    st.caption("5명 캐리커처를 모두 승인하면 열립니다")
else:
    if st.button("카드 합성" if not state.cards_done else "카드 다시 합성", key="cards"):
        try:
            ap.compose_cards(state, D)
            st.rerun()
        except ap.FlowError as e:
            _show_error(e)
    if state.cards_done:
        cols = st.columns(5)
        for i, r in enumerate(state.rows):
            cols[i].image(str(D / state.players[r.slot].card), use_container_width=True, caption=label(state.rows, r))

# ---------------------------------------------------------------- 6. 렌더
st.header("6. 영상 렌더 + CapCut 초안")
if not state.cards_done:
    st.caption("5단계 카드를 합성하면 열립니다")
else:
    rights = st.checkbox("자료 정확성과 이미지·음악 사용 권리를 확인했습니다", value=state.rights_confirmed,
                         key="rights", disabled=state.mock, help="Mock 모드는 시험용이라 확인 없이 _MOCK 으로 만듭니다")
    if st.button("렌더 + CapCut 초안 만들기", key="render", type="primary", disabled=not (rights or state.mock)):
        bar = st.progress(0.0)
        try:
            res = ap.render_all(state, D, rights_confirmed=rights, progress=lambda f, m: bar.progress(min(f, 1.0), m))
            for w in res["warnings"]:
                st.warning(w)
            st.rerun()
        except Exception as e:  # noqa: BLE001
            _show_error(e)
    if state.output and Path(state.output).exists():
        st.video(state.output)
        st.write(f"MP4: `{state.output}`")
        if state.capcut:
            st.write(f"CapCut 초안: `{state.capcut}`")
        if state.credits:
            st.text_area("영상 설명용 사진 출처", state.credits, height=160)
