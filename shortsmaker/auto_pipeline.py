"""노래로 자동 제작: 곡 하나 → yagu_v2 영상 + CapCut 초안.

1. 곡 등록 → 가사 불러오기·자동 맞추기
2. 가사 → 5위~1위 표 초안 → 사용자 확인·수정 (확정해야 다음 단계)
3. 선수별 위키미디어 공용 사진 검색 (라이선스 표시) → 없으면 사용자 이미지
4. AI 캐리커처: 예상 비용을 보여주고 사용자가 승인한 요청만 실행 → 결과 승인
5. 카드 합성 (구단 로고 없이 팀 색·이름)
6. yagu_v2 렌더 + CapCut 초안

진행 상태는 projects/<slug>/auto.json 에 저장해 중간에 끊겨도 이어서 할 수 있다.
API 키가 없으면 Mock 모드로 전체 흐름을 시험한다 (Mock 결과에는 'MOCK 시험용' 표시, 출력 이름에 _MOCK).
"""
from __future__ import annotations

import io
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

from PIL import Image, ImageDraw
from pydantic import BaseModel, Field

from . import production_log as plog
from . import upload as upl
from .bg_remove import remove_background
from .lyrics_check import check_lyrics_text, history_lyrics
from .variation import ACCENTS, Variation, conflicts, record as record_variation, suggest
from .card_compose import FONT_EXTRA, compose_card, save_card
from .manual_prompt import build_prompt, default_scene, manual_dir, prepare_folder
from .lyrics_align import clean_lyrics_text, embedded_lyrics
from .models import MAX_DURATION, MIN_DURATION, OUTPUT_DIR, PROJECTS_DIR, LyricLine, Project, load_project, \
    load_template, save_project
from .player_assets import (Photo, Player, Style, approve_image, digest, download_photo, generate,
                            generation_plan, search_photos, validate_image, write_json_new)
from .rank_extract import (RankRow, assign_slots, column_problems, display_text, extract_ranks, label, rows_complete,
                           team_colors)
from .validate import media_duration

CHANNEL = "yagu_v2"  # 기본 템플릿 (카드형). 스티커형은 "yagu_v2_sticker"
TEMPLATES = {"yagu_v2": "카드형", "yagu_v2_sticker": "스티커형"}
GEN_MODES = {"manual": "수동 생성 (무료, ChatGPT·Gemini)", "openai": "OpenAI 자동 생성 (유료)", "mock": "Mock 시험 (무료)"}
STATE_FILE = "auto.json"
END_TAIL = 2.0  # 마지막 가사가 끝난 뒤 남기는 시간(초). 이 안에서 음악이 페이드아웃된다
ProgressFn = Callable[[float, str], None]


class FlowError(ValueError):
    """순서를 건너뛰었거나 필요한 확인이 빠졌다."""


class PlayerState(BaseModel):
    query: str = ""
    photos: list[Photo] = Field(default_factory=list)
    folder: str | None = None          # 프로젝트 폴더 기준 (players/player_r5)
    source: str | None = None          # "commons" | "upload" | "placeholder"
    generations: list[str] = Field(default_factory=list)
    approved: str | None = None        # 승인한 캐리커처 (프로젝트 폴더 기준)
    card: str | None = None


class CostApproval(BaseModel):
    plan_ids: dict[int, str]           # 자리(slot) → 승인한 생성 계획 id (사진·프롬프트·비용이 바뀌면 id 가 바뀜)
    total_usd: float
    approved_at: str


class AutoState(BaseModel):
    slug: str
    title: str
    music: str
    duration: float
    music_length: float = 0.0
    template: str = CHANNEL
    gen_mode: Literal["manual", "openai", "mock"] = "manual"
    intro_image: str | None = None  # 인트로 캐릭터 (프로젝트 폴더 기준, 배경 제거됨)
    watch_since: float | None = None  # 다운로드 폴더 지켜보기 시작 시각
    end_mode: Literal["lyrics", "music"] = "lyrics"  # 마지막 가사 + 2초 (기본) / 반주 끝까지
    mock: bool = True
    aligned: bool = False
    rows: list[RankRow] = Field(default_factory=list)
    table_confirmed: bool = False
    merge_record_lines: bool = True
    players: dict[int, PlayerState] = Field(default_factory=lambda: {s: PlayerState() for s in (5, 4, 3, 2, 1)})  # 자리(slot)별
    style: Style = Field(default_factory=Style)
    cost_approval: CostApproval | None = None
    cards_done: bool = False
    rights_confirmed: bool = False
    output: str | None = None
    capcut: str | None = None
    credits: str | None = None
    # 수익화 대비 (진정성·권리)
    brief: str | None = None               # 연결한 가사 브리프 (projects/_briefs/<이름>)
    data_source_name: str = ""
    data_source_url: str = ""
    rows_draft: list[dict] = Field(default_factory=list)   # 가사에서 자동 추출한 표 (사람 수정 내역 비교용)
    lyrics_report: dict | None = None
    lyrics_ack: bool = False               # 가사 검사 결과를 사람이 확인함
    variation: dict | None = None
    hook: str = ""                         # 후킹 문구 (첫 0~2초)
    bonus: dict | None = None              # 엔딩 보너스 (models.Bonus)
    gauge_base: str = ""                   # 게이지 기준선 ('50홈런')
    no_logo: dict[int, bool] = Field(default_factory=dict)  # 자리별 '로고·유니폼 글씨 없음' 확인
    suno: dict = Field(default_factory=dict)  # 곡 파일 태그의 Suno 정보 (id, created)
    suno_paid: bool = False
    suno_plan: str = ""
    render_kind: str | None = None         # draft | monetize
    upload: dict | None = None


def suno_info(song: Path) -> dict:
    """Suno 곡 파일 태그(comment: 'made with suno; created=…; id=…')에서 생성 정보."""
    import re
    import subprocess
    proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", str(song)],
                          capture_output=True, text=True)
    tags = json.loads(proc.stdout or "{}").get("format", {}).get("tags", {}) if proc.returncode == 0 else {}
    comment = next((v for k, v in tags.items() if k.lower() == "comment"), "")
    out = {"made_with_suno": "suno" in comment.lower()}
    for key in ("created", "id"):
        m = re.search(rf"{key}=([^;\s]+)", comment)
        if m:
            out[key] = m.group(1)
    return out


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- 상태 저장

def project_dir(slug: str, root: Path = PROJECTS_DIR) -> Path:
    return root / slug


def save_state(state: AutoState, d: Path) -> None:
    (d / STATE_FILE).write_text(state.model_dump_json(indent=2), encoding="utf-8")


