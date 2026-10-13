"""차별화: 마스코트 한장이, 데이터 그래픽, 2.5D 입체, 키워드 터뜨리기, 카드 크기·선수 이름."""
import json

import numpy as np
import pytest
from PIL import Image

from shortsmaker import effects as fx
from shortsmaker import mascot as msc
from shortsmaker import variation as var
from shortsmaker.card_mode import card_timeline, make_renderer, sfx_cues
from shortsmaker.models import LyricLine, Word, load_template
from shortsmaker.quick_parse import EXAMPLE, parse_quick
from tests.test_card_mode import SONG, make_project

FX = {"hook": "slam", "beat": "off", "count": "static", "sfx": "a", "progress": "dots", "ending": "stamp",
      "mascot": "right", "datagfx": "bars", "depth": "off", "burst": "off"}


@pytest.fixture
def t():
    return load_template("yagu_v2")


def wl(start, end, text, words):
    return LyricLine(start=start, end=end, text=text, words=[Word(start=a, end=b, text=w) for a, b, w in words])


# ---------------------------------------------------------------- 마스코트

def test_rests_and_adlibs():
    lines = [wl(1.0, 3.0, "a b", [(1.0, 1.5, "a"), (1.6, 2.0, "b")]),
             wl(3.0, 6.0, "c (와아!)", [(3.5, 4.0, "c"), (5.2, 5.6, "(와아!)")])]
    assert msc.rests(lines, 8.0) == [(0.0, 1.0), (2.0, 3.5), (4.0, 5.2), (5.6, 8.0)]
    assert msc.adlib_times(lines) == [5.2]


def test_bubble_rules():
    assert msc.bubble_problem("구독 꾹!") is None
    assert msc.bubble_problem("와") and msc.bubble_problem("구독과 좋아요 부탁해요")
    b = msc.auto_bubbles("humiliation", {"top": "이건 실화?"})
    assert b["top"] == "이건 실화?" and b["intro"] and b["ending"]
    q = parse_quick(EXAMPLE)
    assert q.bubbles == {"intro": "몇 개일까?", "top": "역시 1위!", "ending": "구독 꾹!"}


def test_mascot_plan_flow(tmp_path, t):
    p = make_project(tmp_path, variation=FX, bonus={"kind": "rank", "rank": 6, "name": "팀6", "value": "15연패"},
                     bubbles={"intro": "몇 번?", "top": "역시 1위!", "ending": "구독 꾹!"}, mood="humiliation")
    scenes = card_timeline(p, t, SONG)
    plan = msc.make_plan(scenes, SONG, mood="humiliation", bubbles=p.bubbles, hook_len=0.0, duration=30)
    assert plan.expr_at(0.5) == "question"
    assert plan.expr_at(7.0) == "sad"                                 # 굴욕: 순위 장면 표정
    top = next(s for s in scenes if s.kind == "rank" and s.item.rank == 1)
    assert plan.expr_at(top.start + 0.2) == "shock" and plan.expr_at(top.start + 2.0) == "sad"
    assert plan.expr_at(29.5) == "wave"                               # 마지막 2초 손 흔들기
    sung = [(a, b) for l in SONG for a, b in ([(w.start, w.end) for w in l.words] or [(l.start, l.end)])]
    for a, b, text in plan.bubbles:                                   # 말풍선은 노래 쉬는 틈에만
        assert not any(sa < b and a < se for sa, se in sung) and 3 <= len(text.replace(" ", "")) <= 8
        assert b - a <= msc.BUBBLE_MAX
    assert [x[2] for x in plan.bubbles][-1] == "구독 꾹!"
    assert plan.bobbing(7.0) and not plan.bobbing(0.5)


