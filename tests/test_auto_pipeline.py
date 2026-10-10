"""노래로 자동 제작 — 인터넷·API 키·무거운 모델 없이 Mock 으로 전체 흐름 확인."""
import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from shortsmaker import auto_pipeline as ap
from shortsmaker.card_compose import compose_card
from shortsmaker.lyrics_align import AlignedLine, clean_lyrics_text
from shortsmaker.models import load_project
from shortsmaker.player_assets import MockEditor, Player, search_photos
from shortsmaker.rank_extract import (RankRow, assign_slots, display_text, extract_ranks, korean_to_int, label,
                                      normalize_numbers, rows_complete, team_colors)

LOSING = """케이비오 역대 최악의 연패는?
지금부터 케이비오 최장 연패 탑파이브
5위, 기아 타이거즈
십육 연패
강팀도 한 번 흐름이 꼬이면
4위, 롯데 자이언츠
십육 연패
3위, 쌍방울 레이더스
십칠 연패
2위, 한화 이글스
십팔 연패
1위, 삼미 슈퍼스타즈
십팔 연패
당신의 팀 최악의 기억은 언제였나요?"""

FA = """백억은 기본, 몸값이 뛴다
[Verse]
5위 오지환, LG의 심장
백이십사 억, 6년 계약
4위 박민우, NC에 남아
백사십 억, 5플러스3년
공동 2위 나성범, KIA로 간다
백오십 억, 6년 계약
그리고 1위는 양의지!
백오십이억, 두산의 안방"""


# ---------------------------------------------------------------- 가사 → 표

@pytest.mark.parametrize("word,value", [("십육", 16), ("이천십", 2010), ("천구백팔십오", 1985), ("일구팔오", 1985),
                                        ("백오십이억", 15_200_000_000), ("백이십사", 124), ("사과", None)])
def test_korean_numbers(word, value):
    assert korean_to_int(word) == value


def test_normalize_only_before_units():
    assert normalize_numbers("십육 연패") == "16연패"
    assert normalize_numbers("이천십 년의 기록") == "2010년의 기록"
    assert normalize_numbers("이야기 사라진") == "이야기 사라진"  # '이', '사' 는 숫자로 안 바꿈
    assert display_text("지금부터 케이비오 최장 연패 탑파이브") == "지금부터 KBO 최장 연패 TOP 5"


def test_extract_losing_streak():
    rows = extract_ranks(clean_lyrics_text(LOSING))
    assert [(r.rank, r.team, r.record) for r in rows] == [
        (5, "KIA 타이거즈", "16연패"), (4, "롯데 자이언츠", "16연패"), (3, "쌍방울 레이더스", "17연패"),
        (2, "한화 이글스", "18연패"), (1, "삼미 슈퍼스타즈", "18연패")]
    assert all(r.player == "" for r in rows)  # 가사에 없는 값은 비워 두고 사람이 채움
    assert any("선수" in p for p in rows_complete(rows))


FA_TIE = FA.replace("그리고 1위는 양의지!", "공동 2위 이대호, 부산의 자존심\n백오십 억, 4년 계약\n그리고 1위는 양의지!")


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


@pytest.mark.parametrize("ranks,ok", [([5, 4, 2, 2, 1], True), ([5, 4, 3, 1, 1], True), ([5, 2, 2, 2, 1], True),
                                      ([4, 4, 3, 2, 1], True),    # 공동 4위 2명 → 5위 없음
                                      ([5, 3, 3, 3, 1], False),   # 1위 다음 공동이면 공동 2위여야 함
                                      ([5, 4, 3, 2, 2], False), ([5, 4, 3, 2, 1], True)])
def test_tie_rule(ranks, ok):
    rows = [RankRow(rank=r, team="t", player=f"p{i}", record="1") for i, r in enumerate(ranks)]
    assert (rows_complete(rows) == []) is ok


def test_team_colors_no_logo_data():
    assert team_colors("기아 타이거즈") == ("#EA0029", "#06141F")
    data = json.loads((Path(ap.__file__).parent / "data" / "kbo_teams.json").read_text(encoding="utf-8"))
    assert all(set(t) == {"name", "short", "aliases", "colors"} for t in data["teams"])  # 로고 항목 없음


# ---------------------------------------------------------------- 흐름

def make_song(path: Path, lyrics: str, seconds: int = 35) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f=440:d={seconds}",
                    "-metadata", f"lyrics={lyrics}", "-c:a", "aac", str(path)], check=True)
    return path


