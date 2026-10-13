"""수익화 대비: 연출 바꾸기·사람의 기여 기록·가사 검사/생성·권리 체크리스트·업로드 초안."""
import json
import subprocess
from pathlib import Path

import pytest

from shortsmaker import auto_pipeline as ap
from shortsmaker import production_log as plog
from shortsmaker import variation as var
from shortsmaker.lyrics_check import check_lyrics_text
from shortsmaker.lyrics_gen import (
    LyricsGenError,
    generate_with_claude,
    parse_response,
    save_brief,
)
from tests.test_auto_pipeline import FA_TIE, _white_bg_png, fake_aligner, make_song

FA_ROWS = [dict(rank=5, player="오지환", team="LG 트윈스", record="124억", notes="6년 계약"),
           dict(rank=4, player="박민우", team="NC 다이노스", record="140억", notes="5+3년 계약"),
           dict(rank=2, player="나성범", team="KIA 타이거즈", record="150억", notes="6년 계약"),
           dict(rank=2, player="이대호", team="롯데 자이언츠", record="150억", notes="4년 계약"),
           dict(rank=1, player="양의지", team="두산 베어스", record="152억", notes="4+2년 계약")]

GOOD = """[Intro]
선수 한 명에 백오십이억?
누가 제일 비쌌을까
[Verse]
5위 오지환, LG 트윈스 백이십사억
이것도 꼴찌라니 믿기 어렵지
4위 박민우, NC 다이노스 백사십억
5위보다 십육억이 더 붙었어
[Chorus]
공동 2위 나성범, 이대호 백오십억
같은 돈, 다른 시간
하지만 1위 양의지 백오십이억
겨우 이억 차이로 갈린 왕관
[Outro]
알고 보면 숫자는 믿음의 크기
구단이 건 기대만큼 무거운 이름"""


# ---------------------------------------------------------------- 연출

def test_variation_avoids_recent_five(tmp_path):
    for i in range(5):
        v = var.Variation(transition="punch", accent="red_gold", subtitle="typing", intro="shrink")
        var.record(f"p{i}", v, tmp_path)
    same = var.Variation()
    assert var.conflicts(same, tmp_path)
    s = var.suggest(tmp_path, seed=1)
    assert not var.conflicts(s, tmp_path) and var.differences(s, same) >= var.MIN_DIFF
    one_diff = var.Variation(transition="slide")
    assert var.conflicts(one_diff, tmp_path)                       # 1요소만 다르면 부족
    assert var.recent(tmp_path, exclude="p4")[0] == same and len(var.recent(tmp_path)) == 5


def test_variation_history_only_last_five(tmp_path):
    for i in range(6):
        var.record(f"p{i}", var.Variation(accent="green_gold") if i == 0 else var.Variation(intro="slide"), tmp_path)
    assert all(r.intro == "slide" for r in var.recent(tmp_path))  # 6번째 전(p0)은 비교 대상 아님


# ---------------------------------------------------------------- 가사 검사

def test_good_lyrics_pass_and_list_lyrics_fail():
    good = check_lyrics_text(GOOD, FA_ROWS)
    assert good.passed, good.errors
    listy = check_lyrics_text("5위 오지환 124억\n4위 박민우 140억\n2위 나성범 150억\n"
                              "2위 이대호 150억\n1위 양의지 152억", FA_ROWS)
    assert any("나열형" in e for e in listy.errors) and any("반전" in e for e in listy.errors)


def test_data_mismatch_errors():
    r = check_lyrics_text(GOOD.replace("백사십억", "백사십오억"), FA_ROWS)
    assert any("데이터에 없는 숫자 145" in e for e in r.errors)
    r = check_lyrics_text(GOOD.replace("5위 오지환", "5위 양의지"), FA_ROWS)
    assert any("5위와 선수 이름" in e for e in r.errors)
    # 비교 줄은 허용
    assert check_lyrics_text(GOOD.replace("하지만 1위 양의지", "하지만 1위 양의지, 오지환보다"), FA_ROWS).passed
    r = check_lyrics_text(GOOD + "\n한화 이글스도 부러워할 돈", FA_ROWS)
    assert any("한화 이글스" in e for e in r.errors)
    assert check_lyrics_text(GOOD, FA_ROWS).passed                 # 차이(16억, 2억)는 데이터에서 계산되어 허용


