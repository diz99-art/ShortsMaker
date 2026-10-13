"""순위 표 로직 단위 테스트 — 한글 숫자 변환, 이름 보호, 가사 → 표 추출, 공동 순위, 표 열 밀림.

흐름 테스트(test_auto_pipeline·test_quick·test_subtitle_numbers)에 흩어져 있던 것을 모으고 경계 사례를 더했다.
"""
import json
from pathlib import Path

import pytest

from shortsmaker import auto_pipeline as ap
from shortsmaker.lyrics_align import clean_lyrics_text
from shortsmaker.models import competition_ranks_ok
from shortsmaker.rank_extract import (
    RankRow,
    _skipped_by_tie,
    assign_slots,
    column_problems,
    display_text,
    extract_ranks,
    korean_to_int,
    label,
    normalize_numbers,
    rows_complete,
    team_colors,
)
from tests.test_auto_pipeline import FA, FA_TIE, LOSING
from tests.test_quick import HR_SONG

NAMES = ["박병호", "심정수", "이승엽", "넥센 히어로즈", "삼성 라이온즈"]


def rows_of(ranks: list[int]) -> list[RankRow]:
    return [RankRow(rank=r, team="t", player=f"p{i}", record="1") for i, r in enumerate(ranks)]


# ---------------------------------------------------------------- 한글 숫자 → 아라비아 숫자

@pytest.mark.parametrize("word,value", [("십육", 16), ("이천십", 2010), ("천구백팔십오", 1985), ("일구팔오", 1985),
                                        ("백오십이억", 15_200_000_000), ("백이십사", 124), ("사과", None),
                                        ("구", 9), ("이십삼", 23), ("구십구", 99), ("천이", 1002),
                                        ("이천이십육", 2026),
                                        ("열", None), ("스물", None)])  # 고유어는 normalize_numbers 에서 따로
def test_korean_numbers(word, value):
    assert korean_to_int(word) == value


def test_normalize_only_before_units():
    assert normalize_numbers("십육 연패") == "16연패"
    assert normalize_numbers("이천십 년의 기록") == "2010년의 기록"
    assert normalize_numbers("이야기 사라진") == "이야기 사라진"  # '이', '사' 는 숫자로 안 바꿈
    assert display_text("지금부터 케이비오 최장 연패 탑파이브") == "지금부터 KBO 최장 연패 TOP 5"


@pytest.mark.parametrize("text,expected", [
    ("열여섯 번째 시즌", "16번째 시즌"),      # 고유어 숫자 + 단위
    ("스물두 경기", "22경기"),
    ("십 회말 역전", "10회말 역전"),          # '회' 뒤 '말'·'초' 는 낱말로 보지 않음
    ("이천이십육 년", "2026년"),
    ("일구팔이 년", "1982년"),                # 한 자리씩 읽은 연도
    ("백오십 억 원", "150억 원"),
    ("16 연패", "16연패"),                    # 숫자와 단위 사이 공백 제거
    ("2026 홈런 전쟁", "2026 홈런 전쟁"),     # 연도는 '년' 에만 붙인다
    ("사이 좋게", "사이 좋게"),               # 한 자리씩 이어진 글자는 연도가 아니면 숫자 아님
    ("오일 동안", "오일 동안"),
    ("세 번", "세 번"),                       # 한 글자 고유어 숫자는 그대로
])
def test_normalize_edges(text, expected):
    assert normalize_numbers(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("이십오년", "25년"),
    ("오십육 홈런", "56홈런"),
    ("서른한 개 홈런", "31개 홈런"),
    ("공동 3위 박병호 오십삼, 이천십오", "공동 3위 박병호 53, 2015"),     # 연도 + 표의 기록 숫자
    ("2위 이승엽 삼성 오십사", "2위 이승엽 삼성 54"),
    ("이천이십육 홈런 전쟁", "2026 홈런 전쟁"),                          # 연도는 다른 단위에 붙이지 않음
    ("십육 연패, 십 회말", "16연패, 10회말"),
    ("열두 번, 스무 경기, 이십 타점, 칠 승", "12번, 20경기, 20타점, 칠 승"),
    ("오늘 구단 삼진 이유 사이", "오늘 구단 삼진 이유 사이"),
    ("이번 시즌 이 경기 사회를", "이번 시즌 이 경기 사회를"),
    ("딱 세 개 더, 한 번 더", "딱 세 개 더, 한 번 더"),
    ("1위 이승엽 오십육 홈런", "1위 이승엽 56홈런"),
])
def test_display_rules(text, expected):
    assert display_text(text, NAMES, [52, 53, 54, 56]) == expected


