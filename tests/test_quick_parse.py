"""자료 양식 읽기 단위 테스트 — ChatGPT 답변(정해진 양식) → 제목·출처·순위 표 (야구한장 quick_parse).

하늘 배지형 양식(quick_parse_info)은 test_quick_parse_info.py 에 있다.
"""
import pytest

from shortsmaker.quick_parse import EXAMPLE, parse_quick, request_prompt


def test_parse_example_and_errors():
    q = parse_quick("```\n" + EXAMPLE + "\n```")
    assert q.ok and q.title == "50홈런도 6위라고?!\nKBO 단일 시즌 홈런 TOP 5" and q.source_name == "뉴시스"
    assert [(r.rank, r.player) for r in q.rows][2] == (3, "심정수") and q.memos
    bad = parse_quick("제목: 하나 / 둘 / 셋\n출처: 뉴시스 | newsis.com\n순위 | 선수 | 팀 | 기록 | 연도 | 장면 설명\n"
                      "5 | 박병호 | 넥센 히어로즈 | 52홈런 | 2014\n9 | 이승엽 | 삼성 라이온즈 | 56홈런 | 2003 | 환호")
    assert any(e.startswith("1번째 줄: 제목") for e in bad.errors)
    assert any(e.startswith("2번째 줄: 출처 URL") for e in bad.errors)
    assert any(e.startswith("4번째 줄: 칸이 5개") for e in bad.errors)
    assert any(e.startswith("5번째 줄: 순위") for e in bad.errors)
    prompt = request_prompt("홈런")
    assert "확인 필요" in prompt and "순위 | 선수 | 팀 | 기록 | 연도 | 장면 설명" in prompt


def test_example_fields():
    q = parse_quick(EXAMPLE)
    assert [(r.rank, r.player, r.year) for r in q.rows] == [
        (5, "박병호", "2014"), (3, "박병호", "2015"), (3, "심정수", "2003"),
        (2, "이승엽", "1999"), (1, "이승엽", "2003")]
    assert q.hook == "50홈런이 6위?!"
    assert q.bonus == {"kind": "rank", "rank": 6, "name": "디아즈", "team": "삼성 라이온즈", "value": "50홈런",
                       "year": "2025"}
    assert q.warnings == []


@pytest.mark.parametrize("old,new", [
    ("3 | 박병호", "공동 3 | 박병호"),        # '공동' 이 붙어도 숫자만 읽는다
    ("3 | 심정수", "공동 3위 | 심정수"),
])
def test_tied_rank_cells(old, new):
    q = parse_quick(EXAMPLE.replace(old, new))
    assert q.ok and [r.rank for r in q.rows] == [5, 3, 3, 2, 1]


def test_markdown_table_pipes():
    """표 줄 앞뒤에 '|' 가 붙은 마크다운 표도 같은 결과."""
    md = "\n".join(f"| {line} |" if line[:1].isdigit() or line.startswith("순위") else line
                   for line in EXAMPLE.splitlines())
    assert [(r.rank, r.player) for r in parse_quick(md).rows] == [(r.rank, r.player) for r in parse_quick(EXAMPLE).rows]


@pytest.mark.parametrize("old,new,error", [
    ("52홈런", "확인 필요", "'확인 필요' 칸이 있습니다"),               # 확인 못 한 칸은 읽지 않는다
    ("| 2014 |", "| 14 |", "4자리 숫자여야 합니다"),
    ("5 | 박병호", "4 | 박병호", "공동 순위 규칙에 맞지 않습니다"),     # 1, 2, 3, 3, 4 → 4위가 아니라 5위여야 함
])
def test_row_errors(old, new, error):
    q = parse_quick(EXAMPLE.replace(old, new, 1))
    assert not q.ok and any(error in e for e in q.errors)


def test_unknown_team_is_warning_not_error():
    q = parse_quick(EXAMPLE.replace("5 | 박병호 | 넥센 히어로즈", "5 | 박병호 | 쌍둥이 구단"))
    assert q.ok and any("구단 사전에 없습니다" in w for w in q.warnings)


@pytest.mark.xfail(strict=True, reason="알려진 버그: pydantic 오류의 마지막 줄(문서 URL)만 보여서 이유가 안 보임")
def test_bonus_error_message_says_why():
    q = parse_quick(EXAMPLE.replace("보너스: 6위 디아즈 | 삼성 라이온즈 | 50홈런 | 2025", "보너스: 6위 디아즈"))
    assert not q.ok
    assert not any("errors.pydantic.dev" in e for e in q.errors)
