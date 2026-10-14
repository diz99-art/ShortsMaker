"""카드 모드(yagu_v2) 테스트. 좌표·색 기대값은 참고 영상에서 잰 값 (templates/yagu_v2/MEASUREMENTS.md)."""
import json
import subprocess

import pytest
from PIL import Image, ImageDraw
from pydantic import ValidationError

from shortsmaker import card_layers
from shortsmaker.card_layers import layout_subtitle, render_chrome
from shortsmaker.card_mode import make_renderer
from shortsmaker.card_timing import CardModeError, card_timeline, char_times, rank_starts_from_lyrics
from shortsmaker.lyrics_align import AlignedLine, match_segments, refine_onsets, to_video_lyrics
from shortsmaker.models import LyricLine, Motion, Project, Template, Word, load_template
from shortsmaker.validate import pre_render

FPS = 30


@pytest.fixture(scope="module")
def t():
    return load_template("yagu_v2")


def make_cards(d):
    """정보가 다 들어간 완성 카드 대신 쓰는 단색 카드 (941×1672, 참고 카드와 같은 비율)."""
    colors = {5: "#C8102E", 4: "#F26522", 3: "#1F3B73", 2: "#FF6600", 1: "#2B4C9B"}
    for r, c in colors.items():
        im = Image.new("RGBA", (941, 1672), c)
        ImageDraw.Draw(im).text((300, 700), f"{r}", fill="white")
        im.save(d / f"card{r}.png")


def line(start, end, text, words=()):
    return LyricLine(start=start, end=end, text=text, words=[Word(start=a, end=b, text=w) for a, b, w in words])


SONG = [
    line(1.0, 4.0, "KBO 역대 최악의 연패는?"),
    line(
        6.0,
        8.0,
        "5위, 기아 타이거즈 16연패",
        [(6.0, 6.5, "5위,"), (6.5, 7.0, "기아"), (7.0, 7.6, "타이거즈"), (7.6, 8.0, "16연패")],
    ),
    line(10.0, 12.0, "4위, 롯데 자이언츠"),
    line(14.0, 16.0, "3위, 쌍방울 레이더스"),
    line(18.0, 20.0, "2위, 한화 이글스"),
    line(22.0, 25.0, "1위, 삼미 슈퍼스타즈"),
]


def make_project(tmp_path, **kw):
    make_cards(tmp_path)
    data = dict(channel="yagu_v2", title="끝나지 않던 연패…\nKBO 역대 최장 연패 TOP 5", duration=30,
                items=[{"rank": r, "name": f"팀{r}", "image": f"card{r}.png"} for r in (5, 4, 3, 2, 1)],
                lyrics=[l.model_dump() for l in SONG], rights_confirmed=True)
    data.update(kw)
    return Project.model_validate(data)


# ---------------------------------------------------------------- 스키마

def test_template_modes(t):
    assert t.mode == "card" and t.max_duration == 90 and t.intro is None
    assert load_template("yagu").max_duration == 60
    with pytest.raises(ValidationError, match="classic"):
        Template.model_validate({"name": "x", "fonts": {}})
    with pytest.raises(ValidationError, match="card"):
        Template.model_validate({"name": "x", "mode": "card", "fonts": {}})


def test_motion_curve():
    m = Motion(curve=[(0, 2.0), (0.1, 1.5), (1.0, 1.0)])
    assert m.scale_at(0.05) == pytest.approx(1.75) and m.scale_at(5) == 1.0 and m.length == 1.0
    assert m.scale_ext(-0.1) == pytest.approx(2.5)  # 첫 구간 기울기로 연장
    with pytest.raises(ValidationError):
        Motion(curve=[(0, 2.0), (1.0, 1.2)])  # 끝은 배율 1


def test_duration_limit_per_template(tmp_path, t):
    p = make_project(tmp_path, duration=90)
    assert not any("최대" in e for e in pre_render(p, tmp_path).errors)
    classic = Project.model_validate({**p.model_dump(), "channel": "yagu", "duration": 61})
    assert any("최대 60초" in e for e in pre_render(classic, tmp_path).errors)