def fake_aligner(audio, lines, model):
    """가사 줄마다 2초씩 (실제 모델 대신)."""
    out = []
    for i, text in enumerate(lines):
        s = 1.0 + 2.0 * i
        words = [(s + 0.3 * k, s + 0.3 * (k + 1), w) for k, w in enumerate(text.split())]
        out.append(AlignedLine(s, s + 1.9, text, 0.9, words=words))
    return out


FILL = {5: ("윤석민", "2010"), 4: ("박정태", "2002"), 3: ("김기태", "1999"), 2: ("김태균", "2020"), 1: ("장명부", "1985")}


@pytest.fixture
def aligned(tmp_path):
    song = make_song(tmp_path / "song.m4a", LOSING)
    state, d = ap.start_from_song(song, "t", title="한 번만 이겼어도…\nKBO 역대 최장 연패 TOP 5", root=tmp_path / "projects")
    ap.align(state, d, aligner=fake_aligner)
    return state, d


def filled(state):
    return [r.model_copy(update={"player": FILL[r.rank][0], "year": FILL[r.rank][1]}) for r in state.rows]


def test_start_refuses_existing_and_lyricless(tmp_path):
    song = make_song(tmp_path / "s.m4a", LOSING)
    ap.start_from_song(song, "a", root=tmp_path)
    with pytest.raises(ap.FlowError, match="이미"):
        ap.start_from_song(song, "a", root=tmp_path)
    with pytest.raises(ap.FlowError, match="가사"):
        ap.start_from_song(make_song(tmp_path / "n.m4a", ""), "b", root=tmp_path)


def test_table_must_be_confirmed_before_photos(aligned):
    state, d = aligned
    assert state.duration == pytest.approx(35, abs=0.1) and len(state.rows) == 5
    with pytest.raises(ap.FlowError, match="표"):
        ap.placeholder_photo(state, d, 5)
    with pytest.raises(ap.FlowError, match="선수"):
        ap.confirm_table(state, d, state.rows)       # 빈칸이 있으면 확정 불가
    rows = filled(state)
    rows[0] = rows[0].model_copy(update={"record": "16연패!"})  # 사용자가 고친 값이 그대로 쓰여야 함
    ap.confirm_table(state, d, rows)
    assert ap.load_state(d).rows[0].record == "16연패!"
    lyrics = [l.text for l in load_project(d / "project.json")[0].lyrics]
    assert "5위, 기아 타이거즈 16연패" in lyrics          # 순위 줄 + 기록 줄 합침, 한글 숫자 정리
    assert lyrics[0] == "KBO 역대 최악의 연패는?"


def test_cost_must_be_approved_and_replanned(aligned):
    state, d = aligned
    ap.confirm_table(state, d, filled(state))
    for r in (5, 4, 3, 2, 1):
        ap.placeholder_photo(state, d, r)
    with pytest.raises(ap.FlowError, match="비용"):
        ap.generate_caricatures(state, d, MockEditor())
    est = ap.cost_estimate(state, d)
    assert est["mock"] and est["total_usd"] == 0 and sorted(est["plans"]) == [1, 2, 3, 4, 5]
    with pytest.raises(ap.FlowError, match="승인"):
        ap.approve_cost(state, d, est, approved=False)
    ap.approve_cost(state, d, est, approved=True)
    state.style = state.style.model_copy(update={"exaggeration": "high"})  # 승인 뒤 설정 변경
    with pytest.raises(ap.FlowError, match="다시"):
        ap.generate_caricatures(state, d, MockEditor())


def test_real_mode_cost_shown_and_editor_mismatch(aligned):
    state, d = aligned
    state.mock = False
    ap.confirm_table(state, d, filled(state))
    ap.upload_photo(state, d, 5, _png(), rights_confirmed=True)
    est = ap.cost_estimate(state, d)
    assert est["total_usd"] > 0 and est["pricing_url"] and list(est["plans"]) == [5]
    ap.approve_cost(state, d, est, approved=True)
    with pytest.raises(ap.FlowError, match="Mock"):
        ap.generate_caricatures(state, d, MockEditor())  # 실제 모드에 Mock 편집기 금지
    with pytest.raises(ap.FlowError, match="Mock 모드"):
        ap.placeholder_photo(state, d, 4)


def _png() -> bytes:
    import io
    buf = io.BytesIO()
    Image.new("RGB", (300, 400), "#336699").save(buf, format="PNG")
    return buf.getvalue()


