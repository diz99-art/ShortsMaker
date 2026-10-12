"""하늘 배지형 순위 장면: 왼쪽 정보 배지 + 오른쪽 캐릭터 (참고 영상 '한국에서 돈 잘 버는 직업 5가지'를 재서 맞춤).

장면 그림은 템플릿 card_box(1080×680, 화면 y 1100~1780) 크기 그대로 만든다. 좌표는 모두 그 안 기준.
배지: 왕관 + 구름 모양 순위 동그라미, 작은 아이콘(선택), 이름, 노란 알약 안의 값, 회색 분류 칩('구분 · 2024년 기준').
1위는 배지·캐릭터를 TOP_SCALE 배 크게.
"""
from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from .card_clean import FONT_DISPLAY
from .card_compose import FONTS_DIR

SIZE = (1080, 680)
FONT_BOLD = FONTS_DIR / "Pretendard-Bold.otf"
FONT_LABEL = FONTS_DIR / "Pretendard-ExtraBold.otf"
NAVY = (16, 32, 74)
YELLOW = (255, 225, 77)
TOP_SCALE = 1.15
# 참고 영상에서 잰 자리 (장면 그림 기준)
PANEL = (60, 195, 565, 460)          # 흰 배지판
RANK_C = (150, 222)                  # 순위 동그라미 중심
RANK_R = 74
ICON_BOX = (78, 282, 288, 448)       # 아이콘 자리
CHAR_BOX = (640, 20, 1030, 612)      # 캐릭터 (아래·가운데 맞춤)


def _font(path: Path, size: int):
    from PIL import ImageFont
    return ImageFont.truetype(str(path), size)


def _fit_font(text: str, path: Path, size: int, max_w: int, min_size: int = 20):
    while size > min_size and _font(path, size).getlength(text) > max_w:
        size -= 2
    return _font(path, size)


def _load(src) -> Image.Image | None:
    if src is None:
        return None
    if isinstance(src, Image.Image):
        return src.convert("RGBA")
    with Image.open(src) as im:
        return im.convert("RGBA")


def _shadow(mask: Image.Image, color=(40, 110, 200), opacity=0.22, blur=14, dy=8) -> Image.Image:
    sh = Image.new("RGBA", mask.size, color + (0,))
    sh.putalpha(mask.point(lambda v: round(v * opacity)).filter(ImageFilter.GaussianBlur(blur)))
    out = Image.new("RGBA", mask.size, (0, 0, 0, 0))
    out.alpha_composite(sh, (0, dy)) if dy < mask.height else None
    return out


def _crown(d: ImageDraw.ImageDraw, cx: float, by: float, w: float) -> None:
    """왕관: 아래 변 가운데 (cx, by), 너비 w."""
    h = w * 0.62
    x0, x1 = cx - w / 2, cx + w / 2
    pts = [(x0, by), (x0 - w * 0.04, by - h * 0.78), (cx - w * 0.24, by - h * 0.42), (cx, by - h),
           (cx + w * 0.24, by - h * 0.42), (x1 + w * 0.04, by - h * 0.78), (x1, by)]
    d.polygon(pts, fill=(255, 200, 30), outline=(196, 120, 0), width=4)
    for px, py in pts[1:6:2] + [pts[3]]:
        r = w * 0.07
        d.ellipse([px - r, py - r, px + r, py + r], fill=(255, 228, 90), outline=(196, 120, 0), width=3)


