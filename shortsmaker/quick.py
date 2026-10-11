"""빠른 제작: 양식 답변 + 곡 + 그림 5장 → 버튼 하나로 시험 렌더까지.

곡 등록 → 가사 맞추기 → 가사와 표 대조 → 표 확정 → 그림 넣기(배경 제거) → 연출 자동 추천 → 합성 → 시험 렌더(_DRAFT).
문제가 있을 때만 멈추고(QuickResult.status == "stopped") 이유를 돌려준다.
항상 새 프로젝트 폴더를 만든다 (같은 이름이 있으면 _2, _3 …). 멈춘 프로젝트를 '이어서' 할 때만 resume=True 로
같은 폴더를 다시 쓴다 — 예전 프로젝트를 새 자료로 착각해 다시 렌더하는 일이 없게.
수익화용 렌더(권리 체크리스트)와 업로드 초안은 '노래로 자동 제작' 화면에서 이어서 한다 (같은 프로젝트).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import auto_pipeline as ap
from . import production_log as plog
from .models import OUTPUT_DIR, PROJECTS_DIR
from .quick_parse import QuickData
from .rank_extract import RankRow, find_team, label, normalize_numbers

ProgressFn = Callable[[float, str], None]


@dataclass
class QuickResult:
    status: str                      # done | stopped
    stage: str
    message: str = ""
    issues: list[str] = field(default_factory=list)
    output: str | None = None
    slug: str = ""


def compare_lyrics(sung: str, rows: list[RankRow]) -> list[str]:
    """곡 가사(실제로 부른 원문)와 표 대조: 순위를 부르는지, 선수 이름·기록 숫자가 같은지."""
    names = [x for r in rows for x in (r.player, r.team) if x]
    lines = [normalize_numbers(l, names) for l in sung.splitlines() if l.strip()]
    issues = []
    used: set[int] = set()
    for r in sorted(rows, key=lambda r: -(r.slot or r.rank)):
        who = f"{label(rows, r)} {r.player}"
        idx = next((i for i, l in enumerate(lines) if i not in used and re.search(rf"(?<!\d){r.rank}\s*위", l)
                    and (not r.player or r.player in l)), None)
        if idx is None:
            idx = next((i for i, l in enumerate(lines) if i not in used and re.search(rf"(?<!\d){r.rank}\s*위", l)), None)
            if idx is None:
                issues.append(f"{who}: 가사에서 '{r.rank}위'를 부르는 줄을 찾지 못했습니다 (장면 전환 시점을 못 정함)")
                continue
            issues.append(f"{who}: '{r.rank}위' 줄에 선수 이름 '{r.player}'이(가) 없습니다: \"{lines[idx]}\"")
        used.add(idx)
        block = " ".join(lines[idx:idx + 3])
        rec = re.search(r"\d+", r.record or "")
        if rec and rec.group(0) not in re.findall(r"\d+", block):
            issues.append(f"{who}: 표의 기록 {r.record} 가 가사 근처에 없습니다 (\"{lines[idx]}\" …)")
        team = find_team(r.team)
        other_teams = {find_team(o.team) for o in rows if find_team(o.team)} - {team}
        others = {t.name for t in other_teams if t}
        if any(o in lines[idx] for o in others):
            issues.append(f"{who}: '{r.rank}위' 줄에 다른 팀 이름이 있습니다: \"{lines[idx]}\"")
    return issues


def new_slug(base: str, root: Path = PROJECTS_DIR) -> str:
    """같은 이름의 프로젝트가 있으면 _2, _3 … 을 붙인 새 이름."""
    base = re.sub(r"[^\w가-힣]+", "_", base).strip("_")[:40] or "quick"
    slug, k = base, 2
    while (root / slug).exists():
        slug, k = f"{base}_{k}", k + 1
    return slug


def run_quick(data: QuickData, song: Path | None, images: list[bytes], *, slug: str, template: str = ap.CHANNEL,
              root: Path = PROJECTS_DIR, accept_mismatch: bool = False, aligner=None, output_dir: Path = OUTPUT_DIR,
              capcut_root: Path | None = None, progress: ProgressFn | None = None,
              use_claude: bool = True, resume: bool = False, hook_image: bytes | None = None) -> QuickResult:
    report = progress or (lambda f, m: None)
    if not data.ok:
        return QuickResult("stopped", "양식", "붙여 넣은 양식에 틀린 줄이 있습니다", data.errors, slug=slug)
    if len(images) != len(data.rows):
        return QuickResult("stopped", "그림", f"그림이 {len(images)}장입니다 ({len(data.rows)}장 필요, 순위 순서대로)",
                           slug=slug)

    if not resume:  # 언제나 새 폴더 (예전 프로젝트를 덮어쓰거나 이어서 렌더하지 않음)
        slug = new_slug(slug, root)
    d = root / slug
    if resume and (d / ap.STATE_FILE).exists():
        state = ap.load_state(d)                      # 멈췄던 이 프로젝트를 이어서
    else:
        if song is None:
            return QuickResult("stopped", "곡", "곡 파일이 필요합니다", slug=slug)
        report(0.02, "곡 등록")
        try:
            state, d = ap.start_from_song(song, slug, title=data.title, mode="manual", template=template, root=root)
        except ap.FlowError as e:
            return QuickResult("stopped", "곡", str(e), slug=slug)
        ap.set_data_source(state, d, data.source_name, data.source_url)
        ap.set_extras(state, d, hook=data.hook, bonus=data.bonus, gauge_base=data.gauge_base, bubbles=data.bubbles)
        if hook_image:
            ap.set_hook_image(state, d, hook_image)
        plog.append(d, "quick_input", title=data.title, rows=[r.model_dump() for r in data.rows], memos=data.memos,
                    source=f"{data.source_name} {data.source_url}", hook=data.hook, bonus=data.bonus,
                    gauge_base=data.gauge_base)

    if not state.aligned:
        report(0.05, "가사 자동 맞추기 (1~2분)")
        try:
            ap.align(state, d, aligner=aligner)
        except Exception as e:  # noqa: BLE001
            return QuickResult("stopped", "가사 맞추기", f"가사 자동 맞추기 실패: {e}", slug=slug)

    if not state.table_confirmed:
        report(0.4, "가사와 표 대조")
        issues = compare_lyrics(ap.sung_lyrics(d), data.rows)
        if issues and not accept_mismatch:
            return QuickResult("stopped", "가사·표 대조", "곡 가사와 표가 다른 곳이 있습니다. 표대로 계속할까요?", issues,
                               slug=slug)
        if issues:
            plog.append(d, "quick_mismatch_accepted", issues=issues)
        extracted = {(r.rank, i): r for i, r in enumerate(state.rows)}
        rows = []
        for i, r in enumerate(data.rows):  # 가사 근거·시각은 추출 결과에서, 값은 표에서
            ex = next((e for (rk, _), e in extracted.items() if rk == r.rank and (not r.player or r.player in e.lyric)),
                      next((e for (rk, _), e in extracted.items() if rk == r.rank), None))
            rows.append(r.model_copy(update={"lyric": ex.lyric if ex else "", "start": ex.start if ex else None,
                                             "stat_line": r.stat_line or (ex.stat_line if ex else "")}))
        try:
            ap.confirm_table(state, d, rows, title=data.title, allow_unknown_team=True)
        except ap.FlowError as e:
            return QuickResult("stopped", "표 확정", str(e), slug=slug)

    report(0.5, "그림 넣기 (배경 제거)")
    slots = [r.slot for r in state.rows]  # 표 순서 = 자리 5→1 (공동은 표 순서)
    for slot, img in zip(slots, images):
        if not state.players[slot].approved:
            if not ap.photo_ready(state, slot):
                ap.skip_photo(state, d, slot)
            try:
                ap.use_manual_image(state, d, slot, img, source_name="빠른 제작 업로드")
            except Exception as e:  # noqa: BLE001
                return QuickResult("stopped", "그림", f"{ap.row_label(state, slot)} 그림을 읽을 수 없습니다: {e}", slug=slug)

    if not state.mood:
        report(0.53, "분위기 판단 (카드 디자인)")
        ap.detect_mood(state, d, use_claude=use_claude)
    if state.variation is None:
        report(0.55, "연출 자동 추천")
        ap.choose_variation(state, d, ap.suggest_variation(state, d), reason="빠른 제작 자동 추천")
    if not state.cards_done:
        report(0.58, "합성")
        ap.compose_cards(state, d)

    report(0.6, "시험 렌더")
    try:
        res = ap.render_all(state, d, kind="draft", output_dir=output_dir, capcut_root=capcut_root,
                            progress=lambda f, m: report(0.6 + 0.4 * f, m))
    except Exception as e:  # noqa: BLE001
        return QuickResult("stopped", "렌더", f"렌더 실패: {e}", slug=slug)
    return QuickResult("done", "시험 렌더", "시험 렌더 완료", output=str(res["output"]), slug=slug,
                       issues=res["warnings"])
