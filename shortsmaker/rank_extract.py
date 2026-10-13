"""가사 → 5위~1위 순위 표 (무료 규칙 기반, 외부 API 없음).

가사에서 "N위" 줄을 찾고, 그 순위 구간(다음 순위 직전까지)의 가사에서
팀(KBO 현재·과거 구단 사전), 기록(숫자+단위), 연도를 뽑는다. 한글로 부른 숫자("십육 연패",
"이천십 년")도 숫자로 바꾼다. 가사에 없는 값(선수 이름 등)은 비워 두고 사람이 표에서 채운다.

결과는 초안일 뿐이다. 화면에서 사용자가 표를 확인·수정해야 다음 단계로 넘어간다.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from .models import LyricLine, competition_ranks_ok

TEAMS_FILE = Path(__file__).parent / "data" / "kbo_teams.json"

DIGITS = {"영": 0, "공": 0, "일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "륙": 6, "칠": 7, "팔": 8, "구": 9}
SMALL_UNITS = {"십": 10, "백": 100, "천": 1000}
BIG_UNITS = {"만": 10_000, "억": 100_000_000}
NUM_CHARS = "".join(DIGITS) + "".join(SMALL_UNITS) + "".join(BIG_UNITS)

# 기록 단위 (긴 것부터). 숫자 바로 뒤(공백 허용)에 와야 기록으로 본다.
RECORD_UNITS = ["연패", "연승", "홈런", "세이브", "탈삼진", "삼진", "타점", "도루", "안타", "경기", "이닝",
                "승", "패", "억 원", "억원", "억", "만 원", "만원", "만", "회", "개", "점", "년", "%"]


class RankRow(BaseModel):
    rank: int = Field(ge=1, le=5)  # 보이는 순위 (공동 2위 2명이면 두 행 모두 2)
    slot: int | None = Field(default=None, ge=1, le=5)  # 영상 속 자리 5→1 (확정할 때 정함, 한 번씩)
    team: str = ""
    player: str = ""
    record: str = ""     # 화면·카드 표기 (예: "16연패")
    year: str = ""       # 예: "2010"
    lyric: str = ""      # 근거 가사 (순위 구간 첫 줄)
    scene: str = ""      # 캐리커처 장면 설명 (악수, 기자회견 등 — 사용자가 고침)
    stat_line: str = ""  # 스티커형 빨간 기록 줄 (1~2줄, 줄바꿈 '\n')
    start: float | None = None  # 그 순위를 부르기 시작한 시각 (가사 맞추기 결과)


# ---------------------------------------------------------------- 한글 숫자

def korean_to_int(word: str) -> int | None:
    """'십육'→16, '이천십'→2010, '천구백팔십오'→1985, '백오십이억'→15200000000. 숫자가 아니면 None."""
    if not word or any(c not in NUM_CHARS for c in word):
        return None
    if all(c in DIGITS for c in word) and len(word) > 1:  # '일구팔오' 처럼 한 자리씩 읽은 경우
        return int("".join(str(DIGITS[c]) for c in word))
    total = section = digit = 0
    seen = False
    for c in word:
        if c in DIGITS:
            digit = DIGITS[c]
            seen = True
        elif c in SMALL_UNITS:
            section += (digit or 1) * SMALL_UNITS[c]
            digit = 0
            seen = True
        elif c in BIG_UNITS:
            section += digit
            total += (section or 1) * BIG_UNITS[c]
            section = digit = 0
            seen = True
    return total + section + digit if seen else None


_KNUM = re.compile(f"[{NUM_CHARS}]+")

# 고유어 숫자 (열 이상만 바꾼다: '서른한 개'→'31개'. '세 개', '한 번'은 읽기 쉬워 그대로)
NATIVE_TENS = {"열": 10, "스물": 20, "스무": 20, "서른": 30, "마흔": 40, "쉰": 50, "예순": 60, "일흔": 70,
               "여든": 80, "아흔": 90}
NATIVE_ONES = {"하나": 1, "한": 1, "둘": 2, "두": 2, "셋": 3, "세": 3, "넷": 4, "네": 4, "다섯": 5, "여섯": 6,
               "일곱": 7, "여덟": 8, "아홉": 9}
_NATIVE = re.compile("(" + "|".join(sorted(NATIVE_TENS, key=len, reverse=True)) + ")("
                     + "|".join(sorted(NATIVE_ONES, key=len, reverse=True)) + ")?")

PARTICLES = set("은는이가을를의도만에과와로째")
# 자막 표기에서 숫자로 바꾸는 단위 (이 단위 바로 앞의 숫자만 바꾼다)
DISPLAY_UNITS = sorted(set(RECORD_UNITS + ["위", "년", "시즌", "번"]), key=len, reverse=True)
# 숫자 글자로 시작하고 바로 뒤에 단위 글자가 붙어 헷갈리는 흔한 낱말
COMMON_WORDS = ("이번", "사회", "육회", "일개", "이년", "구회")


def _is_year(word: str, value: int | None) -> bool:
    """'이천십오'→2015, '일구팔오'→1985 처럼 연도로 읽은 숫자."""
    return value is not None and 1900 <= value <= 2099 and ("천" in word or len(word) == 4)


def _protected(text: str, protect: Iterable[str]) -> tuple[str, list[str]]:
    """이름(선수·팀)을 자리표시로 바꿔 숫자 변환에서 빼낸다. 긴 이름부터."""
    names = sorted({n for n in protect if n and len(n) >= 2}, key=len, reverse=True)
    kept: list[str] = []
    for n in names:
        if n in text:
            text = text.replace(n, f"\ue000{len(kept)}\ue001")
            kept.append(n)
    return text, kept


def _restore(text: str, kept: list[str]) -> str:
    return re.sub("\ue000(\\d+)\ue001", lambda m: kept[int(m.group(1))], text)


def normalize_numbers(text: str, protect: Iterable[str] = (), numbers: Iterable[str | int] = ()) -> str:
    """가사 속 한글 숫자를 아라비아 숫자로 ('십육 연패'→'16연패', '오십육 홈런'→'56홈런', '이천십 년'→'2010년').

    - 단위(홈런·개·년·위·승·경기·번·회 …) 바로 앞의 숫자만 바꾼다 (일반 낱말 속 '이', '사' 등은 그대로)
    - 연도로 읽은 숫자('이천십오', '일구팔오')는 단위가 없어도 '2015'
    - numbers: 표의 기록 숫자. 같은 값이면 단위 없이 불러도 바꾼다 ('2위 이승엽 삼성 오십사'→'… 54')
    - 고유어는 열 이상만 ('서른한 개'→'31개', '세 개'는 그대로)
    - 사람·팀 이름은 절대 바꾸지 않는다: protect 로 받은 이름과 구단 사전의 이름은 미리 빼 둔다 ('이승엽'→그대로)
    - 단위 바로 뒤에 조사가 아닌 한글이 이어지면 낱말의 일부로 본다 ('이승엽', '이승리')
    - 한 글자 숫자는 단위와 붙어 있을 때만 ('이 경기'의 '이'는 '이것'이라는 뜻일 수 있다)
    - '억'·'만'으로 끝나는 한 글자 숫자('백억', '이억')는 그대로, 두 글자 이상('백오십이억')만 '152억'
    """
    teams, _ = load_teams()
    team_names = [n for t in teams for n in (t.name, *t.aliases)]
    work, kept = _protected(text, list(protect) + team_names)
    wanted = {int(n) for n in numbers if int(n) >= 10}

    def unit_after(rest: str) -> tuple[str | None, str, bool]:
        """(뒤따르는 단위, 단위 뒤 한 글자, 숫자와 단위 사이 공백 여부)."""
        for u in DISPLAY_UNITS:
            m = re.match(r"(\s*)" + re.escape(u), rest)
            if m:
                return u, rest[m.end():m.end() + 1], bool(m.group(1))
        return None, "", False

    def word_like(ch: str) -> bool:
        return bool(ch and re.match(r"[가-힣]", ch) and ch not in PARTICLES)

    def hangul_before(src: str, m: re.Match[str]) -> bool:
        return bool(re.match(r"[가-힣]", src[max(0, m.start() - 1):m.start()]))

    def sino(m: re.Match[str]) -> str:
        word = m.group(0)
        if hangul_before(work, m) or any(work.startswith(c, m.start()) for c in COMMON_WORDS):
            return word
        rest = work[m.end():]
        if word[-1] in BIG_UNITS and len(word) > 1:  # '백오십이억' → '152억' (단위는 살린다)
            if len(word) < 3 or word_like(rest[:1]):
                return word
            head = korean_to_int(word[:-1])
            return f"{head}{word[-1]}" if head is not None else word
        value = korean_to_int(word)
        if value is None:
            return word
        unit, after, spaced = unit_after(rest)
        if _is_year(word, value):
            return word if unit is None and word_like(rest[:1]) else str(value)
        if all(c in DIGITS for c in word) and len(word) > 1:
            return word  # '사이', '오일' 처럼 한 자리씩 이어진 글자는 연도(4자리)가 아니면 숫자로 보지 않는다
        if unit is not None:
            inning = unit == "회" and after in ("말", "초")  # '십 회말' → '10회말'
            return word if (word_like(after) and not inning) or (word in DIGITS and spaced) else str(value)
        if value in wanted and len(word) > 1 and not word_like(rest[:1]):
            return str(value)
        return word

    work = _KNUM.sub(sino, work)

    def native(m: re.Match[str]) -> str:
        if hangul_before(work, m):
            return m.group(0)
        unit, after, spaced = unit_after(work[m.end():])
        if unit is None or word_like(after):
            return m.group(0)
        return str(NATIVE_TENS[m.group(1)] + (NATIVE_ONES[m.group(2)] if m.group(2) else 0))

    out = _NATIVE.sub(native, work)
    # '16 연패' → '16연패' (숫자와 단위 사이 공백 제거). 연도는 '년'만 붙인다 ('2026 홈런 전쟁')
    units = "|".join(re.escape(u) for u in DISPLAY_UNITS)

    def glue(g: re.Match[str]) -> str:
        if re.fullmatch(r"(19|20)\d\d", g.group(1)) and g.group(2) != "년":
            return g.group(0)
        return g.group(1) + g.group(2)

    out = re.sub(rf"(?<![\d.])(\d+)\s+({units})", glue, out)
    return _restore(out, kept)


def display_text(text: str, protect: Iterable[str] = (), numbers: Iterable[str | int] = ()) -> str:
    """자막 표시용: 숫자 정리 + 부르는 말 → 표기 ('케이비오'→'KBO', '탑파이브'→'TOP 5'). 이름은 바꾸지 않는다."""
    t = normalize_numbers(text, protect, numbers)
    for a, b in [("케이비오", "KBO"), ("탑파이브", "TOP 5"), ("탑 파이브", "TOP 5"), ("탑텐", "TOP 10")]:
        t = t.replace(a, b)
    return t


# ---------------------------------------------------------------- 팀

@dataclass(frozen=True)
class Team:
    name: str
    short: str
    aliases: tuple[str, ...]
    colors: tuple[str, str]


@lru_cache(maxsize=1)
def load_teams(path: str = str(TEAMS_FILE)) -> tuple[list[Team], tuple[str, str]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    teams = [Team(t["name"], t["short"], tuple(t["aliases"]), tuple(t["colors"])) for t in data["teams"]]
    return teams, tuple(data["default_colors"])


def find_team(text: str) -> Team | None:
    """가사에서 가장 긴 이름이 맞는 구단. '기아 타이거즈' → KIA 타이거즈."""
    teams, _ = load_teams()
    best: tuple[int, Team] | None = None
    for team in teams:
        for alias in (team.name, *team.aliases):
            if alias and alias in text and (best is None or len(alias) > best[0]):
                best = (len(alias), team)
    return best[1] if best else None


def team_colors(name: str) -> tuple[str, str]:
    teams, default = load_teams()
    t = find_team(name)
    return t.colors if t else default


# ---------------------------------------------------------------- 추출

_RANK = re.compile(r"(공동\s*)?(\d)\s*위")
_YEAR = re.compile(r"(?<!\d)(19[5-9]\d|20[0-4]\d)(?:\s*년)?(?!\d)")


def _record(text: str) -> str:
    units = "|".join(re.escape(u) for u in sorted(RECORD_UNITS, key=len, reverse=True) if u not in ("년",))
    m = re.search(rf"(\d[\d,.]*)\s*({units})", text)
    return f"{m.group(1)}{m.group(2).replace(' ', ' ')}" if m else ""


def _player(head: str) -> str:
    """'N위' 바로 뒤 첫 마디가 팀 이름이 아니면 선수 이름으로 본다 ('5위 오지환, LG의 심장' → 오지환)."""
    first = re.split(r"[,!?.·\n]", head.strip(), maxsplit=1)[0].strip()
    first = re.sub(r"^(는|은)\s*", "", first).strip()
    if first and not re.fullmatch(r"[가-힣]{2,4}", first):  # 쉼표 없이 이어 부름: '이승엽 삼성 오십사 홈런'
        first = first.split()[0]
    if not first or find_team(first) or _record(first) or not re.fullmatch(r"[가-힣]{2,4}", first):
        return ""
    return first


def extract_ranks(lines: list[LyricLine] | list[str]) -> list[RankRow]:
    """5위→1위 순서의 표 초안.

    "공동 N위" 는 같은 순위가 여러 번 나와도 모두 항목으로 잡는다 (공동 2위 2명 → 2, 2 / 3위 없음).
    공동이 아닌 같은 순위가 다시 나오면(후렴 반복 등) 처음 것만 쓴다.
    가사에 없는 순위 중 공동 순위로 설명되지 않는 것은 빈 행으로 남겨 사람이 채우게 한다.
    """
    items = [(l.text, l.start) if isinstance(l, LyricLine) else (l, None) for l in lines]
    norm = [normalize_numbers(t) for t, _ in items]
    entries: list[tuple[int, int]] = []  # (순위, 줄 번호) 가사에 나온 순서
    for i, t in enumerate(norm):
        m = _RANK.search(t)
        if not m or not 1 <= int(m.group(2)) <= 5:
            continue
        rank, tied = int(m.group(2)), bool(m.group(1))
        if any(r == rank for r, _ in entries) and not tied:
            continue
        entries.append((rank, i))
    starts = sorted(i for _, i in entries)
    rows: list[RankRow] = []
    for rank, i in entries:
        nxt = next((s for s in starts if s > i), len(norm))
        block = norm[i:nxt]
        rank_m = _RANK.search(block[0])
        assert rank_m is not None  # entries 는 순위 낱말이 있는 줄만 모은 것
        head = block[0][rank_m.end():]  # 'N위' 뒤 (팀·선수 이름이 있는 곳)
        team = find_team(" ".join(block))
        record = _record(head) or next((r for r in (_record(b) for b in block[1:3]) if r), "")
        year = next((m.group(1) for b in block for m in [_YEAR.search(b)] if m), "")
        stat = display_text(items[i + 1][0]) if i + 1 < nxt else ""  # 기록 줄 초안: 순위 다음 가사 줄
        rows.append(RankRow(rank=rank, team=team.name if team else "", player=_player(head), record=record,
                            year=year, lyric=items[i][0], start=items[i][1], stat_line=stat))
    # 공동 순위로 설명되지 않는 빠진 순위만 빈 행 (공동 2위 2명이면 3위는 원래 없음)
    found = [r.rank for r in rows]
    for rank in range(1, 6):
        if rank not in found and len(rows) < 5 and not _skipped_by_tie(found, rank):
            rows.append(RankRow(rank=rank))
    return sorted(rows, key=lambda r: -r.rank)


def _skipped_by_tie(ranks: list[int], rank: int) -> bool:
    """공동 순위 때문에 원래 없는 순위인가 (공동 2위 2명 → 3위)."""
    better = [r for r in set(ranks) if r < rank]
    if not better:
        return False
    top = max(better)
    return ranks.count(top) > 1 and top + ranks.count(top) > rank


def assign_slots(rows: list[RankRow]) -> list[RankRow]:
    """보이는 순위가 낮은 것부터(5위→1위) 영상 자리 5→1. 공동이면 표에 적힌 순서대로."""
    order = sorted(range(len(rows)), key=lambda i: (-rows[i].rank, i))
    out = [rows[i].model_copy(update={"slot": 5 - k}) for k, i in enumerate(order)]
    return out


def label(rows: list[RankRow], row: RankRow) -> str:
    return ("공동 " if sum(1 for r in rows if r.rank == row.rank) > 1 else "") + f"{row.rank}위"


def missing_fields(row: RankRow) -> list[str]:
    """표에서 사람이 채워야 할 칸. 가사에 구단 이름이 없고 별명('부산의 자존심')만 있으면 팀은 비워 둔다."""
    names = {"team": "팀", "player": "선수", "record": "기록"}  # 연도는 선택 (없으면 카드에서 빠짐)
    return [label for field, label in names.items() if not getattr(row, field).strip()]


def column_problems(rows: list[RankRow], *, allow_unknown_team: bool = False) -> list[str]:
    """열이 밀린 것 같은 값 ('선수' 칸에 '52홈런', '팀' 칸에 선수 이름, '연도' 칸에 문장)."""
    out = []
    players = {r.player for r in rows if r.player}
    for r in rows:
        who = label(rows, r) if r.rank else "?"
        if r.player and re.search(r"\d", r.player):
            out.append(f"{who} 선수 칸 '{r.player}'이(가) 기록처럼 보입니다 — 열이 밀리지 않았는지 확인하세요")
        if r.team and not find_team(r.team):
            if r.team in players:
                out.append(f"{who} 팀 칸 '{r.team}'이(가) 선수 이름입니다 — 열이 밀리지 않았는지 확인하세요")
            elif not allow_unknown_team:
                out.append(f"{who} 팀 '{r.team}'이(가) 구단 사전에 없습니다 (맞으면 '사전에 없는 팀 허용' 체크)")
        if r.year and not re.fullmatch(r"\d{4}", r.year.strip()):
            out.append(f"{who} 연도 칸 '{r.year[:20]}'이(가) 4자리 숫자가 아닙니다 — 장면 설명이 들어갔나요?")
        if r.record and re.fullmatch(r"\d{4}", r.record.strip()):
            out.append(f"{who} 기록 칸 '{r.record}'이(가) 연도처럼 보입니다 — 열이 밀리지 않았는지 확인하세요")
    return out


def rows_complete(rows: list[RankRow], *, need_team: bool = True) -> list[str]:
    """다음 단계로 가기 전 확인. 문제 목록 (비어 있으면 통과).

    5명(항목 5개)이어야 하고, 순위는 공동 순위 규칙(1, 2, 2, 4, 5)을 따라야 한다.
    공동 2위가 2명이면 3위 행은 없어도 된다 (빈 3위 행은 무시).
    """
    problems = []
    filled = [r for r in rows if r.team.strip() or r.player.strip() or r.record.strip()]
    ranks = sorted(r.rank for r in filled)
    if len(filled) != 5:
        problems.append(f"항목은 5개여야 합니다 (현재 {len(filled)}개). 공동 순위면 같은 순위를 두 행에 적으세요")
    elif not competition_ranks_ok(ranks):
        problems.append(f"순위가 공동 순위 규칙에 맞지 않습니다 (현재 {ranks}). "
                        "예: 공동 2위 2명이면 1, 2, 2, 4, 5 (3위 없음)")
    for r in filled:
        for field, name in [("team", "팀"), ("player", "선수"), ("record", "기록")]:
            if not getattr(r, field).strip() and (need_team or field != "team"):  # 하늘 배지형: 구분(team)은 비어도 됨
                problems.append(f"{label(filled, r)} {name}이(가) 비어 있습니다")
    empty_needed = [r for r in rows if r not in filled and not _skipped_by_tie([f.rank for f in filled], r.rank)]
    for r in empty_needed:
        if len(filled) < 5:
            problems.append(f"{r.rank}위 행이 비어 있습니다")
    return list(dict.fromkeys(problems))
