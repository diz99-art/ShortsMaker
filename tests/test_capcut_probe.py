import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("probe", Path(__file__).parents[1] / "scripts/capcut_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_synthetic_draft_and_unicode(tmp_path):
    draft = probe.generate(tmp_path / "한글 프로젝트")
    data = json.loads((draft / "draft_content.json").read_text())
    assert data["duration"] == 3_000_000
    assert data["fps"] == 30
    assert data["canvas_config"]["width"] == 1080
    assert data["canvas_config"]["height"] == 1920
    assert [t["type"] for t in data["tracks"]] == ["video", "audio", "text"]
    assert "한글 자막 검증" in data["materials"]["texts"][0]["content"]
    for kind in ("videos", "audios"):
        assert Path(data["materials"][kind][0]["path"]).is_file()
    text = data["tracks"][2]["segments"][0]["target_timerange"]
    assert text == {"start": 500_000, "duration": 2_000_000}


def test_refuses_existing_destination(tmp_path):
    marker = tmp_path / "original.txt"
    marker.write_text("원본")
    with pytest.raises(FileExistsError):
        probe.generate(tmp_path)
    assert marker.read_text() == "원본"


def test_experimental_adapter(tmp_path):
    adapter_spec = importlib.util.spec_from_file_location("adapter", Path(__file__).parents[1] / "scripts/adapt_capcut_probe.py")
    adapter = importlib.util.module_from_spec(adapter_spec)
    adapter_spec.loader.exec_module(adapter)
    source = probe.generate(tmp_path / "source")
    original = (source / "draft_content.json").read_bytes()
    destination = tmp_path / "adapted"
    adapter.adapt(source, destination, tmp_path / "installed")
    data = json.loads((destination / "draft_info.json").read_text())
    assert data["platform"]["os"] == "mac"
    assert data["new_version"] == "132.0.0"
    meta = json.loads((destination / "draft_meta_info.json").read_text())
    entries = next(g["value"] for g in meta["draft_materials"] if g["type"] == 0)
    assert {m["metetype"] for m in entries} == {"photo", "music"}
    assert all(Path(m["file_Path"]).is_file() for m in entries)
    assert (source / "draft_content.json").read_bytes() == original
    with pytest.raises(FileExistsError):
        adapter.adapt(source, destination, tmp_path / "installed")