def test_full_mock_flow_until_cards(aligned):
    state, d = aligned
    ap.confirm_table(state, d, filled(state))
    for r in (5, 4, 3, 2, 1):
        ap.placeholder_photo(state, d, r)
    with pytest.raises(ap.FlowError, match="승인한 캐리커처"):
        ap.compose_cards(state, d)
    ap.approve_cost(state, d, ap.cost_estimate(state, d), approved=True)
    ap.generate_caricatures(state, d, MockEditor())
    assert state.cost_approval is None  # 한 번 승인 = 한 번 실행
    for r in (5, 4, 3, 2, 1):
        ap.approve_caricature(state, d, r, confirmed=True)
    cards = ap.compose_cards(state, d)
    assert len(cards) == 5
    project = ap.build_project(state, d)
    assert project.channel == "yagu_v2" and project.rights_confirmed is False  # Mock 은 권리 확인 불가
    assert [it.image for it in project.ordered_items] == [f"cards/card{r}.png" for r in (5, 4, 3, 2, 1)]
    assert "MOCK" in ap.credits_text(state, d)


def test_card_uses_team_colors_and_mock_mark(tmp_path):
    src = tmp_path / "c.png"
    Image.new("RGBA", (600, 900), (0, 0, 0, 0)).save(src)
    card = compose_card(src, rank=5, record="16연패", year="2010", team="KIA 타이거즈", player="윤석민", mock=True)
    assert card.size == (941, 1672)
    px = [card.getpixel((x, y))[:3] for x in range(0, 941, 7) for y in range(900, 1672, 7) if card.getpixel((x, y))[3] == 255]
    assert (0xEA, 0x00, 0x29) in px                    # 팀 색 (KIA 빨강)
    assert card.getpixel((30, 30))[:3] == (220, 0, 0)  # MOCK 시험용 표시
    real = compose_card(src, rank=5, record="16연패", year="2010", team="KIA 타이거즈", player="윤석민")
    assert real.getpixel((30, 30))[3] == 0


# ---------------------------------------------------------------- Commons 검색 (fixture)

class FakeResponse:
    def __init__(self, body):
        self.body = body
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


class FakeSession:
    def __init__(self, body):
        self.body, self.calls = body, []

    def get(self, url, params=None, **kw):
        self.calls.append(params)
        return FakeResponse(self.body)


def test_commons_search_uses_user_query_and_orders_by_license():
    page = lambda pid, lic: {"pageid": pid, "title": f"File:{pid}.jpg", "imageinfo": [{
        "url": f"https://upload.wikimedia.org/a/{pid}.jpg", "thumburl": f"https://upload.wikimedia.org/t/{pid}.jpg",
        "descriptionurl": f"https://commons.wikimedia.org/wiki/File:{pid}.jpg", "width": 800, "height": 1200,
        "mime": "image/jpeg", "extmetadata": {"LicenseShortName": {"value": lic}, "Artist": {"value": "<b>작가</b>"}}}]}
    s = FakeSession({"query": {"pages": [page(1, "CC BY-NC 2.0"), page(2, "CC BY-SA 4.0")]}})
    photos = search_photos(Player(name="윤석민", team="KIA 타이거즈", season=2010), session=s, query='"윤석민" KIA')
    assert s.calls[0]["gsrsearch"] == '"윤석민" KIA'
    assert [p.page_id for p in photos] == [2, 1] and photos[0].commercial_candidate and not photos[1].commercial_candidate
    assert photos[0].author == "작가"


# ---------------------------------------------------------------- 화면

def test_streamlit_page_flow(aligned, monkeypatch):
    from streamlit.testing.v1 import AppTest
    state, d = aligned
    monkeypatch.setenv("SHORTSMAKER_PROJECTS_DIR", str(d.parent))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # 표의 빈칸은 화면 표에서 채우는 대신 상태에 미리 넣고 '표 확정' 버튼만 누른다
    state.rows = filled(state)
    ap.save_state(state, d)
    page = Path(__file__).resolve().parents[1] / "pages" / "1_노래로_자동_제작.py"
    at = AppTest.from_file(str(page), default_timeout=120).run()
    assert not at.exception
    assert any("API_KEY 없음" in w.value for w in at.sidebar.warning)
    at.session_state["auto_slug"] = d.name
    at.run()
    assert not any(b.key == "cards" for b in at.button)  # 앞 단계 전에는 카드 버튼 없음
    at.button(key="confirm").click().run()
    assert not at.exception and ap.load_state(d).table_confirmed
    for r in (5, 4, 3, 2, 1):
        at.button(key=f"ph{r}").click().run()
    assert all(ps.folder for ps in ap.load_state(d).players.values())
    assert at.button(key="gen").disabled                      # 비용 승인 체크 전에는 생성 버튼 잠김
    at.checkbox(key="cost_ok").check().run()
    at.button(key="gen").click().run()
    assert not at.exception
    for r in (5, 4, 3, 2, 1):
        at.button(key=f"ap{r}").click().run()
    at.button(key="cards").click().run()
    assert not at.exception and ap.load_state(d).cards_done
    assert (d / "cards" / "card1.png").exists()