def _rank_badge(label: str) -> Image.Image:
    """구름 모양 파란 동그라미 + 흰 순위 글자 (+ 위에 왕관, 옆에 노란 빗금 장식)."""
    S = 300
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    m = Image.new("L", (S, S), 0)
    md = ImageDraw.Draw(m)
    cx, cy, r = S / 2, S / 2 + 30, RANK_R
    for dx, dy, rr in [(0, 0, r), (-r * 0.62, r * 0.18, r * 0.58), (r * 0.62, r * 0.2, r * 0.56),
                       (-r * 0.3, -r * 0.5, r * 0.55), (r * 0.35, -r * 0.48, r * 0.55)]:
        md.ellipse([cx + dx - rr, cy + dy - rr, cx + dx + rr, cy + dy + rr], fill=255)
    outline = m.filter(ImageFilter.MaxFilter(11))
    white = Image.new("RGBA", (S, S), (255, 255, 255, 255))
    white.putalpha(outline)
    im.alpha_composite(white)
    blue = Image.new("RGBA", (S, S), (52, 132, 240, 255))
    grad = Image.linear_gradient("L").resize((S, S)).point(lambda v: round(v * 0.35))
    blue.alpha_composite(Image.merge("RGBA", (Image.new("L", (S, S), 20), Image.new("L", (S, S), 80),
                                              Image.new("L", (S, S), 190), grad)))
    blue.putalpha(m)
    im.alpha_composite(blue)
    d = ImageDraw.Draw(im)
    num = re.match(r"^(공동\s*)?(\d+)(위.*)$", label)
    if num:
        big = _font(FONT_DISPLAY, 92)
        small = _font(FONT_DISPLAY, 50)
        tag = (num.group(1) or "").strip()
        wb, ws = big.getlength(num.group(2)), small.getlength(num.group(3))
        x0 = cx - (wb + ws) / 2
        kw = dict(fill=(255, 255, 255), stroke_width=6, stroke_fill=(18, 59, 140))
        d.text((x0, cy + 28), num.group(2), font=big, anchor="ls", **kw)
        d.text((x0 + wb + 2, cy + 28), num.group(3), font=small, anchor="ls", **kw)
        if tag:  # 공동: 동그라미 아래 작은 알약
            f = _font(FONT_LABEL, 26)
            tw = f.getlength(tag)
            d.rounded_rectangle([cx - tw / 2 - 14, cy + r * 0.62, cx + tw / 2 + 14, cy + r * 0.62 + 38], radius=19,
                                fill=(18, 59, 140))
            d.text((cx, cy + r * 0.62 + 19), tag, font=f, fill=(255, 255, 255), anchor="mm")
    else:
        f = _fit_font(label, FONT_DISPLAY, 60, int(r * 1.8))
        d.text((cx, cy), label, font=f, anchor="mm", fill=(255, 255, 255), stroke_width=6, stroke_fill=(18, 59, 140))
    _crown(d, cx - r * 0.25, cy - r * 0.86, r * 0.8)
    for k, (a, b) in enumerate([((cx + r * 0.95, cy - r * 1.05), (cx + r * 1.12, cy - r * 1.35)),
                                ((cx + r * 1.2, cy - r * 0.8), (cx + r * 1.5, cy - r * 0.98))]):
        d.line([a, b], fill=(255, 196, 26), width=9)
    return im


