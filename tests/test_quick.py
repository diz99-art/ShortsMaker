"""DRAFT 버그 수정(제목·그림 자리·이름 속 숫자·카드 글) + 그림 움직임 + 빠른 제작."""
import io
from pathlib import Path

import pytest
from PIL import Image

from shortsmaker import auto_pipeline as ap
from shortsmaker import production_log as plog
from shortsmaker.card_compose import compose_card
from shortsmaker.lyrics_align import AlignedLine
from shortsmaker.quick import compare_lyrics, run_quick
from shortsmaker.quick_parse import EXAMPLE, parse_quick, request_prompt
from shortsmaker.rank_extract import RankRow, column_problems, display_text, extract_ranks, normalize_numbers
from shortsmaker.variation import idle_transform, top_shake
from tests.test_auto_pipeline import make_song

HR_SONG = """50개를 쳐도 끝이 아니다
5위 박병호 넥센 오십이 홈런
시작일 뿐, 다음 해 또 왔다
공동 3위 박병호 오십삼 홈런
공동 3위 심정수 현대 오십삼 홈런
하지만 그해 왕관은 다른 사람 몫
2위 이승엽 삼성 오십사 홈런
1위 이승엽 오십육 홈런
심정수보다 딱 세 개 더"""


# ---------------------------------------------------------------- 이름 속 한글 숫자

@pytest.mark.parametrize("text,expected", [
    ("1위 이승엽 오십육 홈런", "1위 이승엽 56홈런"),
    ("2위 이승엽 삼성 오십사", "2위 이승엽 삼성 오십사"),
    ("이 승리는 값지다", "이 승리는 값지다"),
    ("십승을 거둔 투수", "10승을 거둔 투수"),
    ("백억은 기본", "백억은 기본"),
    ("최고 몸값은 백오십이억!", "최고 몸값은 152억!"),
    ("이대호, 부산의 자존심", "이대호, 부산의 자존심"),
    ("오십육 홈런", "56홈런"),
])
def test_numbers_never_touch_names(text, expected):
    assert display_text(text) == expected


def test_protect_list_and_extract_keeps_names():
    assert normalize_numbers("구승엽 구 승", ["구승엽"]) == "구승엽 9승"
    rows = extract_ranks(HR_SONG.splitlines())
    assert [r.player for r in rows if r.rank in (2, 1)] == ["이승엽", "이승엽"]


# ---------------------------------------------------------------- 표 열 밀림

def test_column_shift_detected():
    shifted = [RankRow(rank=5, player="52홈런", team="박병호", record="2014", year="힘차게 풀스윙하는 타격 자세")]
    probs = " ".join(column_problems(shifted))
    assert "기록처럼" in probs and "4자리" in probs and "연도처럼" in probs and "구단 사전" in probs
    ok = [RankRow(rank=5, player="박병호", team="넥센 히어로즈", record="52홈런", year="2014")]
    assert column_problems(ok) == []
    assert column_problems([RankRow(rank=5, player="오타니", team="LA 다저스", record="54홈런")], allow_unknown_team=True) == []


def test_card_never_prints_scene_text(tmp_path):
    src = tmp_path / "c.png"
    Image.new("RGBA", (600, 900), (0, 0, 0, 0)).save(src)
    with_scene = compose_card(src, rank=5, record="52홈런", year="힘차게 풀스윙하는 타격 자세", team="넥센 히어로즈", player="박병호")
    no_year = compose_card(src, rank=5, record="52홈런", year="", team="넥센 히어로즈", player="박병호")
    assert with_scene.tobytes() == no_year.tobytes()               # 4자리 연도가 아니면 그 줄을 그리지 않음


# ---------------------------------------------------------------- 제목·그림 자리·자막 재생성

def hr_aligner(audio, lines, model):
    out = []
    for i, text in enumerate(lines):
        s = 1.0 + 3.0 * i
        out.append(AlignedLine(s, s + 2.8, text, 0.9, words=[(s + 0.3 * k, s + 0.3 * (k + 1), w)
                                                             for k, w in enumerate(text.split())]))
    return out


def _png(color: str) -> bytes:
    buf = io.BytesIO()
    im = Image.new("RGB", (300, 450), "white")
    from PIL import ImageDraw
    ImageDraw.Draw(im).rectangle([60, 60, 240, 400], fill=color)
    im.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def hr(tmp_path):
    song = make_song(tmp_path / "hr.m4a", HR_SONG, seconds=40)
    return song, tmp_path / "projects"


def test_title_saved_and_swap_and_subtitles_from_sung(hr):
    song, root = hr
    state, d = ap.start_from_song(song, "hr", title="옛 제목", root=root)
    ap.align(state, d, aligner=hr_aligner)
    ap.set_title(state, d, "50홈런도 6위라고?!\nKBO 단일 시즌 홈런 TOP 5")
    assert ap.load_project(d / "project.json")[0].title.startswith("50홈런도")
    q = parse_quick(EXAMPLE)
    rows = [r.model_copy(update={"lyric": e.lyric, "start": e.start}) for r, e in zip(q.rows, state.rows)]
    ap.confirm_table(state, d, rows)
    ap.confirm_table(state, d, rows)                               # 두 번 확정해도 자막이 또 바뀌지 않음
    subs = [l.text for l in ap.load_project(d / "project.json")[0].lyrics]
    assert "1위 이승엽 56홈런" in subs and not any("2승엽" in t for t in subs)
    for s, c in zip((5, 4, 3, 2, 1), ("#800020", "#800021", "#87CEEB", "#0000FF", "#0000FE")):
        ap.skip_photo(state, d, s)
        ap.use_manual_image(state, d, s, _png(c))
    a, b = state.players[1].approved, state.players[5].approved
    ap.swap_slots(state, d, 1, 5)
    assert (state.players[1].approved, state.players[5].approved) == (b, a)
    assert any(e["event"] == "images_swapped" for e in plog.read_log(d))
    assert any(e["event"] == "title_changed" for e in plog.read_log(d))


