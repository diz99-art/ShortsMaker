"""카드 모드(yagu_v2) 레이어 그리기 — 정적 화면(chrome), 움직이는 카드·로고, 타자 효과 자막, 빨간 기록 줄, 제목 카드.

프레임 순서·시간 계산은 card_mode.CardRenderer 가 하고, 여기서는 주어진 상태의 그림만 만든다.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .models import Box, CardLayout, Motion, Project, StatLine, Template, TypingSubtitle
from .scenes import _font, _hex, _wrap

BICUBIC, LANCZOS = Image.Resampling.BICUBIC, Image.Resampling.LANCZOS  # 리샘플링 필터 (줄 길이를 줄이려고 짧은 이름)

Color = str | tuple[int, ...]  # Pillow 가 받는 색 ('#RRGGBB' 또는 RGB(A) 튜플)

# ---------------------------------------------------------------- 정적 레이어

def _rounded(d: ImageDraw.ImageDraw, box: Box, fill: Color, outline: Color | None = None, width: int = 0,
             radius: float | None = None) -> None:
    r = box.h / 2 if radius is None else radius
    d.rounded_rectangle([box.x, box.y, box.x + box.w - 1, box.y + box.h - 1], radius=r,
                        fill=fill, outline=outline, width=width)


def _seam_icon(d: ImageDraw.ImageDraw, x: float, cy: float, h: float, color: Color) -> float:
    """야구공 실밥 ')(' 모양 아이콘 — 서로 등을 맞댄 두 곡선. 그린 너비를 돌려준다."""
    lw = max(3, round(h * 0.16))
    rx, ry = h * 0.75, h * 0.62
    peak1, peak2 = x + h * 0.2, x + h * 0.62          # ')' 의 오른쪽 끝, '(' 의 왼쪽 끝
    d.arc([peak1 - 2 * rx, cy - ry, peak1, cy + ry], start=-48, end=48, fill=color, width=lw)
    d.arc([peak2, cy - ry, peak2 + 2 * rx, cy + ry], start=132, end=228, fill=color, width=lw)
    return h * 0.85


def _bell(d: ImageDraw.ImageDraw, cx: float, cy: float, h: float, color: Color) -> None:
    """종 모양 아이콘."""
    w = h * 0.86
    top = cy - h / 2
    d.pieslice([cx - w / 2, top + h * 0.08, cx + w / 2, top + h * 0.08 + w * 1.05], 180, 360, fill=color)
    d.polygon([(cx - w / 2, top + h * 0.08 + w * 0.52), (cx + w / 2, top + h * 0.08 + w * 0.52),
               (cx + w * 0.62, top + h * 0.8), (cx - w * 0.62, top + h * 0.8)], fill=color)
    d.ellipse([cx - h * 0.1, top - h * 0.02, cx + h * 0.1, top + h * 0.16], fill=color)
    d.ellipse([cx - h * 0.14, top + h * 0.8, cx + h * 0.14, top + h], fill=color)


def _title_lines(text: str, c: CardLayout, font_path: Path) -> tuple[list[str], ImageFont.FreeTypeFont]:
    box = c.title
    size = box.size
    while True:
        font = _font(str(font_path), size)
        lines = text.split("\n") if "\n" in text else _wrap(text, font, box.w)
        if (len(lines) <= box.max_lines and all(font.getlength(l) <= box.w for l in lines)) or size <= box.min_size:
            return lines[: box.max_lines], font
        size -= 2


def render_chrome(t: Template, project: Project, project_dir: Path | None = None) -> tuple[Image.Image, Image.Image]:
    """(바닥 레이어 RGB: 배경+아래 버튼, 머리 레이어 RGBA: 알약 로고+제목+구분선 — 구분선 아래는 투명).
    하늘 배지형: 배경 그림, 그림 로고(brand_logo), 제목 둘째 줄 끝 아이콘(project.title_icon)."""
    c = t.card_layout
    W, H = t.width, t.height
    bg_p = t.asset_path(c.background_image) if c.background_image else None
    if bg_p is not None:
        with Image.open(bg_p) as im:
            base = im.convert("RGB").resize((W, H), LANCZOS)
    else:
        base = Image.new("RGB", (W, H), _hex(c.background_color))
    d = ImageDraw.Draw(base)

    # 아래: @야구한장 | 🔔구독
    f = c.footer
    hf = _font(str(t.font_path(f.handle.font)), f.handle.size)
    d.text((f.handle.x, f.handle.y + f.handle.h / 2), f.handle_text, font=hf, fill=f.handle.color, anchor="lm")
    d.rectangle([f.separator.x, f.separator.y, f.separator.x + f.separator.w - 1,
                 f.separator.y + f.separator.h - 1], fill=_hex(f.separator_color))
    b = f.button
    _rounded(d, b, fill=_hex(b.fill))
    bf = _font(str(t.font_path(b.font)), b.size)
    icon_h = b.h * 0.5
    text_w = bf.getlength(b.text)
    gap = b.h * 0.14
    total = icon_h * 0.86 + gap + text_w
    x0 = b.x + (b.w - total) / 2
    _bell(d, x0 + icon_h * 0.43, b.y + b.h / 2, icon_h, _hex(b.text_color))
    d.text((x0 + icon_h * 0.86 + gap, b.y + b.h / 2), b.text, font=bf, fill=_hex(b.text_color), anchor="lm")

    # 위: 알약 로고 + 제목 + 구분선 (별도 RGBA 레이어)
    head = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    hd = ImageDraw.Draw(head)
    if bg_p is not None:  # 구분선 위도 같은 배경 그림
        head.paste(base.crop((0, 0, W, c.content_top)).convert("RGBA"), (0, 0))
    else:
        hd.rectangle([0, 0, W, c.content_top - 1], fill=_hex(c.background_color) + (255,))
    p = c.pill
    logo_p = t.brand_asset(c.brand_logo) if c.brand_logo else None
    if logo_p is not None:  # 그림 로고 (알약 대신), 자리 안에 비율 유지·왼쪽 정렬
        lb = c.brand_logo_box or p
        with Image.open(logo_p) as im:
            logo = im.convert("RGBA")
        sc_ = min(lb.w / logo.width, lb.h / logo.height)
        logo = logo.resize((max(1, round(logo.width * sc_)), max(1, round(logo.height * sc_))), LANCZOS)
        head.alpha_composite(logo, (lb.x, lb.y + (lb.h - logo.height) // 2))
    else:
        _rounded(hd, p, fill=_hex(p.fill), outline=_hex(p.border_color), width=p.border_width)
    pf = _font(str(t.font_path(p.font)), p.size)
    tx = p.x + p.h * 0.32
    if logo_p is None:
        hd.text((tx, p.y + p.h / 2), p.text, font=pf, fill=_hex(p.text_color), anchor="lm")
    ix = tx + pf.getlength(p.text) + p.h * 0.22
    if logo_p is not None:
        pass
    elif p.icon == "seam":
        _seam_icon(hd, ix, p.y + p.h / 2, p.h * 0.56, _hex(p.icon_color))
    elif p.icon == "dot":
        r_ = p.h * 0.13
        hd.ellipse([ix, p.y + p.h / 2 - r_, ix + 2 * r_, p.y + p.h / 2 + r_], fill=_hex(p.icon_color))

    box = c.title
    icon = None
    if project.title_icon and project_dir is not None and (project_dir / project.title_icon).is_file():
        with Image.open(project_dir / project.title_icon) as im:
            icon = im.convert("RGBA")
    lines, tfont = _title_lines(project.title, c, t.font_path(box.font))
    pitch = tfont.size * c.title_line_pitch
    block = pitch * (len(lines) - 1) + tfont.size
    y = box.y + (box.h - block) / 2
    for k, line in enumerate(lines):
        if icon is not None and k == len(lines) - 1:  # 마지막 줄 끝 아이콘: 글자+아이콘을 함께 가운데 정렬
            ih = round(tfont.size * 1.0)
            ic = icon.resize((max(1, round(icon.width * ih / icon.height)), ih), LANCZOS)
            gap = round(tfont.size * 0.25)
            tw = tfont.getlength(line)
            x0 = box.x + (box.w - (tw + gap + ic.width)) / 2
            hd.text((x0, y), line, font=tfont, fill=_hex(box.color), anchor="lt")
            head.alpha_composite(ic, (round(x0 + tw + gap), round(y + tfont.size * 0.05)))
        else:
            hd.text((box.x + box.w / 2, y), line, font=tfont, fill=_hex(box.color), anchor="mt")
        y += pitch
    dv = c.divider
    hd.rectangle([0, dv.y, W - 1, dv.y + dv.h - 1], fill=_hex(dv.color) + (255,))
    return base, head


# ---------------------------------------------------------------- 움직이는 레이어

@lru_cache(maxsize=16)
def _load_rgba(path: str) -> Image.Image:
    return Image.open(path).convert("RGBA")


def fit_size(img_size: tuple[int, int], box: Box) -> tuple[float, float]:
    iw, ih = img_size
    s = min(box.w / iw, box.h / ih)
    return iw * s, ih * s


def _interp(curve: list[tuple[float, float]], t: float) -> float:
    if t <= curve[0][0]:
        return curve[0][1]
    for (t0, v0), (t1, v1) in zip(curve, curve[1:]):
        if t <= t1:
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return curve[-1][1]


class MovingLayer:
    """박스 가운데를 중심으로 배율 s, 가로 이동 dx 로 그린다. 모션 블러는 셔터 동안의 위치·배율을 평균."""

    def __init__(self, path: Path | Image.Image, box: Box, motion: Motion, fps: int, top: int, canvas: tuple[int, int],
                 dx: list[tuple[float, float]] | None = None):
        self.src = path.convert("RGBA") if isinstance(path, Image.Image) else _load_rgba(str(path))
        self.box, self.motion, self.fps, self.top, self.canvas, self.dx = box, motion, fps, top, canvas, dx
        self.w, self.h = fit_size(self.src.size, box)
        self.cx, self.cy = box.x + box.w / 2, box.y + box.h / 2
        max_s = max(s for _, s in motion.curve)
        # 가장 큰 배율에서도 선명하도록 원본 해상도에서 바로 줄인다
        self._big = self.src if self.src.width >= self.w * max_s else self.src.resize(
            (round(self.w * max_s), round(self.h * max_s)), LANCZOS)

    def variant(self, img: Image.Image) -> MovingLayer:
        """같은 자리·움직임에 그림만 바꾼 레이어 (카운트업 숫자)."""
        return MovingLayer(img, self.box, self.motion, self.fps, self.top, self.canvas, dx=self.dx)

    def _place(self, s: float, dx: float = 0.0, dy: float = 0.0, angle: float = 0.0) -> tuple[Image.Image, int, int]:
        w, h = max(1, round(self.w * s)), max(1, round(self.h * s))
        img = self._big.resize((w, h), BICUBIC if s > 1.01 else LANCZOS)
        if abs(angle) > 0.05:
            img = img.rotate(angle, resample=BICUBIC, expand=True)
        return img, round(self.cx - img.width / 2 + dx), round(self.cy - img.height / 2 + dy)

    def _dx(self, t: float | None) -> float:
        if t is None or not self.dx:
            return 0.0
        if t < 0:  # 등장 직전: 첫 구간 기울기로 연장 (블러용)
            (t0, v0), (t1, v1) = self.dx[0], self.dx[1]
            return v0 + (v1 - v0) / (t1 - t0) * t
        return _interp(self.dx, t)

    @staticmethod
    def _paste(layer: Image.Image, img: Image.Image, x: int, y: int) -> None:
        W, H = layer.size
        if x >= W or y >= H or x + img.width <= 0 or y + img.height <= 0:
            return
        layer.alpha_composite(img, (max(0, x), max(0, y)), (max(0, -x), max(0, -y)))

    def draw(self, frame: Image.Image, t_anim: float | None, idle: tuple[float, float, float, float] | None = None
             ) -> None:
        """t_anim: 등장 후 경과 초 (None 이면 정지 상태). idle: 정지 상태에 더할 (배율, dx, dy, 각도).
        구분선 위(top)로는 그리지 않는다."""
        m = self.motion
        gap = m.gap_frames / self.fps
        if t_anim is not None and t_anim < gap - 1e-9:
            return  # 이전 카드가 사라지고 새 카드가 들어오기 직전의 빈 프레임
        ta = None if t_anim is None else t_anim - gap
        s = 1.0 if ta is None else m.scale_at(ta)
        dx = self._dx(ta)
        layer = Image.new("RGBA", self.canvas, (0, 0, 0, 0))
        if ta is not None and ta < m.blur and m.blur_samples > 1:
            # 셔터가 열린 동안의 배율·위치 변화를 평균 → 확대/이동 방향 모션 블러
            back = m.shutter / self.fps
            prev_s, prev_dx = m.scale_ext(ta - back), self._dx(ta - back)
            acc: np.ndarray | None = None
            for k in range(m.blur_samples):
                f = (k + 1) / m.blur_samples
                img, x, y = self._place(prev_s + (s - prev_s) * f, prev_dx + (dx - prev_dx) * f)
                tmp = Image.new("RGBA", self.canvas, (0, 0, 0, 0))
                self._paste(tmp, img, x, y)
                a = np.asarray(tmp, dtype=np.float32)
                a[..., :3] *= a[..., 3:4] / 255.0  # premultiplied 로 평균해야 가장자리가 검게 번지지 않음
                acc = a if acc is None else acc + a
            assert acc is not None  # blur_samples > 1 이라 반복이 한 번 이상 돈다
            acc /= m.blur_samples
            alpha = acc[..., 3:4]
            rgb = np.where(alpha > 0, acc[..., :3] * 255.0 / np.maximum(alpha, 1e-6), 0)
            layer = Image.fromarray(np.concatenate([rgb, alpha], axis=2).clip(0, 255).astype(np.uint8), "RGBA")
        else:
            ks, idx_, idy, ang = idle or (1.0, 0.0, 0.0, 0.0)
            img, x, y = self._place(s * ks, dx + idx_, idy, ang)
            self._paste(layer, img, x, y)
        if self.top > 0:
            layer.paste((0, 0, 0, 0), (0, 0, self.canvas[0], self.top))
        frame.alpha_composite(layer)


# ---------------------------------------------------------------- 타자 효과 자막

@dataclass
class SubtitleLayout:
    font: ImageFont.FreeTypeFont
    lines: list[tuple[str, int, float, float]]  # (줄 글자, 줄 첫 글자의 원문 위치, x 왼쪽, y 위)
    overflow: bool = False


def layout_subtitle(text: str, st: TypingSubtitle, font_path: Path, center_y: float,
                    align: str = "center") -> SubtitleLayout:
    """최종 문장 기준으로 줄을 나누고 위치를 고정한다 (글자가 늘어나도 자리가 움직이지 않게)."""
    for box_w in [st.w] + ([st.wide_w] if st.wide_w and st.wide_w > st.w else []):
        width = box_w - 2 * st.stroke_width
        size = st.size
        while True:
            font = _font(str(font_path), size)
            wrapped = _wrap(text, font, width)
            if len(wrapped) <= st.max_lines or size <= st.min_size:
                break
            size -= 2
        if len(wrapped) <= st.max_lines:
            break
    overflow = len(wrapped) > st.max_lines
    pitch = size * st.line_pitch
    block = pitch * (len(wrapped) - 1) + size
    y = center_y - block / 2
    out = []
    pos = 0
    for line in wrapped:
        start = text.find(line, pos) if line else pos
        start = pos if start < 0 else start
        lw = font.getlength(line)
        x = st.x + st.stroke_width if align == "left" else st.x + st.w / 2 - lw / 2
        out.append((line, start, x, y))
        pos = start + len(line)
        y += pitch
    return SubtitleLayout(font, out, overflow)


def render_subtitle(layout: SubtitleLayout, visible_chars: int, st: TypingSubtitle, canvas: tuple[int, int],
                    *, alpha: float = 1.0, dy: float = 0.0) -> Image.Image:
    """원문 앞쪽 visible_chars 글자까지만 그린 RGBA 레이어 (그림자 → 검은 테두리 → 흰 글씨).
    alpha·dy: 줄 단위로 떠오르는 자막용 (투명도, 아래로 내린 거리)."""
    layer = Image.new("RGBA", canvas, (0, 0, 0, 0))
    parts = []
    for line, start, x, y in layout.lines:
        n = max(0, min(len(line), visible_chars - start))
        if n:
            parts.append((line[:n], x, y + dy))
    if not parts:
        return layer
    font = layout.font
    sx, sy = st.shadow_offset
    shadow = Image.new("RGBA", canvas, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sh_a = round(255 * st.shadow_opacity)  # (예전에는 alpha 를 덮어써서 줄 떠오르기 투명도가 안 먹었음)
    for s, x, y in parts:
        sd.text((x + sx, y + sy), s, font=font, fill=_hex(st.shadow_color) + (sh_a,), anchor="la",
                stroke_width=st.stroke_width, stroke_fill=_hex(st.shadow_color) + (sh_a,))
    if st.shadow_blur > 0:
        shadow = shadow.filter(ImageFilter.GaussianBlur(st.shadow_blur))
    layer.alpha_composite(shadow)
    d = ImageDraw.Draw(layer)
    for s, x, y in parts:
        d.text((x, y), s, font=font, fill=_hex(st.color), anchor="la",
               stroke_width=st.stroke_width, stroke_fill=_hex(st.stroke_color))
    if alpha < 1.0:
        layer.putalpha(layer.getchannel("A").point(lambda v: round(v * max(0.0, alpha))))
    return layer


# ---------------------------------------------------------------- 빨간 기록 줄 (스티커형)

def layout_stat(text: str, sl: StatLine, font_path: Path) -> SubtitleLayout:
    """'\n' 으로 나눈 줄(없으면 폭에 맞춰 자동), 최대 max_lines, 가운데 정렬, center_y 기준."""
    size = sl.size
    while True:
        font = _font(str(font_path), size)
        lines = []
        for para in text.split("\n"):
            lines += _wrap(para, font, sl.w)
        if (len(lines) <= sl.max_lines and all(font.getlength(l) <= sl.w for l in lines)) or size <= sl.min_size:
            break
        size -= 2
    pitch = size * sl.line_pitch
    block = pitch * (len(lines) - 1) + size
    y = sl.center_y - block / 2
    out, pos = [], 0
    for line in lines:
        start = text.find(line, pos)
        start = pos if start < 0 else start
        out.append((line, start, sl.x + sl.w / 2 - font.getlength(line) / 2, y))
        pos = start + len(line)
        y += pitch
    return SubtitleLayout(font, out, len(lines) > sl.max_lines)


def render_stat(layout: SubtitleLayout, visible_chars: int, sl: StatLine, canvas: tuple[int, int],
                color: str | None = None) -> Image.Image:
    layer = Image.new("RGBA", canvas, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for line, start, x, y in layout.lines:
        n = max(0, min(len(line), visible_chars - start))
        if n:
            d.text((x, y), line[:n], font=layout.font, fill=_hex(color or sl.color), anchor="la",
                   stroke_width=sl.stroke_width, stroke_fill=_hex(sl.stroke_color))
    return layer


def stat_visible(text: str, sl: StatLine, since: float) -> int:
    """장면 시작 후 since 초에 보이는 글자 수 (원문 위치 기준, 줄바꿈 포함)."""
    if since < sl.delay:
        return 0
    return min(len(text), int((since - sl.delay) * sl.rate) + 1)


def render_title_card(t: Template, project: Project) -> Image.Image:
    """인트로 '제목 카드' 연출: 구분선 아래에 영상 제목을 크게 (제목 파랑 고정)."""
    c = t.card_layout
    layer = Image.new("RGBA", (t.width, t.height), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    lines = project.title.split("\n")
    size = 120
    while size > 50:
        font = _font(str(t.font_path("black")), size)
        if all(font.getlength(l) <= t.width - 120 for l in lines):
            break
        size -= 4
    pitch = size * 1.15
    block = pitch * (len(lines) - 1) + size
    top = (c.content_top + c.footer.button.y) / 2 - block / 2
    blue = _hex(c.title.color)
    d.rounded_rectangle([40, top - 70, t.width - 40, top + block + 70], radius=48,
                        fill=(240, 245, 255, 255), outline=blue + (255,), width=10)
    for i, l in enumerate(lines):
        d.text((t.width / 2, top + i * pitch), l, font=font, fill=_hex(c.title.color), anchor="mt",
               stroke_width=6, stroke_fill=(255, 255, 255))
    return layer