def _value_pill(value: str, max_w: int) -> Image.Image:
    """노란 알약 안의 값. 숫자는 크게, 뒤 단위('만원', '억 명')는 작게."""
    m = re.match(r"^([\d.,]+)(.*)$", value.strip())
    head, tail = (m.group(1), m.group(2)) if m else (value, "")
    size = 58
    while True:
        fb, fs = _font(FONT_DISPLAY, size), _font(FONT_DISPLAY, round(size * 0.62))
        w = fb.getlength(head) + (fs.getlength(tail) + 4 if tail else 0)
        if w + 56 <= max_w or size <= 30:
            break
        size -= 2
    h = round(size * 1.35)
    im = Image.new("RGBA", (round(w + 56), h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, im.width - 1, h - 1], radius=h // 2, fill=YELLOW + (255,))
    base = h / 2 + size * 0.36
    d.text((28, base), head, font=fb, fill=NAVY, anchor="ls")
    if tail:
        d.text((28 + fb.getlength(head) + 4, base), tail, font=fs, fill=NAVY, anchor="ls")
    return im


def _chip(text: str, max_w: int) -> Image.Image:
    f = _fit_font(text, FONT_BOLD, 26, max_w - 40, 16)
    w, h = round(f.getlength(text) + 40), 40
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=h // 2, fill=(226, 233, 243, 255))
    d.text((w / 2, h / 2), text, font=f, fill=(64, 78, 104), anchor="mm")
    return im


def badge(*, rank_label: str, name: str, value: str, value_label: str = "", category: str = "", year: str = "",
          icon=None) -> Image.Image:
    """배지 그림 (장면 그림과 같은 좌표계, 투명 바깥)."""
    out = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    x0, y0, x1, y1 = PANEL
    pm = Image.new("L", SIZE, 0)
    ImageDraw.Draw(pm).rounded_rectangle([x0, y0, x1, y1], radius=46, fill=255)
    out.alpha_composite(_shadow(pm))
    panel = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    ImageDraw.Draw(panel).rounded_rectangle([x0, y0, x1, y1], radius=46, fill=(255, 255, 255, 240),
                                            outline=(200, 224, 248, 255), width=4)
    out.alpha_composite(panel)
    ic = _load(icon)
    if ic is not None:  # 왼쪽 아이콘 (비율 유지, 자리 가운데)
        bx0, by0, bx1, by1 = ICON_BOX
        s = min((bx1 - bx0) / ic.width, (by1 - by0) / ic.height)
        ic = ic.resize((max(1, round(ic.width * s)), max(1, round(ic.height * s))), Image.LANCZOS)
        out.alpha_composite(ic, (bx0 + (bx1 - bx0 - ic.width) // 2, by0 + (by1 - by0 - ic.height) // 2))
        col_l, col_r = 296, x1 - 18
    else:  # 아이콘이 없으면 정보 칸을 순위 동그라미 오른쪽으로 (동그라미와 안 겹치게)
        col_l, col_r = RANK_C[0] + RANK_R + 14, x1 - 24
    cx = (col_l + col_r) / 2
    col_w = col_r - col_l
    d = ImageDraw.Draw(out)
    nf = _fit_font(name, FONT_DISPLAY, 60, col_w, 30)
    lf = _fit_font(value_label, FONT_LABEL, 26, col_w - 80, 16) if value_label else None
    pill = _value_pill(value, col_w + 20)
    chip_text = " · ".join(x for x in (category, f"{year}년 기준" if year else "") if x)
    ch = _chip(chip_text, col_w + 20) if chip_text else None
    # 이름 → 라벨 → 노란 알약 → 칩, 배지판 안에서 세로 가운데
    total = nf.size + 14 + (34 if lf else 0) + pill.height + (10 + ch.height if ch else 0)
    y = y0 + max(16, (y1 - y0 - total) / 2)
    d.text((cx, y), name, font=nf, fill=NAVY, anchor="mt")
    y += nf.size + 14
    if lf is not None:
        d.text((cx, y + 13), value_label, font=lf, fill=NAVY, anchor="mm")
        lw = lf.getlength(value_label)
        for sgn in (-1, 1):  # 라벨 양옆 초록 빗금
            ex = cx + sgn * (lw / 2 + 16)
            d.line([(ex, y + 20), (ex + sgn * 12, y + 4)], fill=(46, 184, 92), width=5)
            d.line([(ex + sgn * 16, y + 22), (ex + sgn * 26, y + 10)], fill=(46, 184, 92), width=5)
        y += 34
    out.alpha_composite(pill, (round(cx - pill.width / 2), round(y)))
    y += pill.height + 10
    if ch is not None:
        out.alpha_composite(ch, (round(cx - ch.width / 2), round(y)))
    rb = _rank_badge(rank_label)
    out.alpha_composite(rb, (RANK_C[0] - rb.width // 2, RANK_C[1] - rb.height // 2 - 30))
    return out


def character_image(src) -> Image.Image | None:
    im = _load(src)
    if im is None:
        return None
    bb = im.getchannel("A").getbbox()
    return im.crop(bb) if bb else im


def compose_zip(character, *, rank_label: str, name: str, value: str, value_label: str = "", category: str = "",
                year: str = "", icon=None, top: bool = False, layers: dict | None = None) -> Image.Image:
    """순위 장면 그림 (1080×680, 투명 바깥). 1위는 배지·캐릭터를 TOP_SCALE 배."""
    k = TOP_SCALE if top else 1.0
    out = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    ch = character_image(character)
    char_layer = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    if ch is not None:
        bx0, by0, bx1, by1 = CHAR_BOX
        bw, bh = (bx1 - bx0) * k, (by1 - by0) * k
        s = min(bw / ch.width, bh / ch.height)
        ch = ch.resize((max(1, round(ch.width * s)), max(1, round(ch.height * s))), Image.LANCZOS)
        cx = (bx0 + bx1) / 2 - (k - 1) * 60          # 1위: 커진 만큼 살짝 안쪽으로
        char_layer.alpha_composite(ch, (round(cx - ch.width / 2), max(0, round(by1 + (k - 1) * 40 - ch.height))))
    b = badge(rank_label=rank_label, name=name, value=value, value_label=value_label, category=category,
              year=year, icon=icon)
    if k != 1.0:  # 배지: 배지판 왼쪽 아래를 기준으로 키움
        bb = b.resize((round(SIZE[0] * k), round(SIZE[1] * k)), Image.LANCZOS)
        ax, ay = PANEL[0], PANEL[3]
        b = Image.new("RGBA", SIZE, (0, 0, 0, 0))
        b.alpha_composite(bb.crop((round(ax * k - ax) + 20, round(ay * k - ay) - 10,
                                   round(ax * k - ax) + 20 + SIZE[0], round(ay * k - ay) - 10 + SIZE[1])))
    out.alpha_composite(char_layer)
    out.alpha_composite(b)
    if layers is not None:
        layers.update(art=char_layer, text=b)
    return out
