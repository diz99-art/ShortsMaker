from PIL import Image

from shortsmaker.lyrics import build_ass, check_lyrics, parse_lrc, render_lyric_pngs
from shortsmaker.models import LyricLine, load_template

LRC = """[ti:제목]
[ar:가수]
[00:01.00]첫 줄
[00:03.50]둘째 줄
[00:06.00]
[00:08.00][00:20.00]반복 줄
[00:25.00]마지막
"""


def test_parse_lrc_timing():
    lines = parse_lrc(LRC, duration=27)
    assert [(l.start, l.end, l.text) for l in lines] == [
        (1.0, 3.5, "첫 줄"),
        (3.5, 6.0, "둘째 줄"),        # 빈 태그에서 끝남
        (8.0, 16.0, "반복 줄"),       # 다음 태그(20초)까지지만 최대 8초
        (20.0, 25.0, "반복 줄"),      # 한 줄에 태그 여러 개
        (25.0, 27.0, "마지막"),       # 영상 길이에서 잘림
    ]


def test_parse_lrc_drops_lines_after_duration():
    assert [l.text for l in parse_lrc("[00:01]a\n[00:40]b\n", duration=30)] == ["a"]


def test_check_lyrics_overlap_and_overrun():
    lines = [LyricLine(start=0, end=3, text="a"), LyricLine(start=2.5, end=4, text="b"),
             LyricLine(start=48, end=52, text="c")]
    errs = check_lyrics(lines, 50)
    assert any("겹칩니다" in e for e in errs)
    assert any("넘습니다" in e for e in errs)
    assert check_lyrics([LyricLine(start=0, end=3, text="a"), LyricLine(start=3, end=4, text="b")], 50) == []


def test_render_lyric_pngs(tmp_path):
    t = load_template("yagu")
    lines = [LyricLine(start=1, end=2, text="가사 한 줄"), LyricLine(start=3, end=4, text="두번째")]
    overlays, warnings = render_lyric_pngs(lines, t, tmp_path)
    assert len(overlays) == 2 and not warnings
    ov = overlays[0]
    assert (ov.x, ov.y, ov.start, ov.end) == (t.lyrics.x, t.lyrics.y, 1, 2)
    im = Image.open(ov.path)
    assert im.size == (t.lyrics.w, t.lyrics.h) and im.mode == "RGBA"
    assert im.getchannel("A").getextrema()[1] > 0


def test_build_ass():
    t = load_template("yagu")
    ass = build_ass([LyricLine(start=3.0, end=65.25, text="가사 {태그}")], t)
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "Style: Lyric,Pretendard," in ass
    assert "Dialogue: 0,0:00:03.00,0:01:05.25,Lyric,,0,0,0,,가사 (태그)" in ass
