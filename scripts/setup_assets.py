"""git 에 넣지 않는 실행 재료를 준비한다 (이미 있으면 건너뛴다).

- 폰트: G마켓 산스·Pretendard·Jua (모두 SIL OFL 1.1) 를 공식 배포처에서 내려받는다
- 효과음: 휙·쿵·쾅은 numpy 로 합성, 1위 함성은 credits.json 의 원본(퍼블릭 도메인·CC0)을 받아 SHA-256 확인 후 자른다
- 샘플: samples/ 의 임시 그림·합성 음악 (scripts/make_samples.py)

실행: .venv/bin/python scripts/setup_assets.py   (또는 make setup)
"""
from __future__ import annotations

import hashlib
import io
import json
import ssl
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shortsmaker import sfx  # noqa: E402

FONTS = ROOT / "assets" / "fonts"
PRETENDARD_ZIP = "https://github.com/orioncactus/pretendard/releases/download/v1.3.9/Pretendard-1.3.9.zip"
GMARKET_ZIP = "https://corp.gmarket.com/fonts/GmarketSansTTF.zip"
JUA_TTF = "https://github.com/google/fonts/raw/main/ofl/jua/Jua-Regular.ttf"
PRETENDARD_WEIGHTS = ("Regular", "SemiBold", "Bold", "ExtraBold", "Black")
GMARKET_FILES = ("GmarketSansTTFMedium.ttf", "GmarketSansTTFBold.ttf")
CHEER_START = {"cheer_a.wav": 0.3, "cheer_b.wav": 0.2}  # credits.json 의 edit 기록과 같은 값


def _ssl_context() -> ssl.SSLContext:
    """python.org 판 macOS 파이썬은 인증서 묶음이 없을 수 있어 certifi(requests 와 같이 설치됨)를 쓴다."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ShortsMaker-setup"})
    with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as r:
        return r.read()


def _extract(zip_bytes: bytes, wanted: dict[str, Path], folder: str = "") -> None:
    """zip 안 folder 아래에서 이름이 맞는 파일만 꺼낸다."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for info in z.infolist():
            folder_name, _, name = info.filename.rpartition("/")
            if name in wanted and folder_name == folder:
                wanted[name].write_bytes(z.read(info))


def setup_fonts() -> list[str]:
    FONTS.mkdir(parents=True, exist_ok=True)
    done = []
    pretendard = {f"Pretendard-{w}.otf": FONTS / f"Pretendard-{w}.otf" for w in PRETENDARD_WEIGHTS}
    missing = {k: v for k, v in pretendard.items() if not v.exists()}
    if missing:
        _extract(_get(PRETENDARD_ZIP), missing, "public/static")
        done += list(missing)
    gmarket = {n: FONTS / n for n in GMARKET_FILES if not (FONTS / n).exists()}
    if gmarket:
        _extract(_get(GMARKET_ZIP), gmarket)
        done += list(gmarket)
    jua = FONTS / "Jua-Regular.ttf"
    if not jua.exists():
        jua.write_bytes(_get(JUA_TTF))
        done.append(jua.name)
    return done


def setup_sfx() -> list[str]:
    done = []
    if not all(sfx.sfx_file(k, v).exists() for k in ("whoosh", "thud", "slam") for v in sfx.SETS):
        done += [p.name for p in sfx.build_synth()]
    credits = sfx.load_credits()
    for name, start in CHEER_START.items():
        dst = sfx.SFX_DIR / name
        if dst.exists():
            continue
        c = credits[name]
        data = _get(c["original"])
        if hashlib.sha256(data).hexdigest() != c["original_sha256"]:
            raise SystemExit(f"원본 파일이 기록과 다릅니다 (SHA-256): {c['original']}")
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / Path(c["original"]).name
            src.write_bytes(data)
            sfx.trim_cheer(src, dst, start=start)
        done.append(name)
    return done


def setup_samples() -> list[str]:
    if (ROOT / "samples" / "yagu_demo" / "music.mp3").exists() and (ROOT / "samples" / "info_demo" / "music.mp3").exists():
        return []
    subprocess.run([sys.executable, str(ROOT / "scripts" / "make_samples.py")], check=True)
    return ["samples/yagu_demo", "samples/info_demo"]


def main() -> None:
    for label, step in (("폰트", setup_fonts), ("효과음", setup_sfx), ("샘플", setup_samples)):
        made = step()
        print(f"{label}: {'준비함 ' + ', '.join(made) if made else '이미 있음'}")
    print(json.dumps({"ok": True}))


if __name__ == "__main__":
    main()
