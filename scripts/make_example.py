"""examples/demo 의 그림 5장과 비프음을 만든다 (직접 그린 단색 도형·합성음이라 권리 문제가 없다).

실행: .venv/bin/python scripts/make_example.py
"""
from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shortsmaker.ffmpeg_builder import ffmpeg_bin  # noqa: E402

DEMO = ROOT / "examples" / "demo"
SIZE = (600, 800)  # 하늘 배지형 캐릭터 자리처럼 세로로 긴 투명 그림
DURATION = 30
BPM = 120


def _shape(kind: str, color: str) -> Image.Image:
    img = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy, r = SIZE[0] // 2, SIZE[1] // 2 + 60, 250
    if kind == "circle":
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
    elif kind == "triangle":
        d.polygon([(cx, cy - r), (cx + r, cy + r), (cx - r, cy + r)], fill=color)
    elif kind == "square":
        d.rounded_rectangle((cx - r, cy - r, cx + r, cy + r), radius=40, fill=color)
    elif kind == "diamond":
        d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=color)
    else:  # star
        pts = [(cx + (r if i % 2 == 0 else r * 0.45) * math.sin(i * math.pi / 5),
                cy - (r if i % 2 == 0 else r * 0.45) * math.cos(i * math.pi / 5)) for i in range(10)]
        d.polygon(pts, fill=color)
    return img


# 5위 → 1위 (form.txt 의 순서와 같다)
SHAPES = [("circle", "#F5D328"), ("triangle", "#8E5BD0"), ("square", "#5DBB4A"),
          ("diamond", "#F28C28"), ("star", "#E5484D")]


def make_images() -> list[Path]:
    out = []
    for i, (kind, color) in enumerate(SHAPES, 1):
        p = DEMO / f"item{i}.png"
        _shape(kind, color).save(p, optimize=True)
        out.append(p)
    return out


def make_beep() -> Path:
    """박자마다 짧은 비프음 (마디 첫 박은 높게). FFmpeg 로 바로 mp3 를 만든다."""
    p = DEMO / "beep.mp3"
    beat = 60 / BPM
    expr = (f"0.35*sin(2*PI*if(lt(mod(t,{4 * beat}),{beat}),880,660)*t)"
            f"*lt(mod(t,{beat}),0.08)")
    subprocess.run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"aevalsrc='{expr}':s=44100:d={DURATION}",
                    "-ac", "1", "-b:a", "64k", str(p)], check=True)
    return p


if __name__ == "__main__":
    DEMO.mkdir(parents=True, exist_ok=True)
    made = make_images() + [make_beep()]
    print("예시 자료:", ", ".join(p.name for p in made))