def test_overlap_warning_ignores_data_phrases():
    hist = [("old", ["공동 2위 나성범, 이대호 백오십억", "알고 보면 숫자는 믿음의 크기"])]
    r = check_lyrics_text(GOOD, FA_ROWS, history=hist)
    phrases = {o["phrase"] for o in r.overlaps}
    assert "알고 보면 숫자는" in phrases and not any("나성범" in p for p in phrases)
    assert r.passed and r.warnings                                 # 겹침은 경고 (오류 아님)


# ---------------------------------------------------------------- 가사 생성

def _envelope(payload: dict, *, error=False) -> str:
    body = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
    return json.dumps({"type": "result", "is_error": error, "result": body})


def test_generate_with_claude_fake_runner():
    payload = {"title": "백오십이억의 주인", "style": "K-hiphop",
               "versions": [{"name": "반전", "angle": "a", "lyrics": GOOD}] * 3}
    calls = []

    def runner(cmd, **kw):
        calls.append((cmd, kw["input"]))
        return subprocess.CompletedProcess(cmd, 0, stdout=_envelope(payload), stderr="")
    d = generate_with_claude("프롬프트", runner=runner)
    assert len(d.versions) == 3 and d.title == "백오십이억의 주인"
    assert calls[0][0][:2] == ["claude", "-p"] and "--tools" in calls[0][0] and calls[0][1] == "프롬프트"


@pytest.mark.parametrize(
    "proc",
    [
        subprocess.CompletedProcess([], 1, stdout="", stderr="not logged in"),
        subprocess.CompletedProcess([], 0, stdout=_envelope({}, error=True), stderr=""),
        subprocess.CompletedProcess([], 0, stdout=json.dumps({"result": "몰라요"}), stderr=""),
    ],
)
def test_generate_failures_raise_for_manual_fallback(proc):
    with pytest.raises(LyricsGenError):
        generate_with_claude("p", runner=lambda cmd, **kw: proc)


def test_save_brief_requires_passing_check(tmp_path):
    bad = check_lyrics_text("5위 오지환 124억", FA_ROWS)
    with pytest.raises(LyricsGenError, match="통과"):
        save_brief(tmp_path, "t", "s", "5위 오지환 124억", bad)
    good = check_lyrics_text(GOOD, FA_ROWS, history=[("old", ["알고 보면 숫자는 믿음의 크기"])])
    with pytest.raises(LyricsGenError, match="겹치는"):
        save_brief(tmp_path, "t", "s", GOOD, good)
    path = save_brief(tmp_path, "백오십이억의 주인", "K-hiphop", GOOD, good, overlaps_confirmed=True)
    md = path.read_text(encoding="utf-8")
    assert "## Style" in md and "## Lyrics" in md and "K-hiphop" in md and "[Chorus]" in md
    assert parse_response('설명 {"title":"t","versions":[{"name":"a","lyrics":"x"}]} 끝').title == "t"


# ---------------------------------------------------------------- 흐름: 기록·체크리스트·렌더·업로드

def make_suno_song(path: Path, lyrics: str) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=f=440:d=35",
            "-metadata",
            f"lyrics={lyrics}",
            "-metadata",
            "comment=made with suno; created=2026-10-10T01:15:08Z; id=abc-123",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
    )
    return path


@pytest.fixture
def manual_project(tmp_path):
    root = tmp_path / "projects"
    state, d = ap.start_from_song(make_suno_song(tmp_path / "s.m4a", FA_TIE), "fa", title="FA\nKBO FA TOP 5",
                                  template="yagu_v2_sticker", root=root)
    ap.align(state, d, aligner=fake_aligner)
    rows = [r.model_copy(update={"team": r.team or "롯데 자이언츠"}) for r in state.rows]
    ap.confirm_table(state, d, rows)
    for s in (5, 4, 3, 2, 1):
        ap.skip_photo(state, d, s)
        ap.use_manual_image(state, d, s, _white_bg_png(tmp_path))
    return state, d


def test_suno_tag_and_logs(manual_project):
    state, d = manual_project
    assert state.suno == {"made_with_suno": True, "created": "2026-10-10T01:15:08Z", "id": "abc-123"}
    events = [e["event"] for e in plog.read_log(d)]
    assert events[:2] == ["project_started", "lyrics_aligned"]
    assert "data_confirmed" in events and "lyrics_checked" in events and events.count("caricature_approved") == 5
    confirmed = next(e for e in plog.read_log(d) if e["event"] == "data_confirmed")
    assert {"field": "team", "from": "", "to": "롯데 자이언츠"} in [{k: c[k] for k in ("field", "from", "to")}
                                                                 for c in confirmed["changes"] if "field" in c]
    manual = next(e for e in plog.read_log(d) if e["event"] == "caricature_approved")
    # 사진 없이 진행 → 프롬프트 파일 없음
    assert manual["mode"] == "manual" and "프롬프트" not in (manual.get("prompt") or "")


