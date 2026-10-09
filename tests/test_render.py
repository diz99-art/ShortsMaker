"""실제 렌더 테스트 (느림). 경계 조건: 최소 길이 30초, 음악·가사·이미지 없음."""
import json
import shutil

import pytest

from shortsmaker.models import ROOT, load_project
from shortsmaker.pipeline import render
from shortsmaker.validate import ValidationError, post_render, pre_render


def _write(tmp_path, **kw):
    data = dict(
        channel="info_top5", title="경계 조건 테스트", duration=30,
        items=[{"rank": r, "name": f"항목 {r}"} for r in (5, 4, 3, 2, 1)],
        rights_confirmed=True,
    )
    data.update(kw)
    d = tmp_path / "edge"
    d.mkdir(exist_ok=True)
    (d / "project.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return load_project(d / "project.json")


@pytest.mark.slow
def test_render_30s_without_music(tmp_path):
    project, project_dir = _write(tmp_path)
    out = tmp_path / "edge.mp4"
    res = render(project, project_dir, output=out)
    assert res.subtitle_mode == "none"
    assert any("음악이 없습니다" in w for w in res.warnings)
    rep = post_render(out, 30)
    assert rep.ok, rep.errors  # 무음이어도 오디오 스트림은 있어야 함


def test_pre_render_errors(tmp_path):
    project, project_dir = _write(
        tmp_path, music="없음.mp3",
        items=[{"rank": r, "name": "x", "image": "없음.png" if r == 1 else None} for r in (5, 4, 3, 2, 1)],
        lyrics=[{"start": 1, "end": 5, "text": "a"}, {"start": 4, "end": 6, "text": "b"}],
        rights_confirmed=False,
    )
    rep = pre_render(project, project_dir)
    joined = "\n".join(rep.errors)
    for needle in ("이미지 파일이 없습니다", "음악 파일이 없습니다", "겹칩니다", "권리 확인"):
        assert needle in joined
    with pytest.raises(ValidationError):
        render(project, project_dir, output=tmp_path / "x.mp4")


def test_music_start_beyond_length(tmp_path):
    src = ROOT / "samples" / "yagu_demo" / "music.mp3"
    project, project_dir = _write(tmp_path, music="music.mp3", music_start=100)
    shutil.copy2(src, project_dir / "music.mp3")
    assert any("음악 시작 위치" in e for e in pre_render(project, project_dir).errors)