def load_state(d: Path) -> AutoState:
    return AutoState.model_validate_json((d / STATE_FILE).read_text(encoding="utf-8"))


def _project(state: AutoState, d: Path) -> Project:
    return load_project(d / "project.json")[0]


# ---------------------------------------------------------------- 1. 곡 등록·가사

def start_from_song(song: Path, slug: str, *, title: str | None = None, mock: bool | None = None,
                    mode: str | None = None, template: str = CHANNEL, brief: str | None = None,
                    root: Path = PROJECTS_DIR) -> tuple[AutoState, Path]:
    """mode: manual(기본, 무료) | openai(유료) | mock. 옛 호출(mock=True/False)도 받는다."""
    if mode is None:
        mode = {True: "mock", False: "openai"}.get(mock, "manual")
    if mode not in GEN_MODES:
        raise FlowError(f"생성 방식은 {', '.join(GEN_MODES)} 중 하나입니다")
    if template not in TEMPLATES:
        raise FlowError(f"템플릿은 {', '.join(TEMPLATES)} 중 하나입니다")
    """곡을 새 프로젝트 폴더에 복사하고 상태를 만든다. 같은 이름 폴더가 있으면 덮어쓰지 않는다."""
    d = project_dir(slug, root)
    if d.exists():
        raise FlowError(f"이미 같은 이름의 프로젝트가 있습니다: {d} (이어서 하려면 '불러오기')")
    length = media_duration(song)
    if length is None:
        raise FlowError("음악 파일을 읽을 수 없습니다")
    if length < MIN_DURATION:
        raise FlowError(f"곡이 {length:.1f}초로 너무 짧습니다 (최소 {MIN_DURATION:g}초)")
    if not clean_lyrics_text(embedded_lyrics(song) or ""):
        raise FlowError("곡 파일에 가사(lyrics 태그)가 없습니다. Suno 등에서 가사가 들어간 파일을 쓰세요")
    duration = round(min(length, load_template(template).max_duration, MAX_DURATION), 2)
    d.mkdir(parents=True)
    music = f"music{song.suffix.lower()}"
    shutil.copy2(song, d / music)
    state = AutoState(slug=slug, title=title or song.stem, music=music, duration=duration, mock=mode == "mock",
                      gen_mode=mode, template=template, music_length=round(length, 2), suno=suno_info(song))
    if brief:  # 가사 만들기에서 확정한 데이터·출처를 이어받는다
        b = load_brief(brief, root)
        state.brief, state.data_source_name, state.data_source_url = brief, b.get("source_name", ""), b.get("source_url", "")
        state.title = (title or "").strip() or b.get("title") or state.title
    # 가사 맞추기에 쓸 임시 project.json (순위·이미지는 6단계에서 채움)
    save_project(Project(channel=template, title=state.title, duration=duration, music=music,
                         items=[{"rank": r, "name": f"{r}위"} for r in (5, 4, 3, 2, 1)]), d)
    save_state(state, d)
    plog.append(d, "project_started", song=song.name, template=template, gen_mode=mode, suno=state.suno, brief=brief)
    return state, d


