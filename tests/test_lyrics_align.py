"""가사 자동 맞추기: 모델 없이 도는 단위 테스트 + (설치돼 있으면) 실제 정렬 slow 테스트."""
import importlib.util
import subprocess

import pytest

from shortsmaker.lyrics import check_lyrics, parse_lrc
from shortsmaker.lyrics_align import (
    AlignedLine,
    AlignError,
    auto_align,
    clean_lyrics_text,
    embedded_lyrics,
    lrc_text,
    match_segments,
    to_video_lyrics,
)
from shortsmaker.models import LyricLine, Project

SUNO = """백억은 기본, 몸값이 뛴다
[Verse]

5위 오지환, LG의 심장
(Chorus)
[00:12.50]최고 몸값은 백오십이억!
"""


def make_project(**kw) -> Project:
    data = dict(channel="yagu", title="테스트", music="music.wav",
                items=[{"rank": r, "name": f"선수{r}"} for r in (5, 4, 3, 2, 1)])
    data.update(kw)
    return Project.model_validate(data)


def test_clean_lyrics_text_drops_sections_blank_and_timetags():
    assert clean_lyrics_text(SUNO) == ["백억은 기본, 몸값이 뛴다", "5위 오지환, LG의 심장", "최고 몸값은 백오십이억!"]


def test_match_segments_by_text_and_interpolates_missing():
    lines = ["첫 줄", "둘째 줄", "셋째 줄", "넷째 줄"]
    segs = [(1.0, 2.0, " 첫 줄", [0.9, 0.8]), (6.0, 7.5, "넷째줄!", [0.7])]  # 공백·문장부호 무시
    out = match_segments(lines, segs)
    assert [(a.start, a.end, a.found) for a in out] == [
        (1.0, 2.0, True), (2.0, 4.0, False), (4.0, 6.0, False), (6.0, 7.5, True)]
    assert out[0].prob == 0.85 and not out[0].needs_check
    assert out[1].needs_check


def test_low_probability_or_too_short_needs_check():
    assert AlignedLine(0, 2, "a", prob=0.3).needs_check
    assert AlignedLine(0, 0.2, "a", prob=0.9).needs_check
    assert not AlignedLine(0, 2, "a", prob=0.9).needs_check


def test_to_video_lyrics_shift_clip_overlap_and_hold():
    aligned = [
        AlignedLine(1.0, 3.0, "곡 시작 전"),    # music_start(5) 이전 → 제외
        AlignedLine(4.0, 7.0, "걸침"),          # 앞이 잘림 → 0~2
        AlignedLine(6.5, 9.0, "겹침"),          # 앞 줄 끝(7)과 겹침 → 앞 줄을 1.5에서 끊음
        AlignedLine(9.4, 11.0, "짧은 틈"),      # 0.4초 틈 → 앞 줄을 이어 붙임
        AlignedLine(13.0, 40.0, "아주 김"),     # 최대 8초
        AlignedLine(52.0, 58.0, "영상 끝 걸침"),  # duration(50)에서 자름
        AlignedLine(60.0, 62.0, "영상 밖"),
    ]
    out = to_video_lyrics(aligned, music_start=5.0, duration=50.0)
    assert [(l.start, l.end, l.text) for l in out] == [
        (0.0, 1.5, "걸침"), (1.5, 4.4, "겹침"), (4.4, 6.0, "짧은 틈"),
        (8.0, 16.0, "아주 김"), (47.0, 50.0, "영상 끝 걸침")]
    assert check_lyrics(out, 50.0) == []


def test_lrc_round_trip_keeps_spans():
    lines = [LyricLine(start=0.5, end=2.25, text="하나"), LyricLine(start=2.25, end=4.0, text="둘"),
             LyricLine(start=6.1, end=9.0, text="셋")]
    text = lrc_text(lines)
    assert text.splitlines() == ["[00:00.50]하나", "[00:02.25]둘", "[00:04.00]", "[00:06.10]셋", "[00:09.00]"]
    assert parse_lrc(text, 50) == lines


def test_auto_align_with_fake_aligner(tmp_path):
    (tmp_path / "music.wav").write_bytes(b"")
    calls = {}

    def fake(audio, lines, size):
        calls.update(audio=audio, lines=lines, size=size)
        return [AlignedLine(1.0 + 3 * i, 3.0 + 3 * i, t, prob=0.9 if i else 0.2) for i, t in enumerate(lines)]

    res = auto_align(make_project(), tmp_path, "[Intro]\n첫 줄\n\n둘째 줄\n", vocals=False,
                     model_size="small", aligner=fake)
    assert calls == {"audio": tmp_path / "music.wav", "lines": ["첫 줄", "둘째 줄"], "size": "small"}
    assert [(l.start, l.end, l.text) for l in res.lines] == [(1.0, 3.0, "첫 줄"), (4.0, 6.0, "둘째 줄")]
    assert res.needs_check == ["첫 줄 (신뢰도 0.2)"]
    assert res.lrc_path == tmp_path / "lyrics_auto.lrc"
    assert parse_lrc(res.lrc_path.read_text(encoding="utf-8"), 50) == res.lines


def test_auto_align_errors(tmp_path):
    with pytest.raises(AlignError, match="음악 파일을 찾을 수 없습니다"):
        auto_align(make_project(), tmp_path, "가사", vocals=False, aligner=lambda *a: [])
    (tmp_path / "music.wav").write_bytes(b"")
    with pytest.raises(AlignError, match="가사 텍스트가 없습니다"):
        auto_align(make_project(), tmp_path, "[Verse]\n\n", vocals=False, aligner=lambda *a: [])


def test_embedded_lyrics_reads_tag(tmp_path):
    song = tmp_path / "tagged.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=d=1", "-c:a", "aac",
                    "-metadata", "lyrics=첫 줄\n둘째 줄", str(song)], check=True)
    assert clean_lyrics_text(embedded_lyrics(song) or "") == ["첫 줄", "둘째 줄"]


@pytest.mark.slow
@pytest.mark.skipif(importlib.util.find_spec("stable_whisper") is None, reason="requirements-align 미설치")
def test_real_alignment_orders_lines(tmp_path):
    """실제 모델(small)로 합성 음성 없이도 결과 형식·순서가 맞는지 확인 (사인파라 정확도는 보지 않음)."""
    song = tmp_path / "music.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=440:d=8", str(song)], check=True)
    res = auto_align(make_project(duration=30), tmp_path, "첫 줄\n둘째 줄", vocals=False, model_size="small")
    assert [l.text for l in res.lines] == ["첫 줄", "둘째 줄"]
    assert check_lyrics(res.lines, 30) == []


def test_eval_align_compare():
    import importlib.util as iu

    from shortsmaker.models import ROOT
    spec = iu.spec_from_file_location("eval_align", ROOT / "scripts" / "eval_align.py")
    mod = iu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ref = "[00:01.00]가\n[00:03.00]나\n[00:06.00]다\n[00:08.00]\n"
    auto = "[00:01.20]가\n[00:03.60]나\n[00:07.50]다\n[00:09.00]\n"
    res = mod.compare(auto, ref)
    assert res["total"] == 3 and res["within"] == {0.3: 1, 0.5: 1, 1.0: 2} and res["max"] == 1.5
