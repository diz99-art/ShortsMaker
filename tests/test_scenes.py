from PIL import Image

from shortsmaker.models import Project, TextBox, load_template
from shortsmaker.scenes import build_scenes, fit_text
from shortsmaker.timeline import build_timeline


def _font():
    return load_template("yagu").font_path("bold")


def test_fit_text_keeps_size_when_it_fits():
    box = TextBox(x=0, y=0, w=900, h=120, size=60, min_size=30, max_lines=1)
    ft = fit_text("짧은 글", box, _font())
    assert ft.font.size == 60 and not ft.overflow


def test_fit_text_shrinks_long_text():
    box = TextBox(x=0, y=0, w=600, h=120, size=60, min_size=24, max_lines=2)
    ft = fit_text("아주 긴 설명이 들어가면 글자 크기가 자동으로 줄어드는지 확인하는 문장", box, _font())
    assert ft.font.size < 60 and not ft.overflow
    assert len(ft.lines) <= 2


def test_fit_text_overflow_at_min_size():
    box = TextBox(x=0, y=0, w=300, h=60, size=40, min_size=36, max_lines=1)
    ft = fit_text("이 문장은 최소 크기에서도 절대 한 줄에 들어갈 수 없습니다", box, _font())
    assert ft.overflow
    assert ft.lines[-1].endswith("…")


def test_build_scenes_layers_and_warnings(tmp_path):
    project = Project.model_validate(dict(
        channel="yagu",
        title="테스트 제목",
        items=[{"rank": r, "name": f"선수{r}", "image": "없는파일.png" if r == 3 else None} for r in (5, 4, 3, 2, 1)],
    ))
    t = load_template("yagu")
    scenes = build_timeline(project)
    res = build_scenes(project, tmp_path, t, scenes, tmp_path / "out")
    assert len(res.scenes) == 7
    assert sum("이미지가 없어" in w for w in res.warnings) == 5
    for s in res.scenes:
        assert Image.open(s.base).size == (1080, 1920)
        assert s.preview.exists()
    rank5 = res.scenes[1]
    assert rank5.image and rank5.pop and rank5.image.mask
    inner = rank5.image.box
    bw = t.rank.image.border_width
    assert (inner.w, inner.h) == (t.rank.image.w - 2 * bw, t.rank.image.h - 2 * bw)
    assert Image.open(rank5.image.mask).size == (inner.w, inner.h)
    assert res.scenes[-1].image is None
