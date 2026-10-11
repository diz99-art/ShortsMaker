"""분위기별 카드 디자인 + 분위기 판단."""
import json
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from shortsmaker import card_design as cd
from shortsmaker import variation as var
from shortsmaker.mood import detect_mood, rule_mood


@pytest.fixture(scope="module")
def art(tmp_path_factory):
    p = tmp_path_factory.mktemp("art") / "a.png"
    Image.new("RGBA", (600, 900), (0, 0, 0, 0)).save(p)
    return p


def test_every_design_renders(art):
    assert len(cd.DESIGNS) == 18 and all(len(cd.designs_for(m)) == 3 for m in cd.MOODS)
    for key in cd.DESIGNS:
        for rank in (5, 1):
            card = cd.compose_designed(art, key, rank=rank, record="56홈런", year="2003", team="삼성 라이온즈",
                                       player="이승엽")
            assert card.size == (cd.W, cd.H) and card.getbbox()


def test_priority_order():
    assert cd.designs_for("record")[0] == "stadium"
    assert cd.designs_for("humiliation")[:2] == ["fall", "rain"]


def ctx(rank, primary="#0000FF"):
    c = cd.Ctx(primary, "#FFFFFF", rank, rank == 1, seed=rank)
    c.y_rank, c.y_banner = 1000, 1400
    return c


def test_medal_only_top3_and_ring_only_top3():
    center = (cd.W // 2, 780)
    assert cd._medal_back(ctx(1)).getpixel(center)[3] == 255
    assert cd._medal_back(ctx(3)).getpixel(center)[3] == 255
    assert cd._medal_back(ctx(4)).getpixel(center)[3] == 0      # 4~5위: 리본만
    ring_px = (cd.W // 2 + 400 - 6, 1000 - 120)                 # 테두리 원 위의 점
    assert cd._gold_rays_back(ctx(2)).getpixel(ring_px)[:3] != cd._gold_rays_back(ctx(5)).getpixel(ring_px)[:3]


def test_glass_text_is_clean_and_retro_text_readable():
    c = ctx(1)
    clean = cd._plain("1위", 120, ("#FFFFFF", "#C9D3DD", "#7B8794"), stroke="#0B0F14", outer="#FFFFFF", outer_w=5)
    assert cd._cracked_rank("1위", 120, c).tobytes() == clean.tobytes()   # 금은 글자 위로 지나가지 않음
    film = cd._film_record("56홈런", 120, c)
    a = np.asarray(film)
    assert a[a[..., 3] == 255][:, :3].mean() > 110                   # 밝은 글씨 (어두운 필름 위)
    paper = cd._paper_rank("1위", 120, c)
    b = np.asarray(paper)
    solid = b[b[..., 3] == 255]
    reds = int(((solid[:, 0] > 180) & (solid[:, 1] < 60)).sum())
    darks = int((solid[:, :3].max(axis=1) < 40).sum())
    assert reds > 500 and darks > 2000                               # 진한 검정 + 빨간 테두리


def test_placeholder_never_in_real_cards(art):
    """실제 카드에는 캐리커처 외의 글자 그림이 들어가지 않는다 (미리보기 실루엣 문구는 미리보기 전용)."""
    a = cd.compose_designed(art, "glass", rank=1, record="18연패", year="1985", team="삼미 슈퍼스타즈", player="")
    b = cd.compose_designed(None, "glass", rank=1, record="18연패", year="1985", team="삼미 슈퍼스타즈", player="")
    assert a.tobytes() == b.tobytes()                                # 빈 그림 = 그림 없음 (다른 글이 끼지 않음)


def test_meta_and_countup_text(art):
    meta = {}
    cd.compose_designed(art, "stadium", rank=1, record="56홈런", year="2003", team="삼성 라이온즈", player="이승엽",
                        omit_record=True, meta=meta)
    assert meta["design"] == "stadium" and meta["rank"] == 1 and meta["size"] > 0
    assert cd.record_text_for("stadium", "28홈런", meta["size"], "삼성 라이온즈", 1).getbbox()


# ---------------------------------------------------------------- 분위기

def test_rule_mood():
    assert rule_mood("KBO 단일 시즌 홈런 TOP 5", [2014, 2015, 2003, 1999, 2003]).mood == "record"
    assert rule_mood("한 번만 이겼어도… KBO 역대 최장 연패 TOP 5").mood == "humiliation"
    assert rule_mood("150억이 2위라고? FA 계약").mood == "money"
    assert rule_mood("추억의 기록", [1982, 1985, 1990]).mood == "retro"   # '추억'의 '억'은 돈이 아님
    assert rule_mood("KBO 단일 시즌 홈런 TOP 5", extra="같은 시즌의 홈런 경쟁이었다").mood == "record"  # 메모의 '경쟁'
    assert rule_mood("KBO 최장 연패", extra="").mood == "humiliation"


def test_detect_mood_claude_then_rules():
    ok = lambda cmd, **kw: SimpleNamespace(returncode=0, stdout=json.dumps(
        {"result": '{"mood": "retro", "reason": "2003년 추억"}'}), stderr="")
    r = detect_mood("50홈런도 6위라고?!", runner=ok)
    assert (r.mood, r.via) == ("retro", "claude") and r.designs[0] == "newspaper"
    bad = lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="", stderr="x")
    r = detect_mood("KBO 역대 최장 연패", runner=bad)
    assert (r.mood, r.via) == ("humiliation", "rules")


def test_suggest_design_avoids_recent(tmp_path):
    cands = cd.designs_for("humiliation")
    assert var.suggest_design(cands, tmp_path) == "fall"
    var.record("a", var.Variation(design="fall"), tmp_path)
    assert var.suggest_design(cands, tmp_path) == "rain"
    var.record("b", var.Variation(design="rain", transition="slide"), tmp_path)
    var.record("c", var.Variation(design="glass", transition="pop"), tmp_path)
    assert var.suggest_design(cands, tmp_path) == "fall"                # 다 썼으면 가장 오래전 것
