"""클린 스타일 카드 (시안 A·B) — Pretendard 하나, 단색 글자 + 은은한 그림자, 장식 없음.

- 색: 채널 파랑(#0043C8) + 강조색 하나. 팀 색은 채도를 낮춰 배경 그라데이션에만
- 1위만 작은 포인트 하나 (A: 순위 글씨가 강조색, B: 강조색 작은 점)
- 카드 비율은 941×1162 (화면 너비 75% 로 넣었을 때 아래 '@야구한장 구독'과 여백이 남게)

A 'soft'  : 부드러운 팀 색 그라데이션 판 + 아래 흰 정보판 (둥근 모서리)
B 'line'  : 흰 카드 + 얇은 테두리, 캐리커처 뒤 흐린 원, 큰 순위 숫자
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from .card_compose import FONT_BLACK
from .models import FONTS_DIR
from .rank_extract import team_colors
from .scenes import _font, _hex

Color = str | tuple[int, ...]
ImageSource = str | Path | Image.Image  # 파일 경로 또는 이미 연 그림

W, H = 941, 1162
BLUE = "#0043C8"
INK = "#14171C"
GRAY = "#6B7280"
FONT_BOLD = FONTS_DIR / "Pretendard-Bold.otf"
FONT_DISPLAY = FONTS_DIR / "GmarketSansTTFBold.ttf"  # 순위·기록 숫자 (G마켓 산스). 작은 글씨는 Pretendard
FONT_SEMI = FONTS_DIR / "Pretendard-SemiBold.otf"
LOOKS = {"clean_a": "클린 A (부드러운 판)", "clean_b": "클린 B (흰 카드·라인)"}


def _mix(a: str, b: str, f: float) -> tuple[int, int, int]:
    x, y = _hex(a), _hex(b)
    return tuple(round(x[i] + (y[i] - x[i]) * f) for i in range(3))


def muted(color: str, amount: float = 0.72) -> tuple[int, int, int]:
    """채도·명도를 낮춘 부드러운 팀 색 (회색빛 흰색과 섞음)."""
    return _mix(color, "#EEF1F5", amount)


def soft_text(text: str, size: int, color: Color, font: Path = FONT_BLACK, shadow: int = 10, alpha: float = 0.22
              ) -> Image.Image:
    """단색 글자 + 은은한 그림자 (테두리 없음)."""
    f = _font(str(font), size)
    bb = f.getbbox(text)
    pad = shadow * 3 + 4
    w, h = bb[2] - bb[0] + 2 * pad, bb[3] - bb[1] + 2 * pad
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    org = (pad - bb[0], pad - bb[1])
    if shadow:
        m = Image.new("L", (w, h), 0)
        ImageDraw.Draw(m).text((org[0], org[1] + shadow // 2), text, font=f, fill=round(255 * alpha))
        sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sh.putalpha(m.filter(ImageFilter.GaussianBlur(shadow / 2)))
        img.alpha_composite(sh)
    col = _hex(color) if isinstance(color, str) else tuple(color)
    ImageDraw.Draw(img).text(org, text, font=f, fill=col + (255,))
    return img.crop(img.getbbox())


def _fit(text: str, size: int, max_w: int, color: Color, font: Path = FONT_BLACK, **kw: Any) -> tuple[Image.Image, int]:
    im = soft_text(text, size, color, font, **kw)
    while im.width > max_w and size > 24:
        size = int(size * 0.92)
        im = soft_text(text, size, color, font, **kw)
    return im, size


def _vgrad(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    w, h = size
    t = np.linspace(0, 1, h)[:, None, None]
    rgb = np.broadcast_to(np.array(top) + (np.array(bottom) - np.array(top)) * t, (h, w, 3))
    return Image.fromarray(np.concatenate([rgb, np.full((h, w, 1), 255)], axis=2).astype(np.uint8), "RGBA")


def _rounded_mask(size: tuple[int, int], r: int) -> Image.Image:
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=r, fill=255)
    return m


def _drop_shadow(card: Image.Image, mask: Image.Image, blur: int = 22, alpha: float = 0.16) -> None:
    sh = Image.new("RGBA", card.size, (0, 0, 0, 0))
    sh.putalpha(mask.point(lambda v: round(v * alpha)).filter(ImageFilter.GaussianBlur(blur)))
    card.alpha_composite(sh, (0, 0))


def _art(caricature: ImageSource | None, box: tuple[int, int]) -> Image.Image | None:
    if caricature is None:
        return None
    if isinstance(caricature, Image.Image):
        art = caricature.convert("RGBA")
    else:
        with Image.open(caricature) as im:
            art = ImageOps.exif_transpose(im).convert("RGBA")
    art = art.crop(art.getbbox() or (0, 0, *art.size))
    return ImageOps.contain(art, box, Image.LANCZOS)


def _load(img: ImageSource | None) -> Image.Image | None:
    if img is None:
        return None
    if isinstance(img, Image.Image):
        return img
    with Image.open(img) as im:
        return ImageOps.exif_transpose(im).copy()


def _opaque(img: ImageSource | None) -> bool:
    im = _load(img)
    if im is None:
        return False
    if im.mode not in ("RGBA", "LA", "P"):
        return True
    return im.convert("RGBA").getchannel("A").getextrema()[0] >= 250


def _cover(img: ImageSource, size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(_load(img).convert("RGBA"), size, Image.LANCZOS)


def question_mark(size: int = 520) -> Image.Image:
    """보너스 카드 그림 자리: 연한 회색 물음표 (장식 없이)."""
    return soft_text("?", size, (197, 204, 214), FONT_BLACK, shadow=12, alpha=0.12)


def record_image(text: str, size: int) -> Image.Image:
    """카운트업 숫자 (카드 기록과 같은 G마켓 산스)."""
    return soft_text(text, size, INK, FONT_DISPLAY)


def compose_clean(caricature: Path | Image.Image | None, look: str, *, rank: int, record: str, year: str, team: str,
                  player: str, accent: str = "#F26A00", top: bool | None = None, tied: bool = False,
                  rank_label: str | None = None, tag: str = "", omit_record: bool = False, meta: dict | None = None,
                  layers: dict | None = None, main: str = BLUE) -> Image.Image:
    """클린 카드 (941×1162, 투명 바깥). tag: 작은 회색 라벨 (보너스 '번외').
    main: 채널 색 (야구한장 파랑, 하늘 배지형은 템플릿 main_color). year 는 '2014' 또는 '2024년 기준'."""
    top = rank == 1 if top is None else top
    primary, _ = team_colors(team)
    margin = 34                                  # 그림자 자리
    cw, ch = W - 2 * margin, H - 2 * margin
    L = {n: Image.new("RGBA", (W, H), (0, 0, 0, 0)) for n in ("back", "art", "front", "text")}
    mask = Image.new("L", (W, H), 0)
    mask.paste(_rounded_mask((cw, ch), 44), (margin, margin))
    _drop_shadow(L["back"], mask)
    label = rank_label or (("공동 " if tied else "") + f"{rank}위")
    sub = " · ".join(x for x in (team, year if re.fullmatch(r"\d{4}|\d{4}년 기준", year or "") else "") if x)

    if look == "clean_a":
        panel_h = 360
        art_h = ch - panel_h
        panel = Image.new("RGBA", (cw, ch), (255, 255, 255, 255))  # 위: 부드러운 팀 색 그라데이션, 아래: 흰 정보판
        panel.alpha_composite(_vgrad((cw, art_h), muted(primary, 0.80), muted(primary, 0.62)))
        panel.putalpha(_rounded_mask((cw, ch), 44))
        L["back"].alpha_composite(panel, (margin, margin))
        if _opaque(caricature):  # 배경 있는 그림(나라 카드 등): 위 칸을 꽉 채움 (모서리 둥글게)
            cover = _cover(caricature, (cw, art_h))
            m = Image.new("L", (cw, art_h), 0)
            ImageDraw.Draw(m).rounded_rectangle([0, 0, cw - 1, art_h + 60], radius=44, fill=255)
            cover.putalpha(m)
            L["art"].alpha_composite(cover, (margin, margin))
        else:
            art = _art(caricature, (cw - 40, art_h - 20))
            if art is not None:
                L["art"].alpha_composite(art, (margin + (cw - art.width) // 2, margin + art_h - art.height))
        x0, y0 = margin + 48, margin + art_h + 34
        # 순위와 기록을 비슷한 크기로 한 줄에 (폰에서 한눈에). 1위만 순위 글씨가 강조색
        size, avail = 132, cw - 96 - 44
        while True:
            rk = soft_text(label, size, accent if top else main, FONT_DISPLAY)
            rec = soft_text(record, size, INK, FONT_DISPLAY) if record else None
            if rk.width + (rec.width if rec is not None else 0) <= avail or size <= 48:
                break
            size = int(size * 0.94)
        L["text"].alpha_composite(rk, (x0, y0))
        rsize = size
        if rec is not None:
            rx, ry = margin + cw - 48 - rec.width, y0 + rk.height - rec.height
            if not omit_record:
                L["text"].alpha_composite(rec, (rx, ry))
            if meta is not None:
                meta.update(record=record, size=rsize, x=rx, y=ry, h=rec.height, team=team, design=look, rank=rank,
                            top=top, align="right", right=rx + rec.width)
        ny = y0 + rk.height + 30
        nm, _ = _fit(player or team, 64, cw - 96, INK, FONT_BOLD, shadow=0)
        L["text"].alpha_composite(nm, (x0, ny))
        if sub:
            sb, _ = _fit(sub, 40, cw - 96, GRAY, FONT_SEMI, shadow=0)
            L["text"].alpha_composite(sb, (x0, ny + nm.height + 16))
    else:  # clean_b
        card_bg = Image.new("RGBA", (cw, ch), (255, 255, 255, 255))
        card_bg.putalpha(_rounded_mask((cw, ch), 44))
        L["back"].alpha_composite(card_bg, (margin, margin))
        d = ImageDraw.Draw(L["back"])
        cx, cy, r = W / 2, margin + ch * 0.40, cw * 0.40
        blob = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(blob).ellipse([cx - r, cy - r, cx + r, cy + r], fill=muted(primary, 0.70) + (255,))
        L["back"].alpha_composite(blob.filter(ImageFilter.GaussianBlur(2)))
        d.rounded_rectangle([margin, margin, margin + cw - 1, margin + ch - 1], radius=44, outline=(222, 226, 232, 255),
                            width=3)
        art = _art(caricature, (cw - 80, int(ch * 0.68)))
        if art is not None:
            L["art"].alpha_composite(art, (margin + (cw - art.width) // 2, margin + int(ch * 0.74) - art.height))
        x0 = margin + 48
        num = re.match(r"^(공동\s*)?(\d+)(위.*)$", label)
        big = soft_text(num.group(2) if num else label, 220, main, FONT_DISPLAY, shadow=10, alpha=0.18)
        L["text"].alpha_composite(big, (x0 - 6, margin + 30))
        if num:
            small = soft_text((num.group(1) or "") + num.group(3).strip(), 56, main, FONT_BOLD, shadow=0)
            L["text"].alpha_composite(small, (x0 + big.width + 8, margin + 30 + big.height - small.height - 10))
        if top:  # 1위만: 강조색 작은 점
            ImageDraw.Draw(L["text"]).ellipse([margin + cw - 70, margin + 50, margin + cw - 46, margin + 74],
                                              fill=_hex(accent) + (255,))
        y_info = margin + int(ch * 0.76)
        rec, rsize = _fit(record, 140, cw - 96, INK, FONT_DISPLAY) if record else (None, 0)
        if rec is not None:
            if not omit_record:
                L["text"].alpha_composite(rec, (x0, y_info))
            if meta is not None:
                meta.update(record=record, size=rsize, x=x0, y=y_info, h=rec.height, team=team, design=look,
                            rank=rank, top=top, align="left")
        line = f"{player} · {sub}" if player and sub else (player or sub)
        if line:
            ln, _ = _fit(line, 46, cw - 96, GRAY, FONT_SEMI, shadow=0)
            L["text"].alpha_composite(ln, (x0, y_info + (rec.height if rec is not None else 0) + 22))
    if tag:
        tg = soft_text(tag, 40, GRAY, FONT_BOLD, shadow=0)
        pill = Image.new("RGBA", (tg.width + 44, tg.height + 26), (0, 0, 0, 0))
        ImageDraw.Draw(pill).rounded_rectangle([0, 0, pill.width - 1, pill.height - 1], radius=pill.height // 2,
                                               fill=(243, 244, 246, 255), outline=(222, 226, 232, 255), width=2)
        pill.alpha_composite(tg, (22, 13))
        L["text"].alpha_composite(pill, (margin + cw - pill.width - 40, margin + 40))
    if layers is not None:
        layers.update({n: im.copy() for n, im in L.items()})
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for n in ("back", "art", "front", "text"):
        out.alpha_composite(L[n])
    return out


def clean_bonus(look: str, *, rank: int | None, name: str, team: str, value: str, year: str, text: str = "",
                kind: str = "rank", accent: str = "#F26A00", layers: dict | None = None,
                main: str = BLUE) -> Image.Image:
    """보너스 '?' 카드 (클린): 같은 카드 + 회색 물음표 + '번외' 라벨 (도장·기울기 없음)."""
    if kind == "teaser":
        return compose_clean(question_mark(), look, rank=rank or 6, record="", year="", team=team, player=text,
                             rank_label="다음 편", tag="예고", top=False, accent=accent, layers=layers, main=main)
    return compose_clean(question_mark(), look, rank=rank or 6, record=value, year=year, team=team, player=name,
                         rank_label=f"{rank}위?", tag="번외", top=False, accent=accent, layers=layers, main=main)
