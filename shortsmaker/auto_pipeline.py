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
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

from PIL import Image, ImageDraw
from pydantic import BaseModel, Field

from .bg_remove import remove_background
from .card_compose import FONT_EXTRA, compose_card, save_card
from .manual_prompt import build_prompt, default_scene, manual_dir, prepare_folder
from .lyrics_align import clean_lyrics_text, embedded_lyrics
from .models import MAX_DURATION, MIN_DURATION, OUTPUT_DIR, PROJECTS_DIR, LyricLine, Project, load_project, \
    load_template, save_project
from .player_assets import (Photo, Player, Style, approve_image, digest, download_photo, generate,
                            generation_plan, search_photos, validate_image, write_json_new)
from .rank_extract import RankRow, assign_slots, display_text, extract_ranks, label, rows_complete, team_colors
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
                    mode: str | None = None, template: str = CHANNEL,
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
                      gen_mode=mode, template=template, music_length=round(length, 2))
    # 가사 맞추기에 쓸 임시 project.json (순위·이미지는 6단계에서 채움)
    save_project(Project(channel=template, title=state.title, duration=duration, music=music,
                         items=[{"rank": r, "name": f"{r}위"} for r in (5, 4, 3, 2, 1)]), d)
    save_state(state, d)
    return state, d


def align(state: AutoState, d: Path, *, aligner=None, progress: ProgressFn | None = None) -> list[LyricLine]:
    from .lyrics_align import align_lines, auto_align
    project = _project(state, d)
    # 정렬은 곡 전체(최대 90초) 기준으로 하고, 영상 길이는 정렬 결과로 정한다
    project = project.model_copy(update={"duration": music_cap(state)})
    res = auto_align(project, d, None, aligner=aligner or align_lines, progress=progress)
    save_project(project.model_copy(update={"lyrics": res.lines, "lyrics_lrc": None}), d)
    state.aligned = True
    set_end_mode(state, d, state.end_mode)
    state.rows = [r.model_copy(update={"scene": default_scene(r)}) for r in extract_ranks(res.lines)]
    state.table_confirmed = False
    save_state(state, d)
    return res.lines


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
                  merge_record_lines: bool | None = None) -> None:
    """사용자가 확인·수정한 표를 확정. 빈칸이 있으면 거부. 자막 표시 텍스트도 이때 정한다."""
    if not state.aligned:
        raise FlowError("먼저 가사 자동 맞추기를 끝내세요")
    problems = rows_complete(rows)
    if problems:
        raise FlowError("표를 확정할 수 없습니다: " + "; ".join(problems))
    filled = [r for r in rows if r.team.strip() or r.player.strip() or r.record.strip()]
    state.rows = sorted(assign_slots(filled), key=lambda r: -r.slot)  # 공동 순위도 자리 5→1 로 한 번씩
    if title:
        state.title = title
    if merge_record_lines is not None:
        state.merge_record_lines = merge_record_lines
    project = _project(state, d)
    lines = subtitle_lines(project.lyrics, state.rows, state.merge_record_lines)
    save_project(project.model_copy(update={"title": state.title, "lyrics": lines}), d)
    for r in state.rows:  # 표가 바뀌면 사진 검색어도 새로
        ps = state.players[r.slot]
        if not ps.folder:
            ps.query = f'"{r.player}" {r.team.split()[0]} baseball'
    state.table_confirmed = True
    save_state(state, d)


def subtitle_lines(lines: list[LyricLine], rows: list[RankRow], merge: bool) -> list[LyricLine]:
    """자막 표시: 한글 숫자·'케이비오' 정리. merge 면 'N위, 팀' 줄과 바로 뒤 기록 줄을 한 자막으로."""
    records = {r.record for r in rows if r.record}
    out: list[LyricLine] = []
    i = 0
    while i < len(lines):
        cur = lines[i]
        text = display_text(cur.text)
        if merge and i + 1 < len(lines):
            nxt = display_text(lines[i + 1].text)
            if "위" in text and any(rec in nxt for rec in records) and "위" not in nxt:
                out.append(LyricLine(start=cur.start, end=lines[i + 1].end, text=f"{text} {nxt}",
                                     words=cur.words + lines[i + 1].words))
                i += 2
                continue
        out.append(cur.model_copy(update={"text": text}))
        i += 1
    return out


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


