import pytest
from pydantic import ValidationError

from shortsmaker.models import Project, load_template, list_channels
from shortsmaker.timeline import build_timeline


def make_project(**kw) -> Project:
    data = dict(
        channel="yagu",
        title="테스트",
        items=[{"rank": r, "name": f"선수{r}"} for r in (5, 4, 3, 2, 1)],
    )
    data.update(kw)
    return Project.model_validate(data)


def test_default_50s_layout():
    scenes = build_timeline(make_project(duration=50))
    assert [round(s.duration, 3) for s in scenes] == [3, 8, 8, 8, 8, 10, 5]
    assert [s.label for s in scenes] == ["인트로", "5위", "4위", "3위", "2위", "1위", "아웃트로"]
    assert scenes[-1].end == 50


@pytest.mark.parametrize("duration", [30, 37.3, 40, 59.9, 60])
def test_scaled_total_equals_duration(duration):
    fps, tr = 30, 0.4
    scenes = build_timeline(make_project(duration=duration), fps=fps, transition=tr)
    # 장면이 빈틈 없이 이어짐
    for a, b in zip(scenes, scenes[1:]):
        assert a.end == b.start
    # xfade 체인 후 길이 = 클립 합 - 전환 수 * transition
    total = sum(s.clip_duration for s in scenes) - (len(scenes) - 1) * round(tr * fps) / fps
    assert abs(total - duration) < 1 / fps + 1e-9
    # 모든 경계가 프레임 단위
    for s in scenes:
        assert abs(s.start * fps - round(s.start * fps)) < 1e-6


def test_items_must_be_five_unique():
    with pytest.raises(ValidationError):
        make_project(items=[{"rank": r, "name": "x"} for r in (5, 4, 3, 2)])
    with pytest.raises(ValidationError):
        make_project(items=[{"rank": r, "name": "x"} for r in (5, 4, 3, 3, 1)])


def test_duration_range():
    with pytest.raises(ValidationError):
        make_project(duration=25)
    with pytest.raises(ValidationError):
        make_project(duration=61)


def test_lyrics_single_source():
    with pytest.raises(ValidationError):
        make_project(lyrics=[{"start": 0, "end": 1, "text": "a"}], lyrics_lrc="a.lrc")


def test_items_shown_5_to_1():
    p = make_project(items=[{"rank": r, "name": f"n{r}"} for r in (1, 3, 5, 2, 4)])
    assert [i.rank for i in p.ordered_items] == [5, 4, 3, 2, 1]


def test_templates_load():
    assert {"yagu", "info_top5"} <= set(list_channels())
    for ch in list_channels():
        t = load_template(ch)
        assert t.font_path("bold").exists()
