import pytest

from shortsmaker.models import Project
from shortsmaker.quick_parse_info import InfoProject, QuickParseInfoError, parse_quick_info


@pytest.fixture
def form():
    return "\n".join([
        "제목: 세계의 건물 / 높이 TOP 5",
        "후킹: 얼마나 높을까?",
        "출처: 공식 통계 | https://example.com/data",
        "순위 | 이름 | 구분 | 값 | 기준 연도 | 그림 설명",
        *[f"{rank} | 건물{rank} | 건축물 | {1000-rank}m | 2026 | 하늘을 찌르는 건물{rank}"
          for rank in range(5, 0, -1)],
        "보너스: 6위 건물6 | 관광 시설 | 900m | 2026",
        "메모: 높이는 공식 통계 기준",
        "메모: 전망대 높이와 다름",
        "말풍선: 높다 높아 / 최고 높이 / 구독 꾹!",
    ])


def test_project_mapping_and_metadata(form):
    project = parse_quick_info(form)
    assert isinstance(project, Project)
    assert project.channel == "sky_badge"
    assert project.title == "세계의 건물\n높이 TOP 5"
    assert project.hook == "얼마나 높을까?"
    assert project.source_note == "출처: 공식 통계 (https://example.com/data)"
    assert project.memos == ["높이는 공식 통계 기준", "전망대 높이와 다름"]
    assert project.bubbles == {"intro": "높다 높아", "top": "최고 높이", "ending": "구독 꾹!"}
    assert project.bonus.name == "건물6" and project.bonus.team == "관광 시설"
    item = project.items[0]
    assert (item.rank, item.name, item.description, item.value, item.year, item.scene) == (
        5, "건물5", "건축물", "995m", "2026", "하늘을 찌르는 건물5")
    assert not project.rights_confirmed
    restored = InfoProject.model_validate_json(project.model_dump_json())
    assert restored.items[0].scene == item.scene and restored.memos == project.memos


def test_markdown_and_no_team_lookup(form, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("구단 검사를 호출하면 안 됩니다")
    monkeypatch.setattr("shortsmaker.rank_extract.find_team", forbidden)
    lines = form.splitlines()
    lines[0] = "- **" + lines[0] + "**"
    for i in range(3, 9):
        lines[i] = "| " + lines[i] + " |"
    lines.insert(4, "| --- | --- | --- | --- | --- | --- |")
    assert parse_quick_info("```text\n" + "\n".join(lines) + "\n```").items[0].description == "건축물"


@pytest.mark.parametrize("old,new,error", [
    ("3 | 건물3 | 건축물 | 997m | 2026 | 하늘을 찌르는 건물3",
     "3 | 건물3 | 건축물 | 997m | 2026", "7번째 줄: 칸이 5개입니다(6개 필요)"),
    ("3 | 건물3", "9 | 건물3", "7번째 줄: 순위"),
    ("997m | 2026", "997m | 26", "7번째 줄: 기준 연도"),
    ("3 | 건물3", "3 | ", "7번째 줄: 이름 칸"),
    ("997m | 2026", " | 2026", "7번째 줄: 값 칸"),
    ("997m", "확인 필요", "7번째 줄: '확인 필요'"),
    ("https://example.com/data", "example.com", "3번째 줄: 출처 URL"),
    ("후킹: 얼마나 높을까?", "후킹: " + "가" * 31, "2번째 줄: 후킹"),
    ("말풍선: 높다 높아 / 최고 높이 / 구독 꾹!", "말풍선: 가", "13번째 줄: 말풍선"),
    ("보너스: 6위 건물6 | 관광 시설 | 900m | 2026", "보너스: 예고 | ", "10번째 줄: 보너스"),
])
def test_line_errors(form, old, new, error):
    with pytest.raises(QuickParseInfoError) as exc:
        parse_quick_info(form.replace(old, new))
    assert any(message.startswith(error) for message in exc.value.errors)


def test_ties_sorted_and_teaser(form):
    form = form.replace("4 | 건물4", "공동 3위 | 건물4")
    form = form.replace("6위 건물6 | 관광 시설 | 900m | 2026", "예고 | 세계의 다리")
    project = parse_quick_info(form)
    assert [item.rank for item in project.items] == [5, 4, 3, 2, 1]
    assert [item.shown_rank for item in project.items] == [5, 3, 3, 2, 1]
    assert project.bonus.kind == "teaser" and project.bonus.text == "세계의 다리"


def test_empty_optional_columns(form):
    project = parse_quick_info(form.replace("2026 | 하늘을 찌르는 건물3", " | "))
    assert project.items[2].year == project.items[2].scene == ""


@pytest.mark.parametrize("change,error", [
    (lambda s: s.replace("제목: 세계의 건물 / 높이 TOP 5\n", ""), "제목: 줄이 없습니다"),
    (lambda s: s.replace("출처: 공식 통계 | https://example.com/data\n", ""), "출처: 줄이 없습니다"),
    (lambda s: s.replace("5 | 건물5", "3 | 건물5"), "공동 순위 규칙"),
    (lambda s: s + "\n잘못된 줄", "14번째 줄: 양식에 없는 줄"),
    (lambda s: s + "\n1 | 추가 | 구분 | 10 | 2026 | 그림", "정확히 5줄"),
])
def test_structure_errors(form, change, error):
    with pytest.raises(QuickParseInfoError, match=error):
        parse_quick_info(change(form))