# ---------------------------------------------------------------- 글자 시각·장면 시각

def test_char_times_split_inside_words():
    l = SONG[1]
    times = char_times(l)
    assert times[0] == pytest.approx(6.0)                 # '5'
    assert times[l.text.index("기")] == pytest.approx(6.5)  # 두 번째 단어 시작
    assert times[l.text.index("타")] == pytest.approx(7.0)
    assert times == sorted(times)
    assert times[l.text.index(" ")] == times[l.text.index(" ") + 1]  # 공백은 다음 글자와 함께


def test_char_times_without_words_evenly():
    times = char_times(line(1.0, 2.0, "가나 다라"))
    assert times == pytest.approx([1.0, 1.25, 1.5, 1.5, 1.75])


def test_rank_starts_from_lyrics_and_missing():
    starts = rank_starts_from_lyrics(SONG, [5, 4, 3, 2, 1])
    assert starts == {5: 6.0, 4: 10.0, 3: 14.0, 2: 18.0, 1: 22.0}
    with pytest.raises(CardModeError, match="5위"):
        rank_starts_from_lyrics(SONG[2:], [5, 4, 3, 2, 1])


def test_card_timeline(tmp_path, t):
    p = make_project(tmp_path)
    scenes = card_timeline(p, t, p.lyrics)
    assert [s.label for s in scenes] == ["인트로", "5위", "4위", "3위", "2위", "1위"]
    assert [s.start for s in scenes] == [0, 6, 10, 14, 18, 22] and scenes[-1].end == 30
    explicit = make_project(tmp_path, items=[{"rank": r, "name": "x", "image": f"card{r}.png", "start": s}
                                             for r, s in zip((5, 4, 3, 2, 1), (3, 6, 9, 12, 15))])
    assert [s.start for s in card_timeline(explicit, t, [])][1:] == [3, 6, 9, 12, 15]


# ---------------------------------------------------------------- 배치 (참고 영상 측정값)

def test_chrome_matches_measured_layout(tmp_path, t):
    base, head = render_chrome(t, make_project(tmp_path))
    frame = base.convert("RGBA")
    frame.alpha_composite(head)
    px = frame.convert("RGB").load()
    assert px[540, 150] == (252, 252, 252)                       # 흰 배경
    for y in range(506, 512):
        assert px[540, y] == (3, 3, 3)                           # 검은 구분선 y506~511
    assert px[540, 505] != (3, 3, 3) and px[540, 512] != (3, 3, 3)
    assert px[44, 60] == (17, 17, 17)                            # 알약 왼쪽 테두리 (x42~)
    assert px[262, 1825] == (245, 58, 13)                        # 구독 버튼 (x248~410, y1792~1857)
    blue = [(x, y) for y in range(200, 420) for x in range(0, 1080, 3) if px[x, y] == (0, 67, 200)]
    ys = [y for _, y in blue]
    assert blue and 228 <= min(ys) and max(ys) <= 392            # 파란 제목 두 줄 y236~379 부근
    assert head.getpixel((540, 1000))[3] == 0                    # 구분선 아래는 투명 (카드가 보임)


def test_card_punch_gap_frame_and_scale(tmp_path, t):
    p = make_project(tmp_path)
    r = make_renderer(p, tmp_path, t, p.lyrics)
    start = round(6.0 * FPS)
    gap = r.frame(start)                                          # 이전 카드가 사라진 빈 프레임
    assert gap.getpixel((540, 1300)) == (252, 252, 252)
    first = r.frame(start + 1)                                    # 2배 이상 크게 등장
    assert first.getpixel((40, 1300)) != (252, 252, 252)
    rest = r.frame(start + 3 * FPS)                               # 제자리 (card_box x222~858)
    assert rest.getpixel((150, 1300)) == (252, 252, 252)
    assert rest.getpixel((540, 1300)) != (252, 252, 252)
    # 제목 영역은 가려지지 않음
    assert first.getpixel((540, 300))[2] > 150 or first.getpixel((540, 300)) == (252, 252, 252)


