"""클린 스타일 시안 A·B, 키워드 터뜨리기 규칙, 그래프 기준선 글씨, 카드 여백."""
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from shortsmaker import effects as fx
from shortsmaker import variation as var
from shortsmaker.card_clean import H, W, clean_bonus, compose_clean
from shortsmaker.card_mode import make_renderer
from shortsmaker.models import LyricLine, Word, load_template
from tests.test_card_mode import SONG, make_project


@pytest.fixture
def t():
    return load_template("yagu_v2")


@pytest.fixture
def art(tmp_path):
    p = tmp_path / "a.png"
    im = Image.new("RGBA", (600, 900), (0, 0, 0, 0))
    im.paste((30, 80, 200, 255), (150, 100, 450, 800))
    im.save(p)
    return p


@pytest.mark.parametrize("look", ["clean_a", "clean_b"])
def test_clean_cards(art, look):
    five = compose_clean(art, look, rank=5, record="52홈런", year="2014", team="넥센 히어로즈", player="박병호")
    one = compose_clean(art, look, rank=1, record="56홈런", year="2003", team="삼성 라이온즈", player="이승엽")
    assert five.size == (W, H) == (941, 1162)
    a = np.asarray(one)
    orange = ((a[..., 0] > 220) & (a[..., 1] > 80) & (a[..., 1] < 140) & (a[..., 2] < 60) & (a[..., 3] == 255)).sum()
    b = np.asarray(five)
    orange5 = ((b[..., 0] > 220) & (b[..., 1] > 80) & (b[..., 1] < 140) & (b[..., 2] < 60) & (b[..., 3] == 255)).sum()
    assert orange > 100 and orange5 == 0                              # 1위만 강조색 포인트 하나
    meta, layers = {}, {}
    compose_clean(art, look, rank=1, record="56홈런", year="2003", team="삼성 라이온즈", player="이승엽",
                  omit_record=True, meta=meta, layers=layers)
    assert meta["design"] == look and set(layers) == {"back", "art", "front", "text"}
    assert clean_bonus(look, rank=6, name="디아즈", team="삼성 라이온즈", value="50홈런", year="2025").getbbox()


def test_card_fits_75_percent_with_footer_gap(t):
    b = t.card.card_box
    s = min(b.w / W, b.h / H)
    assert W * s == pytest.approx(t.width * 0.75, abs=2)              # 클린 카드는 화면 너비 75%
    assert b.y + b.h <= t.card.footer.button.y - 25                   # '@야구한장 구독' 위로 여백


def test_bursts_skip_card_words():
    lines = [LyricLine(start=10, end=13, text="1위 이승엽 56홈런",
                       words=[Word(start=10, end=11, text="1위"), Word(start=11, end=12, text="이승엽"),
                              Word(start=12, end=13, text="56홈런")]),
             LyricLine(start=16, end=19, text="역대 최고 6위?!",
                       words=[Word(start=16, end=17, text="역대"), Word(start=17, end=18, text="최고"),
                              Word(start=18, end=19, text="6위?!")])]
    from shortsmaker.card_mode import char_times
    got = fx.pick_bursts(lines, lambda i: char_times(lines[i]), [], [(0, 30)], [],
                         on_cards=["56홈런", "이승엽", "1위", "삼성 라이온즈 2003"])
    assert [w for _, w in got] == ["역대 최고"]                          # 카드에 있는 말은 생략, 5초 간격
    got2 = fx.pick_bursts(lines[1:], lambda i: char_times(lines[1:][i]), [], [(0, 30)], [], on_cards=["역대 최고"])
    assert [w for _, w in got2] == ["6위?!"]


def test_clean_renderer_and_chart_label(tmp_path, t, art):
    for r_ in (5, 4, 3, 2, 1):
        compose_clean(art, "clean_a", rank=r_, record=f"{50 + r_}홈런", year="2003", team="삼성 라이온즈",
                      player="선수").save(tmp_path / f"card{r_}.png")
    p = make_project(tmp_path, hook="50홈런이 6위?!", gauge_base="50홈런",
                     items=[{"rank": r_, "name": f"선수{r_}", "value": f"{50 + r_}홈런", "description": "삼성 라이온즈 2003",
                             "image": f"card{r_}.png"} for r_ in (5, 4, 3, 2, 1)],
                     variation={"look": "clean_a", "beat": "pulse_flash", "motion": "wobble", "datagfx": "bars",
                                "mascot": "right"})
    r = make_renderer(p, tmp_path, t, SONG)
    assert r.clean and (r.v.beat, r.v.motion, r.v.depth) == ("off", "none", "off") and r.flash == 0   # 움직임 없음
    assert r.t.card.subtitle.stroke_width == 0                        # 자막 테두리 없음
    bounds = r._chart_bounds()
    assert bounds["right"] < t.width - 50 - 60                        # 기준선 글씨 자리를 오른쪽에 남김
    assert r.frame_at(1.0).tobytes() != r.frame_at(8.0).tobytes()