def test_full_mock_flow_with_tie(tmp_path):
    song = make_song(tmp_path / "fa.m4a", FA_TIE)
    state, d = ap.start_from_song(song, "fa", title="KBO FA TOP 5", root=tmp_path / "projects")
    ap.align(state, d, aligner=fake_aligner)
    rows = [r.model_copy(update={"team": r.team or "롯데 자이언츠", "year": "2024"}) for r in state.rows]
    ap.confirm_table(state, d, rows)
    assert [ap.row_label(state, s) for s in (5, 4, 3, 2, 1)] == ["5위", "4위", "공동 2위", "공동 2위", "1위"]
    for s in (5, 4, 3, 2, 1):
        ap.placeholder_photo(state, d, s)
    assert len({ps.folder for ps in state.players.values()}) == 5  # 공동 2위 두 명은 사진 폴더가 따로
    ap.approve_cost(state, d, ap.cost_estimate(state, d), approved=True)
    ap.generate_caricatures(state, d, MockEditor())
    for s in (5, 4, 3, 2, 1):
        ap.approve_caricature(state, d, s, confirmed=True)
    ap.compose_cards(state, d)
    project = ap.build_project(state, d)
    assert [(it.rank, it.shown_rank, it.name) for it in project.ordered_items] == [
        (5, 5, "오지환"), (4, 4, "박민우"), (3, 2, "나성범"), (2, 2, "이대호"), (1, 1, "양의지")]
    assert [project.rank_label(it) for it in project.ordered_items] == ["5위", "4위", "공동 2위", "공동 2위", "1위"]
    from shortsmaker.card_mode import card_timeline
    from shortsmaker.models import load_template
    scenes = card_timeline(project, load_template("yagu_v2"), project.lyrics)
    starts = [s.start for s in scenes[1:]]
    assert starts == sorted(starts) and len(set(starts)) == 5        # 공동 2위 두 장면이 각자 부르는 순간에
    assert "공동 2위 이대호" in ap.credits_text(state, d)


def test_card_tie_badge(tmp_path):
    src = tmp_path / "c.png"
    Image.new("RGBA", (600, 900), (0, 0, 0, 0)).save(src)
    tied = compose_card(src, rank=2, record="150억", year="", team="롯데 자이언츠", player="이대호", tied=True)
    solo = compose_card(src, rank=2, record="150억", year="", team="롯데 자이언츠", player="이대호")
    diff = [y for y in range(0, 1672, 4) if any(tied.getpixel((x, y)) != solo.getpixel((x, y)) for x in range(40, 300, 6))]
    assert diff  # '공동' 글자가 순위 위에 추가됨


def test_project_tie_validation():
    from pydantic import ValidationError
    from shortsmaker.models import Project
    base = {"channel": "yagu_v2", "title": "t", "duration": 40}
    ok = Project.model_validate({**base, "items": [{"rank": s, "name": "x", "display_rank": d}
                                                   for s, d in zip((5, 4, 3, 2, 1), (5, 4, 2, 2, 1))]})
    assert ok.rank_label(ok.items[2]) == "공동 2위"
    with pytest.raises(ValidationError, match="공동 순위"):
        Project.model_validate({**base, "items": [{"rank": s, "name": "x", "display_rank": d}
                                                  for s, d in zip((5, 4, 3, 2, 1), (5, 3, 2, 2, 1))]})


def test_streamlit_shows_tie_label(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    song = make_song(tmp_path / "fa.m4a", FA_TIE)
    state, d = ap.start_from_song(song, "fa2", title="FA", root=tmp_path / "projects")
    ap.align(state, d, aligner=fake_aligner)
    state.rows = [r.model_copy(update={"team": r.team or "롯데 자이언츠", "year": "2024"}) for r in state.rows]
    ap.save_state(state, d)
    monkeypatch.setenv("SHORTSMAKER_PROJECTS_DIR", str(d.parent))
    page = Path(__file__).resolve().parents[1] / "pages" / "1_노래로_자동_제작.py"
    at = AppTest.from_file(str(page), default_timeout=120)
    at.session_state["auto_slug"] = d.name
    at.run()
    assert not at.exception
    at.button(key="confirm").click().run()
    assert not at.exception and ap.load_state(d).table_confirmed
    labels = [e.label for e in at.expander]
    assert sum("공동 2위" in l for l in labels) == 2 and not any(l.startswith("3위") for l in labels)