def test_record_number_only_from_table():
    assert display_text("삼성 오십사", NAMES) == "삼성 오십사"      # 표에 없는 값이면 단위 없이는 그대로


# ---------------------------------------------------------------- 이름 속 한글 숫자 ('이승엽' → '2승엽' 버그)

@pytest.mark.parametrize("text,expected", [
    ("1위 이승엽 오십육 홈런", "1위 이승엽 56홈런"),
    ("2위 이승엽 삼성 오십사", "2위 이승엽 삼성 오십사"),
    ("이 승리는 값지다", "이 승리는 값지다"),
    ("십승을 거둔 투수", "10승을 거둔 투수"),
    ("백억은 기본", "백억은 기본"),
    ("최고 몸값은 백오십이억!", "최고 몸값은 152억!"),
    ("이대호, 부산의 자존심", "이대호, 부산의 자존심"),
    ("오십육 홈런", "56홈런"),
])
def test_numbers_never_touch_names(text, expected):
    assert display_text(text) == expected


def test_protect_list_and_extract_keeps_names():
    assert normalize_numbers("구승엽 구승", ["구승엽"]) == "구승엽 9승"
    assert normalize_numbers("이 경기") == "이 경기"                 # 띄어 쓴 한 글자 숫자는 그대로 (이 = 이것)
    assert normalize_numbers("이대호 이십 홈런") == "이대호 20홈런"  # 보호 목록이 없어도 단위 앞 숫자만 바꿈
    rows = extract_ranks(HR_SONG.splitlines())
    assert [r.player for r in rows if r.rank in (2, 1)] == ["이승엽", "이승엽"]


# ---------------------------------------------------------------- 가사 → 표

def test_extract_losing_streak():
    rows = extract_ranks(clean_lyrics_text(LOSING))
    assert [(r.rank, r.team, r.record) for r in rows] == [
        (5, "KIA 타이거즈", "16연패"), (4, "롯데 자이언츠", "16연패"), (3, "쌍방울 레이더스", "17연패"),
        (2, "한화 이글스", "18연패"), (1, "삼미 슈퍼스타즈", "18연패")]
    assert all(r.player == "" for r in rows)  # 가사에 없는 값은 비워 두고 사람이 채움
    assert any("선수" in p for p in rows_complete(rows))


def test_extract_fa_tie_two_players_no_third():
    rows = extract_ranks(clean_lyrics_text(FA_TIE))
    assert [r.rank for r in rows] == [5, 4, 2, 2, 1]                  # 3위 행 없음
    assert [r.player for r in rows] == ["오지환", "박민우", "나성범", "이대호", "양의지"]
    assert [label(rows, r) for r in rows] == ["5위", "4위", "공동 2위", "공동 2위", "1위"]
    assert rows_complete(rows) == ["공동 2위 팀이(가) 비어 있습니다"]   # 이대호 팀(가사엔 '부산')만 채우면 됨
    rows[3] = rows[3].model_copy(update={"team": "롯데 자이언츠"})
    assert rows_complete(rows) == []
    assert [(r.slot, r.rank) for r in assign_slots(rows)] == [(5, 5), (4, 4), (3, 2), (2, 2), (1, 1)]


def test_extract_only_one_tied_player_in_lyrics():
    rows = extract_ranks(clean_lyrics_text(FA))  # 원래 가사에 공동 2위가 1명만 나오는 경우
    assert [r.rank for r in rows] == [5, 4, 3, 2, 1]
    assert rows[2].team == rows[2].player == ""  # 3위는 빈 행 → 공동 2위를 하나 더 적거나 3위를 채움
    assert any("5개" in p for p in rows_complete(rows))
    rows[2] = rows[2].model_copy(update={"rank": 2, "team": "롯데 자이언츠", "player": "이대호", "record": "150억"})
    assert rows_complete(rows) == []                               # 3위 칸을 공동 2위로 바꾸면 확정 가능


