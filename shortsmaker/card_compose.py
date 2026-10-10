"""캐리커처 + 순위·기록·연도·팀 이름·선수 이름 → 완성 카드 PNG (참고 영상 카드 비율 941×1672, 투명 배경).

구단 로고는 넣지 않는다. 팀은 팀 색(붓질 배경·이름 띠)과 이름 글자로만 나타낸다.
Mock 캐리커처로 만든 카드에는 'MOCK 시험용' 표시를 박아 실제 카드와 섞이지 않게 한다.
"""
from __future__ import annotations

import math
import random
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from .models import FONTS_DIR
from .rank_extract import team_colors
from .scenes import _font, _hex

W, H = 941, 1672
FONT_BLACK = FONTS_DIR / "Pretendard-Black.otf"
FONT_EXTRA = FONTS_DIR / "Pretendard-ExtraBold.otf"
GOLD = ("#FFF27A", "#F7B500", "#C86A00")
SILVER = ("#FFFFFF", "#E9EDF2", "#9AA3AE")


def _gradient(size: tuple[int, int], stops: tuple[str, ...]) -> Image.Image:
    w, h = size
    g = Image.new("RGBA", (1, h))
    cols = [_hex(c) for c in stops]
    for y in range(h):
        p = y / max(1, h - 1) * (len(cols) - 1)
        i = min(int(p), len(cols) - 2)
        f = p - i
        g.putpixel((0, y), tuple(round(cols[i][k] + (cols[i + 1][k] - cols[i][k]) * f) for k in range(3)) + (255,))
    return g.resize((w, h))


