"""가사 줄 나누기 단위 테스트 — LRC 경계 사례, 자막 줄 합치기, 폭에 맞춘 줄바꿈, 카드 자막 배치.

LRC 기본 타이밍은 test_lyrics.py, 글자별 시각은 test_subtitle_numbers.py 에 있다.
"""
import pytest

from shortsmaker.auto_pipeline import norm_line, subtitle_lines
from shortsmaker.card_layers import layout_subtitle
from shortsmaker.lyrics import MAX_HOLD, parse_lrc
from shortsmaker.models import FONTS_DIR, LyricLine, load_template
from shortsmaker.rank_extract import RankRow
from shortsmaker.scenes import _font, _wrap

FONT = FONTS_DIR / "Pretendard-Bold.otf"


# ---------------------------------------------------------------- LRC

def test_lrc_minutes_order_and_metadata():
    text = "[ti:제목]\n[ar:가수]\n[01:02.5]뒤\n[00:10.00]앞\n"
    lines = parse_lrc(text)
    assert [(l.start, l.text) for l in lines] == [(10.0, "앞"), (62.5, "뒤")]   # 메타 태그 무시, 시각 순 정렬
    assert lines[0].end == 10.0 + MAX_HOLD                                      # 다음 줄이 멀면 최대 표시 시간까지
    assert lines[1].end == 62.5 + 4.0                                           # 마지막 줄은 기본 4초


def test_lrc_line_cut_at_duration():
    assert [(l.start, l.end) for l in parse_lrc("[00:01.00]a\n[00:03.00]b", duration=2.0)] == [(1.0, 2.0)]


# ---------------------------------------------------------------- 자막 줄 ('N위, 팀' + 기록 줄 합치기)

ROWS = [RankRow(rank=5, team="KIA 타이거즈", record="16연패"), RankRow(rank=4, team="롯데 자이언츠", record="16연패")]
SUNG = [LyricLine(start=1, end=2, text="5위, 기아 타이거즈"), LyricLine(start=2, end=3, text="십육 연패"),
        LyricLine(start=3, end=4, text="강팀도 흐름이 꼬이면")]


def test_subtitle_lines_merge_rank_and_record():
    out = subtitle_lines(SUNG, ROWS, merge=True)
    assert [(l.start, l.end, l.text) for l in out] == [(1, 3, "5위, 기아 타이거즈 16연패"),
                                                      (3, 4, "강팀도 흐름이 꼬이면")]
    assert out[0].sung == "5위, 기아 타이거즈 십육 연패"          # 부른 원문은 글자 시각 계산용으로 남긴다


def test_subtitle_lines_without_merge_keeps_each_line():
    out = subtitle_lines(SUNG, ROWS, merge=False)
    assert [l.text for l in out] == ["5위, 기아 타이거즈", "16연패", "강팀도 흐름이 꼬이면"]
    assert [l.sung for l in out] == [l.text for l in SUNG]


def test_subtitle_lines_prefers_brief_spelling():
    """가사 브리프의 자막 표기(sub_map)가 있으면 자동 규칙보다 먼저 쓴다."""
    sub_map = {norm_line("강팀도 흐름이 꼬이면"): "강팀도, 흐름이 꼬이면"}
    out = subtitle_lines(SUNG[2:], ROWS, merge=False, sub_map=sub_map)
    assert out[0].text == "강팀도, 흐름이 꼬이면"


def test_norm_line_ignores_punctuation_and_spaces():
    assert norm_line("5위, 기아 타이거즈!") == norm_line("5위기아타이거즈") == "5위기아타이거즈"


# ---------------------------------------------------------------- 폭에 맞춘 줄바꿈

def test_wrap_by_words_then_chars():
    font = _font(str(FONT), 40)
    assert _wrap("가나다 라마바 사아자 차카타", font, 200) == ["가나다", "라마바", "사아자", "차카타"]
    long_word = _wrap("아주아주아주아주아주긴단어", font, 120)
    assert "".join(long_word) == "아주아주아주아주아주긴단어" and len(long_word) > 1   # 한 단어가 넘치면 글자 단위
    assert _wrap("첫줄\n둘째 줄", font, 1000) == ["첫줄", "둘째 줄"]                 # 직접 넣은 줄바꿈은 지킨다


# ---------------------------------------------------------------- 카드 자막 배치 (yagu_v2)

@pytest.fixture(scope="module")
def sub_style():
    t = load_template("yagu_v2", local=False)
    st = t.card_layout.subtitle
    return st, t.font_path(st.font)


def test_layout_short_line_keeps_full_size(sub_style):
    st, font = sub_style
    lay = layout_subtitle("짧은 자막", st, font, 1000)
    assert len(lay.lines) == 1 and not lay.overflow and lay.font.size == st.size


def test_layout_explicit_newline_keeps_char_offsets(sub_style):
    st, font = sub_style
    lay = layout_subtitle("첫 줄\n둘째 줄", st, font, 1000)
    assert [(text, start) for text, start, _, _ in lay.lines] == [("첫 줄", 0), ("둘째 줄", 4)]  # 원문 글자 위치


def test_layout_overflow_shrinks_to_min_and_flags(sub_style):
    st, font = sub_style
    lay = layout_subtitle("아주 긴 자막 " * 30, st, font, 1000)
    assert lay.overflow and lay.font.size == st.min_size and len(lay.lines) > st.max_lines
