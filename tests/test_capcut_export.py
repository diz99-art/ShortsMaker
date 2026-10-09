"""CapCut 초안 구조 테스트 (임시 폴더에만 쓴다 — 실제 CapCut 폴더는 건드리지 않음).

자동 테스트 통과 ≠ CapCut 앱에서 열림. 앱 확인은 사람이 해야 한다.
"""
import json

import pytest

from shortsmaker.capcut_export import (
    CapCutExportError, build_srt, export_draft, export_manual_kit,
)
from shortsmaker.models import LyricLine, ROOT, load_project


@pytest.fixture
def sample():
    return load_project(ROOT / "samples" / "yagu_demo" / "project.json")


def test_draft_structure(tmp_path, sample):
    project, project_dir = sample
    res = export_draft(project, project_dir, root=tmp_path, name="ShortsMaker_t")
    info = json.loads((res.path / "draft_info.json").read_text(encoding="utf-8"))
    assert info == json.loads((res.path / "draft_content.json").read_text(encoding="utf-8"))
    assert (info["canvas_config"]["width"], info["canvas_config"]["height"]) == (1080, 1920)
    assert info["duration"] == 50_000_000 and info["platform"]["os"] == "mac"

    tracks = {t["name"]: t for t in info["tracks"]}
    scenes = tracks["장면"]["segments"]
    assert [s["target_timerange"]["start"] for s in scenes] == [0, 3e6, 11e6, 19e6, 27e6, 35e6, 45e6]
    music = tracks["음악"]["segments"][0]
    assert music["source_timerange"] == {"start": 2_000_000, "duration": 50_000_000}
    assert len(tracks["가사"]["segments"]) == 8

    # 샌드박스 대응: 모든 미디어가 초안 폴더 안에 있어야 한다
    for m in info["materials"]["videos"] + info["materials"]["audios"]:
        assert m["path"].startswith(str(res.path / "media"))

    meta = json.loads((res.path / "draft_meta_info.json").read_text(encoding="utf-8"))
    assert meta["draft_name"] == "ShortsMaker_t" and meta["draft_fold_path"] == str(res.path)
    registered = [v for g in meta["draft_materials"] if g["type"] == 0 for v in g["value"]]
    assert len(registered) == 8  # 장면 7 + 음악 1


def test_refuses_existing_name(tmp_path, sample):
    project, project_dir = sample
    (tmp_path / "ShortsMaker_dup").mkdir()
    with pytest.raises(CapCutExportError):
        export_draft(project, project_dir, root=tmp_path, name="ShortsMaker_dup")


def test_missing_root(tmp_path, sample):
    project, project_dir = sample
    with pytest.raises(CapCutExportError):
        export_draft(project, project_dir, root=tmp_path / "없음")


def test_srt():
    srt = build_srt([LyricLine(start=0.5, end=62.25, text="가사")])
    assert srt == "1\n00:00:00,500 --> 00:01:02,250\n가사\n"


def test_manual_kit(tmp_path, sample):
    project, project_dir = sample
    out = export_manual_kit(project, project_dir, tmp_path / "kit")
    assert len(list((out / "scenes").glob("*.png"))) == 7
    assert (out / "music.m4a").exists() and (out / "lyrics.srt").exists()
