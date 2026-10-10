"""노래로 자동 제작 — 곡 하나로 yagu_v2(카드형·스티커형) 영상 + CapCut 초안.

단계마다 앞 단계가 끝나야 다음 버튼이 열린다. 진행 상태는 projects/<이름>/auto.json 에 저장된다.
캐리커처는 수동 생성(무료, 기본) / OpenAI(유료, 비용 승인) / Mock(시험) 중에서 프로젝트마다 고른다.
"""
from __future__ import annotations

import os
import re
import tempfile
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from shortsmaker import auto_pipeline as ap
from shortsmaker import manual_prompt as mp
from shortsmaker.bg_remove import rembg_available
from shortsmaker.models import PROJECTS_DIR, load_project
from shortsmaker.player_assets import MockEditor, OpenAIEditor
from shortsmaker.rank_extract import RankRow, label, missing_fields

st.set_page_config(page_title="노래로 자동 제작", layout="wide")
st.title("노래로 자동 제작 (yagu_v2)")

ROOT = Path(os.environ.get("SHORTSMAKER_PROJECTS_DIR", PROJECTS_DIR))
has_key = bool(os.environ.get("OPENAI_API_KEY"))
ss = st.session_state

with st.sidebar:
    st.subheader("캐리커처 생성")
    st.markdown("- **수동(무료, 기본)**: 프롬프트를 ChatGPT·Gemini 에 붙여 넣고, 내려받은 그림을 고름\n"
                "- **OpenAI(유료)**: 예상 비용을 보고 승인하면 자동 생성 (기본 품질 medium)\n"
                "- **Mock**: 비용 없이 흐름 시험 (결과에 'MOCK 시험용')")
    if has_key:
        st.success("OPENAI_API_KEY 설정됨 (값은 표시하지 않음)")
    else:
        st.warning("OPENAI_API_KEY 없음 → OpenAI 모드는 고를 수 없음")
        st.caption("설정: `~/.zshrc` 에 `export OPENAI_API_KEY=\"sk-...\"` 를 넣고 새 터미널에서 다시 실행")


def _slugify(text: str) -> str:
    return re.sub(r"[^\w가-힣]+", "_", text).strip("_")[:50] or "auto_project"


def _err(e: Exception) -> None:
    st.error(f"{e}")


# ---------------------------------------------------------------- 프로젝트
existing = sorted(p.parent.name for p in ROOT.glob(f"*/{ap.STATE_FILE}")) if ROOT.exists() else []
tab_new, tab_load = st.tabs(["새로 만들기", "이어서 하기"])
with tab_new:
    with st.form("new"):
        song = st.file_uploader("곡 파일 (가사가 들어 있는 m4a·mp3)", type=["m4a", "mp3", "wav"], key="song")
        title = st.text_area("영상 제목 (2줄: 줄바꿈으로 나눔)", "야구선수 한 명에 152억?!\nKBO 역대 FA 몸값 TOP 5", height=80)
        slug = st.text_input("프로젝트 이름 (영문·한글·숫자)", "")
        tpl = st.radio("영상 모드", list(ap.TEMPLATES), format_func=ap.TEMPLATES.get, horizontal=True, key="tpl",
                       help="카드형: 정보가 다 든 카드 / 스티커형: 글씨 없는 치비 스티커 + 빨간 기록 줄")
        briefs = sorted(p.parent.name for p in (ROOT / "_briefs").glob("*/brief.json"))
        brief = st.selectbox("가사 브리프 연결 (가사 만들기에서 확정한 데이터·출처를 이어받음)", ["(없음)"] + briefs,
                             key="brief_pick")
        modes = [m for m in ap.GEN_MODES if m != "openai" or has_key]
        mode = st.radio("캐리커처 생성", modes, format_func=ap.GEN_MODES.get, key="mode")
        go = st.form_submit_button("시작")
    if go:
        if song is None:
            st.error("곡 파일을 올려 주세요")
        else:
            name = _slugify(slug or Path(song.name).stem)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / song.name
                path.write_bytes(song.getvalue())
                try:
                    state, d = ap.start_from_song(path, name, title=title.strip(), mode=mode, template=tpl, root=ROOT,
                                                  brief=None if brief == "(없음)" else brief)
                    ss.auto_slug = name
                    st.success(f"프로젝트 생성: {d}")
                except ap.FlowError as e:
                    _err(e)
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
sticker = ap._style_name(state) == "sticker"
st.info(f"프로젝트 **{state.slug}** · {ap.TEMPLATES[state.template]} · {ap.GEN_MODES[state.gen_mode]} · "
        f"영상 {state.duration:g}초")