def load_brief(name: str, root: Path = PROJECTS_DIR) -> dict:
    p = root / "_briefs" / name / "brief.json"
    if not p.exists():
        raise FlowError(f"가사 브리프가 없습니다: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def align(state: AutoState, d: Path, *, aligner=None, progress: ProgressFn | None = None) -> list[LyricLine]:
    from .lyrics_align import align_lines, auto_align
    project = _project(state, d)
    # 정렬은 곡 전체(최대 90초) 기준으로 하고, 영상 길이는 정렬 결과로 정한다
    project = project.model_copy(update={"duration": music_cap(state)})
    res = auto_align(project, d, None, aligner=aligner or align_lines, progress=progress)
    save_project(project.model_copy(update={"lyrics": res.lines, "lyrics_lrc": None}), d)
    # 실제로 부른 가사 원문 보관 (자막은 확정 때 다듬어지므로 가사 검사는 이 원문으로)
    (d / "lyrics_sung.json").write_text(json.dumps([l.model_dump() for l in res.lines], ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    state.aligned = True
    set_end_mode(state, d, state.end_mode)
    rows = [r.model_copy(update={"scene": default_scene(r)}) for r in extract_ranks(res.lines)]
    if state.brief:  # 브리프의 확정 데이터로 채운다 (가사 근거·시각은 추출 결과)
        brows = sorted(load_brief(state.brief, d.parent).get("rows", []), key=lambda r: -int(r["rank"]))
        for i, r in enumerate(rows):
            if i < len(brows):
                b = brows[i]
                rows[i] = r.model_copy(update={k: str(b.get(k) or getattr(r, k)) for k in ("team", "player", "record", "year")}
                                       | {"rank": int(b["rank"]), "stat_line": b.get("stat_line") or r.stat_line})
    state.rows = rows
    state.rows_draft = [r.model_dump() for r in rows]
    state.table_confirmed = False
    save_state(state, d)
    plog.append(d, "lyrics_aligned", lines=len(res.lines), duration=state.duration)
    return res.lines


def sung_lyrics(d: Path) -> str:
    p = d / "lyrics_sung.json"
    return "\n".join(l["text"] for l in json.loads(p.read_text(encoding="utf-8"))) if p.exists() else ""


def set_data_source(state: AutoState, d: Path, name: str, url: str) -> None:
    state.data_source_name, state.data_source_url = name.strip(), url.strip()
    save_state(state, d)
    plog.append(d, "data_source", name=state.data_source_name, url=state.data_source_url)


def check_song_lyrics(state: AutoState, d: Path) -> dict:
    """곡 가사(실제로 부른 원문)를 확정 데이터·이전 영상과 비교. 이미 만든 곡이라 결과는 사람이 확인한다."""
    rows = [r.model_dump() for r in state.rows]
    rep_ = check_lyrics_text(sung_lyrics(d), rows, history=history_lyrics(d.parent, exclude=state.slug))
    state.lyrics_report, state.lyrics_ack = rep_.as_dict(), False
    save_state(state, d)
    plog.append(d, "lyrics_checked", passed=rep_.passed, errors=rep_.errors, warnings=rep_.warnings)
    return state.lyrics_report


def ack_lyrics(state: AutoState, d: Path, note: str = "") -> None:
    if state.lyrics_report is None:
        raise FlowError("가사 검사를 먼저 실행하세요")
    state.lyrics_ack = True
    save_state(state, d)
    plog.append(d, "lyrics_reviewed", note=note, passed=state.lyrics_report["passed"])


def edit_subtitles(state: AutoState, d: Path, texts: list[str]) -> None:
    """자막 표기 수정 (시간은 그대로). 바뀐 줄을 기록한다."""
    project = _project(state, d)
    if len(texts) != len(project.lyrics):
        raise FlowError("자막 줄 수가 다릅니다")
    changes = [{"line": i, "from": l.text, "to": t.strip()} for i, (l, t) in enumerate(zip(project.lyrics, texts))
               if l.text != t.strip() and t.strip()]
    if changes:
        lines = [l.model_copy(update={"text": t.strip() or l.text}) for l, t in zip(project.lyrics, texts)]
        save_project(project.model_copy(update={"lyrics": lines}), d)
        plog.append(d, "subtitles_edited", changes=changes)


def music_cap(state: AutoState) -> float:
    """반주 끝까지: 곡 길이, 최대 90초 (템플릿 상한)."""
    return round(min(state.music_length or state.duration, load_template(state.template).max_duration), 2)


def end_time(state: AutoState, lines: list[LyricLine]) -> float:
    """영상 길이. 기본은 노래(마지막 가사)가 끝나고 2초 뒤. 곡 길이·90초를 넘지 않고 최소 30초."""
    cap = music_cap(state)
    if state.end_mode == "music" or not lines:
        return cap
    last = max(l.end for l in lines)
    return round(max(MIN_DURATION, min(cap, last + END_TAIL)), 2)


def set_end_mode(state: AutoState, d: Path, mode: str) -> float:
    """영상 끝 기준을 바꾸고 project.json 길이를 다시 맞춘다. 영상 밖으로 나가는 가사는 잘라낸다."""
    state.end_mode = mode  # type: ignore[assignment]
    project = _project(state, d)
    lines = project.lyrics
    state.duration = end_time(state, lines)
    kept = []
    for l in lines:
        if l.start >= state.duration:
            continue
        kept.append(l if l.end <= state.duration else l.model_copy(update={"end": state.duration}))
    save_project(project.model_copy(update={"duration": state.duration, "lyrics": kept}), d)
    save_state(state, d)
    return state.duration


# ---------------------------------------------------------------- 2. 순위 표

def confirm_table(state: AutoState, d: Path, rows: list[RankRow], *, title: str | None = None,
                  merge_record_lines: bool | None = None, allow_unknown_team: bool = False) -> None:
    """사용자가 확인·수정한 표를 확정. 빈칸이 있으면 거부. 자막 표시 텍스트도 이때 정한다."""
    if not state.aligned:
        raise FlowError("먼저 가사 자동 맞추기를 끝내세요")
    problems = rows_complete(rows) + column_problems(rows, allow_unknown_team=allow_unknown_team)
    if problems:
        raise FlowError("표를 확정할 수 없습니다: " + "; ".join(problems))
    filled = [r for r in rows if r.team.strip() or r.player.strip() or r.record.strip()]
    state.rows = sorted(assign_slots(filled), key=lambda r: -r.slot)  # 공동 순위도 자리 5→1 로 한 번씩
    if title:
        set_title(state, d, title)
    if merge_record_lines is not None:
        state.merge_record_lines = merge_record_lines
    project = _project(state, d)
    # 자막은 매번 실제로 부른 가사 원문에서 새로 만든다 (이미 다듬은 자막을 또 다듬지 않음 — 예: '2승엽' 방지)
    lines = subtitle_lines(_sung_base(state, d), state.rows, state.merge_record_lines, brief_subtitles(state, d))
    save_project(project.model_copy(update={"title": state.title, "lyrics": lines}), d)
    for r in state.rows:  # 표가 바뀌면 사진 검색어도 새로
        ps = state.players[r.slot]
        if not ps.folder:
            ps.query = f'"{r.player}" {r.team.split()[0]} baseball'
    state.table_confirmed = True
    save_state(state, d)
    fields = ("rank", "team", "player", "record", "year", "scene", "stat_line")
    plog.append(d, "data_confirmed", changes=plog.diff_rows(state.rows_draft, [r.model_dump() for r in state.rows], fields),
                rows=[{f: getattr(r, f) for f in fields} for r in state.rows])
    check_song_lyrics(state, d)


def set_title(state: AutoState, d: Path, title: str) -> None:
    """영상 제목 (2줄은 줄바꿈). 바로 저장해서 렌더에 반영된다."""
    title = title.strip()
    if not title:
        raise FlowError("영상 제목이 비어 있습니다")
    if title != state.title:
        plog.append(d, "title_changed", **{"from": state.title, "to": title})
    state.title = title
    project = _project(state, d)
    save_project(project.model_copy(update={"title": title}), d)
    save_state(state, d)


def set_extras(state: AutoState, d: Path, *, hook: str | None = None, bonus: dict | None | Literal[False] = False,
               gauge_base: str | None = None) -> None:
    """후킹 문구·엔딩 보너스·게이지 기준선 (None/False 는 그대로 둠, bonus=None 은 지움). 보너스 값은 출처에 있는 값만."""
    from .models import Bonus
    changes = {}
    if hook is not None and hook.strip() != state.hook:
        changes["hook"] = state.hook = hook.strip()
    if bonus is not False:
        b = Bonus.model_validate(bonus).model_dump() if bonus else None
        if b != state.bonus:
            changes["bonus"] = state.bonus = b
    if gauge_base is not None and gauge_base.strip() != state.gauge_base:
        g = gauge_base.strip()
        if g and not re.search(r"\d", g):
            raise FlowError("게이지 기준선에 숫자가 없습니다 (예: 50홈런)")
        changes["gauge_base"] = state.gauge_base = g
    if changes:
        save_state(state, d)
        plog.append(d, "extras_changed", **changes)


def parse_bonus(text: str) -> dict | None:
    """'6위 디아즈 | 삼성 라이온즈 | 50홈런 | 2025' 또는 '예고 | 다음 편: …' → Bonus 사전. 비우면 None."""
    text = text.strip()
    if not text:
        return None
    cells = [c.strip() for c in text.split("|")]
    if cells[0] in ("예고", "다음 편", "다음편"):
        return {"kind": "teaser", "text": " | ".join(cells[1:]).strip() or cells[0]}
    m = re.match(r"^(\d{1,2})\s*위\s*(.*)$", cells[0])
    if not m:
        raise FlowError("보너스는 '6위 이름 | 팀 | 기록 | 연도' 또는 '예고 | 문구' 형식입니다")
    rank, name = int(m.group(1)), m.group(2).strip()
    rest = cells[1:]
    if not name and rest:
        name, rest = rest[0], rest[1:]
    team, value, year = (rest + ["", "", ""])[:3]
    if year and not re.fullmatch(r"\d{4}", year):
        raise FlowError(f"보너스 연도 '{year}' 는 4자리 숫자여야 합니다")
    return {"kind": "rank", "rank": rank, "name": name, "team": team, "value": value, "year": year}


def swap_slots(state: AutoState, d: Path, a: int, b: int) -> None:
    """두 순위의 사진·그림(승인·로고 확인 포함)을 맞바꾼다 (그림이 다른 순위에 들어갔을 때)."""
    if a == b:
        return
    pa, pb = state.players[a], state.players[b]
    state.players[a], state.players[b] = pb, pa
    na, nb = state.no_logo.get(a, False), state.no_logo.get(b, False)
    state.no_logo[a], state.no_logo[b] = nb, na
    state.cards_done = False
    save_state(state, d)
    plog.append(d, "images_swapped", a={"slot": a, "who": row_label(state, a)}, b={"slot": b, "who": row_label(state, b)},
                images={"a": state.players[a].approved, "b": state.players[b].approved})


def record_numbers(rows: list[RankRow]) -> list[int]:
    """표의 기록 숫자 (단위 없이 불러도 자막에서 숫자로 바꾼다: '오십사' → 54)."""
    return [int(n) for r in rows for n in re.findall(r"\d+", r.record.replace(",", ""))]


def norm_line(text: str) -> str:
    return re.sub(r"[\W_]+", "", text)


def subtitle_lines(lines: list[LyricLine], rows: list[RankRow], merge: bool,
                   sub_map: dict[str, str] | None = None) -> list[LyricLine]:
    """자막 표시: 한글 숫자·'케이비오' 정리. merge 면 'N위, 팀' 줄과 바로 뒤 기록 줄을 한 자막으로.

    sub_map(부른 줄 → 자막용 줄, 가사 브리프에서)이 있으면 그 표기를 먼저 쓰고, 없는 줄만 자동 규칙으로 바꾼다.
    부른 원문은 LyricLine.sung 에 남겨 글자 시각을 원래 부른 구간 안에서 나눈다.
    """
    records = {r.record for r in rows if r.record}
    names = [x for r in rows for x in (r.player, r.team) if x]  # 이름 속 한글 숫자는 바꾸지 않음 ('이승엽')
    nums = record_numbers(rows)
    sub_map = sub_map or {}

    def show(text: str) -> str:
        return sub_map.get(norm_line(text)) or display_text(text, names, nums)

    out: list[LyricLine] = []
    i = 0
    while i < len(lines):
        cur = lines[i]
        sung = cur.sung or cur.text
        text = show(sung)
        if merge and i + 1 < len(lines):
            nsung = lines[i + 1].sung or lines[i + 1].text
            nxt = show(nsung)
            if "위" in text and any(rec in nxt for rec in records) and "위" not in nxt:
                out.append(LyricLine(start=cur.start, end=lines[i + 1].end, text=f"{text} {nxt}",
                                     words=cur.words + lines[i + 1].words, sung=f"{sung} {nsung}"))
                i += 2
                continue
        out.append(cur.model_copy(update={"text": text, "sung": sung}))
        i += 1
    return out


def brief_subtitles(state: AutoState, d: Path) -> dict[str, str]:
    """연결한 가사 브리프의 노래용→자막용 줄 대응 (없으면 빈 사전)."""
    if not state.brief:
        return {}
    try:
        b = load_brief(state.brief, d.parent)
    except FlowError:
        return {}
    return pair_map(b.get("chosen_lyrics", ""), b.get("chosen_subtitles", ""))


def pair_map(sung: str, subs: str) -> dict[str, str]:
    a = [l.strip() for l in sung.splitlines() if l.strip() and not l.strip().startswith("[")]
    b = [l.strip() for l in subs.splitlines() if l.strip() and not l.strip().startswith("[")]
    if not b or len(a) != len(b):
        return {}
    return {norm_line(x): y for x, y in zip(a, b)}


def _sung_base(state: AutoState, d: Path) -> list[LyricLine]:
    sung = d / "lyrics_sung.json"
    base = ([LyricLine.model_validate(x) for x in json.loads(sung.read_text(encoding="utf-8"))]
            if sung.exists() else _project(state, d).lyrics)
    return [l if l.end <= state.duration else l.model_copy(update={"end": state.duration})
            for l in base if l.start < state.duration]


def number_preview(state: AutoState, d: Path) -> list[dict]:
    """'숫자 자동 변환' 미리보기: 지금 자막과 규칙대로 바꾼 자막이 다른 줄 (시간은 그대로)."""
    _need_table(state)
    project = _project(state, d)
    auto = subtitle_lines(_sung_base(state, d), state.rows, state.merge_record_lines, brief_subtitles(state, d))
    if len(auto) != len(project.lyrics):  # 줄 구성이 달라졌으면 시작 시각으로 맞춘다
        by_start = {round(l.start, 2): l for l in auto}
        auto = [by_start.get(round(l.start, 2), l) for l in project.lyrics]
    return [{"line": i, "start": l.start, "from": l.text, "to": a.text, "sung": a.sung}
            for i, (l, a) in enumerate(zip(project.lyrics, auto)) if a.text != l.text]


def apply_number_preview(state: AutoState, d: Path, changes: list[dict]) -> int:
    """미리보기에서 고른 줄만 자동 표기로 바꾼다. 바뀐 줄 수.

    부른 원문(sung)이 없는 옛 자막 줄은 함께 채워 둔다 (글자 시각을 부른 구간 안에서 나누도록)."""
    project = _project(state, d)
    lines = list(project.lyrics)
    by_start = {round(l.start, 2): l for l in _sung_base(state, d)}
    for i, l in enumerate(lines):
        src = by_start.get(round(l.start, 2))
        if not l.sung and src is not None and norm_line(display_text(src.text)) == norm_line(display_text(l.text)):
            lines[i] = l.model_copy(update={"sung": src.text})
    for c in changes:
        lines[c["line"]] = lines[c["line"]].model_copy(update={"text": c["to"], "sung": c["sung"]})
    save_project(project.model_copy(update={"lyrics": lines}), d)
    plog.append(d, "subtitles_numbers", changes=[{k: c[k] for k in ("line", "from", "to")} for c in changes])
    return len(changes)


def _need_table(state: AutoState) -> None:
    if not state.table_confirmed:
        raise FlowError("순위 표를 확인·수정하고 '표 확정'을 먼저 누르세요")


def _row(state: AutoState, slot: int) -> RankRow:
    return next(r for r in state.rows if r.slot == slot)


def row_label(state: AutoState, slot: int) -> str:
    """'2위' 또는 '공동 2위'."""
    return label(state.rows, _row(state, slot))


def _player(state: AutoState, slot: int) -> Player:
    r = _row(state, slot)
    season = int(r.year) if r.year.isdigit() else datetime.now().year
    return Player(name=r.player, team=r.team, season=season)


# ---------------------------------------------------------------- 3. 사진

def search(state: AutoState, d: Path, slot: int, query: str | None = None, *, session=None) -> list[Photo]:
    _need_table(state)
    ps = state.players[slot]
    ps.query = (query or ps.query).strip()
    ps.photos = search_photos(_player(state, slot), session=session, query=ps.query or None)
    save_state(state, d)
    return ps.photos


def choose_photo(state: AutoState, d: Path, slot: int, index: int, *, identity_confirmed: bool,
                 rights_confirmed: bool, session=None) -> Path:
    _need_table(state)
    ps = state.players[slot]
    folder = download_photo(_player(state, slot), ps.photos[index], d / "players", f"player_s{slot}",
                            identity_confirmed=identity_confirmed, rights_confirmed=rights_confirmed,
                            session=session)
    ps.folder, ps.source = str(folder.relative_to(d)), "commons"
    _reset_after_photo(state, slot)
    save_state(state, d)
    return folder


def _write_local_photo(state: AutoState, d: Path, slot: int, data: bytes, source: str, note: str) -> Path:
    info = validate_image(data)
    folder = d / "players" / f"player_s{slot}"
    if folder.exists():
        raise FlowError(f"{row_label(state, slot)} 사진이 이미 있습니다 (원본은 덮어쓰지 않습니다). 바꾸려면 폴더를 정리하세요: {folder}")
    folder.mkdir(parents=True)
    name = "original.jpg" if info["format"] == "JPEG" else "original.png"
    (folder / name).write_bytes(data)
    p = _player(state, slot)
    photo = Photo(page_id=0, title=note, url="", thumbnail="", source=note, width=info["width"],
                  height=info["height"], mime=f"image/{info['format'].lower()}", license=note,
                  commercial_candidate=False, warning=note)
    write_json_new(folder / "metadata.json", {
        "created_at": now(), "player": p.model_dump(), "photo": photo.model_dump(), "original": name,
        "original_sha256": digest(data), "image": info, "identity_confirmed": True, "rights_confirmed": True,
    })
    ps = state.players[slot]
    ps.folder, ps.source = str(folder.relative_to(d)), source
    _reset_after_photo(state, slot)
    save_state(state, d)
    return folder


def upload_photo(state: AutoState, d: Path, slot: int, data: bytes, *, rights_confirmed: bool) -> Path:
    """사진이 없을 때 사용자가 직접 준 이미지."""
    _need_table(state)
    if not rights_confirmed:
        raise FlowError("올린 이미지의 이용·변형 권한을 확인해 주세요")
    return _write_local_photo(state, d, slot, data, "upload", "사용자 제공 이미지 (권한 확인: 사용자)")


def placeholder_photo(state: AutoState, d: Path, slot: int) -> Path:
    """Mock 모드 전용: 사진 없이 흐름을 끝까지 시험하기 위한 팀 색 실루엣."""
    _need_table(state)
    if not state.mock:
        raise FlowError("자리표시 이미지는 Mock 모드에서만 쓸 수 있습니다")
    r = _row(state, slot)
    primary, secondary = team_colors(r.team)
    im = Image.new("RGB", (768, 1152), "#F2F2F2")
    dr = ImageDraw.Draw(im)
    dr.ellipse([284, 120, 484, 330], fill=primary)                       # 머리
    dr.rounded_rectangle([214, 330, 554, 820], radius=90, fill=primary)  # 몸
    dr.rectangle([254, 820, 354, 1100], fill=secondary)                  # 다리
    dr.rectangle([414, 820, 514, 1100], fill=secondary)
    dr.text((300, 520), f"{r.rank}", fill="white")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return _write_local_photo(state, d, slot, buf.getvalue(), "placeholder", "MOCK 자리표시 실루엣 (실제 사진 아님)")


def skip_photo(state: AutoState, d: Path, slot: int) -> None:
    """수동 생성 모드: 선수 사진 없이 이름·설명만으로 그림을 만들 때."""
    _need_table(state)
    if state.gen_mode != "manual":
        raise FlowError("사진 없이 진행은 수동 생성 모드에서만 됩니다")
    ps = state.players[slot]
    ps.folder, ps.source = None, "none"
    _reset_after_photo(state, slot)
    save_state(state, d)


def photo_ready(state: AutoState, slot: int) -> bool:
    ps = state.players[slot]
    return bool(ps.folder) or (state.gen_mode == "manual" and ps.source == "none")


def _reset_after_photo(state: AutoState, slot: int) -> None:
    ps = state.players[slot]
    ps.generations, ps.approved, ps.card = [], None, None
    state.cost_approval = None
    state.cards_done = False


# ---------------------------------------------------------------- 4. 캐리커처 (비용 승인)

def slot_style(state: AutoState, slot: int) -> Style:
    """선수별 유료 생성 설정: 장면 설명 + 로고·글씨 금지 (+ 스티커형은 2등신 치비)."""
    r = _row(state, slot)
    extra = [f"장면: {r.scene or default_scene(r)}.",
             "구단 로고·엠블럼, 글자·숫자·등번호·영문 팀 이름은 절대 그리지 마."]
    if _style_name(state) == "sticker":
        extra.insert(0, "2등신 치비(chibi) 스티커 스타일, 큰 머리와 작은 몸.")
    return state.style.model_copy(update={"extra": " ".join(extra)})


def cost_estimate(state: AutoState, d: Path) -> dict:
    """생성 전에 보여줄 예상 비용. 사진이 있고 아직 승인 이미지가 없는 선수만."""
    _need_table(state)
    plans = {}
    for slot, ps in state.players.items():
        if ps.folder and not ps.approved:
            plans[slot] = generation_plan(d / ps.folder, slot_style(state, slot))
    missing = [s for s, ps in state.players.items() if not ps.folder]
    total = 0.0 if state.mock else round(sum(p["estimated_output_usd"] for p in plans.values()), 3)
    high = 0.0 if state.mock else round(sum(p["estimated_total_usd_range"][1] for p in plans.values()), 3)
    any_plan = next(iter(plans.values()), None)
    return {"plans": plans, "missing_photo": missing, "total_usd": total, "total_usd_high": high,
            "mock": state.mock, "model": state.style.model, "quality": state.style.quality,
            "pricing_url": any_plan["pricing_url"] if any_plan else None,
            "pricing_checked_at": any_plan["pricing_checked_at"] if any_plan else None,
            "note": any_plan["estimate_note"] if any_plan else ""}


def approve_cost(state: AutoState, d: Path, estimate: dict, *, approved: bool) -> CostApproval:
    """사용자가 화면에서 본 예상 비용을 승인. 이후 사진·설정이 바뀌면 계획 id 가 달라져 다시 승인해야 한다."""
    if not approved:
        raise FlowError("예상 비용을 확인하고 승인해야 캐리커처를 만듭니다")
    if not estimate["plans"]:
        raise FlowError("캐리커처를 만들 선수가 없습니다 (사진을 먼저 준비하세요)")
    state.cost_approval = CostApproval(plan_ids={s: p["id"] for s, p in estimate["plans"].items()},
                                       total_usd=estimate["total_usd"], approved_at=now())
    save_state(state, d)
    return state.cost_approval


def generate_caricatures(state: AutoState, d: Path, editor, *, progress: ProgressFn | None = None) -> dict[int, Path]:
    """승인한 계획만 실행. 승인 뒤 사진·설정이 바뀐 선수는 실행하지 않고 다시 승인을 요구한다."""
    _need_table(state)
    if state.cost_approval is None:
        raise FlowError("예상 비용 승인이 먼저 필요합니다")
    if bool(editor.is_mock) != state.mock:
        raise FlowError("Mock 모드와 편집기 종류가 다릅니다")
    out: dict[int, Path] = {}
    slots = sorted(state.cost_approval.plan_ids, reverse=True)
    for n, slot in enumerate(slots, 1):
        ps = state.players[slot]
        folder = d / ps.folder
        plan_id = state.cost_approval.plan_ids[slot]
        style = slot_style(state, slot)
        if generation_plan(folder, style)["id"] != plan_id:
            raise FlowError(f"{row_label(state, slot)}: 승인 뒤 사진이나 설정이 바뀌었습니다. 비용을 다시 확인·승인하세요")
        if progress:
            progress((n - 1) / len(slots), f"{row_label(state, slot)} 캐리커처 생성 중")
        target = generate(folder, style, editor, approved_plan_id=plan_id,
                          paid_and_upload_approved=not state.mock)
        ps.generations.append(str(target.relative_to(d)))
        out[slot] = target
        save_state(state, d)
    state.cost_approval = None  # 한 번 승인으로 한 번만 실행 (재생성은 다시 승인)
    save_state(state, d)
    return out


def approve_caricature(state: AutoState, d: Path, slot: int, *, confirmed: bool, no_logo_text: bool = False) -> Path:
    ps = state.players[slot]
    if not ps.generations:
        raise FlowError(f"{row_label(state, slot)} 캐리커처를 먼저 만드세요")
    path = approve_image(d / ps.folder, d / ps.generations[-1], confirmed=confirmed, allow_mock=state.mock)
    ps.approved, ps.card = str(path.relative_to(d)), None
    state.no_logo[slot] = no_logo_text
    state.cards_done = False
    save_state(state, d)
    plog.append(d, "caricature_approved", slot=slot, who=row_label(state, slot), mode=state.gen_mode,
                image=ps.approved, no_logo_text=no_logo_text)
    return path


# ---------------------------------------------------------------- 4'. 수동 생성 (무료)

def _style_name(state: AutoState) -> str:
    return load_template(state.template).card.style


def manual_prepare(state: AutoState, d: Path, slot: int) -> tuple[Path, str]:
    """선수별 프롬프트 + 스타일 참고 그림 + 선수 사진을 한 폴더에 모은다."""
    _need_table(state)
    if not photo_ready(state, slot):
        raise FlowError(f"{row_label(state, slot)}: 사진을 고르거나 '사진 없이 진행'을 먼저 누르세요")
    r = _row(state, slot)
    ps = state.players[slot]
    photo = next((d / ps.folder).glob("original.*"), None) if ps.folder else None
    t = load_template(state.template)
    return prepare_folder(d, slot, r, r.scene or default_scene(r), style=t.card.style,
                          style_ref=t.asset_path("style_ref.png"), photo=photo)


def intro_prompt(state: AutoState) -> str:
    style = "2등신 치비 캐리커처 스티커" if _style_name(state) == "sticker" else "역동적인 만화풍 캐리커처"
    return "\n".join([
        "[첨부: 스타일 참고 그림]",
        f"첨부한 참고 그림과 같은 그림체로, 영상 '{state.title.replace(chr(10), ' ')}' 의 인트로에 쓸 야구 선수 캐릭터를 {style}로 그려줘.",
        "- 실존 인물이 아닌 가상의 캐릭터, 배트를 휘두르는 신나는 동작",
        "- 금지: 구단 로고·엠블럼, 글자·숫자, 워터마크 — 그림 안에 글씨가 하나도 없어야 함",
        "- 배경: 투명 배경 PNG. 안 되면 완전히 흰 단색 배경",
    ])


def _cutout(data: bytes, method: str) -> tuple[bytes, str]:
    with Image.open(io.BytesIO(data)) as im:
        out, how = remove_background(im, method=method)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue(), how


def use_manual_image(state: AutoState, d: Path, slot: int, data: bytes, *, source_name: str = "",
                     method: str = "auto", no_logo_text: bool = False) -> Path:
    """다운로드한(또는 올린) 그림을 배경 제거 후 이 순위의 캐리커처로. 버튼을 누른 것이 승인이다."""
    _need_table(state)
    if state.gen_mode != "manual":
        raise FlowError("수동 생성 모드가 아닙니다")
    validate_image(data) if data[:4] != b"RIFF" else None  # webp 는 Pillow 로만 확인
    png, how = _cutout(data, method)
    folder = manual_dir(d, slot)
    folder.mkdir(parents=True, exist_ok=True)
    n = len(list(folder.glob("result_*.png"))) + 1
    path = folder / f"result_{n:03d}.png"
    path.write_bytes(png)
    write_json_new(path.with_suffix(".json"), {"created_at": now(), "source": source_name, "background": how,
                                               "sha256": digest(png), "approved": True})
    ps = state.players[slot]
    ps.approved, ps.card = str(path.relative_to(d)), None
    state.no_logo[slot] = no_logo_text
    state.cards_done = False
    save_state(state, d)
    prompt_file = folder / "prompt.txt"
    plog.append(d, "caricature_approved", slot=slot, who=row_label(state, slot), mode="manual", image=ps.approved,
                source=source_name, background=how, no_logo_text=no_logo_text,
                prompt=prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else None)
    return path


def confirm_no_logo(state: AutoState, d: Path, slot: int, ok: bool) -> None:
    """승인한 그림에 구단 로고·유니폼 글씨가 없음을 사람이 확인 (렌더 체크리스트)."""
    state.no_logo[slot] = ok
    save_state(state, d)
    plog.append(d, "no_logo_checked", slot=slot, who=row_label(state, slot), ok=ok)


def set_intro_image(state: AutoState, d: Path, data: bytes, *, method: str = "auto") -> Path:
    png, _ = _cutout(data, method)
    path = d / "intro.png"
    path.write_bytes(png)
    state.intro_image = "intro.png"
    save_state(state, d)
    return path


# ---------------------------------------------------------------- 5'. 연출 (영상마다 다르게)

def suggest_variation(state: AutoState, d: Path) -> Variation:
    return suggest(d.parent, exclude=state.slug)


def choose_variation(state: AutoState, d: Path, v: Variation, *, reason: str = "") -> list[str]:
    """최근 5편과 너무 비슷하면 거부. 정한 연출을 기록한다."""
    bad = conflicts(v, d.parent, exclude=state.slug)
    if bad:
        raise FlowError("최근 영상과 연출이 너무 비슷합니다: " + "; ".join(bad))
    state.variation = v.model_dump()
    state.cards_done = False  # 강조색이 카드에 들어가므로 다시 합성
    save_state(state, d)
    plog.append(d, "variation_chosen", variation=state.variation, describe=v.describe(), reason=reason)
    return bad


# ---------------------------------------------------------------- 5. 카드

def compose_cards(state: AutoState, d: Path) -> dict[int, Path]:
    _need_table(state)
    if state.variation is None:
        raise FlowError("연출(전환·강조색·자막·인트로)을 먼저 고르세요")
    waiting = [s for s, ps in state.players.items() if not ps.approved]
    if waiting:
        raise FlowError("승인한 캐리커처가 없는 순위: "
                        + ", ".join(row_label(state, s) for s in sorted(waiting, reverse=True)))
    out = {}
    sticker = _style_name(state) == "sticker"
    for r in state.rows:
        ps = state.players[r.slot]
        if sticker:  # 스티커형: 글씨 없는 그림 그대로 (순위·기록은 영상의 자막·빨간 줄로)
            card = _sticker(d / ps.approved, mock=state.mock)
        else:
            kw = dict(rank=r.rank, record=r.record, year=r.year, team=r.team, player=r.player, mock=state.mock,
                      tied=label(state.rows, r).startswith("공동"),
                      rank_fill=ACCENTS[Variation.model_validate(state.variation).accent]["rank"])
            card = compose_card(d / ps.approved, **kw)
            meta: dict = {}  # 카운트업용: 기록 글자를 뺀 바탕 카드 + 글자 위치
            base = compose_card(d / ps.approved, **kw, omit_record=True, meta=meta)
            save_card(base, d / "cards" / f"card{r.slot}_base.png")
            meta["base"] = f"cards/card{r.slot}_base.png"
            (d / "cards" / f"card{r.slot}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        out[r.slot] = save_card(card, d / "cards" / f"card{r.slot}.png")
        ps.card = str(out[r.slot].relative_to(d))
    state.cards_done = True
    save_state(state, d)
    return out


# ---------------------------------------------------------------- 6. 렌더·CapCut

def _sticker(path: Path, *, mock: bool) -> Image.Image:
    with Image.open(path) as im:
        img = im.convert("RGBA")
    img = img.crop(img.getbbox() or (0, 0, *img.size))
    if mock:
        from .scenes import _font
        dr = ImageDraw.Draw(img)
        dr.rounded_rectangle([8, 8, 250, 60], radius=12, fill=(220, 0, 0, 235))
        dr.text((129, 34), "MOCK 시험용", font=_font(str(FONT_EXTRA), 34), fill="white", anchor="mm")
    return img


def credits_text(state: AutoState, d: Path) -> str:
    """영상 설명에 넣을 사진 출처 (CC BY / BY-SA 는 저작자·라이선스 표시 필요)."""
    rows = ["[사진 출처]"]
    for r in state.rows:
        ps = state.players[r.slot]
        if not ps.folder:
            if ps.approved and "manual" in ps.approved:
                rows.append(f"{label(state.rows, r)} {r.player}: 사용자가 직접 만든 그림 (사진 없음)")
            continue
        meta = json.loads((d / ps.folder / "metadata.json").read_text(encoding="utf-8"))
        ph = meta["photo"]
        rows.append(f"{label(state.rows, r)} {r.player}: {ph['title']} / 저작자 {ph.get('author') or '미상'} / "
                    f"{ph['license']} {ph.get('license_url', '')} / {ph['source']}".rstrip())
    rows.append("MOCK 시험용 — 실제 영상에 쓰지 마세요." if state.mock else
                "캐리커처는 위 사진을 바탕으로 AI로 변형한 2차 저작물입니다.")
    cues, variant = used_sfx(state, d)
    if cues:
        from .sfx import credits_lines
        rows += ["", "[효과음]"] + credits_lines(cues, variant)
    return "\n".join(rows)


def used_sfx(state: AutoState, d: Path) -> tuple[list, str]:
    """이 영상에 들어갈 효과음 (큐, 세트). 카드가 아직 없으면 ([], '')."""
    v = Variation.model_validate(state.variation or {})
    if v.sfx == "off" or not state.cards_done:
        return [], ""
    from .card_mode import card_timeline, sfx_cues
    try:
        project = build_project(state, d)
        t = load_template(project.channel)
        scenes = card_timeline(project, t, project.lyrics)
    except Exception:  # noqa: BLE001 — 렌더 단계에서 같은 오류가 다시 보인다
        return [], ""
    return sfx_cues(project, t, scenes), v.sfx


def build_project(state: AutoState, d: Path) -> Project:
    if not state.cards_done:
        raise FlowError("카드를 먼저 합성하세요")
    project = _project(state, d)
    items = [{"rank": r.slot, "display_rank": r.rank, "name": r.player or r.team, "value": r.record,
              "description": " ".join(x for x in (r.team, r.year) if x), "image": state.players[r.slot].card,
              "stat_line": r.stat_line} for r in state.rows]
    source = state.data_source_name + (f" ({state.data_source_url})" if state.data_source_url else "")
    project = Project.model_validate({**project.model_dump(), "channel": state.template, "title": state.title,
                                      "items": items, "intro_image": state.intro_image, "variation": state.variation,
                                      "hook": state.hook, "bonus": state.bonus, "gauge_base": state.gauge_base,
                                      "source_note": f"출처: {source}" if source else "",
                                      "rights_confirmed": state.rights_confirmed and not state.mock})
    save_project(project, d)
    return project


# ---------------------------------------------------------------- 6. 권리 체크리스트·렌더

def set_suno(state: AutoState, d: Path, *, paid: bool, plan: str) -> None:
    state.suno_paid, state.suno_plan = paid, plan.strip()
    save_state(state, d)
    plog.append(d, "suno_confirmed", paid=paid, plan=state.suno_plan, suno=state.suno)


def checklist(state: AutoState, d: Path) -> list[dict]:
    """수익화용 렌더 전 체크리스트. 하나라도 ok=False 면 수익화용 렌더를 막는다."""
    items = []

    def add(key: str, label: str, ok: bool, detail: str = "") -> None:
        items.append({"key": key, "label": label, "ok": bool(ok), "detail": detail})

    add("not_mock", "Mock 이 아닌 실제 제작", not state.mock, "Mock 프로젝트는 수익화용으로 렌더할 수 없습니다" if state.mock else "")
    add("table", "순위 표 확정", state.table_confirmed, "" if state.table_confirmed else "2단계 표를 먼저 확정하세요")
    if not state.table_confirmed:
        return items
    add("suno", "Suno 유료 플랜으로 생성한 곡", state.suno_paid and bool(state.suno_plan),
        f"플랜: {state.suno_plan or '미입력'} · 곡 태그: " +
        (f"Suno id {state.suno.get('id', '없음')}, 생성 {state.suno.get('created', '알 수 없음')}"
         if state.suno.get("made_with_suno") else "Suno 표시 없음 (확인 필요)"))
    missing = [row_label(state, s) for s in state.players if not state.no_logo.get(s)]
    add("no_logo", "캐리커처에 구단 로고·유니폼 글씨 없음 (사람 확인)", not missing,
        ("확인 안 된 순위: " + ", ".join(missing)) if missing else "5명 모두 확인")
    bad_credit = []
    for r in state.rows:
        ps = state.players[r.slot]
        if ps.source == "placeholder" or (not ps.folder and ps.source != "none"):
            bad_credit.append(label(state.rows, r))
        elif ps.folder and not (d / ps.folder / "metadata.json").exists():
            bad_credit.append(label(state.rows, r))
    add("photo_credits", "사진 출처 기록 (credits.txt)", not bad_credit,
        ("출처가 없는 순위: " + ", ".join(bad_credit)) if bad_credit else "모든 사진 출처 기록됨 (또는 사진 없이 직접 그림)")
    add("data_source", "데이터 출처 (이름·URL)", bool(state.data_source_name and state.data_source_url),
        state.data_source_name or "미입력")
    add("lyrics", "가사 검사 결과 확인 (나열형·데이터 일치·이전 영상과 겹침)", state.lyrics_ack,
        "" if state.lyrics_report is None else ("통과" if state.lyrics_report["passed"] else
                                                "검사 오류 있음: " + "; ".join(state.lyrics_report["errors"][:2])))
    cues, variant = used_sfx(state, d)
    if cues:
        from .sfx import problems as sfx_problems
        bad = sfx_problems(cues, variant)
        add("sfx", "효과음 출처·라이선스 (자체 제작·퍼블릭 도메인·CC0)", not bad,
            "; ".join(bad) if bad else "쓴 효과음 모두 출처 기록됨")
    v_ok = state.variation is not None and not conflicts(Variation.model_validate(state.variation), d.parent,
                                                         exclude=state.slug)
    add("variation", "연출이 최근 5편과 충분히 다름", v_ok,
        Variation.model_validate(state.variation).describe() if state.variation else "미선택")
    return items


def render_all(state: AutoState, d: Path, *, rights_confirmed: bool = False, kind: str = "draft",
               capcut_root: Path | None = None, output_dir: Path = OUTPUT_DIR,
               progress: ProgressFn | None = None) -> dict:
    """kind=draft: 언제나 가능, 출력 이름 _DRAFT (Mock 은 _MOCK).
    kind=monetize: 체크리스트가 모두 통과하고 자료·권리 확인을 해야 렌더. 연출 기록(_history)에 남는다."""
    from .capcut_export import CapCutExportError, drafts_dir, export_draft
    from .pipeline import render
    if kind not in ("draft", "monetize"):
        raise FlowError("렌더 종류는 draft 또는 monetize 입니다")
    state.credits = credits_text(state, d)
    (d / "credits.txt").write_text(state.credits, encoding="utf-8")
    if kind == "monetize":
        failed = [c for c in checklist(state, d) if not c["ok"]]
        if failed:
            raise FlowError("수익화용 렌더를 할 수 없습니다 — " + "; ".join(f"{c['label']}: {c['detail']}" for c in failed))
        if not rights_confirmed:
            raise FlowError("자료 정확성과 이미지·음악 사용 권리를 확인해야 수익화용으로 렌더합니다")
    state.rights_confirmed = rights_confirmed and kind == "monetize"
    project = build_project(state, d)
    suffix = "_MOCK" if state.mock else ("" if kind == "monetize" else "_DRAFT")
    res = render(project, d, output_dir / f"{state.slug}{suffix}.mp4", progress=progress,
                 require_rights=kind == "monetize")
    state.output, state.render_kind = str(res.output), kind
    warnings = list(res.warnings)
    try:
        draft = export_draft(project, d, root=capcut_root or drafts_dir(),
                             name=f"ShortsMaker_{state.slug}{suffix}_{datetime.now():%Y%m%d_%H%M%S}")
        state.capcut = str(draft.path)
        warnings += draft.warnings
    except CapCutExportError as e:  # CapCut 실패는 MP4 에 영향 없음
        warnings.append(f"CapCut 초안을 만들지 못했습니다: {e}")
    save_state(state, d)
    if kind == "monetize":
        record_variation(state.slug, Variation.model_validate(state.variation), d.parent)
    plog.append(d, "render", kind=kind, output=state.output, capcut=state.capcut,
                checklist=checklist(state, d), final_confirmed=kind == "monetize")
    return {"output": res.output, "capcut": state.capcut, "warnings": warnings, "credits": state.credits}


# ---------------------------------------------------------------- 7. 업로드 준비

def make_upload(state: AutoState, d: Path) -> dict:
    if not state.output:
        raise FlowError("영상을 먼저 렌더하세요")
    source = state.data_source_name + (f" {state.data_source_url}" if state.data_source_url else "")
    state.credits = credits_text(state, d)
    draft = upl.build(state.title, state.rows, data_source=source, credits=state.credits, gen_mode=state.gen_mode)
    upl.write(d, draft)
    state.upload = draft
    save_state(state, d)
    plog.append(d, "upload_draft", titles=draft["titles"], tags=len(draft["tags"]))
    return draft
