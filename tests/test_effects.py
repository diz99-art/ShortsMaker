"""시청 유지 효과: 후킹·비트·카운트업·게이지·효과음·진행 표시·엔딩 보너스·연출 요소."""
import json
import subprocess
import wave

import numpy as np
import pytest
from PIL import Image

from shortsmaker import auto_pipeline as ap
from shortsmaker import effects as fx
from shortsmaker import sfx
from shortsmaker import variation as var
from shortsmaker.card_mode import card_timeline, hook_length, make_renderer, render_video, sfx_cues
from shortsmaker.models import Bonus, load_template
from shortsmaker.quick_parse import EXAMPLE, parse_quick
from tests.test_card_mode import SONG, make_project

FX = {"hook": "slam", "beat": "off", "count": "static", "sfx": "a", "progress": "dots", "ending": "stamp"}


@pytest.fixture
def t():
    return load_template("yagu_v2")


# ---------------------------------------------------------------- 비트

def click_track(path, bpm=120, seconds=16.0, sr=22050):
    """박자마다 킥 드럼 같은 '쿵' + 뒤 절반은 크게 (강한 구간)."""
    n = int(seconds * sr)
    y = np.zeros(n, dtype=np.float32)
    step = 60 / bpm
    kick_n = int(0.15 * sr)
    tt = np.arange(kick_n) / sr
    kick = (np.sin(2 * np.pi * (60 + 90 * np.exp(-tt / 0.03)) * tt) * np.exp(-tt / 0.05)).astype(np.float32)
    for k in range(int(seconds / step)):
        i = int(k * step * sr)
        amp = 0.35 if k * step < seconds / 2 else 0.95
        y[i:i + kick_n] += amp * kick[: n - i]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())
    return path


def test_beats_and_strong_sections(tmp_path):
    bm = fx.analyze_beats(click_track(tmp_path / "c.wav"))
    assert 110 <= bm.tempo <= 130
    gaps = np.diff(bm.beats)
    assert np.median(gaps) == pytest.approx(0.5, abs=0.03)
    assert bm.strong and bm.strong[0][0] >= 6.5                    # 크게 친 뒤 절반만 강한 구간
    sh = bm.shifted(2.0)
    assert sh.beats[0] >= 0 and sh.beats[0] == pytest.approx(bm.beats[[b >= 2 for b in bm.beats].index(True)] - 2)


def test_pulse_state_and_transform():
    bm = fx.BeatMap(120, [1.0, 1.5, 2.0], [(1.4, 3.0)])
    assert fx.pulse_state(bm, 0.5, 30, True) is None               # 첫 박자 전
    ps = fx.pulse_state(bm, 1.0, 30, True)
    assert ps == (0, 0, False)                                      # 강한 구간 밖
    s, dx, fl = fx.pulse_transform(ps, 30)
    assert s > 1.03 and dx == 0 and fl == 0
    strong = fx.pulse_state(bm, 2.0 + 1 / 30, 30, True)
    assert strong[2] and fx.pulse_transform(strong, 30)[2] > 0     # 강한 구간 짝수 박자: 번쩍
    assert fx.pulse_state(bm, 1.0 + 0.4, 30, True) is None         # 펄스 끝


# ---------------------------------------------------------------- 카운트업·게이지

def test_count_text():
    assert fx.count_text("56홈런", 0.5) == "28홈런"
    assert fx.count_text("56홈런", 1.0) == "56홈런"
    assert fx.count_text("152억", 0.0) == "0억"
    assert fx.count_text("1,234안타", 0.5) == "617안타"
    assert fx.count_text(".333", 1.0) == ".333" and fx.count_text("3.45", 0.5) == "1.73"
    assert fx.count_frac(0.0) == 0 and fx.count_frac(10) == 1
    assert fx.count_frac(fx.COUNT_DELAY + fx.COUNT_LEN / 2) > 0.5     # 처음 빠르게
    g = fx.make_gauge("50홈런", [52, 53, 56], 512, 1792)
    assert g.base == 50 and g.top == pytest.approx(56 * 1.08)
    assert fx.make_gauge("기준 없음", [52], 512, 1792) is None


# ---------------------------------------------------------------- 양식·보너스