# ---------------------------------------------------------------- 1. 가사
st.header("1. 가사 불러오기 → 자동 맞추기")
if st.button("가사 자동 맞추기 실행" if not state.aligned else "다시 맞추기", key="align"):
    bar = st.progress(0.0)
    try:
        with st.spinner("보컬 분리·가사 정렬 중 (처음엔 모델 다운로드로 오래 걸림)"):
            ap.align(state, D, progress=lambda f, m: bar.progress(min(f, 1.0), m))
        st.rerun()
    except Exception as e:  # noqa: BLE001
        _err(e)
if state.aligned:
    ends = {"lyrics": "노래가 끝나면 (마지막 가사 + 2초)", "music": "반주 끝까지 (최대 90초)"}
    end = st.radio("영상 끝", list(ends), format_func=ends.get, index=list(ends).index(state.end_mode),
                   horizontal=True, key="end_mode")
    if end != state.end_mode:
        ap.set_end_mode(state, D, end)
        st.rerun()
    st.caption(f"곡 {state.music_length:g}초 → 영상 {state.duration:g}초")
    lyr = load_project(D / "project.json")[0].lyrics
    st.dataframe(pd.DataFrame([{"시작": l.start, "끝": l.end, "가사": l.text} for l in lyr]), hide_index=True,
                 height=200)

# ---------------------------------------------------------------- 2. 표
st.header("2. 순위 표 확인·수정")
if not state.aligned:
    st.caption("1단계를 먼저 끝내세요")
else:
    st.caption("가사에서 뽑은 초안입니다. 확정해야 다음 단계가 열립니다. 공동 순위는 같은 숫자를 두 행에 적으면 "
               "'공동 2위'로 표시되고 다음 순위(3위)는 비워 둡니다. 장면 설명은 캐리커처 그림에 쓰입니다"
               + (", 빨간 기록 줄은 자막 아래에 1~2줄로 나옵니다 (줄바꿈은 ' / ')." if sticker else "."))
    df = pd.DataFrame([{"순위": r.rank, "표시": label(state.rows, r), "팀": r.team, "선수": r.player,
                        "기록": r.record, "연도": r.year, "장면 설명": r.scene,
                        "기록 줄(빨간)": r.stat_line.replace("\n", " / "),
                        "확인 필요": ", ".join(missing_fields(r)) + (" (가사에 없음 — 직접 입력)" if missing_fields(r) else ""),
                        "근거 가사": r.lyric} for r in state.rows])
    if not sticker:
        df = df.drop(columns=["기록 줄(빨간)"])
    empty = [f"{label(state.rows, r)}: {', '.join(missing_fields(r))}" for r in state.rows if missing_fields(r)]
    if empty and not state.table_confirmed:
        st.warning("가사에 없어 비워 둔 칸이 있습니다 (별명만 나오는 팀 등은 추측하지 않습니다). 표에서 직접 채우세요 — "
                   + " / ".join(empty))
    edited = st.data_editor(df, hide_index=True, disabled=["표시", "확인 필요", "근거 가사"], key="table",
                            num_rows="dynamic", column_config={
                                "순위": st.column_config.NumberColumn(min_value=1, max_value=5, step=1, required=True),
                                "기록": st.column_config.TextColumn(help="카드에 들어갈 표기 (예: 16연패)"),
                                "장면 설명": st.column_config.TextColumn(width="large")})
    title2 = st.text_area("영상 제목", state.title, key="title2", height=80)
    merge = st.checkbox("자막: 'N위, 팀' 줄과 기록 줄을 한 자막으로 합치기", value=state.merge_record_lines, key="merge")
    if st.button("표 확정", key="confirm", type="primary"):
        starts = {r.lyric: r.start for r in state.rows if r.lyric}

        def _s(v) -> str:
            return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()

        rows = [RankRow(rank=int(x["순위"]), team=_s(x["팀"]), player=_s(x["선수"]), record=_s(x["기록"]),
                        year=_s(x["연도"]), lyric=_s(x["근거 가사"]), start=starts.get(_s(x["근거 가사"])),
                        scene=_s(x["장면 설명"]), stat_line=_s(x.get("기록 줄(빨간)")).replace(" / ", "\n"))
                for x in edited.to_dict("records") if x.get("순위") is not None and not pd.isna(x["순위"])]
        try:
            ap.confirm_table(state, D, rows, title=title2.strip(), merge_record_lines=merge)
            st.rerun()
        except ap.FlowError as e:
            _err(e)
    if state.table_confirmed:
        st.success("표 확정됨 (수정 후 다시 '표 확정'을 누르면 갱신)")
        st.subheader("데이터 출처")
        a1, a2 = st.columns(2)
        dsn = a1.text_input("출처 이름", state.data_source_name, key="dsn")
        dsu = a2.text_input("출처 URL", state.data_source_url, key="dsu")
        if st.button("출처 저장", key="ds_save") and (dsn, dsu) != (state.data_source_name, state.data_source_url):
            ap.set_data_source(state, D, dsn, dsu)
            st.rerun()

        st.subheader("가사 검사 (곡 원문 vs 확정 데이터·이전 영상)")
        rep_ = state.lyrics_report or {}
        if rep_.get("passed"):
            st.success("통과: 나열형 아님 · 반전·해설·비교 있음 · 숫자·이름 일치")
        for e in rep_.get("errors", []):
            st.error(e)
        for w in rep_.get("warnings", []):
            st.warning(w)
        st.caption("이미 만든 곡이라 가사를 고칠 수는 없습니다. 결과를 보고 확인하세요 (다음 곡은 '가사 만들기'에서 검사를 통과한 가사로).")
        if not state.lyrics_ack and st.button("가사 검사 결과를 확인했습니다", key="lyr_ack"):
            ap.ack_lyrics(state, D)
            st.rerun()
        elif state.lyrics_ack:
            st.caption("✅ 가사 검사 결과 확인함")

        with st.expander("자막 표기 고치기 (시간은 그대로)"):
            proj = load_project(D / "project.json")[0]
            sub = st.data_editor(pd.DataFrame([{"시작": l.start, "자막": l.text} for l in proj.lyrics]), hide_index=True,
                                 disabled=["시작"], key="subs")
            if st.button("자막 저장", key="subs_save"):
                try:
                    ap.edit_subtitles(state, D, [str(x) for x in sub["자막"].tolist()])
                    st.rerun()
                except ap.FlowError as e:
                    _err(e)