def test_look_axis():
    assert set(var.LOOKS) == {"classic", "clean_a", "clean_b"} and "look" in var.FIXED
    assert var.Variation().look == "classic"
    assert var.Variation.model_validate({"count": "countup_gauge"}).count == "countup"   # 게이지 삭제


def test_default_look_is_clean_a(tmp_path):
    assert var.DEFAULT_LOOK == "clean_a" and var.suggest(tmp_path, seed=3).look == "clean_a"
    assert "classic" in var.LOOKS                                     # 화려한 스타일은 선택 옵션으로


def test_clean_a_rank_size_and_top_color(art):
    from shortsmaker.card_clean import BLUE
    five = np.asarray(compose_clean(art, "clean_a", rank=5, record="52홈런", year="2014", team="넥센 히어로즈",
                                    player="박병호"))
    blue = (five[..., 0] < 20) & (np.abs(five[..., 1].astype(int) - 67) < 15) & (five[..., 2] > 180)
    ys = np.where(blue.any(axis=1))[0]
    assert ys.max() - ys.min() > 85                                   # 순위 글씨가 기록과 비슷한 크기 (예전 약 70px)


def test_hook_two_lines_and_wipe():
    full = fx.clean_hook_image("50홈런이 6위?!", "#F26A00", 1080, progress=1.0)
    none = fx.clean_hook_image("50홈런이 6위?!", "#F26A00", 1080, progress=0.0)
    assert full.height > 300 and full.width <= 1000                   # 두 줄로 크게
    a, b = np.asarray(full), np.asarray(none)
    oranged = lambda x: ((x[..., 0] > 230) & (x[..., 1] < 130) & (x[..., 3] > 60) & (x[..., 3] < 160)).sum()  # 반투명 주황 띠
    assert oranged(a) > 5000 and oranged(b) == 0                      # 밑줄이 그어지는 효과
    assert fx.hook_wipe(0.0) == 0 and fx.hook_wipe(1.0) == 1


def test_info_v2_auto_cards(tmp_path):
    from shortsmaker.models import Project, load_template
    for r_ in (5, 4, 3, 2, 1):
        Image.new("RGB", (600, 560), (40 * r_, 90, 160)).save(tmp_path / f"img{r_}.png")
    p = Project.model_validate(dict(channel="info_v2", title="세계 인구 TOP 5", duration=30,
                                    lyrics=[l.model_dump() for l in SONG],
                                    items=[{"rank": r_, "name": f"나라{r_}", "value": f"{r_}억 명", "image": f"img{r_}.png"}
                                           for r_ in (5, 4, 3, 2, 1)]))
    t2 = load_template("info_v2")
    r = make_renderer(p, tmp_path, t2, SONG)
    assert r.v.look == "clean_a" and t2.card.pill.text == "정보한장"
    assert all(s.item.image.startswith("cards_auto/") for s in r.scenes[1:])
    first = sorted((tmp_path / "cards_auto").glob("card*.png"))
    make_renderer(p, tmp_path, t2, SONG)                               # 입력이 같으면 다시 만들지 않음
    assert sorted((tmp_path / "cards_auto").glob("card*.png")) == first
    assert r.frame_at(8.0).getpixel((540, 1100)) != r.frame_at(0.5).getpixel((540, 1100))


def test_bubble_moves_off_card_text(tmp_path, t, art):
    meta_cards(tmp_path, art)
    p = make_project(tmp_path, bubbles={"ending": "구독 꾹!"}, mood="record",
                     items=[{"rank": r_, "name": f"선수{r_}", "value": f"{50 + r_}홈런", "description": "삼성 라이온즈 2003",
                             "image": f"card{r_}.png"} for r_ in (5, 4, 3, 2, 1)],
                     variation={"look": "clean_a", "mascot": "right"})
    r = make_renderer(p, tmp_path, t, SONG)
    i = len(r.mascot.bubbles) - 1
    a, _, _ = r.mascot.bubbles[i]
    r.frame_at(a + 0.3)
    bx, by = next(v for k, v in r._bubble_xy.items() if k[0] == i)
    mask = np.asarray(r._text_mask(r.scene_at(a + 1e-3).index))
    b = r._sub_cache[("bubble", i)]
    assert (mask[by:by + b.height, bx:bx + b.width] > 40).sum() < 30   # 카드 글자와 안 겹침
    x, y = r._mascot_box()
    assert by < y                                                     # 마스코트보다 위


