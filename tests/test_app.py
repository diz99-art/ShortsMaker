"""Streamlit 화면 흐름 테스트 (샘플 불러오기 → 미리보기 → MP4 제작).

실제 렌더가 들어가 20초 정도 걸린다. 건너뛰려면: pytest -m "not slow"
"""
import json
import shutil

import pytest
from streamlit.testing.v1 import AppTest

from shortsmaker.models import OUTPUT_DIR, PROJECTS_DIR, ROOT

SLUG = "_apptest_demo"


@pytest.fixture
def cleanup():
    yield
    shutil.rmtree(PROJECTS_DIR / SLUG, ignore_errors=True)
    (OUTPUT_DIR / f"{SLUG}.mp4").unlink(missing_ok=True)


def _button(at, label):
    return next(b for b in at.button if b.label == label)


@pytest.mark.slow
def test_load_preview_render(cleanup, tmp_path):
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180).run()
    assert not at.exception

    sel = at.sidebar.selectbox[0]
    idx = next(i for i, o in enumerate(sel.options) if o.endswith("yagu_demo"))
    sel.select_index(idx).run()
    _button(at, "불러오기").click().run()
    assert at.text_input(key="title").value.startswith("KBO 역대 FA")
    assert at.text_input(key="name_1").value == "테스트 선수 가"
    assert _button(at, "🎬 MP4로 완성").disabled is False  # 샘플은 권리 확인됨

    # 권리 확인 해제 → 제작 버튼 비활성
    at.checkbox(key="rights").uncheck().run()
    assert _button(at, "🎬 MP4로 완성").disabled
    at.checkbox(key="rights").check().run()

    at.text_input(key="slug").set_value(SLUG).run()
    _button(at, "🖼️ 장면 미리보기").click().run()
    assert not at.error, [e.value for e in at.error]
    assert len(at.image) >= 7 + 5  # 장면 7 + 항목 이미지 5

    saved = json.loads((PROJECTS_DIR / SLUG / "project.json").read_text(encoding="utf-8"))
    assert saved["music"] == "music.mp3" and len(saved["lyrics"]) == 8
    assert (PROJECTS_DIR / SLUG / "img1.png").exists()

    _button(at, "🎬 MP4로 완성").click().run()
    assert not at.error, [e.value for e in at.error]
    assert any("완성!" in s.value for s in at.success)
    assert (OUTPUT_DIR / f"{SLUG}.mp4").exists()

    # CapCut 초안 (임시 폴더로 — 실제 CapCut 폴더는 건드리지 않음)
    at.text_input(key="capcut_dir").set_value(str(tmp_path)).run()
    _button(at, "✂️ CapCut 초안으로 보내기").click().run()
    assert not at.error, [e.value for e in at.error]
    drafts = [d for d in tmp_path.iterdir() if d.name.startswith("ShortsMaker_")]
    assert len(drafts) == 1 and (drafts[0] / "draft_info.json").exists()
