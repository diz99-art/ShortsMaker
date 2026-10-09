from pathlib import Path

from shortsmaker.ffmpeg_builder import TimedOverlay, final_args
from shortsmaker.models import Project, load_template
from shortsmaker.timeline import build_timeline


def _project(**kw):
    data = dict(channel="yagu", title="t", items=[{"rank": r, "name": "n"} for r in (5, 4, 3, 2, 1)])
    data.update(kw)
    return Project.model_validate(data)


def _filter(args: list[str]) -> str:
    return args[args.index("-filter_complex") + 1]


def test_xfade_offsets_match_scene_starts():
    t = load_template("yagu")
    p = _project(duration=50)
    scenes = build_timeline(p, fps=t.fps, transition=t.effects.transition)
    clips = [Path(f"c{i}.mp4") for i in range(len(scenes))]
    args = final_args(clips, [s.start for s in scenes], t, p.duration, Path("o.mp4"))
    fc = _filter(args)
    assert fc.count("xfade=") == 6
    for s in scenes[1:]:
        assert f"offset={s.start:g}" in fc
    assert "anullsrc" in " ".join(args)  # 음악 없으면 무음 트랙
    assert args[-1] == "o.mp4" and "+faststart" in args


def test_music_trim_and_fade():
    t = load_template("yagu")
    args = final_args([Path("a.mp4"), Path("b.mp4")], [0, 3], t, 40, Path("o.mp4"),
                      music=Path("m.mp3"), music_start=2.5)
    i = args.index("m.mp3")
    assert args[i - 3:i - 1] == ["-ss", "2.5"]
    fc = _filter(args)
    assert "atrim=0:40" in fc and "afade=t=out:st=38.5:d=1.5" in fc


def test_timed_overlays():
    t = load_template("yagu")
    ov = [TimedOverlay(Path("l0.png"), 60, 1660, 3.0, 6.5)]
    fc = _filter(final_args([Path("a.mp4")], [0], t, 30, Path("o.mp4"), overlays=ov))
    assert "enable='between(t\\,3\\,6.5)'" in fc
