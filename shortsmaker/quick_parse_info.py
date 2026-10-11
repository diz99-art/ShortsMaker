"""정보한장 빠른 양식 → Project. 카드·렌더 모듈과 독립적으로 사용한다.

사용: project = parse_quick_info(text)
잘못된 입력은 QuickParseInfoError.errors(줄별 오류 목록)로 확인할 수 있다.
InfoItem의 year/scene, InfoProject의 memos는 model_dump()에도 보존된다.
보너스: '6위 이름 | 구분 | 값 | 기준 연도' 또는 '예고 | 다음 편 문구'.
"""
from __future__ import annotations

import re

from pydantic import Field

from .models import Bonus, Item, Project, competition_ranks_ok
from .quick_parse import UNSURE, _clean

COLUMNS = ["순위", "이름", "구분", "값", "기준 연도", "그림 설명"]


class InfoItem(Item):
    year: str = ""
    scene: str = ""


class InfoProject(Project):
    items: list[InfoItem]
    memos: list[str] = Field(default_factory=list)


class QuickParseInfoError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


def _bonus(value: str) -> Bonus | None:
    if not value:
        return None
    cells = [c.strip() for c in value.split("|")]
    if cells[0] in ("예고", "다음 편", "다음편"):
        return Bonus(kind="teaser", text=" | ".join(cells[1:]).strip())
    match = re.fullmatch(r"(\d{1,2})\s*위\s*(.+)", cells[0])
    if len(cells) != 4 or not match:
        raise ValueError("보너스는 '6위 이름 | 구분 | 값 | 기준 연도' 또는 '예고 | 문구' 형식입니다")
    if cells[3] and not re.fullmatch(r"[0-9]{4}", cells[3]):
        raise ValueError(f"기준 연도 '{cells[3]}' 는 4자리 숫자여야 합니다")
    return Bonus(rank=int(match[1]), name=match[2].strip(), team=cells[1],
                 value=cells[2], year=cells[3])


def parse_quick_info(text: str) -> Project:
    """6칸 정보 양식을 읽는다. 공동 순위는 표시 순위로, 자리는 5→1로 저장한다.

    출처는 기존 양식처럼 '이름 | http(s) URL'. 빈 연도·그림 설명은 허용한다.
    메모와 그림 설명은 확장 필드에 보존하며 권리 확인은 기본 False다.
    """
    data = dict(channel="info_top5", title="", source_note="", hook="",
                bonus=None, bubbles={}, memos=[])
    errors: list[str] = []
    rows: list[tuple[int, InfoItem]] = []
    for no, raw in enumerate(text.splitlines(), 1):
        line = _clean(raw)
        if not line or line.startswith("```") or re.fullmatch(r"\|?[\s:\-|]+\|?", line):
            continue
        problem = None
        if any(u in line for u in UNSURE):
            problem = "'확인 필요' 칸이 있습니다 — 직접 확인해 채운 뒤 다시 붙여 넣으세요"
        else:
            meta = re.match(r"^(제목|후킹|출처|보너스|메모|말풍선)\s*[:：]\s*(.*)$", line)
            if meta:
                key, val = meta[1], meta[2].strip()
                if key == "제목":
                    parts = [p.strip() for p in re.split(r"\s+/\s+|\s*\|\s*", val) if p.strip()]
                    if not 1 <= len(parts) <= 2:
                        problem = "제목은 '1줄 / 2줄' 형식이어야 합니다 (1~2줄)"
                    else:
                        data["title"] = "\n".join(parts)
                elif key == "출처":
                    parts = [p.strip() for p in val.split("|")]
                    if len(parts) != 2 or not parts[0]:
                        problem = "출처는 '이름 | URL' 형식이어야 합니다"
                    elif not re.fullmatch(r"https?://\S+", parts[1]):
                        problem = "출처 URL 이 http(s):// 로 시작하지 않습니다"
                    else:
                        data["source_note"] = " | ".join(parts)
                elif key == "후킹":
                    if len(val) > 30:
                        problem = f"후킹 문구가 너무 깁니다 ({len(val)}자, 30자 이내)"
                    data["hook"] = val
                elif key == "메모":
                    if val:
                        data["memos"].append(val)
                elif key == "보너스":
                    try:
                        data["bonus"] = _bonus(val)
                    except ValueError as exc:
                        problem = f"보너스 — {exc}"
                else:
                    from .mascot import SLOTS, bubble_problem
                    parts = [p.strip() for p in val.split("/")]
                    if len(parts) > 3:
                        problem = "말풍선은 '인트로 / 1위 / 엔딩' 3칸입니다"
                    else:
                        for slot, bubble in zip(SLOTS, parts):
                            if bubble:
                                issue = bubble_problem(bubble)
                                if issue:
                                    errors.append(f"{no}번째 줄: {issue}")
                                data["bubbles"][slot] = bubble
            elif "|" in line:
                # 바깥 구분자만 제거해 마지막 빈 칸도 정확히 센다.
                table = line[1:] if line.startswith("|") else line
                table = table[:-1] if line.startswith("|") and table.endswith("|") else table
                cells = [c.strip() for c in table.split("|")]
                if cells[0] == "순위":
                    if cells != COLUMNS:
                        problem = f"머리줄은 '{' | '.join(COLUMNS)}' 형식이어야 합니다"
                elif len(cells) != 6:
                    problem = f"칸이 {len(cells)}개입니다(6개 필요)"
                else:
                    rank, name, description, value, year, scene = cells
                    if not re.fullmatch(r"(공동\s*)?[1-5](위)?", rank):
                        problem = f"순위 '{rank}' 는 1~5 숫자여야 합니다"
                    elif not name:
                        problem = "이름 칸이 비어 있습니다"
                    elif not value:
                        problem = "값 칸이 비어 있습니다"
                    elif year and not re.fullmatch(r"[0-9]{4}", year):
                        problem = f"기준 연도 '{year}' 는 4자리 숫자여야 합니다"
                    else:
                        rows.append((no, InfoItem(rank=int(re.search(r"[1-5]", rank)[0]),
                                                 name=name, description=description, value=value,
                                                 year=year, scene=scene)))
            else:
                problem = f"양식에 없는 줄입니다: {line[:50]}"
        if problem:
            errors.append(f"{no}번째 줄: {problem}")
    if not data["title"]:
        errors.append("제목: 줄이 없습니다")
    if not data["source_note"]:
        errors.append("출처: 줄이 없습니다")
    if len(rows) != 5:
        errors.append(f"순위 표는 정확히 5줄이어야 합니다 (현재 {len(rows)}줄)")
    elif not competition_ranks_ok([item.rank for _, item in rows]):
        for no, _ in rows:
            errors.append(f"{no}번째 줄: 순위가 공동 순위 규칙에 맞지 않습니다 (예: 5, 3, 3, 2, 1)")
    if errors:
        raise QuickParseInfoError(errors)
    ordered = sorted(rows, key=lambda row: -row[1].rank)
    data["items"] = [item.model_copy(update={"rank": slot, "display_rank": item.rank})
                     for slot, (_, item) in zip(range(5, 0, -1), ordered)]
    return InfoProject.model_validate(data)