def test_quick_form_extras():
    q = parse_quick(EXAMPLE)
    assert q.ok and q.hook == "50홈런이 6위?!" and q.gauge_base == "50홈런"
    assert q.bonus == {"kind": "rank", "rank": 6, "name": "디아즈", "team": "삼성 라이온즈", "value": "50홈런",
                       "year": "2025"}
    bad = parse_quick(EXAMPLE.replace("기준: 50홈런", "기준: 오십") + "\n후킹: " + "가" * 40)
    assert any("기준선" in e for e in bad.errors) and any("후킹" in e for e in bad.errors)
    assert ap.parse_bonus("예고 | 다음 편: KBO 도루 TOP 5") == {"kind": "teaser", "text": "다음 편: KBO 도루 TOP 5"}
    with pytest.raises(ap.FlowError):
        ap.parse_bonus("디아즈 50홈런")
    with pytest.raises(ValueError):
        Bonus(kind="rank", rank=6, name="디아즈")                    # 기록 없음


# ---------------------------------------------------------------- 타임라인·렌더러

def fx_project(tmp_path, **kw):
    data = dict(variation=FX, hook="연패가 16번?!", bonus={"kind": "rank", "rank": 6, "name": "팀6", "team": "",
                                                          "value": "15연패"})
    data.update(kw)
    return make_project(tmp_path, **data)


def test_timeline_bonus_and_hook(tmp_path, t):
    p = fx_project(tmp_path)
    scenes = card_timeline(p, t, SONG)
    assert [s.kind for s in scenes][-2:] == ["rank", "bonus"]
    assert scenes[-1].start == pytest.approx(max(22 + fx.BONUS_MIN_TOP, 30 - fx.BONUS_LEN)) and scenes[-1].end == 30
    assert hook_length(p, scenes) == fx.HOOK_LEN
    short = make_project(tmp_path, duration=30, bonus={"kind": "teaser", "text": "다음 편"},
                         items=[{"rank": r, "name": f"팀{r}", "image": f"card{r}.png", "start": s}
                                for r, s in zip((5, 4, 3, 2, 1), (6, 10, 14, 18, 26.5))])
    assert all(s.kind != "bonus" for s in card_timeline(short, t, SONG))   # 1위 뒤 자리가 없으면 빼고 경고
    assert hook_length(p.model_copy(update={"hook": ""}), scenes) == 0


def test_sfx_cues_and_credits(tmp_path, t):
    p = fx_project(tmp_path)
    cues = sfx_cues(p, t, card_timeline(p, t, SONG))
    kinds = [c.kind for c in cues]
    assert kinds[0] == "slam" and kinds.count("whoosh") == 6 and kinds.count("thud") == 6 and kinds.count("cheer") == 1
    assert sfx.problems(cues, "a") == []                              # 들어 있는 효과음은 모두 출처 확인됨
    lines = sfx.credits_lines(cues, "a")
    assert any("Clapping hurray" in l and "Public domain" in l and "commons.wikimedia.org" in l for l in lines)
    assert any("자체 제작" in l for l in lines)
    assert sfx_cues(p.model_copy(update={"variation": {**FX, "sfx": "off"}}), t, card_timeline(p, t, SONG)) == []


def test_sfx_unlicensed_is_blocked(tmp_path):
    folder = tmp_path / "sfx"
    sfx.write_wav(folder / "cheer_a.wav", np.zeros(100))
    (folder / "credits.json").write_text(json.dumps({"cheer_a.wav": {"license": "CC BY 4.0"}}), encoding="utf-8")
    probs = sfx.problems([sfx.Cue(1.0, "cheer"), sfx.Cue(2.0, "thud")], "a", folder)
    assert any("라이선스" in p for p in probs) and any("파일이 없습니다" in p for p in probs)


def test_synth_is_deterministic_and_short():
    a, b = sfx.synth_whoosh("a"), sfx.synth_whoosh("a")
    assert np.array_equal(a, b) and 0.2 < len(a) / sfx.RATE < 0.5 and np.max(np.abs(a)) <= 0.9
    assert len(sfx.synth_thud()) / sfx.RATE < 0.6


