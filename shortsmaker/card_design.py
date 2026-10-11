"""분위기별 카드 디자인 — 매번 같은 '빨간 붓 터치 + 지그재그' 대신 주제 분위기에 맞는 배경·글자 효과.

분위기(mood) 6가지 × 디자인 3가지. 전부 Pillow 로 직접 그린다 (외부 그림·폰트 없음, Pretendard 만).
색은 선수별 팀 색(team_colors), 1위는 더 화려하게(top=True). 카드 크기·배치는 card_compose 와 같다
(941×1672, 아래에서부터 이름 리본 → 팀 띠 → 연도 → 기록 → 순위, 캐리커처는 위에 크게).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from .card_compose import FONT_BLACK, FONT_EXTRA, H, W, _paste_center, brush_band, styled_text
from .rank_extract import team_colors
from .scenes import _font, _hex

MOODS = {"record": "기록·최고", "humiliation": "굴욕·불운", "money": "돈", "rivalry": "대결",
         "retro": "옛날 기록", "live": "현재·신기록"}

RGBA = tuple[int, int, int, int]


def _rgba(c: str, a: int = 255) -> RGBA:
    return _hex(c) + (a,)


def _mix(a: str, b: str, f: float) -> str:
    x, y = _hex(a), _hex(b)
    return "#%02X%02X%02X" % tuple(round(x[i] + (y[i] - x[i]) * f) for i in range(3))


def _lum(c: str) -> float:
    r, g, b = _hex(c)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


# ---------------------------------------------------------------- 글자 효과

def glow(img: Image.Image, color: str, radius: int = 18, strength: float = 1.6) -> Image.Image:
    """글자 뒤로 번지는 빛."""
    pad = radius * 3
    out = Image.new("RGBA", (img.width + 2 * pad, img.height + 2 * pad), (0, 0, 0, 0))
    a = Image.new("L", out.size, 0)
    a.paste(img.getchannel("A"), (pad, pad))
    a = a.filter(ImageFilter.GaussianBlur(radius)).point(lambda v: min(255, int(v * strength)))
    g = Image.new("RGBA", out.size, _rgba(color))
    g.putalpha(a)
    out.alpha_composite(g)
    out.alpha_composite(img, (pad, pad))
    box = out.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox()  # 거의 투명한 번짐 끝은 잘라 크기 유지
    return out.crop(box) if box else out


def _glow_canvas(img: Image.Image, color: str, radius: int, strength: float) -> Image.Image:
    """화면 전체 크기 레이어에 빛 번짐 (크기·위치 그대로)."""
    a = img.getchannel("A").filter(ImageFilter.GaussianBlur(radius)).point(lambda v: min(255, int(v * strength)))
    out = Image.new("RGBA", img.size, _rgba(color))
    out.putalpha(a)
    out.alpha_composite(img)
    return out


def led(img: Image.Image, pitch: int = 9) -> Image.Image:
    """전광판 점 무늬: 글자를 작은 원 점으로."""
    w, h = img.size
    dots = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(dots)
    r = pitch * 0.42
    for y in range(0, h, pitch):
        for x in range(0, w, pitch):
            d.ellipse([x + pitch / 2 - r, y + pitch / 2 - r, x + pitch / 2 + r, y + pitch / 2 + r], fill=255)
    out = img.copy()
    out.putalpha(ImageChops.multiply(img.getchannel("A"), dots))
    return out


def neon_text(text: str, size: int, color: str, *, core: str = "#FFFFFF") -> Image.Image:
    """네온관 글자: 색 테두리 + 흰 심 + 넓은 번짐."""
    font = _font(str(FONT_BLACK), size)
    bbox = font.getbbox(text)
    pad = 30
    w, h = bbox[2] - bbox[0] + 2 * pad, bbox[3] - bbox[1] + 2 * pad
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    org = (pad - bbox[0], pad - bbox[1])
    d.text(org, text, font=font, fill=_rgba(color), stroke_width=max(4, size // 22), stroke_fill=_rgba(color))
    d.text(org, text, font=font, fill=_rgba(core))
    return glow(glow(img, color, radius=max(8, size // 10), strength=2.2), color, radius=max(20, size // 4),
                strength=1.2)


def ink(img: Image.Image, seed: int, amount: float = 0.18) -> Image.Image:
    """인쇄 잉크처럼 거칠게 (점 무늬로 군데군데 빠짐)."""
    rnd = np.random.default_rng(seed)
    a = np.asarray(img.getchannel("A"), dtype=np.float32)
    noise = rnd.random(a.shape) > amount
    blur = Image.fromarray((noise * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.1))
    out = img.copy()
    out.putalpha(ImageChops.multiply(img.getchannel("A"), blur.point(lambda v: 255 if v > 110 else 0)))
    return out


def cracked(img: Image.Image, seed: int, color: str = "#1A1D22", n: int = 7) -> Image.Image:
    """금 간 글자: 가는 갈라짐 선."""
    out = img.copy()
    d = ImageDraw.Draw(out)
    rnd = random.Random(seed)
    w, h = img.size
    for _ in range(n):
        x, y = rnd.uniform(0, w), rnd.uniform(0, h)
        for _ in range(rnd.randint(3, 6)):
            nx, ny = x + rnd.uniform(-60, 60), y + rnd.uniform(-45, 45)
            d.line([x, y, nx, ny], fill=_rgba(color), width=rnd.choice((3, 4, 5)))
            x, y = nx, ny
    out.putalpha(ImageChops.multiply(out.getchannel("A"), img.getchannel("A")))
    return out


def stamp(text: str, size: int, color: str, *, angle: float = -8, seed: int = 3) -> Image.Image:
    """도장 찍은 글자 (테두리 상자 + 거친 잉크)."""
    t = styled_text(text, size, color, stroke=color, stroke_w=2, skew=0, shadow=0)
    box = Image.new("RGBA", (t.width + 60, t.height + 40), (0, 0, 0, 0))
    ImageDraw.Draw(box).rounded_rectangle([4, 4, box.width - 5, box.height - 5], radius=18, outline=_rgba(color),
                                          width=9)
    box.alpha_composite(t, (30, 20))
    return ink(box, seed, 0.25).rotate(angle, expand=True, resample=Image.BICUBIC)


# ---------------------------------------------------------------- 배경 요소

def rays(size: tuple[int, int], center: tuple[float, float], colors: tuple[str, str], n: int = 24,
         spread: float = 0.5, alpha: int = 255, rot: float = 0.0) -> Image.Image:
    w, h = size
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(out)
    R = math.hypot(w, h)
    cx, cy = center
    for k in range(n):
        a0 = rot + 2 * math.pi * k / n
        a1 = a0 + 2 * math.pi / n * spread
        d.polygon([(cx, cy), (cx + R * math.cos(a0), cy + R * math.sin(a0)),
                   (cx + R * math.cos(a1), cy + R * math.sin(a1))], fill=_rgba(colors[k % 2], alpha))
    return out


def radial(size: tuple[int, int], center: tuple[float, float], inner: str, outer: str, radius: float,
           alpha_in: int = 255, alpha_out: int = 255) -> Image.Image:
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.clip(np.hypot(xx - center[0], yy - center[1]) / radius, 0, 1)[..., None]
    a, b = np.array(_hex(inner), dtype=np.float32), np.array(_hex(outer), dtype=np.float32)
    rgb = a + (b - a) * r
    al = alpha_in + (alpha_out - alpha_in) * r
    return Image.fromarray(np.concatenate([rgb, al], axis=2).astype(np.uint8), "RGBA")


def vgrad(size: tuple[int, int], top: str, bottom: str, a_top: int = 255, a_bot: int = 255) -> Image.Image:
    w, h = size
    t = np.linspace(0, 1, h)[:, None, None]
    a, b = np.array(_hex(top), dtype=np.float32), np.array(_hex(bottom), dtype=np.float32)
    rgb = np.broadcast_to(a + (b - a) * t, (h, w, 3))
    al = np.broadcast_to(a_top + (a_bot - a_top) * t, (h, w, 1))
    return Image.fromarray(np.concatenate([rgb, al], axis=2).astype(np.uint8), "RGBA")


def panel_mask(size: tuple[int, int], kind: str, seed: int = 1) -> Image.Image:
    """카드 판 모양: rounded / torn(찢긴 종이) / shard(유리 조각) / tilt(기울어진 판)."""
    w, h = size
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    rnd = random.Random(seed)
    if kind == "rounded":
        d.rounded_rectangle([0, 0, w - 1, h - 1], radius=60, fill=255)
    elif kind == "torn":
        pts = []
        for i in range(41):
            pts.append((w * i / 40, rnd.uniform(0, 26)))
        for i in range(41):
            pts.append((w - rnd.uniform(0, 22), h * i / 40))
        for i in range(41):
            pts.append((w - w * i / 40, h - rnd.uniform(0, 26)))
        for i in range(41):
            pts.append((rnd.uniform(0, 22), h - h * i / 40))
        d.polygon(pts, fill=255)
    elif kind == "shard":
        d.polygon([(30, 70), (w * 0.55, 0), (w - 10, 40), (w - 40, h * 0.6), (w, h - 30), (w * 0.4, h),
                   (10, h - 60), (40, h * 0.45)], fill=255)
    elif kind == "tilt":
        d.polygon([(40, 60), (w - 10, 0), (w - 40, h - 40), (0, h)], fill=255)
    return m


def masked(img: Image.Image, mask: Image.Image) -> Image.Image:
    out = img.copy()
    out.putalpha(ImageChops.multiply(img.getchannel("A"), mask))
    return out


def sparkles(size: tuple[int, int], color: str, n: int, seed: int, area: tuple[float, float, float, float]
             ) -> Image.Image:
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(out)
    rnd = random.Random(seed)
    x0, y0, x1, y1 = area
    for _ in range(n):
        x, y, r = rnd.uniform(x0, x1), rnd.uniform(y0, y1), rnd.uniform(10, 32)
        d.polygon([(x, y - r), (x + r * 0.22, y - r * 0.22), (x + r, y), (x + r * 0.22, y + r * 0.22), (x, y + r),
                   (x - r * 0.22, y + r * 0.22), (x - r, y), (x - r * 0.22, y - r * 0.22)], fill=_rgba(color))
    return _glow_canvas(out, color, 8, 1.2)


def bolt(d: ImageDraw.ImageDraw, x: float, y: float, length: float, rnd: random.Random, color: RGBA,
         width: int = 10) -> None:
    pts = [(x, y)]
    for _ in range(7):
        x += rnd.uniform(-70, 70)
        y += length / 7
        pts.append((x, y))
    d.line(pts, fill=color, width=width, joint="curve")


def crown(size: int, color: str) -> Image.Image:
    img = Image.new("RGBA", (size, int(size * 0.75)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w, h = img.size
    d.polygon([(0.05 * w, 0.95 * h), (0.05 * w, 0.3 * h), (0.3 * w, 0.6 * h), (0.5 * w, 0.05 * h), (0.7 * w, 0.6 * h),
               (0.95 * w, 0.3 * h), (0.95 * w, 0.95 * h)], fill=_rgba(color), outline=(60, 30, 0, 255), width=8)
    for cx in (0.05, 0.5, 0.95):
        d.ellipse([cx * w - 16, (0.3 if cx != 0.5 else 0.05) * h - 16, cx * w + 16,
                   (0.3 if cx != 0.5 else 0.05) * h + 16], fill=(255, 255, 255, 255))
    return glow(img, color, 14, 1.4)


# ---------------------------------------------------------------- 디자인 정의

@dataclass
class Ctx:
    """디자인 함수가 받는 값."""
    primary: str
    secondary: str
    rank: int
    top: bool
    seed: int
    y_rank: int = 0       # 순위 글자 위쪽 (배치가 끝난 뒤 채움)
    y_banner: int = 0
    art_box: tuple[int, int, int, int] = (0, 0, W, H)
    off: bool = False     # 보너스 반전: 조명 하나 꺼짐 등 (디자인별)


@dataclass
class Design:
    key: str
    mood: str
    label: str
    back: Callable[[Ctx], Image.Image]                       # 캐리커처 뒤
    rank_text: Callable[[str, int, Ctx], Image.Image]          # 순위 글자
    record_text: Callable[[str, int, Ctx], Image.Image]        # 기록 글자
    front: Callable[[Ctx], Image.Image | None] = lambda c: None   # 캐리커처 앞 (비, 유리 금, 반짝이)
    banner_fill: Callable[[Ctx], tuple[str, str, str]] = lambda c: (c.primary, c.secondary, "#FFFFFF")  # 띠, 테두리, 글자
    ribbon: str = "#111111"
    year_color: str = "#FFFFFF"
    tags: tuple[str, ...] = field(default_factory=tuple)


def _player_text(player: str) -> Image.Image:
    """카드 아래 선수 이름 (예전의 2배 크기)."""
    from .card_compose import _fit_text
    return _fit_text(player, 116, W - 120, fill="#FFFFFF", stroke="#111111", stroke_w=8, skew=0.06, shadow=0)


def _plain(text: str, size: int, fill, **kw) -> Image.Image:
    base = dict(stroke="#111111", stroke_w=11, skew=0.14, shadow=8)
    base.update(kw)
    return styled_text(text, size, fill, **base)


GOLD = ("#FFF7B0", "#FFD23F", "#C88A00")
SILVER = ("#FFFFFF", "#E9EDF2", "#9AA3AE")


# --- 기록·최고 ----------------------------------------------------------------

def _gold_rays_back(c: Ctx) -> Image.Image:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cy = c.y_rank - 120
    img.alpha_composite(radial((W, H), (W / 2, cy), "#FFF3B0", "#FFFFFF", 900, 255, 0))
    img.alpha_composite(rays((W, H), (W / 2, cy), ("#FFD23F", "#FFF0A0"), n=32 if c.top else 24, spread=0.5,
                             alpha=170 if c.top else 120, rot=0.05 * c.rank))
    if c.rank <= 3:  # 테두리 원은 1~3위만 (하위 순위는 덜 복잡하게)
        ring = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(ring).ellipse([W / 2 - 400, cy - 400, W / 2 + 400, cy + 400], outline=_rgba(c.primary, 200),
                                     width=26)
        img.alpha_composite(ring)
    img.alpha_composite(brush_band((W, 420), c.primary, seed=c.seed, angle=-6, thickness=0.6), (0, c.y_rank + 60))
    return masked(img, panel_mask((W, H), "rounded"))


def _gold_rank(text: str, size: int, c: Ctx) -> Image.Image:
    t = _plain(text, size, GOLD, stroke="#5A2E00", outer="#FFFFFF", outer_w=7)
    return glow(t, "#FFD23F", 26, 1.8) if c.top else t


def _sparkle_front(c: Ctx) -> Image.Image | None:
    return sparkles((W, H), "#FFF6C8", 14 if c.top else 6, c.seed, (40, 60, W - 40, c.y_banner - 100))


def _stadium_back(c: Ctx) -> Image.Image:
    img = vgrad((W, H), "#06122B", _mix(c.primary, "#06122B", 0.55))
    d = ImageDraw.Draw(img)
    for sx in (60, W - 60):  # 위 양쪽 조명탑에서 내려오는 빛줄기
        dark = c.off and sx == 60  # 보너스 반전: 왼쪽 조명탑이 꺼짐
        if not dark:
            beam = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            bd = ImageDraw.Draw(beam)
            tx = W / 2 + (sx - W / 2) * -0.2
            bd.polygon([(sx - 30, 40), (sx + 30, 40), (tx + 260, c.y_rank), (tx - 260, c.y_rank)],
                       fill=(255, 250, 220, 30 if c.off else (70 if c.top else 50)))
            img.alpha_composite(beam.filter(ImageFilter.GaussianBlur(18)))
        for k in range(4):  # 조명 알
            for j in range(3):
                x, y = sx - 45 + j * 45, 40 + k * 38
                d.ellipse([x - 15, y - 15, x + 15, y + 15], fill=(60, 64, 72, 255) if dark else (255, 255, 240, 255))
    # 관중석 실루엣 + 잔디
    crowd = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(crowd)
    rnd = random.Random(c.seed)
    for x in range(0, W, 22):
        cd.ellipse([x, c.y_rank - 40 + rnd.uniform(-10, 10), x + 26, c.y_rank - 14], fill=(4, 8, 20, 230))
    cd.rectangle([0, c.y_rank - 26, W, H], fill=(4, 8, 20, 230))
    img.alpha_composite(crowd)
    img.alpha_composite(vgrad((W, H - c.y_rank + 40), "#1E7A3A", "#0B3D1E", 255, 255), (0, c.y_rank + 40))
    return masked(img, panel_mask((W, H), "rounded"))


def _flare_front(c: Ctx) -> Image.Image | None:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for sx in ((W - 60,) if c.off else (60, W - 60)):
        img.alpha_composite(radial((W, H), (sx, 100), "#FFFFFF", "#FFFFFF", 160 if c.top else 110, 200, 0))
    if c.top:
        img.alpha_composite(sparkles((W, H), "#FFFFFF", 10, c.seed + 4, (60, 200, W - 60, c.y_banner - 200)))
    return img


def _medal_back(c: Ctx) -> Image.Image:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 리본 두 줄 + 큰 메달 원판
    d.polygon([(W / 2 - 260, 0), (W / 2 - 60, 0), (W / 2 + 40, 520), (W / 2 - 140, 520)], fill=_rgba(c.primary))
    d.polygon([(W / 2 + 260, 0), (W / 2 + 60, 0), (W / 2 - 40, 520), (W / 2 + 140, 520)], fill=_rgba(c.secondary))
    if c.rank > 3:  # 4~5위는 리본만 (메달은 1~3위: 금·은·동)
        return img
    medal_c = {1: GOLD, 2: SILVER, 3: ("#FFE0C0", "#E0904A", "#8A4A18")}[c.rank]
    disk = radial((900, 900), (450, 380), medal_c[0], medal_c[2], 520)
    m = Image.new("L", (900, 900), 0)
    ImageDraw.Draw(m).ellipse([20, 20, 880, 880], fill=255)
    disk.putalpha(m)
    rim = Image.new("RGBA", (900, 900), (0, 0, 0, 0))
    ImageDraw.Draw(rim).ellipse([20, 20, 880, 880], outline=_rgba(medal_c[2]), width=34)
    ImageDraw.Draw(rim).ellipse([90, 90, 810, 810], outline=_rgba(medal_c[0], 200), width=10)
    disk.alpha_composite(rim)
    img.alpha_composite(disk, (W // 2 - 450, 330))
    return img


def _medal_rank(text: str, size: int, c: Ctx) -> Image.Image:
    t = _plain(text, size, ("#FFFFFF", "#FFF2C0", "#E7B100"), stroke="#3B1E00", outer=c.primary, outer_w=8)
    return glow(t, "#FFE27A", 22, 1.6) if c.top else t


# --- 굴욕·불운 ----------------------------------------------------------------

def _rain_back(c: Ctx) -> Image.Image:
    img = vgrad((W, H), "#2B3138", "#0E1114")
    img.alpha_composite(radial((W, H), (W / 2, 500), _mix(c.primary, "#2B3138", 0.75), "#0E1114", 800, 160, 0))
    d = ImageDraw.Draw(img)
    rnd = random.Random(c.seed)
    for _ in range(18):  # 먹구름
        x, y, r = rnd.uniform(-50, W + 50), rnd.uniform(-60, 220), rnd.uniform(90, 180)
        d.ellipse([x - r, y - r * 0.6, x + r, y + r * 0.6], fill=(58, 64, 72, 220))
    puddle = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(puddle).ellipse([60, c.y_banner - 70, W - 60, c.y_banner + 50], fill=(90, 110, 130, 120))
    img.alpha_composite(puddle.filter(ImageFilter.GaussianBlur(6)))
    return masked(img, panel_mask((W, H), "rounded"))


def _rain_front(c: Ctx) -> Image.Image | None:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rnd = random.Random(c.seed + 9)
    for _ in range(260 if c.top else 170):
        x, y, L = rnd.uniform(0, W + 200), rnd.uniform(0, H), rnd.uniform(40, 110)
        d.line([x, y, x - L * 0.35, y + L], fill=(200, 215, 230, rnd.randint(70, 150)), width=rnd.choice((2, 3)))
    if c.top:  # 1위(최악): 번개
        bolt(d, W * 0.78, 0, c.y_rank * 0.8, rnd, (235, 240, 255, 230), 9)
    return masked(img, panel_mask((W, H), "rounded"))


def _cold_rank(text: str, size: int, c: Ctx) -> Image.Image:
    t = _plain(text, size, ("#F2F6FA", "#B8C4D0", "#6B7886"), stroke="#0B0F14", outer="#5A6878", outer_w=6)
    return glow(t, "#E23B2E", 18, 1.3) if c.top else t


def _glass_back(c: Ctx) -> Image.Image:
    img = vgrad((W, H), "#1B2026", _mix(c.primary, "#05070A", 0.7))
    shard = vgrad((W, H), "#9FB4C8", "#48586A", 60, 30)
    img.alpha_composite(masked(shard, panel_mask((W, H), "shard", c.seed)))
    return masked(img, panel_mask((W, H), "shard", c.seed))


def _glass_front(c: Ctx) -> Image.Image | None:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rnd = random.Random(c.seed + 3)
    cx, cy = W * 0.62, c.y_rank * 0.55
    for k in range(18 if c.top else 12):  # 충격점에서 뻗는 금
        a = 2 * math.pi * k / (18 if c.top else 12) + rnd.uniform(-0.15, 0.15)
        x, y = cx, cy
        for _ in range(6):
            nx = x + math.cos(a) * rnd.uniform(60, 140)
            ny = y + math.sin(a) * rnd.uniform(60, 140)
            d.line([x, y, nx, ny], fill=(235, 245, 255, 210), width=4)
            x, y = nx, ny
            a += rnd.uniform(-0.25, 0.25)
    for r in (60, 130, 220):  # 동심원 금
        pts = [(cx + r * math.cos(t) * rnd.uniform(0.85, 1.1), cy + r * math.sin(t) * rnd.uniform(0.85, 1.1))
               for t in np.linspace(0, 2 * math.pi, 14)]
        d.line(pts, fill=(235, 245, 255, 150), width=3)
    return img


def _cracked_rank(text: str, size: int, c: Ctx) -> Image.Image:
    """금은 배경·그림에만 (글자 위로 지나가지 않게 글자는 깨끗하게)."""
    return _plain(text, size, ("#FFFFFF", "#C9D3DD", "#7B8794"), stroke="#0B0F14", outer="#FFFFFF", outer_w=5)


def _fall_back(c: Ctx) -> Image.Image:
    """하락 그래프: 어두운 붉은 배경 + 떨어지는 꺾은선 화살표."""
    img = vgrad((W, H), "#3A0D10", "#120405")
    d = ImageDraw.Draw(img)
    for y in range(120, c.y_banner, 110):
        d.line([40, y, W - 40, y], fill=(255, 255, 255, 28), width=2)
    for x in range(60, W, 140):
        d.line([x, 80, x, c.y_banner], fill=(255, 255, 255, 20), width=2)
    rnd = random.Random(c.seed)
    pts, y = [], 160
    for k in range(8):
        pts.append((60 + k * (W - 120) / 7, y))
        y += rnd.uniform(40, 150) * (1.4 if c.top else 1.0)
    line = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(line)
    ld.line(pts, fill=(255, 60, 60, 255), width=18, joint="curve")
    ex, ey = pts[-1]
    ld.polygon([(ex + 10, ey + 50), (ex - 60, ey - 10), (ex + 30, ey - 50)], fill=(255, 60, 60, 255))
    img.alpha_composite(_glow_canvas(line, "#FF3030", 14, 1.2))
    return masked(img, panel_mask((W, H), "tilt", c.seed))


def _red_rank(text: str, size: int, c: Ctx) -> Image.Image:
    return _plain(text, size, ("#FFE3E3", "#FF5A5A", "#A50E0E"), stroke="#1A0000", outer="#FFFFFF", outer_w=6)


def _warning_front(c: Ctx) -> Image.Image | None:
    if not c.top:
        return None
    s = stamp("최악", 120, "#FF2D2D", angle=12, seed=c.seed)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    img.alpha_composite(s, (W - s.width - 30, 60))
    return img


# --- 돈 -----------------------------------------------------------------------

def _bill(w: int, h: int, color: str, seed: int) -> Image.Image:
    b = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(b)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=10, fill=_rgba(color), outline=(20, 60, 30, 255), width=5)
    d.rounded_rectangle([14, 14, w - 15, h - 15], radius=8, outline=(255, 255, 255, 120), width=3)
    d.ellipse([w / 2 - h * 0.32, h * 0.18, w / 2 + h * 0.32, h * 0.82], fill=(255, 255, 255, 70),
              outline=(20, 60, 30, 180), width=4)
    f = _font(str(FONT_BLACK), int(h * 0.36))
    d.text((w / 2, h / 2), "₩", font=f, fill=(20, 60, 30, 230), anchor="mm")
    f2 = _font(str(FONT_EXTRA), int(h * 0.2))
    d.text((24, 22), "50000", font=f2, fill=(255, 255, 255, 200))
    return b


def _bills_back(c: Ctx) -> Image.Image:
    img = radial((W, H), (W / 2, c.y_rank - 200), "#F4FFE8", "#BFE3B0", 900)
    rnd = random.Random(c.seed)
    for _ in range(26 if c.top else 18):
        bill = _bill(360, 170, rnd.choice(("#7BC27A", "#5FAE63", "#E9C46A")), rnd.randint(0, 99))
        bill = bill.rotate(rnd.uniform(-40, 40), expand=True, resample=Image.BICUBIC)
        img.alpha_composite(bill, (int(rnd.uniform(-120, W - 200)), int(rnd.uniform(-60, c.y_banner - 160))))
    img.alpha_composite(brush_band((W, 400), c.primary, seed=c.seed, angle=-6, thickness=0.55), (0, c.y_rank + 80))
    return masked(img, panel_mask((W, H), "rounded"))


def _money_rank(text: str, size: int, c: Ctx) -> Image.Image:
    t = _plain(text, size, ("#E8FFD8", "#58C25A", "#1C6B2A"), stroke="#062B10", outer="#FFFFFF", outer_w=6)
    return glow(t, "#7CFF7C", 20, 1.4) if c.top else t


def _money_record(text: str, size: int, c: Ctx) -> Image.Image:
    return _plain(text, size, GOLD, stroke="#3B2400", outer=c.primary, outer_w=7)


def _coin(r: int, seed: int) -> Image.Image:
    img = radial((2 * r, 2 * r), (r * 0.7, r * 0.6), "#FFF6B8", "#C88A00", r * 1.4)
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).ellipse([0, 0, 2 * r - 1, 2 * r - 1], fill=255)
    img.putalpha(m)
    d = ImageDraw.Draw(img)
    d.ellipse([3, 3, 2 * r - 4, 2 * r - 4], outline=(140, 90, 0, 255), width=max(3, r // 10))
    d.text((r, r), "₩", font=_font(str(FONT_BLACK), int(r * 1.1)), fill=(150, 95, 0, 255), anchor="mm")
    return img


def _coins_back(c: Ctx) -> Image.Image:
    img = vgrad((W, H), "#2A1A00", "#0D0800")
    img.alpha_composite(radial((W, H), (W / 2, c.y_rank - 100), "#FFD86B", "#2A1A00", 700, 200, 0))
    rnd = random.Random(c.seed)
    for k in range(5):  # 동전 탑
        x = 90 + k * 190
        h = int(rnd.uniform(5, 13)) * (2 if c.top else 1)
        for j in range(h):
            coin = Image.new("RGBA", (150, 50), (0, 0, 0, 0))
            cd = ImageDraw.Draw(coin)
            cd.ellipse([0, 0, 149, 49], fill=(232, 176, 30, 255), outline=(120, 75, 0, 255), width=4)
            img.alpha_composite(coin, (x - 75, c.y_banner - 60 - j * 22))
    for _ in range(16 if c.top else 9):  # 떨어지는 동전
        coin = _coin(rnd.randint(34, 60), rnd.randint(0, 9)).rotate(rnd.uniform(-30, 30), expand=True)
        img.alpha_composite(coin, (int(rnd.uniform(20, W - 120)), int(rnd.uniform(20, c.y_rank - 120))))
    return masked(img, panel_mask((W, H), "rounded"))


def _receipt_back(c: Ctx) -> Image.Image:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    paper = vgrad((W - 120, H - 120), "#FFFFFF", "#F1EEE6")
    pm = panel_mask(paper.size, "torn", c.seed)
    d = ImageDraw.Draw(paper)
    f = _font(str(FONT_EXTRA), 40)
    rnd = random.Random(c.seed)
    labels = ("계약금", "연봉", "옵션", "총액")
    for k, y in enumerate(range(60, c.y_rank - 140, 64)):  # 지어낸 금액이 데이터로 보이지 않게 숫자 없이 회색 막대
        d.text((50, y), labels[k % len(labels)], font=f, fill=(90, 90, 90, 255))
        d.rounded_rectangle([paper.width - 50 - rnd.uniform(180, 320), y + 10, paper.width - 50, y + 36], radius=8,
                            fill=(200, 200, 200, 255))
        d.line([50, y + 54, paper.width - 50, y + 54], fill=(180, 180, 180, 255), width=2)
    paper.putalpha(pm)
    shadow = Image.new("RGBA", paper.size, (0, 0, 0, 90))
    shadow.putalpha(pm.point(lambda v: v * 0.35))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(10)), (72, 72))
    img.alpha_composite(paper.rotate(-2, expand=False, resample=Image.BICUBIC), (60, 60))
    img.alpha_composite(brush_band((W, 380), c.primary, seed=c.seed, angle=-5, thickness=0.55), (0, c.y_rank + 90))
    return img


def _receipt_front(c: Ctx) -> Image.Image | None:
    s = stamp("PAID" if not c.top else "최고액", 110, "#D91E2A", angle=-14, seed=c.seed)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    img.alpha_composite(s, (W - s.width - 40, 90))
    return img


# --- 대결 ---------------------------------------------------------------------

def _vs_back(c: Ctx) -> Image.Image:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.polygon([(0, 0), (W * 0.62, 0), (W * 0.38, H), (0, H)], fill=_rgba(c.primary))
    d.polygon([(W * 0.62, 0), (W, 0), (W, H), (W * 0.38, H)], fill=_rgba(c.secondary if _lum(c.secondary) < 0.85
                                                                         else "#1A1A1A"))
    edge = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(edge).line([(W * 0.62, 0), (W * 0.38, H)], fill=(255, 255, 255, 255), width=22)
    img.alpha_composite(_glow_canvas(edge, "#FFFFFF", 16, 1.5))
    vs = styled_text("VS", 260, ("#FFFFFF", "#FFE45C", "#FF9A00"), stroke="#111111", stroke_w=12, skew=0.12,
                     outer="#FFFFFF", outer_w=6)
    img.alpha_composite(vs.rotate(8, expand=True).point(lambda v: v) if False else vs, (W // 2 - vs.width // 2, 40))
    return masked(img, panel_mask((W, H), "tilt", c.seed))


def _split_rank(text: str, size: int, c: Ctx) -> Image.Image:
    t = _plain(text, size, ("#FFFFFF", "#FFE45C", "#FF7A00"), stroke="#111111", outer=c.primary, outer_w=9)
    return glow(t, "#FFE45C", 22, 1.6) if c.top else t


def _bolt_back(c: Ctx) -> Image.Image:
    img = vgrad((W, H), "#0A0820", _mix(c.primary, "#0A0820", 0.4))
    img.alpha_composite(radial((W, H), (W / 2, c.y_rank - 250), "#8C7BFF", "#0A0820", 700, 170, 0))
    return masked(img, panel_mask((W, H), "rounded"))


def _bolt_front(c: Ctx) -> Image.Image | None:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rnd = random.Random(c.seed)
    for k in range(4 if c.top else 2):
        bolt(d, rnd.uniform(60, W - 60), 0, c.y_rank * rnd.uniform(0.6, 0.95), rnd, (255, 250, 200, 255), 10)
    return _glow_canvas(img, "#B9A8FF", 16, 1.8)


def _electric_rank(text: str, size: int, c: Ctx) -> Image.Image:
    t = _plain(text, size, ("#FFFFFF", "#D9D2FF", "#7B61FF"), stroke="#120A3A", outer="#FFFFFF", outer_w=6)
    return glow(t, "#9E8CFF", 26 if c.top else 16, 1.8)


def _flame_back(c: Ctx) -> Image.Image:
    img = vgrad((W, H), "#1A0500", "#000000")
    flames = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(flames)
    rnd = random.Random(c.seed)
    for layer, (col, scale) in enumerate((("#FF3D00", 1.0), ("#FF9100", 0.75), ("#FFE066", 0.45))):
        for k in range(9):
            x = k * W / 8 + rnd.uniform(-30, 30)
            hgt = rnd.uniform(500, 900) * scale * (1.25 if c.top else 1)
            base = c.y_banner + 40
            d.polygon([(x - 120 * scale, base), (x - 30, base - hgt * 0.55), (x + rnd.uniform(-40, 40), base - hgt),
                       (x + 40, base - hgt * 0.5), (x + 120 * scale, base)], fill=_rgba(col, 235))
    img.alpha_composite(flames.filter(ImageFilter.GaussianBlur(5)))
    return masked(img, panel_mask((W, H), "rounded"))


def _fire_rank(text: str, size: int, c: Ctx) -> Image.Image:
    t = _plain(text, size, ("#FFF6C2", "#FFB300", "#FF3D00"), stroke="#2A0500", outer="#FFFFFF", outer_w=5)
    return glow(t, "#FF6A00", 22 if c.top else 12, 1.5)


# --- 옛날 기록 ----------------------------------------------------------------

SEPIA = ("#F3E6C8", "#E2CFA4", "#C9B07C")


def _paper(size: tuple[int, int], seed: int) -> Image.Image:
    img = vgrad(size, SEPIA[0], SEPIA[2])
    rnd = np.random.default_rng(seed)
    noise = (rnd.random((size[1], size[0])) * 40).astype(np.uint8)
    n = Image.fromarray(noise).filter(ImageFilter.GaussianBlur(1.2))
    dark = Image.new("RGBA", size, (80, 55, 20, 0))
    dark.putalpha(n)
    img.alpha_composite(dark)
    return img


def _newspaper_back(c: Ctx) -> Image.Image:
    img = _paper((W, H), c.seed)
    d = ImageDraw.Draw(img)
    f_mast = _font(str(FONT_BLACK), 92)
    d.text((W / 2, 40), "호 외", font=f_mast, fill=(45, 30, 10, 255), anchor="mt")  # 실제 신문 이름은 쓰지 않음
    d.line([40, 150, W - 40, 150], fill=(45, 30, 10, 255), width=6)
    d.line([40, 162, W - 40, 162], fill=(45, 30, 10, 255), width=2)
    rnd = random.Random(c.seed)
    for col in range(3):  # 기사 단 (글자 대신 회색 줄)
        x0 = 50 + col * (W - 100) / 3
        for y in range(200, c.y_rank - 40, 26):
            d.line([x0 + 10, y, x0 + (W - 100) / 3 - 20 - rnd.uniform(0, 60), y], fill=(110, 90, 60, 150), width=8)
    d.rectangle([60, 210, W - 60, c.y_rank - 60], outline=(45, 30, 10, 255), width=5)
    return masked(img, panel_mask((W, H), "torn", c.seed))


def _ink_rank(text: str, size: int, c: Ctx) -> Image.Image:
    t = _plain(text, size, ("#3A2510", "#2A1A08", "#1A0F04"), stroke="#F3E6C8", stroke_w=6, skew=0.05, shadow=0)
    return ink(t, c.seed, 0.12)


def _film_rank(text: str, size: int, c: Ctx) -> Image.Image:
    """어두운 필름 위: 흰색~밝은 금색 + 굵은 검정 테두리 + 흰 바깥 테두리."""
    t = _plain(text, size, ("#FFFFFF", "#FFF1C2", "#F2C84B"), stroke="#120C04", stroke_w=14, outer="#FFFFFF",
               outer_w=6, skew=0.06)
    return glow(t, "#FFE7A0", 18, 1.3) if c.top else t


def _film_record(text: str, size: int, c: Ctx) -> Image.Image:
    return _plain(text, size, ("#FFFFFF", "#FFF6DC", "#E8D3A0"), stroke="#120C04", stroke_w=14, outer=c.primary,
                  outer_w=7, skew=0.06)


def _paper_rank(text: str, size: int, c: Ctx) -> Image.Image:
    """신문 지면: 진한 검정 + 빨간 테두리."""
    return _plain(text, size, ("#1A1A1A", "#050505", "#000000"), stroke="#D0141E", stroke_w=10, outer="#FFFFFF",
                  outer_w=5, skew=0.05, shadow=6)


def _sepia_record(text: str, size: int, c: Ctx) -> Image.Image:
    return ink(_plain(text, size, ("#FFF6E0", "#E9D3A6", "#B48A4A"), stroke="#2A1A08", outer=c.primary, outer_w=6,
                      skew=0.06), c.seed + 1, 0.06)


def _film_back(c: Ctx) -> Image.Image:
    img = Image.new("RGBA", (W, H), (20, 16, 12, 255))
    d = ImageDraw.Draw(img)
    for x0 in (0, W - 90):  # 필름 구멍
        for y in range(30, H, 90):
            d.rounded_rectangle([x0 + 22, y, x0 + 68, y + 50], radius=8, fill=(235, 225, 205, 255))
    frame = _paper((W - 200, c.y_banner - 80), c.seed)
    vign = radial(frame.size, (frame.width / 2, frame.height / 2), "#000000", "#000000", frame.width * 0.8, 0, 170)
    frame.alpha_composite(vign)
    img.alpha_composite(frame, (100, 40))
    rnd = random.Random(c.seed)
    for _ in range(12):  # 필름 긁힘
        x = rnd.uniform(110, W - 110)
        d.line([x, 40, x + rnd.uniform(-8, 8), c.y_banner - 40], fill=(255, 250, 230, 60), width=2)
    return masked(img, panel_mask((W, H), "rounded"))


def _polaroid_back(c: Ctx) -> Image.Image:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pol = Image.new("RGBA", (W - 140, c.y_rank + 80), (250, 247, 240, 255))
    inner = _paper((pol.width - 60, pol.height - 200), c.seed)
    inner.alpha_composite(vgrad(inner.size, _mix(c.primary, SEPIA[0], 0.7), SEPIA[1], 140, 60))
    pol.alpha_composite(inner, (30, 30))
    pol = pol.rotate(-3, expand=True, resample=Image.BICUBIC)
    sh = Image.new("RGBA", pol.size, (0, 0, 0, 0))
    sh.putalpha(pol.getchannel("A").point(lambda v: v * 0.35))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(12)), (82, 42))
    img.alpha_composite(pol, (70, 30))
    tape = Image.new("RGBA", (220, 70), (240, 225, 170, 190)).rotate(-12, expand=True)
    img.alpha_composite(tape, (W // 2 - 110, 0))
    img.alpha_composite(brush_band((W, 380), c.primary, seed=c.seed, angle=-5, thickness=0.5), (0, c.y_rank + 90))
    return img


def _retro_front(c: Ctx) -> Image.Image | None:
    if not c.top:
        return None
    s = stamp("전설", 120, "#B3261E", angle=-10, seed=c.seed)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    img.alpha_composite(s, (40, 70))
    return img


# --- 현재·신기록 ---------------------------------------------------------------

def _board_back(c: Ctx) -> Image.Image:
    img = Image.new("RGBA", (W, H), (8, 10, 14, 255))
    d = ImageDraw.Draw(img)
    for y in range(0, H, 14):  # 꺼진 LED 점
        for x in range(0, W, 14):
            d.ellipse([x + 4, y + 4, x + 10, y + 10], fill=(30, 34, 40, 255))
    d.rectangle([18, 18, W - 19, H - 19], outline=_rgba(c.primary), width=14)
    f = _font(str(FONT_BLACK), 60)
    head = Image.new("RGBA", (W, 120), (0, 0, 0, 0))
    ImageDraw.Draw(head).text((W / 2, 60), "NEW RECORD" if c.top else "KBO LIVE", font=f, fill=(255, 196, 0, 255),
                              anchor="mm")
    img.alpha_composite(_glow_canvas(led(head, 7), "#FFC400", 10, 1.4), (0, 40))
    return masked(img, panel_mask((W, H), "rounded"))


def _led_rank(text: str, size: int, c: Ctx) -> Image.Image:
    t = _plain(text, size, ("#FFF3B0", "#FFC400", "#FF8A00"), stroke="#FFC400", stroke_w=2, skew=0.0, shadow=0)
    return glow(led(t, 10), "#FFB000", 14 if c.top else 8, 1.5)


def _led_record(text: str, size: int, c: Ctx) -> Image.Image:
    col = c.primary if _lum(c.primary) > 0.5 else _mix(c.primary, "#FFFFFF", 0.62)
    t = _plain(text, size, ("#FFFFFF", col, col), stroke="#FFFFFF", stroke_w=2, skew=0.0, shadow=0)
    return glow(led(t, 9), col, 10, 1.6)


def _neon_back(c: Ctx) -> Image.Image:
    img = vgrad((W, H), "#14061F", "#05010A")
    tube = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(tube)
    col = _mix(c.primary, "#FFFFFF", 0.35) if _lum(c.primary) < 0.3 else c.primary
    d.rounded_rectangle([50, 50, W - 50, c.y_banner - 40], radius=70, outline=_rgba(col), width=12)
    d.rounded_rectangle([80, 80, W - 80, c.y_banner - 70], radius=50, outline=(255, 64, 200, 255), width=6)
    img.alpha_composite(_glow_canvas(tube, col, 18, 1.6))
    # 바닥 격자
    g = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(g)
    hy = c.y_rank + 40
    for k in range(-8, 9):
        gd.line([W / 2, hy, W / 2 + k * 160, H], fill=(255, 64, 200, 110), width=3)
    for k in range(8):
        y = hy + (H - hy) * (k / 8) ** 1.6
        gd.line([0, y, W, y], fill=(255, 64, 200, 110), width=3)
    img.alpha_composite(g)
    return masked(img, panel_mask((W, H), "rounded"))


def _neon_rank(text: str, size: int, c: Ctx) -> Image.Image:
    return neon_text(text, size, "#FF40C8" if not c.top else "#FFE45C")


def _neon_record(text: str, size: int, c: Ctx) -> Image.Image:
    col = _mix(c.primary, "#FFFFFF", 0.35) if _lum(c.primary) < 0.3 else c.primary
    return neon_text(text, size, col)


def _breaking_back(c: Ctx) -> Image.Image:
    img = vgrad((W, H), "#0D1B3E", "#060B18")
    d = ImageDraw.Draw(img)
    for k in range(0, W + H, 60):  # 빗금 무늬
        d.line([k, 0, k - H, H], fill=(255, 255, 255, 14), width=20)
    bar = Image.new("RGBA", (W, 110), (210, 20, 30, 255))
    bd = ImageDraw.Draw(bar)
    bd.rectangle([0, 0, 230, 110], fill=(255, 255, 255, 255))
    bd.text((115, 55), "LIVE", font=_font(str(FONT_BLACK), 64), fill=(210, 20, 30, 255), anchor="mm")
    bd.text((260, 55), "속보 · 신기록" if c.top else "실시간 순위", font=_font(str(FONT_BLACK), 58), fill="white",
            anchor="lm")
    img.alpha_composite(bar, (0, 40))
    img.alpha_composite(brush_band((W, 420), c.primary, seed=c.seed, angle=-6, thickness=0.6), (0, c.y_rank + 60))
    return masked(img, panel_mask((W, H), "rounded"))


def _news_rank(text: str, size: int, c: Ctx) -> Image.Image:
    t = _plain(text, size, ("#FFFFFF", "#FFFFFF", "#DDE3EE"), stroke="#D2141E", stroke_w=12, outer="#FFFFFF",
               outer_w=5)
    return glow(t, "#FF2D3A", 22, 1.5) if c.top else t


# ---------------------------------------------------------------- 목록

def _std_record(text: str, size: int, c: Ctx) -> Image.Image:
    return _plain(text, size, SILVER, stroke="#111111", outer=c.primary, outer_w=7)


DESIGNS: dict[str, Design] = {d.key: d for d in [
    Design("stadium", "record", "스타디움 조명", _stadium_back, _gold_rank, _std_record, _flare_front),
    Design("gold_rays", "record", "금색 광선", _gold_rays_back, _gold_rank, _std_record, _sparkle_front),
    Design("medal", "record", "메달·리본", _medal_back, _medal_rank, _std_record, _sparkle_front),
    Design("fall", "humiliation", "추락 그래프", _fall_back, _red_rank, _std_record, _warning_front,
           banner_fill=lambda c: ("#1A0506", c.primary, "#FFFFFF")),
    Design("rain", "humiliation", "어두운 비", _rain_back, _cold_rank, _std_record, _rain_front,
           banner_fill=lambda c: (_mix(c.primary, "#20252B", 0.45), "#0B0F14", "#FFFFFF")),
    Design("glass", "humiliation", "금 간 유리", _glass_back, _cracked_rank, _std_record, _glass_front,
           banner_fill=lambda c: (_mix(c.primary, "#20252B", 0.35), "#E8EEF4", "#FFFFFF")),
    Design("bills", "money", "지폐 비", _bills_back, _money_rank, _money_record, _sparkle_front),
    Design("coins", "money", "금화 더미", _coins_back, _money_rank, _money_record, _sparkle_front),
    Design("receipt", "money", "계약서 영수증", _receipt_back, _money_rank, _money_record, _receipt_front),
    Design("vs_split", "rivalry", "VS 분할", _vs_back, _split_rank, _std_record),
    Design("lightning", "rivalry", "번개", _bolt_back, _electric_rank, _std_record, _bolt_front),
    Design("flames", "rivalry", "불꽃 대결", _flame_back, _fire_rank, _std_record),
    Design("newspaper", "retro", "신문 지면", _newspaper_back, _paper_rank, _paper_rank, _retro_front,
           banner_fill=lambda c: ("#3A2510", c.primary, "#F3E6C8"), ribbon="#3A2510", year_color="#F3E6C8"),
    Design("film", "retro", "옛날 필름", _film_back, _film_rank, _film_record, _retro_front,
           banner_fill=lambda c: ("#3A2510", "#C9B07C", "#F3E6C8"), ribbon="#3A2510"),
    Design("polaroid", "retro", "빛바랜 사진", _polaroid_back, _ink_rank, _sepia_record, _retro_front),
    Design("scoreboard", "live", "전광판", _board_back, _led_rank, _led_record,
           banner_fill=lambda c: ("#111418", c.primary, "#FFFFFF")),
    Design("neon", "live", "네온", _neon_back, _neon_rank, _neon_record,
           banner_fill=lambda c: ("#14061F", "#FF40C8", "#FFFFFF")),
    Design("breaking", "live", "속보 LIVE", _breaking_back, _news_rank, _std_record),
]}


def designs_for(mood: str) -> list[str]:
    """그 분위기의 디자인 (추천 순서: 앞이 우선)."""
    return [k for k, d in DESIGNS.items() if d.mood == mood]


# ---------------------------------------------------------------- 합성

def compose_designed(caricature: Path | Image.Image | None, design: str, *, rank: int, record: str, year: str,
                     team: str, player: str, mock: bool = False, tied: bool = False, top: bool | None = None,
                     omit_record: bool = False, meta: dict | None = None, rank_label: str | None = None,
                     off: bool = False, banner: bool = True, layers: dict | None = None) -> Image.Image:
    """card_compose.compose_card 와 같은 배치에 디자인별 배경·글자 효과. top: 1위 장식 (기본: rank == 1).
    rank_label: 순위 자리 글자 (보너스 '6위?!'), off: 디자인별 반전 상태 (조명 꺼짐 등)."""
    import re as _re
    dz = DESIGNS[design]
    primary, secondary = team_colors(team)
    c = Ctx(primary, secondary, rank, rank == 1 if top is None else top, seed=rank * 13 + len(design), off=off)

    rank_img = _rank_with_twist(dz, rank_label, c) if rank_label else dz.rank_text(f"{rank}위", 270, c)
    if rank_img.width > 560:
        rank_img = rank_img.resize((560, round(rank_img.height * 560 / rank_img.width)), Image.LANCZOS)
    rec_size = 190
    rec = dz.record_text(record, rec_size, c) if record else None
    while rec is not None and rec.width > W - 90 and rec_size > 40:
        rec_size = int(rec_size * 0.92)
        rec = dz.record_text(record, rec_size, c)
    year = year.strip() if year and _re.fullmatch(r"\s*\d{4}\s*", year) else ""
    yr = styled_text(year, 66, dz.year_color, stroke="#111111", stroke_w=6, skew=0.08, shadow=4) if year else None
    b_fill, b_edge, b_text = dz.banner_fill(c)
    name = styled_text(team or "팀", 104, b_text, stroke=b_edge if _lum(b_edge) < 0.9 else "#111111", stroke_w=9,
                       skew=0.12, shadow=6)
    if name.width > W - 150:
        name = name.resize((W - 150, round(name.height * (W - 150) / name.width)), Image.LANCZOS)
    pl = _player_text(player) if player else None

    y = H - 8
    if pl:
        rh = pl.height + 22
        y_player = y - rh
        y = y_player - 4
    bh = name.height + 30
    y_banner = y - bh
    y = y_banner - 10
    if yr:
        y_year = y - yr.height
        y = y_year - 2
    if rec:
        y_rec = y - rec.height + 18
        y = y_rec + 30
    y_rank = y - rank_img.height
    c.y_rank, c.y_banner = y_rank, y_banner

    # 층: 배경 → 캐리커처 → 앞 효과 → 글자 (2.5D 패럴랙스에서 층마다 따로 움직임)
    L = {n: Image.new("RGBA", (W, H), (0, 0, 0, 0)) for n in ("back", "art", "front", "text")}
    L["back"].alpha_composite(dz.back(c))
    if caricature is not None:
        if isinstance(caricature, Image.Image):
            art = caricature.convert("RGBA")
        else:
            with Image.open(caricature) as im:
                art = ImageOps.exif_transpose(im).convert("RGBA")
        art = ImageOps.contain(art, (W - 20, min(1320, y_banner - 20)), Image.LANCZOS)
        L["art"].alpha_composite(art, ((W - art.width) // 2, 10))
    front = dz.front(c)
    if front is not None:
        L["front"].alpha_composite(front)
    card = L["text"]
    if c.top and dz.mood in ("record", "money", "live", "rivalry"):  # 1위: 왕관
        cr = crown(220, "#FFD23F")
        card.alpha_composite(cr, (max(0, 34 + rank_img.width // 2 - cr.width // 2), max(0, y_rank - cr.height + 50)))
    card.alpha_composite(rank_img, (34, y_rank))
    if tied:
        co = dz.rank_text("공동", 96, c)
        card.alpha_composite(co, (40, max(0, y_rank - co.height + 24)))
    if rec and not omit_record:
        card.alpha_composite(rec, (56, y_rec))
    if rec and meta is not None:
        meta.update(record=record, size=rec_size, x=56, y=y_rec, h=rec.height, team=team, design=design, rank=rank,
                    top=c.top)
    if yr:
        d = ImageDraw.Draw(card)
        cy = y_year + yr.height / 2
        for sx in (-1, 1):
            x0 = W / 2 + sx * (yr.width / 2 + 20)
            x1 = W / 2 + sx * (yr.width / 2 + 130)
            d.line([x0, cy, x1, cy], fill=_rgba(dz.year_color), width=8)
        _paste_center(card, yr, W / 2, cy)
    if banner:
        plate = Image.new("RGBA", (W - 60, bh), (0, 0, 0, 0))
        ImageDraw.Draw(plate).polygon([(30, 0), (W - 60, 0), (W - 90, bh), (0, bh)], fill=_rgba(b_fill),
                                      outline=_rgba(b_edge), width=6)
        card.alpha_composite(plate, (30, y_banner))
        _paste_center(card, name, W / 2, y_banner + bh / 2)
    if pl:
        rw = pl.width + 70
        ribbon = Image.new("RGBA", (rw, rh), (0, 0, 0, 0))
        ImageDraw.Draw(ribbon).polygon([(18, 0), (rw, 0), (rw - 18, rh), (0, rh)], fill=_rgba(dz.ribbon, 235))
        card.alpha_composite(ribbon, (round(W / 2 - rw / 2), y_player))
        _paste_center(card, pl, W / 2, y_player + rh / 2)
    if mock:
        d = ImageDraw.Draw(card)
        d.rounded_rectangle([16, 16, 330, 84], radius=14, fill=(220, 0, 0, 235))
        d.text((173, 50), "MOCK 시험용", font=_font(str(FONT_EXTRA), 44), fill="white", anchor="mm")
    if layers is not None:
        layers.update({n: im.copy() for n, im in L.items()})
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for n in ("back", "art", "front", "text"):
        out.alpha_composite(L[n])
    return out


def _rank_with_twist(dz: Design, label: str, c: Ctx) -> Image.Image:
    """'6위?!' — 순위는 디자인 글자, '?!' 는 빨갛게 크게 (반전 강조)."""
    import re as _re
    m = _re.match(r"^(.*?)([?!？！]+)$", label)
    head, tail = (m.group(1), m.group(2)) if m else (label, "")
    a = dz.rank_text(head, 270, c)
    if not tail:
        return a
    b = styled_text(tail, 300, ("#FFE3E3", "#FF3B3B", "#B00010"), stroke="#1A0000", stroke_w=12, outer="#FFFFFF",
                    outer_w=7, skew=0.18)
    b = glow(b, "#FF3030", 16, 1.3)
    out = Image.new("RGBA", (a.width + b.width - 30, max(a.height, b.height)), (0, 0, 0, 0))
    out.alpha_composite(a, (0, out.height - a.height))
    out.alpha_composite(b, (a.width - 30, out.height - b.height))
    return out


def question_art(color: str = "#FFD23F") -> Image.Image:
    """보너스 카드 그림 자리: 커다란 물음표 (캐리커처가 없을 때)."""
    q = styled_text("?", 600, (_mix(color, "#FFFFFF", 0.5), color, _mix(color, "#000000", 0.35)), stroke="#111111",
                    stroke_w=16, outer="#FFFFFF", outer_w=9, skew=0.08, shadow=12)
    q = glow(q, color, 26, 1.2)
    canvas = Image.new("RGBA", (W - 20, 1320), (0, 0, 0, 0))  # 그림 자리의 60% 쯤만 차지 (순위 글자와 안 겹치게)
    q.thumbnail((int(canvas.width * 0.62), int(canvas.height * 0.62)))
    canvas.alpha_composite(q, ((canvas.width - q.width) // 2, 60))
    return canvas


def compose_bonus(design: str, kind: str, *, rank: int | None = None, name: str = "", team: str = "", value: str = "",
                  year: str = "", text: str = "", art: Path | Image.Image | None = None, off: bool = False,
                  tilt: float = -5.0) -> Image.Image:
    """엔딩 보너스 카드: 영상 카드와 같은 디자인 + 반전 ('6위?!', '근데…' 도장, 기울기, off=디자인별 반전 상태)."""
    if kind == "teaser":
        card = compose_designed(art, design, rank=rank or 6, record="", year="", team=team, player="",
                                rank_label="다음 편?!", top=False, off=off, banner=False)
        lines = [l.strip() for l in text.replace(":", "\n").split("\n") if l.strip()][:3]
        y = 380
        for l in lines:
            im = styled_text(l, 110, "#FFFFFF", stroke="#111111", stroke_w=10, skew=0.08, shadow=6)
            if im.width > W - 60:
                im = im.resize((W - 60, round(im.height * (W - 60) / im.width)), Image.LANCZOS)
            card.alpha_composite(im, ((W - im.width) // 2, y))
            y += im.height + 24
    else:
        card = compose_designed(art if art is not None else question_art(), design, rank=rank or 6, record=value,
                                year=year, team=team, player=name, rank_label=f"{rank}위?!", top=False, off=off)
    st = stamp("근데…", 104, "#D91E2A", angle=-12, seed=7)
    card.alpha_composite(st, (W - st.width - 24, 40))
    if off and DESIGNS[design].back is not _stadium_back:  # 조명 반전이 없는 디자인은 전체가 한순간 어두워짐
        from PIL import ImageEnhance
        a = card.getchannel("A")
        card = ImageEnhance.Brightness(card.convert("RGB")).enhance(0.55).convert("RGBA")
        card.putalpha(a)
    if tilt:
        card = card.rotate(tilt, expand=True, resample=Image.BICUBIC)
    return card


def record_text_for(design: str | None, text: str, size: int, team: str, rank: int = 5,
                    top: bool | None = None) -> Image.Image:
    """카운트업용 기록 글자 (카드와 같은 디자인 효과)."""
    from .card_compose import record_text
    if design in ("clean_a", "clean_b"):
        from .card_clean import record_image
        return record_image(text, size)
    if not design or design not in DESIGNS:
        return record_text(text, size, team)
    p, s = team_colors(team)
    return DESIGNS[design].record_text(text, size, Ctx(p, s, rank, rank == 1 if top is None else top,
                                                       seed=rank * 13 + len(design)))