def meta_cards(d, art):
    import json
    for r_ in (5, 4, 3, 2, 1):
        kw = dict(rank=r_, record=f"{50 + r_}홈런", year="2003", team="삼성 라이온즈", player="선수")
        compose_clean(art, "clean_a", **kw).save(d / f"card{r_}.png")
        meta, layers = {}, {}
        compose_clean(art, "clean_a", **kw, omit_record=True, meta=meta, layers=layers).save(d / f"card{r_}_base.png")
        meta["base"] = f"card{r_}_base.png"
        meta["layers"] = {}
        for n, im in layers.items():
            im.save(d / f"L{r_}{n}.png")
            meta["layers"][n] = f"L{r_}{n}.png"
        (d / f"card{r_}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def test_info_v2_hook_from_title_mascot_folder_and_placeholder_block(tmp_path):
    from shortsmaker.info_cards import is_placeholder, mark_placeholder
    from shortsmaker.models import Project, load_template
    from shortsmaker.validate import pre_render
    for r_ in (5, 4, 3, 2, 1):
        Image.new("RGB", (600, 560), (40 * r_, 90, 160)).save(tmp_path / f"img{r_}.png")
    mark_placeholder(tmp_path / "img3.png")
    assert is_placeholder(tmp_path / "img3.png") and not is_placeholder(tmp_path / "img2.png")
    data = dict(channel="info_v2", title="세계 인구 TOP 5\n2024 기준", duration=30, lyrics=[l.model_dump() for l in SONG],
                variation={"mascot": "right"}, rights_confirmed=True,
                items=[{"rank": r_, "name": f"나라{r_}", "value": f"{r_}억 명", "image": f"img{r_}.png"}
                       for r_ in (5, 4, 3, 2, 1)])
    p = Project.model_validate(data)
    t2 = load_template("info_v2")
    r = make_renderer(p, tmp_path, t2, SONG)
    assert r.hook_len == pytest.approx(r.scenes[0].duration) and r.project.hook == "세계 인구 TOP 5"   # 로고 없이 후킹
    assert r.mascot is None and any("mascot_info" in w for w in r.warnings)  # 정보한장 마스코트 폴더가 비어 있음
    rep = pre_render(p, tmp_path, require_rights=True)
    assert any("임시 글자 카드" in e for e in rep.errors)                       # 수익화용은 막힘
    assert not any("임시" in e for e in pre_render(p, tmp_path, require_rights=False).errors)  # 시험 렌더는 됨
    del data["items"][0]["image"]
    assert any("그림이 없습니다" in e for e in pre_render(Project.model_validate(data), tmp_path).errors)



def test_clean_cards_only_fade_in_and_out(tmp_path, t, art):
    for r_ in (5, 4, 3, 2, 1):
        compose_clean(art, "clean_a", rank=r_, record=f"{50 + r_}홈런", year="2003", team="삼성 라이온즈",
                      player="선수").save(tmp_path / f"card{r_}.png")
    p = make_project(tmp_path, items=[{"rank": r_, "name": f"선수{r_}", "value": f"{50 + r_}홈런",
                                       "image": f"card{r_}.png"} for r_ in (5, 4, 3, 2, 1)],
                     variation={"look": "clean_a", "beat": "pulse_flash", "motion": "wobble", "depth": "pan",
                                "inout": "fade_up", "mascot": "right"})
    r = make_renderer(p, tmp_path, t, SONG)
    fps = t.fps
    sc = next(s for s in r.scenes if s.kind == "rank")
    assert r.state(round(sc.start * fps) + 2)[1][0] == "in"
    mid = round((sc.start + sc.end) / 2 * fps)
    assert r.state(mid)[1] is None and r.frame(mid).tobytes() == r.frame(mid + 10).tobytes()   # 가운데는 정지
    assert r.state(round(sc.end * fps) - 3)[1][0] == "out"
    assert r.state(mid)[-1][1] == 0.0                                  # 한장이 까딱 없음
    for _ in range(3):
        v = var.suggest(tmp_path, seed=_)
        assert (v.beat, v.motion, v.depth) == ("off", "none", "off") and v.inout in var.INOUTS


def test_hook_layout_no_overlap(tmp_path, t):
    Image.new("RGB", (941, 1672), (30, 60, 120)).save(tmp_path / "hook.png")
    make_cards_simple(tmp_path)
    p = make_project(tmp_path, hook="우승 반지 8개를 가진 선수는?!", hook_image="hook.png",
                     variation={"look": "clean_a", "mascot": "right"})
    r = make_renderer(p, tmp_path, t, SONG)
    h = r._hook_text_height()
    cy, (x0, y0, x1, y1) = r._hook_slots(h)
    st_ = t.card.subtitle
    assert cy - h / 2 > st_.y + st_.h + 30                              # 자막 자리(2줄) 아래에 문구
    assert y0 >= cy + h / 2                                             # 문구 아래에 그림
    assert y1 <= r._mascot_box()[1]                                     # 그림은 한장이 위까지


def make_cards_simple(d):
    for r_ in (5, 4, 3, 2, 1):
        Image.new("RGBA", (941, 1162), (200, 200, 200, 255)).save(d / f"card{r_}.png")