def test_renderer_frames(tmp_path, t):
    p = fx_project(tmp_path, variation={**FX, "count": "static"})
    r = make_renderer(p, tmp_path, t, SONG)
    hook, plain = r.frame_at(0.5), make_renderer(make_project(tmp_path), tmp_path, t, SONG).frame_at(0.5)
    assert hook.tobytes() != plain.tobytes()                         # 0~2초 후킹 문구
    assert r.state(round(1.0 * 30))[1] == "hook" and r.state(round(2.5 * 30))[1] != "hook"
    # 진행 점: 5위 장면 1개, 1위 장면 5개 채움
    assert r.state(round(7.0 * 30))[13][0] == 1 and r.state(round(23.0 * 30))[13][0] == 5
    bonus = r.frame_at(28.0)
    assert bonus.tobytes() != r.frame_at(24.0).tobytes()
    assert r.state(round(28 * 30))[0] == len(r.scenes) - 1


def test_countup_and_gauge_frames(tmp_path, t):
    from shortsmaker.card_compose import compose_card
    src = tmp_path / "art.png"
    Image.new("RGBA", (600, 900), (0, 0, 0, 0)).save(src)
    for r_ in (5, 4, 3, 2, 1):
        kw = dict(rank=r_, record=f"{10 + r_}연패", year="2010", team="KIA 타이거즈", player="")
        compose_card(src, **kw).save(tmp_path / f"card{r_}.png")
        meta = {}
        compose_card(src, **kw, omit_record=True, meta=meta).save(tmp_path / f"card{r_}_base.png")
        meta["base"] = f"card{r_}_base.png"
        (tmp_path / f"card{r_}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    items = [{"rank": r_, "name": f"팀{r_}", "value": f"{10 + r_}연패", "image": f"card{r_}.png"} for r_ in (5, 4, 3, 2, 1)]
    from shortsmaker.models import Project
    p = Project.model_validate(dict(channel="yagu_v2", title="t\nu", duration=30, items=items,
                                    lyrics=[l.model_dump() for l in SONG], gauge_base="13연패",
                                    variation={**FX, "count": "countup_gauge", "sfx": "off"}))
    r = make_renderer(p, tmp_path, t, SONG)
    early = r.state(round((6.0 + fx.COUNT_DELAY + 0.1) * 30))
    late = r.state(round(9.0 * 30))
    assert early[11] is not None and early[11] != "15연패" and late[11] is None   # 카운트업 중 / 끝나면 원래 글자
    assert early[12] < late[12] == 15.0                                  # 게이지도 같이 올라감
    assert r.frame_at(6.0 + fx.COUNT_DELAY + 0.1).tobytes() != r.frame_at(9.0).tobytes()


def test_render_mixes_sfx(tmp_path, t):
    p = fx_project(tmp_path, variation={**FX, "progress": "none"}, hook="", bonus=None, duration=30)
    r = make_renderer(p, tmp_path, t, SONG)
    out = tmp_path / "o.mp4"
    render_video(r, out)

    def vol(start, dur=0.4):
        res = subprocess.run(["ffmpeg", "-hide_banner", "-ss", str(start), "-t", str(dur), "-i", str(out),
                              "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
        return float([l for l in res.splitlines() if "max_volume" in l][0].split(":")[1].split()[0])
    assert vol(6.0) > -30 and vol(3.0) < -60                          # 음악 없이도 5위 '쿵'만 들림


# ---------------------------------------------------------------- 연출 요소

def test_variation_axes(tmp_path):
    assert set(FX) <= set(var.AXES) and var.MIN_DIFF == 3
    old = var.Variation.model_validate({"transition": "slide", "accent": "red_gold", "subtitle": "word",
                                        "intro": "shrink"})
    assert (old.beat, old.count, old.sfx, old.progress) == ("off", "static", "off", "none")   # 예전 영상은 그대로
    for i in range(3):
        var.record(f"p{i}", var.suggest(tmp_path, seed=i), tmp_path)
    s = var.suggest(tmp_path, seed=9)
    assert all(getattr(s, k) != v for k, v in var.OFF.items())       # 추천은 효과를 끄지 않음
    assert not var.conflicts(s, tmp_path)
