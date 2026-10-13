"""하늘 배지형 템플릿: 참고 영상에서 잰 자리, 배지(값 라벨·칩·아이콘), 1위 크기,
임시 캐릭터 차단, 엔딩 카드, 제목 아이콘."""
from pathlib import Path

import numpy as np
from PIL import Image

from shortsmaker import quick_parse_info as qi
from shortsmaker.card_layers import render_chrome
from shortsmaker.card_timing import card_timeline
from shortsmaker.card_zip import CHAR_BOX, PANEL, SIZE, compose_zip
from shortsmaker.info_cards import is_placeholder, prepare_cards
from shortsmaker.models import Project, load_template
from tests.test_quick import hr_aligner  # noqa: F401  (같은 가짜 정렬기를 쓰는 다른 테스트와 맞춤)

T = load_template("sky_badge", local=False)  # 내 컴퓨터의 template.local.json 과 무관하게 공개 템플릿으로


def _project(tmp_path: Path, **kw) -> Project:
    d = dict(channel="sky_badge", title="지구에서 사람 제일 많은 나라\n세계 인구 TOP 5", duration=40,
             items=[{"rank": r, "name": f"나라{r}", "value": f"{r}.5억 명", "description": "아시아", "year": "2024",
                     "start": 4.0 + (5 - r) * 5} for r in (5, 4, 3, 2, 1)])
    d.update(kw)
    return Project.model_validate(d)


def test_template_matches_reference_measurements():
    c = T.card
    assert c.title.color == "#EB1B1B" and c.divider.y == 506 and c.divider.color == "#0350B7"
    assert c.footer.handle_text == "@sky_badge" and c.scene_style == "zip" and c.keep_subtitle_style
    assert c.subtitle.font == "jua" and T.font_path("jua").is_file() and c.subtitle.stroke_width > 0
    assert c.card_box.w == 1080 and (c.card_box.w, c.card_box.h) == SIZE
    assert T.brand_asset(c.brand_logo) and T.brand_asset(c.default_character) and T.brand_asset(c.outro_image)
    assert c.mascot_dir == "mascot_zip"                                   # 비어 있음 → 마스코트 없이


def test_badge_content_icon_and_top_scale(tmp_path):
    kw = dict(rank_label="5위", name="파키스탄", value="2.5억 명", category="아시아", year="2024")
    plain = np.asarray(compose_zip(None, **kw))
    labelled = np.asarray(compose_zip(None, **kw, value_label="인구"))
    assert (plain != labelled).any()                                      # 값 라벨 줄
    icon = Image.new("RGBA", (100, 100), (230, 40, 40, 255))
    with_icon = np.asarray(compose_zip(None, **kw, icon=icon))
    assert (with_icon[300:440, 90:280, 0] > 200).any() and not (plain[300:440, 90:280, 1] < 60).any()
    ch = Image.new("RGBA", (300, 600), (20, 60, 160, 255))
    small = np.asarray(compose_zip(ch, **kw))[..., 3]
    big = np.asarray(compose_zip(ch, **kw, top=True))[..., 3]
    assert (big[:, CHAR_BOX[0] - 120:] > 0).sum() > (small[:, CHAR_BOX[0] - 120:] > 0).sum() * 1.2   # 1위는 크게
    a = plain[..., 3]
    assert a[PANEL[1] + 10, PANEL[0] + 200] > 200                         # 배지판


def test_prepare_uses_default_character_and_marks_placeholder(tmp_path):
    p = _project(tmp_path, value_label="인구")
    out = prepare_cards(p, tmp_path, T)
    imgs = [tmp_path / it.image for it in out.ordered_items]
    assert all(i.is_file() and is_placeholder(i) for i in imgs)          # 기준 캐릭터 = 임시
    assert out.variation["datagfx"] == "off" and out.variation["count"] == "static"
    ch = tmp_path / "c.png"
    Image.new("RGBA", (200, 400), (20, 60, 160, 255)).save(ch)
    p2 = _project(tmp_path, items=[{**it.model_dump(), "image": "c.png"} for it in p.items])
    out2 = prepare_cards(p2, tmp_path, T)
    assert not any(is_placeholder(tmp_path / it.image) for it in out2.items)


def test_outro_scene_and_title_icon(tmp_path):
    p = _project(tmp_path)
    kinds = [s.kind for s in card_timeline(p, T, [])]
    assert kinds[-1] == "outro" and kinds.count("rank") == 5
    icon = tmp_path / "ic.png"
    Image.new("RGBA", (80, 80), (0, 200, 0, 255)).save(icon)
    _, head_no = render_chrome(T, p, tmp_path)
    _, head = render_chrome(T, p.model_copy(update={"title_icon": "ic.png"}), tmp_path)
    g = np.asarray(head)
    assert ((g[200:440, :, 1] > 180) & (g[200:440, :, 0] < 40)).any()   # 제목 끝 초록 아이콘
    assert np.asarray(head_no)[0, 1079].tolist() != [252, 252, 252, 255]  # 배경 그림 (단색 아님)


def test_value_label_line_in_form():
    q = qi.parse_info_quick(qi.EXAMPLE)
    assert q.ok and q.value_label == "인구"
    bad = qi.parse_info_quick(qi.EXAMPLE.replace("값 이름: 인구", "값 이름: 아주아주아주아주긴값이름입니다"))
    assert not bad.ok and any("값 이름" in e for e in bad.errors)
    no = qi.parse_info_quick(qi.EXAMPLE.replace("값 이름: 인구\n", ""))
    assert no.ok and no.value_label == ""
