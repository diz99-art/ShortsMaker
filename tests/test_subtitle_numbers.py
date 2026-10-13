"""자막 숫자: 노래는 한글 숫자, 자막은 아라비아 숫자 (규칙·글자 시각·가사 두 벌·미리보기)."""
import json

import pytest

from shortsmaker import auto_pipeline as ap
from shortsmaker.card_timing import char_times
from shortsmaker.lyrics_check import check_lyrics_text
from shortsmaker.lyrics_gen import auto_subtitles, pair_problems, parse_response, save_brief
from shortsmaker.models import LyricLine, Word
from shortsmaker.quick_parse import EXAMPLE, parse_quick
from tests.test_quick import hr, hr_aligner  # noqa: F401


def line(text, sung, words):
    return LyricLine(start=words[0][0], end=words[-1][1], text=text, sung=sung,
                     words=[Word(start=a, end=b, text=w) for a, b, w in words])


def test_char_times_stay_inside_sung_span():
    words = [(40.0, 41.0, "1위"), (41.0, 42.0, "이승엽"), (42.0, 43.0, "오십육"), (43.0, 43.5, "홈런")]
    l = line("1위 이승엽 56홈런", "1위 이승엽 오십육 홈런", words)
    t = dict(zip(range(len(l.text)), char_times(l)))
    i5, i6, ih = l.text.index("5"), l.text.index("6"), l.text.index("홈")
    assert t[i5] == pytest.approx(42.0) and 42.0 < t[i6] < 43.0 and t[ih] == pytest.approx(43.0)
    assert t[l.text.index("이")] == pytest.approx(41.0)            # 바뀌지 않은 글자는 부른 시각 그대로
    y = line("25년", "이십오년", [(10.0, 12.0, "이십오년")])
    ty = char_times(y)
    assert ty[0] == 10.0 and ty[1] < ty[2] <= 12.0                   # 4글자 → 3글자, 원래 구간 안에서 나뉨


def test_lyrics_two_versions():
    rows = [{"rank": 1, "player": "이승엽", "team": "삼성 라이온즈", "record": "56홈런"}]
    sung = "[Verse]\n1위 이승엽 오십육 홈런\n이천삼 년의 여름"
    assert auto_subtitles(sung, rows) == "[Verse]\n1위 이승엽 56홈런\n2003년의 여름"
    assert pair_problems(sung, "[Verse]\n1위 56홈런", rows)            # 줄 수 다름
    assert any("이승엽" in p for p in pair_problems(sung, "[Verse]\n1위 2승엽 56홈런\n2003년의 여름", rows))
    d = parse_response('{"title":"t","versions":[{"name":"a","lyrics":"' + sung.replace("\n", "\\n") + '"}]}', rows)
    assert d.versions[0].subtitles.splitlines()[1] == "1위 이승엽 56홈런"   # 자막용이 없으면 자동으로


def test_brief_subtitles_used_in_project(hr, tmp_path):  # noqa: F811
    song, root = hr
    rows = [r.model_dump() | {"notes": ""} for r in parse_quick(EXAMPLE).rows]
    from tests.test_quick import HR_SONG
    subs = auto_subtitles(HR_SONG, rows).replace("심정수보다 딱 세 개 더", "심정수보다 딱 3개 더")
    brief = root / "_briefs" / "hr"
    rep = check_lyrics_text(HR_SONG, rows)
    if not rep.passed:
        rep.errors.clear()
    save_brief(brief, "t", "s", HR_SONG, rep, subtitles=subs, rows=rows)
    (brief / "brief.json").write_text(json.dumps({"rows": rows, "title": "t", "source_name": "뉴시스",
                                                  "source_url": "https://x", "chosen_lyrics": HR_SONG,
                                                  "chosen_subtitles": subs}, ensure_ascii=False), encoding="utf-8")
    assert "## 자막용" in (brief / "suno_brief.md").read_text(encoding="utf-8")
    state, d = ap.start_from_song(song, "hb", title="x", root=root, brief="hr")
    ap.align(state, d, aligner=hr_aligner)
    q = parse_quick(EXAMPLE)
    rows = [r.model_copy(update={"lyric": e.lyric, "start": e.start}) for r, e in zip(q.rows, state.rows)]
    ap.confirm_table(state, d, rows)
    texts = [l.text for l in ap.load_project(d / "project.json")[0].lyrics]
    assert "심정수보다 딱 3개 더" in texts and "1위 이승엽 56홈런" in texts
    proj = ap.load_project(d / "project.json")[0]
    assert all(l.sung for l in proj.lyrics) and any(l.sung != l.text for l in proj.lyrics)


def test_number_preview_and_apply(hr):  # noqa: F811
    song, root = hr
    state, d = ap.start_from_song(song, "np", title="x", root=root)
    ap.align(state, d, aligner=hr_aligner)
    q = parse_quick(EXAMPLE)
    rows = [r.model_copy(update={"lyric": e.lyric, "start": e.start}) for r, e in zip(q.rows, state.rows)]
    ap.confirm_table(state, d, rows)
    proj = ap.load_project(d / "project.json")[0]
    # 옛 프로젝트처럼 한글 숫자
    old = [l.model_copy(update={"text": l.sung or l.text, "sung": ""}) for l in proj.lyrics]
    ap.save_project(proj.model_copy(update={"lyrics": old}), d)
    prev = ap.number_preview(state, d)
    assert any(c["from"] == "1위 이승엽 오십육 홈런" and c["to"] == "1위 이승엽 56홈런" for c in prev)
    ap.apply_number_preview(state, d, [c for c in prev if "오십육" in c["from"]])
    texts = [l.text for l in ap.load_project(d / "project.json")[0].lyrics]
    assert "1위 이승엽 56홈런" in texts and "5위 박병호 넥센 오십이 홈런" in texts   # 고른 줄만
