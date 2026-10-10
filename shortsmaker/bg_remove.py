"""캐리커처·스티커 이미지 배경 제거 (추가 설치·비용 없음).

1. 이미 투명 배경이면 그대로 쓴다.
2. 아니면 테두리에서 이어진 단색(흰색 등) 배경만 지운다 — 인물 안쪽의 흰 부분(유니폼 등)은
   테두리와 이어져 있지 않으면 남는다. 가장자리는 1px 부드럽게.
3. rembg 가 설치되어 있으면(pip install rembg) method="rembg" 로 고를 수 있다.
"""
from __future__ import annotations

import importlib.util

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

MARK = (255, 0, 255)


def has_transparency(img: Image.Image) -> bool:
    if img.mode not in ("RGBA", "LA") and "transparency" not in img.info:
        return False
    lo, hi = img.convert("RGBA").getchannel("A").getextrema()
    return lo < 250


def rembg_available() -> bool:
    return importlib.util.find_spec("rembg") is not None


def remove_background(img: Image.Image, *, method: str = "auto", tolerance: int = 32) -> tuple[Image.Image, str]:
    """(투명 배경 RGBA, 쓴 방법). method: auto | keep | flood | rembg"""
    img = ImageOps.exif_transpose(img)
    if method in ("auto", "keep") and has_transparency(img):
        return img.convert("RGBA"), "keep"
    if method == "rembg":
        if not rembg_available():
            raise RuntimeError("rembg 가 설치되어 있지 않습니다 (.venv/bin/pip install rembg)")
        from rembg import remove
        return remove(img.convert("RGBA")), "rembg"
    return _flood(img.convert("RGB"), tolerance), "flood"


def _flood(rgb: Image.Image, tolerance: int) -> Image.Image:
    w, h = rgb.size
    marked = rgb.copy()
    # 테두리를 따라 여러 점에서 배경 채우기 (모서리마다 배경이 조금씩 달라도)
    step = max(1, min(w, h) // 40)
    seeds = [(x, 0) for x in range(0, w, step)] + [(x, h - 1) for x in range(0, w, step)] + \
            [(0, y) for y in range(0, h, step)] + [(w - 1, y) for y in range(0, h, step)]
    border = sorted(rgb.getpixel(xy) for xy in seeds)
    bg = tuple(sorted(c[k] for c in border)[len(border) // 2] for k in range(3))  # 테두리 대표색 (중앙값)
    for xy in seeds:  # 배경색과 비슷한 테두리 점에서만 시작 (테두리에 닿은 인물은 지우지 않음)
        px = marked.getpixel(xy)
        if px != MARK and max(abs(px[k] - bg[k]) for k in range(3)) <= tolerance:
            ImageDraw.floodfill(marked, xy, MARK, thresh=tolerance)
    diff = ImageChops.difference(marked, Image.new("RGB", (w, h), MARK)).convert("L")
    alpha = diff.point(lambda v: 255 if v > 0 else 0)
    alpha = alpha.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.8))
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out.crop(out.getbbox() or (0, 0, w, h))