def test_typing_keeps_positions_and_two_lines(t):
    st = t.card.subtitle
    font = t.font_path(st.font)
    lay = layout_subtitle("5위, 기아 타이거즈 16연패", st, font, 655)
    assert len(lay.lines) <= 2
    part = card_layers.render_subtitle(lay, 3, st, (1080, 1920)).getbbox()
    full = card_layers.render_subtitle(lay, 99, st, (1080, 1920)).getbbox()
    assert part[0] == full[0] and part[1] == full[1] and part[2] < full[2]  # 왼쪽·위는 그대로, 글자만 늘어남
    long = layout_subtitle("팬들 입장에선 오늘은 이기겠지라는 기대마저 힘들던 시간", st, font, 655)
    assert len(long.lines) == 2 and not long.overflow                     # 폭을 넓혀서라도 2줄


def test_subtitle_replaced_when_next_line_starts(tmp_path, t):
    p = make_project(tmp_path)
    r = make_renderer(p, tmp_path, t, p.lyrics)
    assert r.state(round(5.0 * FPS))[2] == 0       # 첫 줄은 끝난 뒤에도 다음 가사 전까지 남음
    assert r.state(round(6.0 * FPS))[2] == 1
    shown = [r.state(round(s * FPS))[3] for s in (6.0, 6.6, 7.2, 8.0)]
    assert shown == sorted(shown) and shown[0] < shown[-1]


@pytest.mark.slow  # 실제 MP4 인코딩 — PR CI 에서는 건너뛰고 main·수동 실행에서만
def test_render_short_video(tmp_path, t):
    from shortsmaker.pipeline import render
    p = make_project(tmp_path)
    res = render(p, tmp_path, output=tmp_path / "out.mp4")
    assert res.subtitle_mode == "typing"
    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=width,height:format=duration", "-of", "json",
         str(res.output)], capture_output=True, text=True).stdout)
    assert abs(float(probe["format"]["duration"]) - 30) < 0.5
    assert (probe["streams"][0]["width"], probe["streams"][0]["height"]) == (1080, 1920)


def test_capcut_card_draft(tmp_path, t):
    from shortsmaker.capcut_export import export_draft
    pdir = tmp_path / "p"
    pdir.mkdir()
    p = make_project(pdir)
    res = export_draft(p, pdir, root=tmp_path, name="ShortsMaker_v2")
    info = json.loads((res.path / "draft_info.json").read_text(encoding="utf-8"))
    tracks = {tr["name"]: tr for tr in info["tracks"]}
    assert {"배경", "로고·카드", "제목", "자막"} <= set(tracks)
    starts = [round(s["target_timerange"]["start"] / 1e6, 3) for s in tracks["로고·카드"]["segments"]]
    assert starts == [0.0] + [round(s + 1 / FPS, 3) for s in (6, 10, 14, 18, 22)]  # 빈 프레임 1개 뒤
    card = tracks["로고·카드"]["segments"][1]
    box = t.card.card_box
    assert card["clip"]["transform"]["y"] == pytest.approx((960 - (box.y + box.h / 2)) / 960, abs=1e-3)
    assert card["common_keyframes"]
    assert len(tracks["자막"]["segments"]) == len(SONG)


# ---------------------------------------------------------------- 가사 단어 타이밍

def test_words_kept_and_onset_refined():
    segment = (9.1, 12.3, "5위, 기아", [0.9], [(9.1, 11.8, " 5위,"), (11.8, 12.3, " 기아")])
    aligned = match_segments(["5위, 기아"], [segment])
    assert aligned[0].words == [(9.1, 11.8, "5위,"), (11.8, 12.3, "기아")]
    db = [-20.0] * 2000
    for i in range(905, 1140):  # 9.05~11.4초 조용, 11.4초부터 노래
        db[i] = -80.0
    refine_onsets(aligned, db)
    assert aligned[0].words[0][0] == pytest.approx(11.4) and aligned[0].start == pytest.approx(11.4)
    video = to_video_lyrics(aligned, music_start=1.0, duration=60)
    assert video[0].words[0].start == pytest.approx(10.4)