def test_mascot_files_replace_placeholder(tmp_path):
    ph = msc.expression_image("clap", 120, folder=tmp_path)
    Image.new("RGB", (300, 300), "white").save(tmp_path / "x.png")
    im = Image.new("RGB", (300, 400), "white")
    im.paste((200, 30, 30), (100, 100, 200, 300))
    im.save(tmp_path / "default.png")
    got = msc.expression_image("clap", 120, folder=tmp_path)          # clap.png 없음 → default.png
    assert got.tobytes() != ph.tobytes() and got.getpixel((5, 5))[3] == 0   # 흰 배경은 투명하게
    im.save(tmp_path / "clap.png")
    assert msc.missing_files(tmp_path) == [e for e in msc.EXPRESSIONS if e != "clap"]


@pytest.mark.skipif(not (msc.MASCOT_DIR / "default.png").is_file(),
                    reason="마스코트 원본 그림은 git 에 없음 (내 컴퓨터에서만 확인)")
def test_real_mascot_files_present():
    assert not msc.missing_files(), "assets/mascot 에 표정 그림 10개가 있어야 합니다"
    assert (msc.MASCOT_DIR / "default.png").is_file()


def test_renderer_draws_mascot_and_chart(tmp_path, t):
    p = make_project(
        tmp_path,
        variation=FX,
        gauge_base="12연패",
        mood="record",
        items=[
            {
                "rank": r,
                "name": f"팀{r}",
                "value": f"{10 + r}연패",
                "description": f"KIA 타이거즈 20{10 + r}",
                "image": f"card{r}.png",
            }
            for r in (5, 4, 3, 2, 1)
        ],
    )
    r = make_renderer(p, tmp_path, t, SONG)
    kinds = [s.kind for s in r.scenes]
    assert kinds[-1] == "chart" and r.scenes[-1].start == pytest.approx(30 - fx.CHART_LEN)
    x, y = r._mascot_box()
    assert x + msc.SIZE <= t.width and y + msc.SIZE <= t.card.footer.button.y + t.card.footer.button.h
    assert y > t.card.subtitle.y + t.card.subtitle.h                  # 자막과 안 겹침
    frame = np.asarray(r.frame_at(8.0))
    # 마스코트가 그려짐
    assert frame[y + msc.SIZE // 2 - 30:y + msc.SIZE // 2 + 30, x + 40:x + msc.SIZE - 40].std() > 10
    st = r.state(round(29.5 * 30))
    assert st[13] is not None and st[16][0] == "wave"                 # 그래프 장면 + 손 흔들기
    assert r._chart_bounds()["right"] < x                              # 그래프가 마스코트 자리를 비움
    bars = fx.chart_bars(p.ordered_items)
    assert [b.value for b in bars] == [15, 14, 13, 12, 11] and bars[-1].top and bars[0].year == "2015"
    cues = sfx_cues(p, t, r.scenes)
    assert sum(1 for c in cues if c.kind == "thud") == 5              # 그래프 장면에는 '쿵' 없음


# ---------------------------------------------------------------- 키워드·2.5D

def test_pick_bursts_rules():
    lines = [wl(10.0, 12.0, "1위 이승엽 56홈런", [(10.0, 10.5, "1위"), (10.5, 11.0, "이승엽"), (11.0, 12.0, "56홈런")]),
             wl(12.0, 14.0, "첫 50홈런 시대", [(12.0, 12.4, "첫"), (12.4, 13.2, "50홈런"), (13.2, 14.0, "시대")]),
             wl(20.0, 22.0, "54홈런 이승엽", [(20.0, 21.0, "54홈런"), (21.0, 22.0, "이승엽")])]
    from shortsmaker.card_mode import char_times
    got = fx.pick_bursts(lines, lambda i: char_times(lines[i]), ["이승엽"], strong=[(9.0, 25.0)], blocked=[])
    assert [w for _, w in got] == ["1위", "54홈런"]                    # 5초 간격, 강한 구간만
    assert all(b - a >= fx.BURST_GAP for (a, _), (b, _) in zip(got, got[1:]))
    assert fx.pick_bursts(lines, lambda i: char_times(lines[i]), [], strong=[], blocked=[]) == []
    assert fx.burst_frame("stamp", 0.0)[0] == 2.0 and fx.burst_frame("zoom", fx.BURST_LEN)[2] == 0


def test_parallax_layers_move_differently():
    pan = fx.parallax("pan", 1.5)
    assert pan["front"][1] > pan["art"][1] > 0 > pan["back"][1] and pan["text"] == (1.0, 0.0)
    dolly = fx.parallax("dolly", 8.0)
    assert dolly["front"][0] > dolly["art"][0] > dolly["back"][0] > 1.0


def test_depth_frames_change(tmp_path, t):
    from shortsmaker.card_design import compose_designed
    art = tmp_path / "a.png"
    im = Image.new("RGBA", (600, 900), (0, 0, 0, 0))
    im.paste((30, 80, 200, 255), (150, 100, 450, 800))
    im.save(art)
    for r_ in (5, 4, 3, 2, 1):
        kw = dict(rank=r_, record=f"{10 + r_}연패", year="2010", team="KIA 타이거즈", player="선수")
        compose_designed(art, "stadium", **kw).save(tmp_path / f"card{r_}.png")
        meta, layers = {}, {}
        compose_designed(art, "stadium", **kw, omit_record=True, meta=meta, layers=layers)
        meta["layers"] = {}
        for n, l in layers.items():
            l.save(tmp_path / f"L{r_}{n}.png")
            meta["layers"][n] = f"L{r_}{n}.png"
        (tmp_path / f"card{r_}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    from shortsmaker.models import Project
    p = Project.model_validate(dict(channel="yagu_v2", title="t\nu", duration=30, lyrics=[l.model_dump() for l in SONG],
                                    items=[{"rank": r_, "name": f"팀{r_}", "value": f"{10 + r_}연패",
                                            "image": f"card{r_}.png"} for r_ in (5, 4, 3, 2, 1)],
                                    variation={**FX, "depth": "pan", "mascot": "off", "datagfx": "off"}))
    r = make_renderer(p, tmp_path, t, SONG)
    assert r._depth and not r.warnings
    a, b = r.frame_at(7.85), r.frame_at(8.9)            # 등장 끝(7.4초) 뒤 패럴랙스 0.4초 vs 1.5초
    assert r.state(round(8.9 * 30))[14] is not None and a.tobytes() != b.tobytes()


# ---------------------------------------------------------------- 카드 크기·이름·연출

def test_card_box_75_percent_width(t):
    b = t.card.card_box
    assert b.w == round(t.width * 0.75) and b.x + b.w / 2 == t.width / 2          # 화면 너비 75%, 가운데
    assert b.y >= t.card.subtitle.y + t.card.subtitle.h                          # 자막 아래
    assert b.y + b.h <= t.card.footer.button.y + t.card.footer.button.h


def test_player_name_doubled(tmp_path):
    from shortsmaker.card_compose import compose_card
    src = tmp_path / "a.png"
    Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(src)
    with_name = compose_card(src, rank=5, record="52홈런", year="", team="넥센 히어로즈", player="박병호")
    no_name = compose_card(src, rank=5, record="52홈런", year="", team="넥센 히어로즈", player="")
    diff = np.asarray(with_name)[..., 3].astype(int) - np.asarray(no_name)[..., 3].astype(int)
    rows = np.where(np.abs(diff).sum(axis=1) > 0)[0]
    assert rows.max() - rows.min() > 120                              # 이름 리본 높이 (예전 약 80px의 2배 쯤)


def test_new_axes_in_variation():
    assert {"mascot", "datagfx", "depth", "burst"} <= set(var.AXES)
    old = var.Variation.model_validate({"transition": "slide"})
    assert (old.mascot, old.datagfx, old.depth, old.burst) == ("off", "off", "off", "off")
    s = var.suggest(seed=1, root=__import__("pathlib").Path("/nonexistent"))
    assert s.mascot == "right" and s.datagfx != "off" and s.burst != "off" and s.depth == "off"   # 2.5D 는 끔
