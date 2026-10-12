"""빠른 제작 정보한장: 양식 → 표, 그림 0장(임시 글자 카드), 카드의 구분·기준 연도, 채널 색, 마스코트 없음."""
from pathlib import Path

import numpy as np
from PIL import Image

from shortsmaker import auto_pipeline as ap
from shortsmaker import quick_parse_info as qi
from shortsmaker.card_clean import compose_clean
from shortsmaker.info_cards import is_placeholder
from shortsmaker.models import load_project, load_template
from shortsmaker.quick import run_quick
from tests.test_auto_pipeline import make_song
from tests.test_quick import _png, hr_aligner

POP_SONG = """지구에서 사람 제일 많은 나라
5위 파키스탄 2억 5천만
4위 인도네시아 2억 8천만
3위는 미국 3억 4천만
2위 중국 14억 1천만
1위 인도 14억 5천만
2024년 기준 세계 인구 TOP 5"""


def test_info_form_to_quick_rows():
    q = qi.parse_info_quick(qi.EXAMPLE)
    assert q.ok and q.source_name == "World Bank" and q.source_url.startswith("https://")
    assert q.hook == "인구 1위는 중국일까?" and q.memos
    r = q.rows[0]
    assert (r.rank, r.player, r.team, r.record, r.year) == (5, "파키스탄", "아시아", "2.5억 명", "2024")
    bad = qi.parse_info_quick(qi.EXAMPLE.replace("| 2024 | 대도시", "| 24년 | 대도시"))
    assert not bad.ok and any("기준 연도" in e for e in bad.errors)
    assert "구분" in qi.request_prompt("세계 인구") and "기준 연도" in qi.request_prompt("세계 인구")


def test_card_shows_category_and_basis_year():
    kw = dict(rank=5, record="2.5억 명", team="아시아", player="파키스탄", accent="#1FB873")
    with_year = np.asarray(compose_clean(None, "clean_a", year="2024년 기준", **kw))
    no_year = np.asarray(compose_clean(None, "clean_a", year="", **kw))
    assert (with_year != no_year).any()                                     # '아시아 · 2024년 기준'
    blue = np.asarray(compose_clean(None, "clean_a", year="", **kw))
    green = np.asarray(compose_clean(None, "clean_a", year="", **kw, main="#0B7A4F"))
    assert (blue != green).any()                                            # 순위 글씨가 채널 색


def test_info_template_colors_and_no_mascot_folder():
    c = load_template("info_v2").card
    assert c.main_color and c.accent_color and c.main_color.upper() != "#0043C8"
    assert c.title.color == c.main_color and c.mascot_dir == "mascot_info"


def test_run_quick_info_without_images(tmp_path):
    song = make_song(tmp_path / "pop.m4a", POP_SONG, seconds=40)
    root = tmp_path / "projects"
    q = qi.parse_info_quick(qi.EXAMPLE)
    res = run_quick(q, song, [_png("#3366AA")], slug="pop", root=root, template="info_v2", aligner=hr_aligner,
                    output_dir=tmp_path, capcut_root=tmp_path, use_claude=False)
    assert res.status == "done", (res.stage, res.message, res.issues)
    d = root / res.slug
    state = ap.load_state(d)
    assert state.template == "info_v2" and not state.mood                 # 정보한장은 분위기 판단 없음
    sources = [state.players[r.slot].source for r in state.rows]
    assert sources == ["upload", "placeholder", "placeholder", "placeholder", "placeholder"]
    assert is_placeholder(d / state.players[state.rows[1].slot].approved)
    p = load_project(d / "project.json")[0]
    it = p.ordered_items[0]
    assert (it.name, it.description, it.year, it.value) == ("파키스탄", "아시아", "2024", "2.5억 명")
    assert p.channel == "info_v2" and p.source_note.startswith("출처: World Bank")
    assert any("마스코트 없이" in w for w in res.issues) and len(res.issues) == len(set(res.issues))
    bad = {c["key"] for c in ap.checklist(state, d) if not c["ok"]}
    assert "info_images" in bad and "no_logo" not in {c["key"] for c in ap.checklist(state, d)}
    assert Path(res.output).exists()


def test_run_quick_sky_badge_icons_and_title_icon(tmp_path):
    song = make_song(tmp_path / "pop.m4a", POP_SONG, seconds=40)
    root = tmp_path / "projects"
    q = qi.parse_info_quick(qi.EXAMPLE)
    res = run_quick(q, song, [_png("#3366AA")], slug="zip", root=root, template="sky_badge", aligner=hr_aligner,
                    output_dir=tmp_path, capcut_root=tmp_path, use_claude=False,
                    icons=[_png("#22AA22"), _png("#AA2222")], title_icon=_png("#FFAA00"))
    assert res.status == "done", (res.stage, res.message, res.issues)
    d = root / res.slug
    state = ap.load_state(d)
    assert state.value_label == "인구" and state.title_icon == "title_icon.png" and len(state.icons) == 2
    assert [state.players[r.slot].source for r in state.rows] == ["upload"] + ["placeholder"] * 4
    p = load_project(d / "project.json")[0]
    assert p.channel == "sky_badge" and p.value_label == "인구" and p.ordered_items[0].icon
    im = Image.open(d / state.players[state.rows[0].slot].approved)
    assert im.mode == "RGBA" and im.getchannel("A").getextrema()[0] == 0   # 캐릭터 배경을 지움
    assert Path(res.output).exists()
