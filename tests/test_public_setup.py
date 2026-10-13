"""공개 저장소 준비: 임시 그림 대체, 내 컴퓨터 전용 템플릿 덮어쓰기, 올리면 안 되는 파일 검사."""
import json

from PIL import Image

from scripts import check_repo_files as crf
from shortsmaker import models
from shortsmaker.info_cards import is_placeholder


def test_asset_path_falls_back_to_placeholder(tmp_path, monkeypatch):
    tdir = tmp_path / "templates" / "demo"
    tdir.mkdir(parents=True)
    ph = tmp_path / "placeholders"
    ph.mkdir()
    Image.new("RGBA", (4, 4)).save(ph / "logo.png")
    monkeypatch.setattr(models, "PLACEHOLDER_DIR", ph)
    t = models.load_template("yagu_v2")
    t._template_dir = tdir
    assert t.asset_path("logo.png") == ph / "logo.png"          # 원본 없음 → 임시 그림
    Image.new("RGBA", (4, 4)).save(tdir / "logo.png")
    assert t.asset_path("logo.png") == tdir / "logo.png"        # 원본이 있으면 원본
    assert t.asset_path("nothing.png") is None


def test_committed_placeholders_are_marked():
    files = sorted(models.PLACEHOLDER_DIR.glob("*.png"))
    assert files and all(is_placeholder(p) for p in files)      # 임시 표시 (기본 캐릭터 카드는 수익화용 렌더 차단)


def test_local_template_override(tmp_path, monkeypatch):
    src = models.TEMPLATES_DIR / "sky_badge" / "template.json"
    tdir = tmp_path / "sky_badge"
    tdir.mkdir()
    (tdir / "template.json").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(models, "TEMPLATES_DIR", tmp_path)
    base = models.load_template("sky_badge")
    (tdir / "template.local.json").write_text(json.dumps(
        {"name": "내 채널", "card": {"footer": {"handle_text": "@my_channel"}}}), encoding="utf-8")
    t = models.load_template("sky_badge")
    assert t.name == "내 채널" and t.card.footer.handle_text == "@my_channel"
    assert t.card.title == base.card.title                      # 나머지는 그대로 (깊은 병합)
    assert models.load_template("sky_badge", local=False).name == base.name


def test_repo_file_check(tmp_path, monkeypatch):
    monkeypatch.setattr(crf, "ROOT", tmp_path)
    for rel, data in {"assets/fonts/a.ttf": b"x", "examples/demo/a.png": b"x", "assets/placeholders/l.png": b"x",
                      "big.bin": b"0" * (crf.MAX_BYTES + 1), ".env": b"K=1", ".env.example": b"K=",
                      "notes.md": "비밀 채널 이름".encode()}.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    found = crf.problems(["assets/fonts/a.ttf", "examples/demo/a.png", "assets/placeholders/l.png",
                          "big.bin", ".env", ".env.example", "notes.md"], ["채널 이름"])
    joined = "\n".join(found)
    assert "assets/fonts/a.ttf" in joined and "big.bin" in joined and ".env:" in joined and "notes.md" in joined
    assert "examples/demo/a.png" not in joined and "assets/placeholders/l.png:" not in joined and ".env.example:" not in joined