def test_checklist_blocks_monetize_until_all_ok(manual_project, tmp_path):
    state, d = manual_project
    ap.choose_variation(state, d, ap.suggest_variation(state, d))
    ap.compose_cards(state, d)
    failing = {c["key"] for c in ap.checklist(state, d) if not c["ok"]}
    assert failing == {"suno", "no_logo", "data_source", "lyrics"}  # 표 확정·사진 출처·연출은 통과
    with pytest.raises(ap.FlowError, match="수익화용"):
        ap.render_all(state, d, rights_confirmed=True, kind="monetize", capcut_root=tmp_path, output_dir=tmp_path)
    ap.set_suno(state, d, paid=True, plan="Pro")
    for s in (5, 4, 3, 2, 1):
        ap.confirm_no_logo(state, d, s, True)
    ap.set_data_source(state, d, "KBO 공식 FA 계약 발표", "https://www.koreabaseball.com")
    ap.ack_lyrics(state, d)
    assert all(c["ok"] for c in ap.checklist(state, d))
    with pytest.raises(ap.FlowError, match="권리"):
        ap.render_all(state, d, rights_confirmed=False, kind="monetize", capcut_root=tmp_path, output_dir=tmp_path)


@pytest.mark.slow  # 실제 MP4 인코딩 — PR CI 에서는 건너뛰고 main·수동 실행에서만
def test_draft_and_monetize_render_and_upload(manual_project, tmp_path):
    state, d = manual_project
    chosen = var.Variation(transition="pop", accent="green_gold", subtitle="word", intro="title_card")
    ap.choose_variation(state, d, chosen)
    ap.compose_cards(state, d)
    draft = ap.render_all(state, d, kind="draft", capcut_root=tmp_path, output_dir=tmp_path)
    assert Path(draft["output"]).name == "fa_DRAFT.mp4"
    assert var.load_history(d.parent) == []                        # 시험 렌더는 연출 이력에 안 남음
    ap.set_suno(state, d, paid=True, plan="Pro")
    for s in (5, 4, 3, 2, 1):
        ap.confirm_no_logo(state, d, s, True)
    ap.set_data_source(state, d, "KBO 공식 FA 계약 발표", "https://www.koreabaseball.com")
    ap.ack_lyrics(state, d)
    res = ap.render_all(state, d, rights_confirmed=True, kind="monetize", capcut_root=tmp_path, output_dir=tmp_path)
    assert Path(res["output"]).name == "fa.mp4"
    assert var.load_history(d.parent)[-1]["slug"] == "fa"
    last = plog.read_log(d)[-1]
    assert last["event"] == "render" and last["kind"] == "monetize" and last["final_confirmed"]
    up = ap.make_upload(state, d)
    assert all(len(t) <= 100 for t in up["titles"]) and len(", ".join(up["tags"])) <= 500
    for must in ("데이터 출처: KBO 공식 FA 계약 발표", "음악: Suno로 제작", "[사진 출처]", "공동 2위"):
        assert must in up["description"]
    assert (d / "upload.txt").exists() and "오지환" in up["tags"]
    project = json.loads((d / "project.json").read_text(encoding="utf-8"))
    assert project["variation"]["transition"] == "pop" and project["rights_confirmed"] is True


def test_mock_cannot_monetize(tmp_path):
    state, d = ap.start_from_song(make_song(tmp_path / "s.m4a", FA_TIE), "m", mode="mock", root=tmp_path / "p")
    assert not next(c for c in ap.checklist(state, d) if c["key"] == "not_mock")["ok"]


def test_brief_link_prefills_data(tmp_path):
    root = tmp_path / "projects"
    (root / "_briefs" / "fa").mkdir(parents=True)
    (root / "_briefs" / "fa" / "brief.json").write_text(json.dumps({
        "topic": "FA", "title": "브리프 제목", "source_name": "KBO 발표", "source_url": "https://x",
        "rows": [{"rank": r["rank"], "player": r["player"], "team": r["team"], "record": r["record"], "year": "2024"}
                 for r in FA_ROWS]}, ensure_ascii=False), encoding="utf-8")
    state, d = ap.start_from_song(make_song(tmp_path / "s.m4a", FA_TIE), "x", brief="fa", root=root)
    assert state.title == "브리프 제목" and state.data_source_name == "KBO 발표"
    ap.align(state, d, aligner=fake_aligner)
    assert [(r.rank, r.team, r.year) for r in state.rows][3] == (2, "롯데 자이언츠", "2024")


