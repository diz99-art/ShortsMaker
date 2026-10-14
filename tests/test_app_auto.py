"""'노래로 자동 제작' 화면이 모든 템플릿의 프로젝트를 열 수 있는지 (정보 채널 프로젝트 열기 오류 재현).

프로젝트 폴더는 SHORTSMAKER_PROJECTS_DIR 로 tmp 에 만든다 (실제 projects/ 를 건드리지 않음).
"""
import pytest
from streamlit.testing.v1 import AppTest

from shortsmaker import auto_pipeline as ap
from shortsmaker.models import ROOT
from tests.test_auto_pipeline import LOSING, fake_aligner, make_song

PAGE = str(ROOT / "pages" / "1_노래로_자동_제작.py")
ALL_TEMPLATES = [*ap.TEMPLATES, *ap.INFO_TEMPLATES]


def _open(root, slug):
    at = AppTest.from_file(PAGE, default_timeout=120).run()
    at.selectbox(key="pick").set_value(slug).run()
    at.button(key="load").click().run()
    return at


@pytest.mark.parametrize("template", ALL_TEMPLATES)
def test_open_project_of_every_template(template, tmp_path, monkeypatch):
    root = tmp_path / "projects"
    monkeypatch.setenv("SHORTSMAKER_PROJECTS_DIR", str(root))
    song = make_song(tmp_path / "song.m4a", LOSING)
    state, d = ap.start_from_song(song, "p", title="제목", mode="mock", template=template, root=root)
    ap.align(state, d, aligner=fake_aligner)
    at = _open(root, "p")
    assert not at.exception, [e.value for e in at.exception]
    assert any("프로젝트 **p**" in i.value for i in at.info)


def test_template_labels_cover_every_flow_template():
    for t in ALL_TEMPLATES:
        assert (ROOT / "templates" / t / "template.json").exists()
        assert ap.template_label(t) != t                                   # 화면용 이름이 있음
    assert ap.template_label("새_템플릿") == "새_템플릿"                     # 모르는 템플릿은 이름 그대로