def styled_text(text: str, size: int, fill: tuple[str, ...] | str, *, font_path: Path = FONT_BLACK,
                stroke: str = "#111111", stroke_w: int = 10, outer: str | None = None, outer_w: int = 0,
                skew: float = 0.16, shadow: int = 10) -> Image.Image:
    """굵은 기울임 글자 (그라데이션 채움 + 테두리 + 바깥 테두리 + 그림자). 글자에 맞게 잘린 RGBA."""
    font = _font(str(font_path), size)
    pad = stroke_w + outer_w + shadow + 20
    bbox = font.getbbox(text)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    cw, ch = tw + 2 * pad + int(th * skew), th + 2 * pad
    origin = (pad - bbox[0], pad - bbox[1])

    def mask(width: int) -> Image.Image:
        m = Image.new("L", (cw, ch), 0)
        ImageDraw.Draw(m).text(origin, text, font=font, fill=255, stroke_width=width, stroke_fill=255)
        return m

    layer = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    if shadow:
        sh = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        sh.putalpha(mask(stroke_w + outer_w).point(lambda v: v * 0.55))
        sh = sh.filter(ImageFilter.GaussianBlur(shadow / 2))
        layer.alpha_composite(sh, (shadow // 2, shadow))
    if outer and outer_w:
        layer.paste(Image.new("RGBA", (cw, ch), _hex(outer) + (255,)), (0, 0), mask(stroke_w + outer_w))
    layer.paste(Image.new("RGBA", (cw, ch), _hex(stroke) + (255,)), (0, 0), mask(stroke_w))
    body = _gradient((cw, ch), fill if isinstance(fill, tuple) else (fill, fill))
    layer.paste(body, (0, 0), mask(0))
    if skew:  # 오른쪽으로 기울임
        layer = layer.transform(layer.size, Image.AFFINE, (1, skew, -skew * ch * 0.5, 0, 1, 0), Image.BICUBIC)
    return layer.crop(layer.getbbox())


def brush_band(size: tuple[int, int], color: str, *, seed: int, angle: float = -8.0, thickness: float = 0.42,
               roughness: int = 46) -> Image.Image:
    """붓으로 쓱 칠한 듯한 거친 띠 (구단 로고 대신 팀 색을 보여주는 배경)."""
    w, h = size
    rnd = random.Random(seed)
    band = Image.new("L", size, 0)
    d = ImageDraw.Draw(band)
    top, bottom = [], []
    steps = 28
    for i in range(steps + 1):
        x = -40 + (w + 80) * i / steps
        mid = h / 2 + math.tan(math.radians(angle)) * (x - w / 2)
        half = h * thickness / 2 * (0.75 + 0.5 * math.sin(i / steps * math.pi))
        top.append((x, mid - half + rnd.uniform(-roughness, roughness * 0.4)))
        bottom.append((x, mid + half + rnd.uniform(-roughness * 0.4, roughness)))
    d.polygon(top + bottom[::-1], fill=255)
    for _ in range(140):  # 붓끝 튄 자국
        x, y = rnd.uniform(0, w), rnd.uniform(0, h)
        if band.getpixel((int(min(x, w - 1)), int(min(y, h - 1)))) == 0 and rnd.random() < 0.35:
            r = rnd.uniform(2, 9)
            d.ellipse([x - r, y - r, x + r, y + r], fill=255)
        elif rnd.random() < 0.5:
            d.line([x, y, x + rnd.uniform(40, 160), y + rnd.uniform(-12, 12)], fill=0, width=rnd.randint(2, 6))
    band = band.filter(ImageFilter.GaussianBlur(1.2))
    out = Image.new("RGBA", size, _hex(color) + (0,))
    out.putalpha(band)
    return out


def _paste_center(canvas: Image.Image, img: Image.Image, cx: float, cy: float) -> None:
    canvas.alpha_composite(img, (round(cx - img.width / 2), round(cy - img.height / 2)))


def _fit_text(text: str, size: int, max_w: int, **kw) -> Image.Image:
    img = styled_text(text, size, **kw)
    while img.width > max_w and size > 24:
        size = int(size * 0.92)
        img = styled_text(text, size, **kw)
    return img


def compose_card(caricature: Path, *, rank: int, record: str, year: str, team: str, player: str,
                 mock: bool = False, tied: bool = False, rank_fill: tuple[str, ...] | None = None) -> Image.Image:
    """아래에서부터 쌓는다: 선수 이름 리본 → 팀 이름 띠 → 연도 → 기록 → 순위. 캐리커처는 그 위·뒤에 크게."""
    primary, secondary = team_colors(team)
    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    gold = tuple(rank_fill) if rank_fill else GOLD  # 영상마다 바꾸는 순위 강조색 (variation.ACCENTS)
    rank_img = _fit_text(f"{rank}위", 270, 540, fill=gold, stroke="#3B0A00", stroke_w=12, outer="#FFFFFF", outer_w=6)
    rec = (_fit_text(record, 190, W - 90, fill=SILVER, stroke="#111111", stroke_w=11, outer=primary, outer_w=7)
           if record else None)
    year = year.strip() if year and re.fullmatch(r"\s*\d{4}\s*", year) else ""  # 장면 설명 등 다른 글은 카드에 안 찍음
    yr = styled_text(year, 66, "#FFFFFF", stroke="#111111", stroke_w=6, skew=0.08, shadow=4) if year else None
    name = _fit_text(team or "팀", 104, W - 150, fill="#FFFFFF", stroke=secondary, stroke_w=9, skew=0.12, shadow=6)
    pl = (_fit_text(player, 58, W - 360, fill="#FFFFFF", stroke="#111111", stroke_w=5, skew=0.06, shadow=0)
          if player else None)

    # 세로 배치 (아래부터)
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
    band_top = y_rank + rank_img.height // 3

    # 1) 팀 색 붓질 배경 (순위·기록 뒤, 로고 대신)
    band_h = max(300, y_banner - band_top + 60)
    card.alpha_composite(brush_band((W, band_h + 60), secondary, seed=rank * 7 + 1, angle=-10, thickness=0.62),
                         (0, band_top - 30))
    card.alpha_composite(brush_band((W, band_h), primary, seed=rank * 7 + 2, angle=-7, thickness=0.55), (0, band_top))

    # 2) 캐리커처: 남은 위쪽 공간에 크게 (아래로 순위 글자 뒤까지 겹침)
    with Image.open(caricature) as im:
        art = ImageOps.exif_transpose(im).convert("RGBA")
    art = ImageOps.contain(art, (W - 20, min(1320, y_banner - 20)), Image.LANCZOS)
    card.alpha_composite(art, ((W - art.width) // 2, 10))

    # 3) 글자들
    card.alpha_composite(rank_img, (34, y_rank))
    if tied:  # 공동 순위: 순위 글자 위에 '공동'
        co = styled_text("공동", 96, gold, stroke="#3B0A00", stroke_w=8, outer="#FFFFFF", outer_w=5, shadow=6)
        card.alpha_composite(co, (40, max(0, y_rank - co.height + 24)))
    if rec:
        card.alpha_composite(rec, (56, y_rec))
    if yr:
        d = ImageDraw.Draw(card)
        cy = y_year + yr.height / 2
        for sx in (-1, 1):
            x0 = W / 2 + sx * (yr.width / 2 + 20)
            x1 = W / 2 + sx * (yr.width / 2 + 130)
            d.line([x0, cy, x1, cy], fill=(255, 255, 255, 255), width=8)
            d.line([x0, cy + 5, x1, cy + 5], fill=(17, 17, 17, 255), width=3)
        _paste_center(card, yr, W / 2, cy)

    banner = Image.new("RGBA", (W - 60, bh), (0, 0, 0, 0))
    ImageDraw.Draw(banner).polygon([(30, 0), (W - 60, 0), (W - 90, bh), (0, bh)], fill=_hex(primary) + (255,),
                                   outline=_hex(secondary) + (255,), width=6)
    card.alpha_composite(banner, (30, y_banner))
    _paste_center(card, name, W / 2, y_banner + bh / 2)
    if pl:
        rw = pl.width + 70
        ribbon = Image.new("RGBA", (rw, rh), (0, 0, 0, 0))
        ImageDraw.Draw(ribbon).polygon([(18, 0), (rw, 0), (rw - 18, rh), (0, rh)], fill=(17, 17, 17, 235))
        card.alpha_composite(ribbon, (round(W / 2 - rw / 2), y_player))
        _paste_center(card, pl, W / 2, y_player + rh / 2)

    if mock:
        d = ImageDraw.Draw(card)
        f = _font(str(FONT_EXTRA), 44)
        d.rounded_rectangle([16, 16, 330, 84], radius=14, fill=(220, 0, 0, 235))
        d.text((173, 50), "MOCK 시험용", font=f, fill="white", anchor="mm")
    return card


def save_card(card: Image.Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    card.save(path)
    return path