# ---------------------------------------------------------------- 연출 렌더

def test_variation_frames(tmp_path):
    from shortsmaker.card_mode import make_renderer
    from shortsmaker.models import Project, load_template
    from tests.test_card_mode import SONG, make_cards
    make_cards(tmp_path)
    t = load_template("yagu_v2")
    base = dict(channel="yagu_v2", title="제목 한 줄\n둘째 줄", duration=30, lyrics=[l.model_dump() for l in SONG],
                items=[{"rank": r, "name": f"선수{r}", "image": f"card{r}.png"} for r in (5, 4, 3, 2, 1)])

    def r_(**v):
        p = Project.model_validate({**base, "variation": v})
        return make_renderer(p, tmp_path, t, p.lyrics)
    tc = r_(intro="title_card").frame_at(0.5)
    assert tc.getpixel((44, 1150)) == (0, 67, 200)                 # 제목 카드 파란 테두리 (제목 파랑 고정)
    fl = r_(transition="flash").frame_at(6.0 + 1 / 30)
    assert min(fl.getpixel((540, 1300))) > 200                     # 흰 섬광
    sl = r_(transition="slide").frame_at(6.0 + 2 / 30)
    assert sl.getpixel((300, 1300)) == (252, 252, 252)              # 아직 오른쪽에서 들어오는 중
    w = r_(subtitle="word")
    assert w.state(round(6.6 * 30))[3] == len("5위, 기아 ")           # 단어 단위 (두 번째 단어 시작 6.5초)
    ln = r_(subtitle="line")
    # 줄 전체, 떠오르는 중
    assert ln.state(round(6.0 * 30))[3] == len(SONG[1].text) and ln.state(round(6.0 * 30))[7] == 0


def test_lyrics_page_manual_flow(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("SHORTSMAKER_PROJECTS_DIR", str(tmp_path))
    page = Path(__file__).resolve().parents[1] / "pages" / "2_가사_만들기.py"
    at = AppTest.from_file(str(page), default_timeout=120).run()
    assert not at.exception
    at.text_input[0].set_value("KBO 역대 FA 계약 총액 TOP 5")
    at.button[0].click().run()                                     # 새 브리프
    name = "KBO_역대_FA_계약_총액_TOP_5"
    bj = tmp_path / "_briefs" / name / "brief.json"
    b = json.loads(bj.read_text(encoding="utf-8"))
    b["rows"] = FA_ROWS
    bj.write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
    at.run()
    at.text_area(key="b_title").set_value("야구선수 한 명에 152억?!\nKBO 역대 FA 몸값 TOP 5")
    at.text_input(key="b_src").set_value("KBO 공식 FA 계약 발표")
    at.text_input(key="b_url").set_value("https://www.koreabaseball.com")
    at.button(key="b_confirm").click().run()
    assert not at.exception and json.loads(bj.read_text(encoding="utf-8"))["data_confirmed"]
    bad = GOOD.replace("하지만 ", "").replace("알고 보면 ", "")
    answer = json.dumps(
        {
            "title": "백오십이억의 주인",
            "style": "K-hiphop",
            "versions": [
                {"name": "좋은 버전", "angle": "반전", "lyrics": GOOD},
                {"name": "나열 버전", "angle": "-", "lyrics": "5위 오지환 124억\n1위 양의지 152억"},
                {"name": "반전 없음", "angle": "-", "lyrics": bad},
            ],
        },
        ensure_ascii=False,
    )
    at.text_area(key="pasted").set_value(answer)
    at.button(key="parse").click().run()
    assert not at.exception and json.loads(bj.read_text(encoding="utf-8"))["draft"]
    assert not at.button(key="save_brief").disabled                # 1번: 검사 통과
    at.radio(key="ver").set_value(1).run()
    assert at.button(key="save_brief").disabled                    # 2번: 나열형 → 저장 잠김
    at.radio(key="ver").set_value(0).run()
    at.button(key="save_brief").click().run()
    md = (tmp_path / "_briefs" / name / "suno_brief.md").read_text(encoding="utf-8")
    assert "백오십이억의 주인" in md and "## Lyrics" in md
    log = json.loads(bj.read_text(encoding="utf-8"))["log"]
    assert [e["event"] for e in log] == ["data_confirmed", "lyrics_generated", "lyrics_chosen"]
