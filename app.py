"""ShortsMaker Streamlit 화면.

실행: .venv/bin/streamlit run app.py
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import pandas as pd
import streamlit as st

from shortsmaker.capcut_export import DEFAULT_DRAFTS_DIR
from shortsmaker.ffmpeg_builder import FFmpegError
from shortsmaker.lyrics import parse_lrc
from shortsmaker.models import (
    MAX_DURATION, MIN_DURATION, PROJECTS_DIR, ROOT, Project, list_channels, load_project,
    load_template, save_project,
)
from shortsmaker.pipeline import LyricsError, prepare, render
from shortsmaker.validate import ValidationError, pre_render

SAMPLES_DIR = ROOT / "samples"
RANKS = (5, 4, 3, 2, 1)
LYRIC_COLUMNS = ["start", "end", "text"]

st.set_page_config(page_title="ShortsMaker", page_icon="🎬", layout="wide")
ss = st.session_state


# ---------------------------------------------------------------- 상태

def _empty_lyrics() -> pd.DataFrame:
    return pd.DataFrame({"start": pd.Series(dtype=float), "end": pd.Series(dtype=float),
                         "text": pd.Series(dtype=str)})


def _init_state() -> None:
    defaults = {
        "slug": "my_top5", "source_dir": None, "channel": list_channels()[0],
        "title": "", "subtitle": "", "duration": 50.0, "source_note": "",
        "music_file": None, "music_start": 0.0, "lyrics_mode": "직접 입력",
        "lyrics_base": _empty_lyrics(), "lyrics_ver": 0, "lrc_file": None, "rights": False,
        "previews": None, "result": None, "upload_ver": 0, "capcut_dir": str(DEFAULT_DRAFTS_DIR),
    }
    for r in RANKS:
        defaults |= {f"name_{r}": "", f"value_{r}": "", f"desc_{r}": "", f"img_{r}": None}
    for k, v in defaults.items():
        ss.setdefault(k, v)


def _load_into_state(path: Path) -> None:
    project, project_dir = load_project(path)
    ss.slug = project_dir.name
    ss.source_dir = str(project_dir)
    ss.channel = project.channel
    ss.title, ss.subtitle = project.title, project.subtitle
    ss.duration = float(project.duration)
    ss.source_note = project.source_note
    ss.music_file, ss.music_start = project.music, float(project.music_start)
    ss.lrc_file = project.lyrics_lrc
    ss.lyrics_mode = "LRC 파일" if project.lyrics_lrc else "직접 입력"
    ss.lyrics_base = (pd.DataFrame([l.model_dump() for l in project.lyrics], columns=LYRIC_COLUMNS)
                      if project.lyrics else _empty_lyrics())
    ss.lyrics_ver += 1
    ss.upload_ver += 1  # 업로드 위젯 초기화
    ss.rights = project.rights_confirmed
    for it in project.items:
        ss[f"name_{it.rank}"], ss[f"value_{it.rank}"] = it.name, it.value
        ss[f"desc_{it.rank}"], ss[f"img_{it.rank}"] = it.description, it.image
    ss.previews = ss.result = None


def _source_file(name: str | None) -> Path | None:
    if name and ss.source_dir:
        p = Path(ss.source_dir) / name
        return p if p.exists() else None
    return None


def _safe_slug(s: str) -> str:
    s = re.sub(r"[\s/\\:]+", "_", s.strip())
    return re.sub(r"[^\w\-가-힣]", "", s) or "my_top5"


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w.\-가-힣]", "_", Path(name).name)


def _target_dir() -> Path:
    return PROJECTS_DIR / _safe_slug(ss.slug)


def _collect_project(lyrics_df: pd.DataFrame) -> tuple[Project, Path]:
    """화면 입력 → projects/<slug>/ 에 파일 저장 + project.json 작성."""
    target = _target_dir()
    target.mkdir(parents=True, exist_ok=True)

    def place(upload, existing: str | None, stem: str) -> str | None:
        if upload is not None:
            name = f"{stem}{Path(upload.name).suffix.lower()}"
            (target / name).write_bytes(upload.getvalue())
            return name
        src = _source_file(existing)
        if src is None:
            return existing if existing and (target / existing).exists() else None
        if src.resolve().parent != target.resolve():
            shutil.copy2(src, target / src.name)
        return src.name

    items = []
    for r in RANKS:
        image = place(ss.get(f"up_img_{r}_{ss.upload_ver}"), ss[f"img_{r}"], f"img{r}")
        ss[f"img_{r}"] = image
        items.append({"rank": r, "name": ss[f"name_{r}"].strip() or f"{r}위",
                      "value": ss[f"value_{r}"], "description": ss[f"desc_{r}"], "image": image})

    music = place(ss.get(f"up_music_{ss.upload_ver}"), ss.music_file, "music")
    lyrics, lrc = [], None
    if ss.lyrics_mode == "LRC 파일":
        lrc = place(ss.get(f"up_lrc_{ss.upload_ver}"), ss.lrc_file, "lyrics")
    else:
        df = lyrics_df.dropna(subset=["start", "end"])
        df = df[df["text"].fillna("").str.strip() != ""]
        lyrics = [{"start": float(r.start), "end": float(r.end), "text": str(r.text).strip()}
                  for r in df.sort_values("start").itertuples()]

    project = Project.model_validate({
        "channel": ss.channel, "title": ss.title.strip() or "제목 없음", "subtitle": ss.subtitle,
        "duration": ss.duration, "source_note": ss.source_note, "items": items,
        "music": music, "music_start": ss.music_start, "lyrics": lyrics, "lyrics_lrc": lrc,
        "rights_confirmed": ss.rights,
    })
    save_project(project, target)
    ss.music_file, ss.lrc_file = music, lrc
    ss.source_dir = str(target)
    ss.upload_ver += 1  # 저장한 업로드는 다시 쓰지 않도록 위젯 초기화
    return project, target


# ---------------------------------------------------------------- 화면

_init_state()

with st.sidebar:
    st.header("📁 프로젝트")
    found = sorted(PROJECTS_DIR.glob("*/project.json")) + sorted(SAMPLES_DIR.glob("*/project.json"))
    labels = {"": "— 프로젝트 선택 —"} | {str(p): f"{p.parent.parent.name}/{p.parent.name}" for p in found}
    pick = st.selectbox("불러오기", list(labels), format_func=lambda k: labels.get(k, k))
    if st.button("불러오기", disabled=not pick, width="stretch"):
        _load_into_state(Path(pick))
        st.rerun()
    st.text_input("저장 폴더 이름 (projects/…)", key="slug")
    st.caption(f"저장 위치: `{_target_dir().relative_to(ROOT)}`")
    save_clicked = st.button("💾 저장", width="stretch")
    with st.expander("⚙️ 설정"):
        st.text_input("CapCut 초안 폴더", key="capcut_dir",
                      help="CapCut 설정 → 초안 위치. 기본: ~/Movies/CapCut/User Data/Projects/com.lveditor.draft")

st.title("🎬 ShortsMaker — TOP 5 쇼츠 만들기")

# 1. 채널·제목
st.subheader("1. 채널 · 제목")
channels = list_channels()
c1, c2, c3 = st.columns([1, 2, 1])
c1.selectbox("채널", channels, key="channel", format_func=lambda c: load_template(c).name)
c2.text_input("제목", key="title", placeholder="예: KBO 역대 FA 계약 TOP 5")
c3.text_input("부제", key="subtitle", placeholder="예: 총액 기준")

# 2. 항목
st.subheader("2. 5위 → 1위")
cols = st.columns(5)
for col, r in zip(cols, RANKS):
    with col, st.container(border=True):
        st.markdown(f"### {r}위")
        st.text_input("이름", key=f"name_{r}")
        st.text_input("값", key=f"value_{r}", placeholder="예: 80억")
        st.text_area("설명", key=f"desc_{r}", height=80)
        up = st.file_uploader("이미지", type=["png", "jpg", "jpeg", "webp"], key=f"up_img_{r}_{ss.upload_ver}")
        if up is not None:
            st.image(up, width="stretch")
        elif (p := _source_file(ss[f"img_{r}"])) is not None:
            st.image(str(p), width="stretch", caption=p.name)
        else:
            st.caption("이미지 없음 → 대체 이미지 사용")

# 3. 음악·길이
st.subheader("3. 음악 · 길이")
c1, c2, c3 = st.columns([2, 1, 1])
with c1:
    up_music = st.file_uploader("음악 (mp3·wav·m4a)", type=["mp3", "wav", "m4a", "aac"],
                                key=f"up_music_{ss.upload_ver}")
    if up_music is not None:
        st.audio(up_music)
    elif (p := _source_file(ss.music_file)) is not None:
        st.audio(str(p))
        st.caption(p.name)
c2.number_input("음악 시작 위치(초)", min_value=0.0, step=0.5, key="music_start")
c3.slider("영상 길이(초)", MIN_DURATION, MAX_DURATION, step=1.0, key="duration")

# 4. 가사
st.subheader("4. 가사")
st.radio("입력 방식", ["직접 입력", "LRC 파일"], key="lyrics_mode", horizontal=True)
lyrics_df = ss.lyrics_base
if ss.lyrics_mode == "직접 입력":
    lyrics_df = st.data_editor(
        ss.lyrics_base, num_rows="dynamic", width="stretch", key=f"lyrics_editor_{ss.lyrics_ver}",
        column_config={
            "start": st.column_config.NumberColumn("시작(초)", min_value=0.0, step=0.1, format="%.2f"),
            "end": st.column_config.NumberColumn("끝(초)", min_value=0.0, step=0.1, format="%.2f"),
            "text": st.column_config.TextColumn("가사", width="large"),
        },
    )
else:
    up_lrc = st.file_uploader("LRC 파일", type=["lrc", "txt"], key=f"up_lrc_{ss.upload_ver}")
    lrc_text = None
    if up_lrc is not None:
        lrc_text = up_lrc.getvalue().decode("utf-8-sig")
    elif (p := _source_file(ss.lrc_file)) is not None:
        lrc_text = p.read_text(encoding="utf-8-sig")
        st.caption(p.name)
    if lrc_text:
        parsed = parse_lrc(lrc_text, ss.duration)
        st.dataframe(pd.DataFrame([l.model_dump() for l in parsed], columns=LYRIC_COLUMNS),
                     width="stretch", hide_index=True)

# 5. 출처·권리
st.subheader("5. 출처 · 권리 확인")
st.text_input("출처", key="source_note", placeholder="예: 출처: KBO 공식 기록 (2026.10 기준)")
st.checkbox("자료 정확성과 이미지·음악 사용 권리를 확인했습니다", key="rights")

# 6. 미리보기·제작
st.subheader("6. 미리보기 · 제작")
b1, b2, b3 = st.columns(3)
preview_clicked = b1.button("🖼️ 장면 미리보기", width="stretch")
mp4_clicked = b2.button("🎬 MP4로 완성", type="primary", disabled=not ss.rights, width="stretch")
capcut_clicked = b3.button("✂️ CapCut 초안으로 보내기", disabled=not ss.rights, width="stretch")
if not ss.rights:
    st.caption("권리 확인을 체크해야 제작 버튼이 활성화됩니다.")


def _collect_or_error() -> tuple[Project, Path] | None:
    try:
        return _collect_project(lyrics_df)
    except Exception as e:  # noqa: BLE001 — 입력 오류는 화면에 그대로 보여준다
        st.error(f"입력 오류: {e}")
        return None


def _show_report(project: Project, project_dir: Path) -> bool:
    rep = pre_render(project, project_dir, require_rights=False)
    for e in rep.errors:
        st.error(e)
    for w in rep.warnings:
        st.warning(w)
    return rep.ok


if save_clicked and (got := _collect_or_error()):
    st.success(f"저장했습니다: {got[1] / 'project.json'}")

if preview_clicked and (got := _collect_or_error()):
    project, project_dir = got
    if _show_report(project, project_dir):
        with st.spinner("장면 이미지 생성 중…"):
            t, scenes, built = prepare(project, project_dir)
        ss.previews = [(s.scene.label, f"{s.scene.start:.1f}~{s.scene.end:.1f}초", str(s.preview))
                       for s in built.scenes]

if mp4_clicked and (got := _collect_or_error()):
    project, project_dir = got
    bar = st.progress(0.0, text="시작")
    try:
        res = render(project, project_dir, progress=lambda f, m: bar.progress(min(f, 1.0), text=m))
        ss.result = {"output": str(res.output), "warnings": res.warnings, "subtitle_mode": res.subtitle_mode}
    except ValidationError as e:
        bar.empty()
        for err in e.report.errors:
            st.error(err)
    except LyricsError as e:
        bar.empty()
        st.error(f"가사 오류\n\n{e}")
    except FFmpegError as e:
        bar.empty()
        st.error("FFmpeg 실패")
        st.code(e.command_line, language="bash")
        st.code(e.stderr_tail)

if capcut_clicked and (got := _collect_or_error()):
    project, project_dir = got
    try:
        # 비공식 라이브러리 — import·생성 실패가 MP4 기능에 영향을 주지 않도록 여기서만 불러온다
        from shortsmaker.capcut_export import export_draft
        with st.spinner("CapCut 초안 만드는 중…"):
            draft = export_draft(project, project_dir, root=Path(ss.capcut_dir).expanduser())
        st.success(f"CapCut 초안을 만들었습니다: **{draft.name}**\n\nCapCut을 열어 홈 화면 목록에서 확인하세요.\n\n`{draft.path}`")
        for w in draft.warnings:
            st.warning(w)
    except Exception as e:  # noqa: BLE001
        st.error(f"CapCut 초안 생성 실패: {e}")
        try:
            from shortsmaker.capcut_export import export_manual_kit
            kit = export_manual_kit(project, project_dir)
            st.info(f"대신 CapCut에 직접 가져올 재료(장면 PNG·음악·가사 SRT)를 만들었습니다:\n\n`{kit}`")
        except Exception as e2:  # noqa: BLE001
            st.error(f"가져오기 재료 생성도 실패: {e2}")

if ss.previews:
    tl_cols = st.columns(len(ss.previews))
    for col, (label, span, path) in zip(tl_cols, ss.previews):
        col.image(path, caption=f"{label} · {span}", width="stretch")

if ss.result:
    st.success(f"완성! `{ss.result['output']}` (가사 자막: {ss.result['subtitle_mode']})")
    for w in ss.result["warnings"]:
        st.warning(w)
    out = Path(ss.result["output"])
    if out.exists():
        v1, _ = st.columns([1, 2])
        v1.video(str(out))
        v1.download_button("⬇️ MP4 다운로드", out.read_bytes(), file_name=out.name, mime="video/mp4",
                           width="stretch")