# ---------------------------------------------------------------- 3. 사진
st.header("3. 선수 사진 (위키미디어 공용)")
if not state.table_confirmed:
    st.caption("2단계 표를 확정하면 열립니다")
else:
    for r in state.rows:
        k = r.slot
        ps = state.players[k]
        ok = ap.photo_ready(state, k)
        with st.expander(f"{label(state.rows, r)} {r.player} ({r.team})" + (" ✅" if ok else ""), expanded=not ok):
            if ps.folder:
                orig = next((D / ps.folder).glob("original.*"), None)
                if orig:
                    st.image(str(orig), width=180, caption=f"사용 중 ({ps.source})")
                continue
            if ps.source == "none":
                st.caption("사진 없이 진행 (이름·장면 설명만으로 그림)")
                continue
            q = st.text_input("검색어 (직접 고칠 수 있음, 자동으로 넓히지 않음)", ps.query, key=f"q{k}")
            if st.button("Commons 검색", key=f"s{k}"):
                try:
                    ap.search(state, D, k, q)
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    _err(e)
            if ps.photos:
                cols = st.columns(4)
                for i, ph in enumerate(ps.photos[:8]):
                    with cols[i % 4]:
                        st.image(ph.thumbnail, use_container_width=True)
                        st.caption(f"{ph.license} · {ph.author[:30]}"
                                   + (" · 상업 이용 후보" if ph.commercial_candidate else " · ⚠ 권한 불명"))
                        st.caption(ph.warning)
                        idc = st.checkbox("동일인 확인", key=f"id{k}_{i}")
                        rgt = st.checkbox("이용·변형 권한 확인", key=f"rg{k}_{i}")
                        if st.button("이 사진 사용", key=f"use{k}_{i}", disabled=not (idc and rgt)):
                            try:
                                ap.choose_photo(state, D, k, i, identity_confirmed=idc, rights_confirmed=rgt)
                                st.rerun()
                            except Exception as e:  # noqa: BLE001
                                _err(e)
            elif ps.query:
                st.info("검색 결과가 없거나 아직 검색하지 않았습니다. 사진이 없으면 아래에 이미지를 올려 주세요.")
            up = st.file_uploader("직접 이미지 올리기 (사진이 없을 때)", type=["png", "jpg", "jpeg"], key=f"up{k}")
            upok = st.checkbox("올린 이미지의 이용·변형 권한을 확인했습니다", key=f"upok{k}")
            if st.button("올린 이미지 사용", key=f"upuse{k}", disabled=up is None or not upok):
                try:
                    ap.upload_photo(state, D, k, up.getvalue(), rights_confirmed=upok)
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    _err(e)
            if state.gen_mode == "manual" and st.button("사진 없이 진행", key=f"skip{k}"):
                ap.skip_photo(state, D, k)
                st.rerun()
            if state.mock and st.button("Mock 자리표시 실루엣 사용 (시험용)", key=f"ph{k}"):
                ap.placeholder_photo(state, D, k)
                st.rerun()