# ---------------------------------------------------------------- 공동 순위 (경쟁 순위: 1, 2, 2, 4, 5)

@pytest.mark.parametrize("ranks,ok", [([5, 4, 2, 2, 1], True), ([5, 4, 3, 1, 1], True), ([5, 2, 2, 2, 1], True),
                                      ([4, 4, 3, 2, 1], True),    # 공동 4위 2명 → 5위 없음
                                      ([5, 3, 3, 3, 1], False),   # 1위 다음 공동이면 공동 2위여야 함
                                      ([5, 4, 3, 2, 2], False), ([5, 4, 3, 2, 1], True)])
def test_tie_rule(ranks, ok):
    assert (rows_complete(rows_of(ranks)) == []) is ok


@pytest.mark.parametrize("ranks,ok", [([1, 1, 1, 1, 1], True),    # 다섯 명 모두 공동 1위
                                      ([5, 3, 3, 2, 1], True),    # 공동 3위 → 4위 없음
                                      ([5, 5, 5, 5, 5], False),   # 1위가 없음
                                      ([5, 4, 3, 2, 0], False)])  # 0위
def test_competition_ranks_edges(ranks, ok):
    assert competition_ranks_ok(ranks) is ok


def test_tie_slots_and_labels():
    """자리(slot)는 화면 순서 5→1 로 하나씩, 순위 글자는 공동을 그대로."""
    all_first = rows_of([1, 1, 1, 1, 1])
    assert [(r.slot, r.rank) for r in assign_slots(all_first)] == [(5, 1), (4, 1), (3, 1), (2, 1), (1, 1)]
    assert {label(all_first, r) for r in all_first} == {"공동 1위"}
    fourth = rows_of([4, 4, 3, 2, 1])
    assert [label(fourth, r) for r in fourth] == ["공동 4위", "공동 4위", "3위", "2위", "1위"]


def test_skipped_rank_after_tie():
    """공동 순위 뒤에 비는 순위 (공동 2위 2명이면 3위 없음) 는 가사에서 찾지 않는다."""
    assert _skipped_by_tie([5, 4, 2, 2, 1], 3)
    assert not _skipped_by_tie([5, 4, 2, 2, 1], 4)
    assert not _skipped_by_tie([5, 4, 3, 2, 1], 3)


# ---------------------------------------------------------------- 표 열 밀림

def test_column_shift_detected():
    shifted = [RankRow(rank=5, player="52홈런", team="박병호", record="2014", year="힘차게 풀스윙하는 타격 자세")]
    probs = " ".join(column_problems(shifted))
    assert "기록처럼" in probs and "4자리" in probs and "연도처럼" in probs and "구단 사전" in probs
    ok = [RankRow(rank=5, player="박병호", team="넥센 히어로즈", record="52홈런", year="2014")]
    assert column_problems(ok) == []
    row = RankRow(rank=5, player="오타니", team="LA 다저스", record="54홈런")
    assert column_problems([row], allow_unknown_team=True) == []


def test_column_shift_single_cells():
    """한 칸만 밀려도 잡는다: 선수 칸의 숫자, 팀 칸의 기록."""
    year_in_player = RankRow(rank=5, player="2014", team="넥센 히어로즈", record="52홈런", year="2014")
    assert any("기록처럼" in p for p in column_problems([year_in_player]))
    record_in_team = RankRow(rank=5, player="박병호", team="52홈런", record="52홈런", year="2014")
    assert any("구단 사전" in p for p in column_problems([record_in_team]))
    no_year = RankRow(rank=5, player="박병호", team="넥센 히어로즈", record="52홈런", year="")
    assert column_problems([no_year]) == []                       # 연도는 비워도 됨


def test_team_colors_no_logo_data():
    assert team_colors("기아 타이거즈") == ("#EA0029", "#06141F")
    data = json.loads((Path(ap.__file__).parent / "data" / "kbo_teams.json").read_text(encoding="utf-8"))
    assert all(set(t) == {"name", "short", "aliases", "colors"} for t in data["teams"])  # 로고 항목 없음
