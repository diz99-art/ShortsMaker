"""하늘 배지형 빠른 양식 → Project (또는 빠른 제작용 QuickData).

사용: project = parse_quick_info(text)  /  빠른 제작: data = parse_info_quick(text)
잘못된 입력은 QuickParseInfoError.errors(줄별 오류 목록)로 확인할 수 있다.
InfoItem의 year/scene, InfoProject의 memos는 model_dump()에도 보존된다.
보너스: '6위 이름 | 구분 | 값 | 기준 연도' 또는 '예고 | 다음 편 문구'.
"""
from __future__ import annotations

import re

from pydantic import Field

from .models import Bonus, Item, Project, competition_ranks_ok
from .quick_parse import UNSURE, QuickData, _clean
from .rank_extract import RankRow

COLUMNS = ["순위", "이름", "구분", "값", "기준 연도", "그림 설명"]
CHANNEL = "sky_badge"  # 하늘 배지형 (@sky_badge). 예전 클린 카드는 info_v2

EXAMPLE = """제목: 지구에서 사람 제일 많은 나라 / 세계 인구 TOP 5
후킹: 인구 1위는 중국일까?
출처: World Bank | https://data.worldbank.org/indicator/SP.POP.TOTL
값 이름: 인구
순위 | 이름 | 구분 | 값 | 기준 연도 | 그림 설명
5 | 파키스탄 | 아시아 | 2.5억 명 | 2024 | 붐비는 시장 거리
4 | 인도네시아 | 아시아 | 2.8억 명 | 2024 | 섬이 많은 바다 풍경
3 | 미국 | 북아메리카 | 3.4억 명 | 2024 | 대도시 스카이라인
2 | 중국 | 아시아 | 14.1억 명 | 2024 | 사람이 가득한 광장
1 | 인도 | 아시아 | 14.5억 명 | 2024 | 활기찬 기차역
메모: 2023년에 인도가 중국을 넘어 인구 1위가 되었다
말풍선: 몇 명일까? / 인도 1위! / 구독 꾹!"""


def request_prompt(topic: str) -> str:
    """ChatGPT 에 하늘 배지형 양식으로 답하게 하는 프롬프트."""
    return f"""주제: {topic or '(주제를 적어 주세요)'}
위 주제의 TOP 5 를 유튜브 쇼츠 '하늘 배지형'용으로 정리해줘. 아래 양식 그대로, 다른 설명 없이 답해.

규칙
- 공식 통계(국제기구·정부 기관)나 믿을 만한 기사로 확인된 값만 쓴다. 확인하지 못한 칸은 '확인 필요'라고 적는다.
- 출처는 실제로 확인한 페이지 1개 (이름 | URL).
- 공동 순위는 같은 숫자로 적고 다음 순위는 건너뛴다 (예: 5, 3, 3, 2, 1). 항목은 정확히 5줄.
- 구분은 이름 아래 작게 나올 분류 (예: 아시아, 전자제품). 없으면 비워도 된다.
- 값은 숫자+단위 (예: 14.5억 명, 828m). 기준 연도는 4자리 (값을 잰 해).
- 값 이름은 값이 무엇인지 2~6글자 (예: 평균 연봉, 인구, 높이).
- 그림 설명은 오른쪽 캐릭터 그림용 모습·소품 (국기·로고·상표 글씨는 넣지 말 것).
- 메모는 가사·해설에 쓸 사실 2~5줄 (출처에 있는 사실만, 줄마다 '메모:'로 시작).
- 후킹은 첫 2초에 띄울 짧은 질문 (15자 안팎).
- 보너스는 TOP 5 바로 밖 6위 (출처에 있는 값만) 또는 '예고 | 다음 편 주제'.
- 말풍선은 마스코트가 노래 쉬는 틈에 할 말 '인트로 / 1위 / 엔딩' (각 3~8글자).

양식
제목: 1줄 / 2줄
후킹: 첫 2초 질문
출처: 이름 | URL
값 이름: 값이 무엇인지
순위 | 이름 | 구분 | 값 | 기준 연도 | 그림 설명
5 | 이름 | 구분 | 값 | 기준 연도 | 그림 설명
(5줄)
메모: 가사·해설에 쓸 사실
보너스: 6위 이름 | 구분 | 값 | 기준 연도
말풍선: 인트로 / 1위 / 엔딩"""


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
    data = dict(channel=CHANNEL, title="", source_note="", hook="", value_label="",
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
            meta = re.match(r"^(제목|후킹|출처|보너스|메모|말풍선|값 ?이름)\s*[:：]\s*(.*)$", line)
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
                        data["source_note"] = f"출처: {parts[0]} ({parts[1]})"  # 야구한장과 같은 형식
                elif key == "후킹":
                    if len(val) > 30:
                        problem = f"후킹 문구가 너무 깁니다 ({len(val)}자, 30자 이내)"
                    data["hook"] = val
                elif key.replace(" ", "") == "값이름":
                    if len(val) > 12:
                        problem = f"값 이름이 너무 깁니다 ({len(val)}자, 12자 이내 — 예: 평균 연봉)"
                    data["value_label"] = val
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


def parse_info_quick(text: str) -> QuickData:
    """빠른 제작 화면용: 하늘 배지형 양식 → QuickData (선수=이름, 팀=구분, 기록=값, 연도=기준 연도).
    오류는 예외 대신 errors 목록으로 (야구한장 parse_quick 과 같은 모양)."""
    q = QuickData()
    try:
        p = parse_quick_info(text)
    except QuickParseInfoError as e:
        q.errors = list(e.errors)
        return q
    m = re.fullmatch(r"출처: (.+) \((\S+)\)", p.source_note)
    q.title, q.hook, q.bubbles, q.memos = p.title, p.hook, dict(p.bubbles), list(p.memos)
    q.value_label = p.value_label
    q.source_name, q.source_url = (m[1], m[2]) if m else (p.source_note, "")
    q.bonus = p.bonus.model_dump() if p.bonus else None
    q.rows = [RankRow(rank=it.display_rank or it.rank, player=it.name, team=it.description, record=it.value,
                      year=it.year, scene=it.scene) for it in p.items]  # 5→1 (공동은 표 순서)
    return q