# ---------------------------------------------------------------- 4. 캐리커처
st.header("4. 캐리커처" + (" — 수동 생성 (무료)" if state.gen_mode == "manual" else " (생성 전 비용 확인·승인)"))
ready = state.table_confirmed and all(ap.photo_ready(state, s) for s in state.players)
if not ready:
    st.caption("5명 모두 사진이 준비되면(수동 모드는 '사진 없이 진행'도 가능) 열립니다")
elif state.gen_mode == "manual":
    st.caption("① '프롬프트 복사' → ChatGPT·Gemini 에 붙여 넣고, '폴더 열기'의 참고 그림·선수 사진을 첨부해 생성 "
               "② 그림을 내려받기 ③ 아래 '다운로드 폴더 지켜보기'에 뜬 그림에서 순위를 골라 '이 순위에 사용' "
               "(배경을 지워 넣습니다)")
    bg_methods = ["auto"] + (["rembg"] if rembg_available() else [])
    method = st.selectbox("배경 제거", bg_methods, key="bg",
                          help="auto: 투명이면 그대로, 아니면 테두리에 이어진 단색 배경 제거. rembg 는 설치돼 있을 때만")
    for r in state.rows:
        k = r.slot
        ps = state.players[k]
        with st.expander(f"{label(state.rows, r)} {r.player}" + (" ✅" if ps.approved else ""), expanded=not ps.approved):
            folder, prompt = ap.manual_prepare(state, D, k)
            st.code(prompt, language=None)
            c1, c2 = st.columns(2)
            if c1.button("프롬프트 복사", key=f"cp{k}"):
                mp.copy_to_clipboard(prompt)
                st.toast("클립보드에 복사했습니다")
            if c2.button("폴더 열기", key=f"op{k}"):
                mp.open_folder(folder)
            if ps.approved:
                st.image(str(D / ps.approved), width=220, caption="사용 중 (배경 제거됨)")
            upm = st.file_uploader("또는 그림 파일 직접 올리기", type=["png", "jpg", "jpeg", "webp"], key=f"mu{k}")
            if upm is not None and st.button("올린 그림 사용", key=f"muse{k}"):
                ap.use_manual_image(state, D, k, upm.getvalue(), source_name=upm.name, method=method)
                st.rerun()

    with st.expander("인트로 캐릭터 (선택 — 없으면 채널 로고)" + (" ✅" if state.intro_image else "")):
        st.code(ap.intro_prompt(state), language=None)
        if st.button("인트로 프롬프트 복사", key="cpi"):
            mp.copy_to_clipboard(ap.intro_prompt(state))
        if state.intro_image:
            st.image(str(D / state.intro_image), width=200)

    watching = st.toggle("다운로드 폴더 지켜보기 (~/Downloads)", value=state.watch_since is not None, key="watch")
    if watching and state.watch_since is None:
        state.watch_since = time.time()
        ap.save_state(state, D)
    elif not watching and state.watch_since is not None:
        state.watch_since = None
        ap.save_state(state, D)

    @st.fragment(run_every=2 if watching else None)
    def _watcher() -> None:
        cur = ap.load_state(D)
        if cur.watch_since is None:
            return
        imgs = mp.new_images(cur.watch_since)
        st.caption(f"지켜보기 시작 이후 새 이미지 {len(imgs)}장 (2초마다 확인)")
        targets = {f"s{r.slot}": f"{label(cur.rows, r)} {r.player}" for r in cur.rows}
        targets["intro"] = "인트로 캐릭터"
        cols = st.columns(3)
        for i, p in enumerate(imgs[:6]):
            with cols[i % 3]:
                st.image(str(p), use_container_width=True, caption=p.name)
                to = st.selectbox("넣을 곳", list(targets), format_func=targets.get, key=f"to{p.name}")
                if st.button("이 순위에 사용", key=f"use_{p.name}"):
                    try:
                        if to == "intro":
                            ap.set_intro_image(cur, D, p.read_bytes(), method=method)
                        else:
                            ap.use_manual_image(cur, D, int(to[1:]), p.read_bytes(), source_name=p.name, method=method)
                        st.rerun(scope="app")
                    except Exception as e:  # noqa: BLE001
                        _err(e)

    _watcher()