def approve_caricature(state: AutoState, d: Path, slot: int, *, confirmed: bool) -> Path:
    ps = state.players[slot]
    if not ps.generations:
        raise FlowError(f"{row_label(state, slot)} 캐리커처를 먼저 만드세요")
    path = approve_image(d / ps.folder, d / ps.generations[-1], confirmed=confirmed, allow_mock=state.mock)
    ps.approved, ps.card = str(path.relative_to(d)), None
    state.cards_done = False
    save_state(state, d)
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
                     method: str = "auto") -> Path:
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
    state.cards_done = False
    save_state(state, d)
    return path


def set_intro_image(state: AutoState, d: Path, data: bytes, *, method: str = "auto") -> Path:
    png, _ = _cutout(data, method)
    path = d / "intro.png"
    path.write_bytes(png)
    state.intro_image = "intro.png"
    save_state(state, d)
    return path


# ---------------------------------------------------------------- 5. 카드

def compose_cards(state: AutoState, d: Path) -> dict[int, Path]:
    _need_table(state)
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
            card = compose_card(d / ps.approved, rank=r.rank, record=r.record, year=r.year, team=r.team,
                                player=r.player, mock=state.mock, tied=label(state.rows, r).startswith("공동"))
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
    return "\n".join(rows)


def build_project(state: AutoState, d: Path) -> Project:
    if not state.cards_done:
        raise FlowError("카드를 먼저 합성하세요")
    project = _project(state, d)
    items = [{"rank": r.slot, "display_rank": r.rank, "name": r.player or r.team, "value": r.record,
              "description": " ".join(x for x in (r.team, r.year) if x), "image": state.players[r.slot].card,
              "stat_line": r.stat_line} for r in state.rows]
    project = Project.model_validate({**project.model_dump(), "channel": state.template, "title": state.title,
                                      "items": items, "intro_image": state.intro_image,
                                      "rights_confirmed": state.rights_confirmed and not state.mock})
    save_project(project, d)
    return project


def render_all(state: AutoState, d: Path, *, rights_confirmed: bool, capcut_root: Path | None = None,
               output_dir: Path = OUTPUT_DIR, progress: ProgressFn | None = None) -> dict:
    """MP4 + CapCut 초안. 실제 모드는 자료·이미지·음악 권리 확인 체크가 필요하다."""
    from .capcut_export import CapCutExportError, drafts_dir, export_draft
    from .pipeline import render
    if not state.mock and not rights_confirmed:
        raise FlowError("자료 정확성과 이미지·음악 사용 권리를 확인해야 렌더합니다")
    state.rights_confirmed = rights_confirmed
    project = build_project(state, d)
    suffix = "_MOCK" if state.mock else ""
    res = render(project, d, output_dir / f"{state.slug}{suffix}.mp4", progress=progress,
                 require_rights=not state.mock)
    state.output = str(res.output)
    warnings = list(res.warnings)
    try:
        draft = export_draft(project, d, root=capcut_root or drafts_dir(),
                             name=f"ShortsMaker_{state.slug}{suffix}_{datetime.now():%Y%m%d_%H%M%S}")
        state.capcut = str(draft.path)
        warnings += draft.warnings
    except CapCutExportError as e:  # CapCut 실패는 MP4 에 영향 없음
        warnings.append(f"CapCut 초안을 만들지 못했습니다: {e}")
    state.credits = credits_text(state, d)
    (d / "credits.txt").write_text(state.credits, encoding="utf-8")
    save_state(state, d)
    return {"output": res.output, "capcut": state.capcut, "warnings": warnings, "credits": state.credits}