# ---------------------------------------------------------------- 그림 움직임

def test_idle_motions():
    assert idle_transform("kenburns", 5.0)[0] > idle_transform("kenburns", 1.0)[0] > 1.0
    assert idle_transform("float", 0.6)[2] != 0 and idle_transform("wobble", 0.45)[3] != 0
    assert abs(idle_transform("bounce", 0.12)[2]) > abs(idle_transform("bounce", 1.6)[2])
    assert idle_transform("float", 0.6, 1.5)[2] == pytest.approx(1.5 * idle_transform("float", 0.6)[2])
    assert idle_transform("none", 3.0) == (1.0, 0.0, 0.0, 0.0) and top_shake(1.0) == 0


def test_motion_frames_change_and_top_flash(tmp_path):
    from shortsmaker.card_mode import make_renderer
    from shortsmaker.models import Project, load_template
    from tests.test_card_mode import SONG, make_cards
    make_cards(tmp_path)
    t = load_template("yagu_v2")
    p = Project.model_validate(dict(channel="yagu_v2", title="t\nu", duration=30, lyrics=[l.model_dump() for l in SONG],
                                    items=[{"rank": r, "name": f"선수{r}", "image": f"card{r}.png"} for r in (5, 4, 3, 2, 1)],
                                    variation={"motion": "float"}))
    r = make_renderer(p, tmp_path, t, p.lyrics)
    a, b = r.frame_at(8.0), r.frame_at(8.5)                         # 5위 장면, 등장 끝난 뒤
    assert a.tobytes() != b.tobytes()                              # 그림이 계속 움직임
    top = r.frame_at(22.0 + 1 / 30)                                # 1위 등장 직후 번쩍
    assert min(top.getpixel((540, 1300))) > 200


# ---------------------------------------------------------------- 빠른 제작

def test_parse_example_and_errors():
    q = parse_quick("```\n" + EXAMPLE + "\n```")
    assert q.ok and q.title == "50홈런도 6위라고?!\nKBO 단일 시즌 홈런 TOP 5" and q.source_name == "뉴시스"
    assert [(r.rank, r.player) for r in q.rows][2] == (3, "심정수") and q.memos
    bad = parse_quick("제목: 하나 / 둘 / 셋\n출처: 뉴시스 | newsis.com\n순위 | 선수 | 팀 | 기록 | 연도 | 장면 설명\n"
                      "5 | 박병호 | 넥센 히어로즈 | 52홈런 | 2014\n9 | 이승엽 | 삼성 라이온즈 | 56홈런 | 2003 | 환호")
    assert any(e.startswith("1번째 줄: 제목") for e in bad.errors)
    assert any(e.startswith("2번째 줄: 출처 URL") for e in bad.errors)
    assert any(e.startswith("4번째 줄: 칸이 5개") for e in bad.errors)
    assert any(e.startswith("5번째 줄: 순위") for e in bad.errors)
    assert "확인 필요" in request_prompt("홈런") and "순위 | 선수 | 팀 | 기록 | 연도 | 장면 설명" in request_prompt("홈런")


def test_compare_lyrics_finds_mismatch():
    q = parse_quick(EXAMPLE)
    assert compare_lyrics(HR_SONG, q.rows) == []
    wrong = HR_SONG.replace("1위 이승엽 오십육", "1위 이승엽 오십오")
    assert any("56홈런" in i for i in compare_lyrics(wrong, q.rows))
    assert any("'1위'" in i for i in compare_lyrics(HR_SONG.replace("1위 이승엽 오십육 홈런", "그리고 마지막"), q.rows))


def test_run_quick_end_to_end(hr, tmp_path):
    song, root = hr
    q = parse_quick(EXAMPLE)
    images = [_png(c) for c in ("#800020", "#800021", "#87CEEB", "#0000FF", "#0000FE")]
    short = run_quick(q, song, images[:4], slug="q", root=root, aligner=hr_aligner)
    assert short.status == "stopped" and short.stage == "그림"
    bad_song = make_song(tmp_path / "bad.m4a", HR_SONG.replace("1위 이승엽 오십육", "1위 이승엽 오십오"), seconds=40)
    stop = run_quick(q, bad_song, images, slug="q2", root=root, aligner=hr_aligner, output_dir=tmp_path,
                     capcut_root=tmp_path)
    assert stop.status == "stopped" and stop.stage == "가사·표 대조" and stop.issues
    done = run_quick(q, None, images, slug="q2", root=root, aligner=hr_aligner, accept_mismatch=True,
                     output_dir=tmp_path, capcut_root=tmp_path)       # 이어서 하기 (곡 다시 안 올려도 됨)
    assert done.status == "done" and Path(done.output).name == "q2_DRAFT.mp4"
    state = ap.load_state(root / "q2")
    assert state.title.startswith("50홈런도") and state.data_source_name == "뉴시스" and state.variation
    assert [r.player for r in state.rows] == ["박병호", "박병호", "심정수", "이승엽", "이승엽"]
    events = [e["event"] for e in plog.read_log(root / "q2")]
    assert "quick_input" in events and "quick_mismatch_accepted" in events and events[-1] == "render"