else:
    if state.gen_mode == "openai":
        q = st.selectbox("품질", ["low", "medium", "high"], index=["low", "medium", "high"].index(state.style.quality),
                         key="quality")
        if q != state.style.quality:
            state.style = state.style.model_copy(update={"quality": q})
            ap.save_state(state, D)
    est = ap.cost_estimate(state, D)
    if est["plans"]:
        rows = [{"순위": ap.row_label(state, r), "선수": ap._row(state, r).player, "모델": est["model"],
                 "품질": est["quality"],
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
                _err(e)
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
                        _err(e)
            else:
                st.caption("아직 생성 전")

# ---------------------------------------------------------------- 5. 확인·연출·합성
st.header("5. 로고·글씨 확인 → 연출 고르기 → " + ("스티커 정리" if sticker else "카드 합성"))
if not all(ps.approved for ps in state.players.values()):
    st.caption("5명 캐리커처가 모두 준비되면 열립니다")
else:
    st.subheader("① 그림에 구단 로고·유니폼 글씨가 없는지 확인")
    cols = st.columns(5)
    for i, r in enumerate(state.rows):
        ps = state.players[r.slot]
        with cols[i]:
            st.image(str(D / ps.approved), use_container_width=True, caption=label(state.rows, r))
            ok = st.checkbox("로고·글씨 없음", value=state.no_logo.get(r.slot, False), key=f"nl{r.slot}")
            if ok != state.no_logo.get(r.slot, False):
                ap.confirm_no_logo(state, D, r.slot, ok)
                st.rerun()

    st.subheader("② 연출 (최근 5편과 2요소 이상 달라야 함, 제목 파랑·로고는 고정)")
    from shortsmaker import variation as var
    cur = var.Variation.model_validate(state.variation) if state.variation else ap.suggest_variation(state, D)
    v1, v2, v3, v4 = st.columns(4)
    tr = v1.selectbox("장면 전환", list(var.TRANSITIONS), index=list(var.TRANSITIONS).index(cur.transition),
                      format_func=var.TRANSITIONS.get, key="v_tr")
    ac = v2.selectbox("강조색 (기록 줄·순위)", list(var.ACCENTS), index=list(var.ACCENTS).index(cur.accent),
                      format_func=lambda k: var.ACCENTS[k]["label"], key="v_ac")
    sb = v3.selectbox("자막 등장", list(var.SUBTITLES), index=list(var.SUBTITLES).index(cur.subtitle),
                      format_func=var.SUBTITLES.get, key="v_sb")
    it = v4.selectbox("인트로", list(var.INTROS), index=list(var.INTROS).index(cur.intro),
                      format_func=var.INTROS.get, key="v_it")
    pick = var.Variation(transition=tr, accent=ac, subtitle=sb, intro=it)
    bad = var.conflicts(pick, D.parent, exclude=state.slug)
    for b_ in bad:
        st.warning(b_)
    recent = var.recent(D.parent, exclude=state.slug)
    st.caption("최근 영상 연출: " + (" / ".join(r_.describe() for r_ in recent) if recent else "기록 없음"))
    if st.button("이 연출로 정하기" if not state.variation else "연출 바꾸기", key="v_set", disabled=bool(bad)):
        try:
            ap.choose_variation(state, D, pick)
            st.rerun()
        except ap.FlowError as e:
            _err(e)
    if state.variation:
        st.success("연출: " + var.Variation.model_validate(state.variation).describe())

    st.subheader("③ " + ("스티커 정리" if sticker else "카드 합성 (구단 로고 없이 팀 색·이름, 순위 글자는 강조색)"))
    if st.button("합성" if not state.cards_done else "다시 합성", key="cards", disabled=state.variation is None):
        try:
            ap.compose_cards(state, D)
            st.rerun()
        except ap.FlowError as e:
            _err(e)
    if state.cards_done:
        cols = st.columns(5)
        for i, r in enumerate(state.rows):
            cols[i].image(str(D / state.players[r.slot].card), use_container_width=True, caption=label(state.rows, r))

# ---------------------------------------------------------------- 6. 렌더
st.header("6. 권리 체크 → 렌더 + CapCut 초안")
if not state.cards_done:
    st.caption("5단계를 끝내면 열립니다")
else:
    st.subheader("Suno 곡")
    info = state.suno
    st.caption("곡 파일 태그: " + (f"Suno 곡 id {info.get('id', '없음')} · 생성 {info.get('created', '알 수 없음')}"
                                  if info.get("made_with_suno") else "Suno 표시를 찾지 못함"))
    q1, q2 = st.columns([1, 2])
    paid = q1.checkbox("Suno 유료 플랜으로 생성한 곡입니다", value=state.suno_paid, key="suno_paid", disabled=state.mock)
    plan = q2.text_input("플랜 이름 (예: Pro, Premier)", state.suno_plan, key="suno_plan", disabled=state.mock)
    if (paid, plan.strip()) != (state.suno_paid, state.suno_plan) and st.button("Suno 확인 저장", key="suno_save"):
        ap.set_suno(state, D, paid=paid, plan=plan)
        st.rerun()

    st.subheader("수익화용 렌더 체크리스트")
    items = ap.checklist(state, D)
    for c in items:
        (st.success if c["ok"] else st.error)(f"{'✅' if c['ok'] else '❌'} {c['label']} — {c['detail']}")
    all_ok = all(c["ok"] for c in items)
    rights = st.checkbox("자료 정확성과 이미지·음악 사용 권리를 최종 확인했습니다", value=state.rights_confirmed,
                         key="rights", disabled=state.mock or not all_ok)
    r1, r2 = st.columns(2)
    bar = st.progress(0.0)
    for col, kind, lab, ok in ((r1, "draft", "시험 렌더 (_DRAFT, 언제나 가능)", True),
                               (r2, "monetize", "수익화용 렌더", all_ok and rights)):
        if col.button(lab, key=f"render_{kind}", type="primary" if kind == "monetize" else "secondary", disabled=not ok):
            try:
                res = ap.render_all(state, D, rights_confirmed=rights, kind=kind,
                                    progress=lambda f, m: bar.progress(min(f, 1.0), m))
                for w in res["warnings"]:
                    st.warning(w)
                st.rerun()
            except Exception as e:  # noqa: BLE001
                _err(e)
    if not all_ok:
        st.caption("❌ 항목이 있으면 수익화용 렌더는 막힙니다 (시험 렌더는 가능)")
    if state.output and Path(state.output).exists():
        st.video(state.output)
        st.write(f"MP4 ({'수익화용' if state.render_kind == 'monetize' else '시험'}): `{state.output}`")
        if state.capcut:
            st.write(f"CapCut 초안: `{state.capcut}`")

# ---------------------------------------------------------------- 7. 업로드 준비
st.header("7. 업로드 준비 (제목·설명·태그 초안)")
if not state.output:
    st.caption("렌더하면 열립니다")
else:
    if st.button("초안 만들기" if not state.upload else "다시 만들기", key="upload"):
        ap.make_upload(state, D)
        st.rerun()
    if state.upload:
        up = state.upload
        for i, t_ in enumerate(up["titles"]):
            c_a, c_b = st.columns([5, 1])
            c_a.write(f"제목 후보 {i + 1}: **{t_}**")
            if c_b.button("복사", key=f"cp_t{i}"):
                mp.copy_to_clipboard(t_)
        st.text_area("설명", up["description"], height=260, key="desc_view")
        if st.button("설명 복사", key="cp_desc"):
            mp.copy_to_clipboard(up["description"])
        st.text_input("태그", ", ".join(up["tags"]), key="tags_view")
        if st.button("태그 복사", key="cp_tags"):
            mp.copy_to_clipboard(", ".join(up["tags"]))
        st.caption(f"저장: {D / 'upload.txt'} · 업로드는 직접 하세요")

with st.expander("제작 기록 (production_log.json)"):
    from shortsmaker import production_log as plog
    st.json(plog.read_log(D), expanded=False)