def test_onset_not_moved_when_sung_continuously():
    a = [AlignedLine(30.84, 31.82, "3위", 0.9, words=[(30.84, 31.82, "3위")])]
    refine_onsets(a, [-20.0] * 4000)
    assert a[0].start == 30.84


# ---------------------------------------------------------------- 스티커형 (yagu_v2_sticker)

@pytest.fixture(scope="module")
def ts():
    return load_template("yagu_v2_sticker")


def make_sticker_project(tmp_path, **kw):
    make_cards(tmp_path)
    Image.new("RGBA", (600, 800), "#336699").save(tmp_path / "intro.png")
    stat = "2022 | 104G 0.267 4HR\n38타점, 21도루"
    items = [{"rank": r, "name": f"선수{r}", "image": f"card{r}.png", "stat_line": stat} for r in (5, 4, 3, 2, 1)]
    data = dict(channel="yagu_v2_sticker", title="야구선수 한 명에 152억?!\nKBO 역대 FA 몸값 TOP 5", duration=30,
                items=items, lyrics=[l.model_dump() for l in SONG], rights_confirmed=True, intro_image="intro.png")
    data.update(kw)
    return Project.model_validate(data)


def test_sticker_template_measured(ts):
    c = ts.card
    assert c.style == "sticker" and c.stat_line.color == "#EB1B1B" and c.stat_line.max_lines == 2
    assert c.subtitle.intro_align == "left" and c.intro_box is not None
    assert ts.asset_path("style_ref.png") and ts.asset_path("logo.png")


def test_stat_line_types_after_scene_start(tmp_path, ts):
    p = make_sticker_project(tmp_path)
    r = make_renderer(p, tmp_path, ts, p.lyrics)
    start = 6.0
    counts = [r.state(round((start + dt) * FPS))[5] for dt in (0.0, 0.1, 0.3, 0.6, 2.0)]
    assert counts[0] == counts[1] == 0                      # 0.2초 전에는 안 보임
    assert 0 < counts[2] < counts[3] < counts[4] == len(p.items[0].stat_line)
    red = r.frame(round((start + 2.0) * FPS))
    reds = [red.getpixel((x, y)) for x in range(100, 1000, 4) for y in range(830, 930, 4)]
    assert (235, 27, 27) in reds                            # 빨간 기록 줄 (#EB1B1B)


def test_intro_character_and_left_subtitle(tmp_path, ts):
    p = make_sticker_project(tmp_path)
    r = make_renderer(p, tmp_path, ts, p.lyrics)
    img = r.frame(round(3.5 * FPS))                         # 인트로: 첫 줄 다 나온 뒤, 캐릭터 제자리 근처
    assert img.getpixel((410, 1300))[:3] == (0x33, 0x66, 0x99)  # intro_box (x60~760) 안의 캐릭터
    lay = layout_subtitle("KBO 역대 최악의 연패는?", ts.card.subtitle, ts.font_path("black"), 645, "left")
    assert lay.lines[0][2] == ts.card.subtitle.x + ts.card.subtitle.stroke_width


def test_capcut_sticker_has_stat_track(tmp_path, ts):
    from shortsmaker.capcut_export import export_draft
    pdir = tmp_path / "p"
    pdir.mkdir()
    p = make_sticker_project(pdir)
    res = export_draft(p, pdir, root=tmp_path, name="ShortsMaker_st")
    info = json.loads((res.path / "draft_info.json").read_text(encoding="utf-8"))
    tracks = {tr["name"]: tr for tr in info["tracks"]}
    assert len(tracks["기록 줄"]["segments"]) == 5
    assert tracks["기록 줄"]["segments"][0]["target_timerange"]["start"] == round((6.0 + 0.2) * 1e6)
