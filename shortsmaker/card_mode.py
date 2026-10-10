"""카드 모드(yagu_v2) 렌더 — 순위 이미지가 정보가 다 들어간 완성 카드일 때.

classic 모드와 달리 장면 클립 + xfade 가 아니라, Pillow 로 프레임을 직접 그려 FFmpeg 로 인코딩한다.
확대 + 모션 블러로 '쾅' 들어오는 카드, 크게 시작해 제자리로 줄어드는 로고, 노래에 맞춰
한 글자씩 나타나는 자막을 프레임 단위로 정확히 맞추기 위해서다.

레이어 (아래 → 위)
  chrome : 흰 배경 + 아래쪽 '@야구한장 | 🔔구독' (움직이지 않음)
  moving : 인트로 로고 / 순위 카드 (구분선 아래로만 보임)
  header : 알약 로고 + 파란 2줄 제목 + 검은 구분선 (움직이는 레이어가 넘어와도 가려지지 않게 위에)
  sub    : 타자 효과 자막

장면 전환 시점은 items[].start 가 있으면 그 값을, 없으면 가사 자동 맞추기 결과에서
"5위", "4위"… 를 부르는 순간(단어 타이밍 안에서 글자 위치로 계산)을 쓴다.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .ffmpeg_builder import FINAL_ACODEC, FINAL_VCODEC, TO_TV_YUV420, FFmpegError, _f, ffmpeg_bin
from .models import MIN_SCENE, Box, CardLayout, LyricLine, Motion, Project, Template, TypingSubtitle
from .scenes import SceneBuildResult, SceneLayers, _font, _hex, _wrap
from .timeline import Scene, snap

ProgressFn = Callable[[float, str], None]


class CardModeError(ValueError):
    pass


def _norm(s: str) -> str:
    return re.sub(r"[\W_]+", "", s).lower()


# ---------------------------------------------------------------- 가사 → 글자별 시각

def char_times(line: LyricLine) -> list[float]:
    """줄의 글자마다 나타나는 시각. 문장 부호는 앞 글자와, 공백은 다음 글자와 함께 나타난다.

    단어 타이밍(words)이 있으면 그 안에서 글자를 나눈다: 화면 글자(글자·숫자만) k번째를
    단어들의 글자 수 누적 비율로 대응시켜 해당 단어 구간 안에서 보간한다.
    (화면 글자가 부른 가사와 조금 달라도 — 예: '케이비오'→'KBO' — 비율로 맞춘다.)
    단어 타이밍이 없으면 줄 구간을 글자 수로 고르게 나눈다.
    """
    chars = list(line.text)
    visible = [i for i, c in enumerate(chars) if _norm(c)]  # 글자·숫자만 센다 (단어 길이와 같은 기준)
    n = len(visible)
    times = [line.start] * len(chars)
    if n == 0:
        return times
    words = [(w.start, w.end, max(1, len(_norm(w.text)) or len(w.text.strip()))) for w in line.words
             if w.end > w.start]
    if words:
        total = sum(w[2] for w in words)
        for k, ci in enumerate(visible):
            p = k / n * total
            acc = 0
            for ws, we, wl in words:
                if p < acc + wl or (ws, we, wl) == words[-1]:
                    frac = min(max((p - acc) / wl, 0.0), 1.0)
                    times[ci] = ws + frac * (we - ws)
                    break
                acc += wl
    else:
        span = max(line.end - line.start, 1e-3)
        for k, ci in enumerate(visible):
            times[ci] = line.start + span * k / n
    # 문장 부호는 바로 앞 글자와 함께, 공백은 다음 글자와 함께 나타난다
    counted = set(visible)
    for i, ch in enumerate(chars):
        if i not in counted and not ch.isspace():
            times[i] = times[i - 1] if i else line.start
    nxt = None
    for i in range(len(chars) - 1, -1, -1):
        if chars[i].isspace():
            times[i] = nxt if nxt is not None else times[i - 1] if i else line.start
        else:
            nxt = times[i]
    # 단조 증가 보장
    for i in range(1, len(times)):
        times[i] = max(times[i], times[i - 1])
    return times


def rank_starts_from_lyrics(lines: list[LyricLine], ranks: list[int], word_format: str = "{rank}위"
                            ) -> dict[int, float]:
    """rank_times 의 순위별 사전판 (순위가 겹치지 않을 때)."""
    return dict(zip(ranks, rank_times(lines, ranks, word_format)))


def rank_times(lines: list[LyricLine], ranks: list[int], word_format: str = "{rank}위") -> list[float]:
    """가사에서 각 순위 낱말("5위")을 부르는 시각. ranks 는 화면 순서(5→1, 공동이면 2, 2 처럼 겹침),
    앞에서부터 차례로 찾는다. 같은 순위가 또 나오면 앞 사람 다음에 나오는 줄을 찾는다."""
    out: list[float] = []
    after = -1.0
    for rank in ranks:
        word = word_format.format(rank=rank)
        target = _norm(word)
        found = None
        for line in lines:
            if line.start < after - 1e-6:
                continue
            idx = _norm(line.text).find(target)
            if idx < 0:
                continue
            # 정규화 글자 위치 → 원문 글자 위치
            seen = -1
            for ci, ch in enumerate(line.text):
                if _norm(ch):
                    seen += 1
                    if seen == idx:
                        found = char_times(line)[ci]
                        break
            if found is not None and (found > after or (not out and found >= after)):
                break
            found = None
        if found is None:
            raise CardModeError(f"가사에서 '{word}' 를 부르는 줄을 찾지 못했습니다. "
                                "가사를 확인하거나 items[].start 로 장면 시작 시각을 직접 주세요")
        out.append(round(found, 3))
        after = found
    return out


# ---------------------------------------------------------------- 타임라인

def card_timeline(project: Project, t: Template, lines: list[LyricLine]) -> list[Scene]:
    """인트로 + 5위~1위. 아웃트로 없이 1위 카드가 끝까지 남는다 (참고 영상과 같음)."""
    items = project.ordered_items
    if project.has_scene_starts:
        starts = {it.rank: it.start for it in items}
    else:  # 가사에서 보이는 순위("공동 2위"면 2위를 두 번)를 부르는 순간
        times = rank_times(lines, [it.shown_rank for it in items], t.card.rank_word)
        starts = {it.rank: tm for it, tm in zip(items, times)}
    fps = t.fps
    bounds = [0.0] + [snap(starts[it.rank], fps) for it in items] + [snap(project.duration, fps)]
    labels = ["인트로"] + [project.rank_label(it) for it in items]
    for label, a, b in zip(labels, bounds, bounds[1:]):
        if b - a < MIN_SCENE:
            raise CardModeError(f"{label} 장면이 {b - a:.2f}초로 너무 짧습니다 (장면 시작 시각을 확인하세요)")
    scenes = []
    for i, (a, b) in enumerate(zip(bounds, bounds[1:])):
        scenes.append(Scene(index=i, kind="intro" if i == 0 else "rank", start=a, end=b,
                            clip_duration=b - a, item=items[i - 1] if i else None))
    return scenes


# ---------------------------------------------------------------- 정적 레이어

def _rounded(d: ImageDraw.ImageDraw, box: Box, fill, outline=None, width=0, radius=None) -> None:
    r = box.h / 2 if radius is None else radius
    d.rounded_rectangle([box.x, box.y, box.x + box.w - 1, box.y + box.h - 1], radius=r,
                        fill=fill, outline=outline, width=width)


def _seam_icon(d: ImageDraw.ImageDraw, x: float, cy: float, h: float, color) -> float:
    """야구공 실밥 ')(' 모양 아이콘 — 서로 등을 맞댄 두 곡선. 그린 너비를 돌려준다."""
    lw = max(3, round(h * 0.16))
    rx, ry = h * 0.75, h * 0.62
    peak1, peak2 = x + h * 0.2, x + h * 0.62          # ')' 의 오른쪽 끝, '(' 의 왼쪽 끝
    d.arc([peak1 - 2 * rx, cy - ry, peak1, cy + ry], start=-48, end=48, fill=color, width=lw)
    d.arc([peak2, cy - ry, peak2 + 2 * rx, cy + ry], start=132, end=228, fill=color, width=lw)
    return h * 0.85


def _bell(d: ImageDraw.ImageDraw, cx: float, cy: float, h: float, color) -> None:
    """종 모양 아이콘."""
    w = h * 0.86
    top = cy - h / 2
    d.pieslice([cx - w / 2, top + h * 0.08, cx + w / 2, top + h * 0.08 + w * 1.05], 180, 360, fill=color)
    d.polygon([(cx - w / 2, top + h * 0.08 + w * 0.52), (cx + w / 2, top + h * 0.08 + w * 0.52),
               (cx + w * 0.62, top + h * 0.8), (cx - w * 0.62, top + h * 0.8)], fill=color)
    d.ellipse([cx - h * 0.1, top - h * 0.02, cx + h * 0.1, top + h * 0.16], fill=color)
    d.ellipse([cx - h * 0.14, top + h * 0.8, cx + h * 0.14, top + h], fill=color)


def _title_lines(text: str, c: CardLayout, font_path: Path) -> tuple[list[str], object]:
    box = c.title
    size = box.size
    while True:
        font = _font(str(font_path), size)
        lines = text.split("\n") if "\n" in text else _wrap(text, font, box.w)
        if (len(lines) <= box.max_lines and all(font.getlength(l) <= box.w for l in lines)) or size <= box.min_size:
            return lines[: box.max_lines], font
        size -= 2


def render_chrome(t: Template, project: Project) -> tuple[Image.Image, Image.Image]:
    """(바닥 레이어 RGB: 배경+아래 버튼, 머리 레이어 RGBA: 알약 로고+제목+구분선 — 구분선 아래는 투명)."""
    c = t.card
    W, H = t.width, t.height
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
    hd.rectangle([0, 0, W, c.content_top - 1], fill=_hex(c.background_color) + (255,))
    p = c.pill
    _rounded(hd, p, fill=_hex(p.fill), outline=_hex(p.border_color), width=p.border_width)
    pf = _font(str(t.font_path(p.font)), p.size)
    tx = p.x + p.h * 0.32
    hd.text((tx, p.y + p.h / 2), p.text, font=pf, fill=_hex(p.text_color), anchor="lm")
    _seam_icon(hd, tx + pf.getlength(p.text) + p.h * 0.22, p.y + p.h / 2, p.h * 0.56, _hex(p.icon_color))

    box = c.title
    lines, tfont = _title_lines(project.title, c, t.font_path(box.font))
    pitch = tfont.size * c.title_line_pitch
    block = pitch * (len(lines) - 1) + tfont.size
    y = box.y + (box.h - block) / 2
    for line in lines:
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


class MovingLayer:
    """박스 가운데를 중심으로 배율 s 로 그린다. 모션 블러는 여러 배율을 평균."""

    def __init__(self, path: Path, box: Box, motion: Motion, fps: int, top: int, canvas: tuple[int, int]):
        self.src = _load_rgba(str(path))
        self.box, self.motion, self.fps, self.top, self.canvas = box, motion, fps, top, canvas
        self.w, self.h = fit_size(self.src.size, box)
        self.cx, self.cy = box.x + box.w / 2, box.y + box.h / 2
        max_s = max(s for _, s in motion.curve)
        # 가장 큰 배율에서도 선명하도록 원본 해상도에서 바로 줄인다
        self._big = self.src if self.src.width >= self.w * max_s else self.src.resize(
            (round(self.w * max_s), round(self.h * max_s)), Image.LANCZOS)

    def _place(self, s: float) -> tuple[Image.Image, int, int]:
        w, h = max(1, round(self.w * s)), max(1, round(self.h * s))
        img = self._big.resize((w, h), Image.BICUBIC if s > 1.01 else Image.LANCZOS)
        return img, round(self.cx - w / 2), round(self.cy - h / 2)

    def draw(self, frame: Image.Image, t_anim: float | None) -> None:
        """t_anim: 등장 후 경과 초 (None 이면 정지 상태). 구분선 위(top)로는 그리지 않는다."""
        m = self.motion
        gap = m.gap_frames / self.fps
        if t_anim is not None and t_anim < gap - 1e-9:
            return  # 이전 카드가 사라지고 새 카드가 들어오기 직전의 빈 프레임
        ta = None if t_anim is None else t_anim - gap
        s = 1.0 if ta is None else m.scale_at(ta)
        layer = Image.new("RGBA", self.canvas, (0, 0, 0, 0))
        if ta is not None and ta < m.blur and m.blur_samples > 1:
            # 셔터가 열린 1프레임 동안의 배율 변화를 평균 → 확대 방향 모션 블러
            back = m.shutter / self.fps  # 셔터가 열린 동안의 움직임
            prev = m.scale_ext(ta - back)
            acc = None
            for k in range(m.blur_samples):
                sk = prev + (s - prev) * (k + 1) / m.blur_samples
                img, x, y = self._place(sk)
                tmp = Image.new("RGBA", self.canvas, (0, 0, 0, 0))
                tmp.alpha_composite(img, (max(0, x), max(0, y)), (max(0, -x), max(0, -y)))
                a = np.asarray(tmp, dtype=np.float32)
                a[..., :3] *= a[..., 3:4] / 255.0  # premultiplied 로 평균해야 가장자리가 검게 번지지 않음
                acc = a if acc is None else acc + a
            acc /= m.blur_samples
            alpha = acc[..., 3:4]
            rgb = np.where(alpha > 0, acc[..., :3] * 255.0 / np.maximum(alpha, 1e-6), 0)
            layer = Image.fromarray(np.concatenate([rgb, alpha], axis=2).clip(0, 255).astype(np.uint8), "RGBA")
        else:
            img, x, y = self._place(s)
            layer.alpha_composite(img, (max(0, x), max(0, y)), (max(0, -x), max(0, -y)))
        if self.top > 0:
            layer.paste((0, 0, 0, 0), (0, 0, self.canvas[0], self.top))
        frame.alpha_composite(layer)


# ---------------------------------------------------------------- 타자 효과 자막

@dataclass
class SubtitleLayout:
    font: object
    lines: list[tuple[str, int, float, float]]  # (줄 글자, 줄 첫 글자의 원문 위치, x 왼쪽, y 위)
    overflow: bool = False


def layout_subtitle(text: str, st: TypingSubtitle, font_path: Path, center_y: float) -> SubtitleLayout:
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
        out.append((line, start, st.x + st.w / 2 - lw / 2, y))
        pos = start + len(line)
        y += pitch
    return SubtitleLayout(font, out, overflow)


def render_subtitle(layout: SubtitleLayout, visible_chars: int, st: TypingSubtitle, canvas: tuple[int, int]
                    ) -> Image.Image:
    """원문 앞쪽 visible_chars 글자까지만 그린 RGBA 레이어 (그림자 → 검은 테두리 → 흰 글씨)."""
    layer = Image.new("RGBA", canvas, (0, 0, 0, 0))
    parts = []
    for line, start, x, y in layout.lines:
        n = max(0, min(len(line), visible_chars - start))
        if n:
            parts.append((line[:n], x, y))
    if not parts:
        return layer
    font = layout.font
    sx, sy = st.shadow_offset
    shadow = Image.new("RGBA", canvas, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    alpha = round(255 * st.shadow_opacity)
    for s, x, y in parts:
        sd.text((x + sx, y + sy), s, font=font, fill=_hex(st.shadow_color) + (alpha,), anchor="la",
                stroke_width=st.stroke_width, stroke_fill=_hex(st.shadow_color) + (alpha,))
    if st.shadow_blur > 0:
        shadow = shadow.filter(ImageFilter.GaussianBlur(st.shadow_blur))
    layer.alpha_composite(shadow)
    d = ImageDraw.Draw(layer)
    for s, x, y in parts:
        d.text((x, y), s, font=font, fill=_hex(st.color), anchor="la",
               stroke_width=st.stroke_width, stroke_fill=_hex(st.stroke_color))
    return layer


# ---------------------------------------------------------------- 프레임 렌더러

@dataclass
class CardRenderer:
    project: Project
    project_dir: Path
    t: Template
    scenes: list[Scene]
    lines: list[LyricLine]
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        t, c = self.t, self.t.card
        self.canvas = (t.width, t.height)
        self.base, self.head = render_chrome(t, self.project)
        logo = t.asset_path(c.logo)
        if logo is None:
            raise CardModeError(f"채널 로고 이미지가 없습니다: templates/{self.project.channel}/{c.logo}")
        self.layers: dict[int, MovingLayer] = {
            0: MovingLayer(logo, c.logo_box, c.intro_motion, t.fps, c.content_top, self.canvas)}
        for s in self.scenes[1:]:
            if not s.item.image:
                raise CardModeError(f"[{s.item.rank}위] 카드 이미지가 지정되지 않았습니다")
            self.layers[s.index] = MovingLayer(self.project_dir / s.item.image, c.card_box, c.card_motion,
                                               t.fps, c.content_top, self.canvas)
        st = c.subtitle
        font_path = t.font_path(st.font)
        self._times = [char_times(l) for l in self.lines]
        self._layouts: dict[tuple[int, bool], SubtitleLayout] = {}
        self._font_path = font_path
        for i, l in enumerate(self.lines):
            for intro in (True, False):
                lay = self._layout(i, intro)
                if lay.overflow:
                    self.warnings.append(f"자막이 {st.max_lines}줄을 넘어 최소 크기로도 다 들어가지 않습니다: {l.text}")
                    break
        self.warnings = list(dict.fromkeys(self.warnings))
        self._cache_key = None
        self._cache_frame: Image.Image | None = None

    def _layout(self, i: int, intro: bool) -> SubtitleLayout:
        key = (i, intro)
        if key not in self._layouts:
            st = self.t.card.subtitle
            cy = (st.intro_center_y if intro and st.intro_center_y else st.y + st.h / 2)
            self._layouts[key] = layout_subtitle(self.lines[i].text, st, self._font_path, cy)
        return self._layouts[key]

    def scene_at(self, sec: float) -> Scene:
        for s in self.scenes:
            if sec < s.end - 1e-9:
                return s
        return self.scenes[-1]

    def state(self, frame_no: int) -> tuple:
        """프레임 모양을 결정하는 값. 같으면 이전 프레임을 그대로 쓴다."""
        sec = frame_no / self.t.fps
        sc = self.scene_at(sec)
        m = self.layers[sc.index].motion
        since = frame_no - round(sc.start * self.t.fps)
        anim = since if since / self.t.fps < m.length + m.gap_frames / self.t.fps else None
        line_i, shown = None, 0
        for i, l in enumerate(self.lines):  # 다음 가사가 시작되면 교체
            if l.start <= sec + 1e-9:
                line_i = i
            else:
                break
        if line_i is not None:
            shown = sum(1 for x in self._times[line_i] if x <= sec + 1e-9)
        return (sc.index, anim, line_i, shown, sc.kind == "intro")

    def frame(self, frame_no: int) -> Image.Image:
        key = self.state(frame_no)
        if key == self._cache_key and self._cache_frame is not None:
            return self._cache_frame
        idx, anim, line_i, shown, intro = key
        img = self.base.convert("RGBA")
        self.layers[idx].draw(img, None if anim is None else anim / self.t.fps)
        img.alpha_composite(self.head)
        if line_i is not None and shown:
            img.alpha_composite(render_subtitle(self._layout(line_i, intro), shown, self.t.card.subtitle, self.canvas))
        out = img.convert("RGB")
        self._cache_key, self._cache_frame = key, out
        return out

    def frame_at(self, sec: float) -> Image.Image:
        return self.frame(round(sec * self.t.fps))


def make_renderer(project: Project, project_dir: Path, t: Template, lines: list[LyricLine]) -> CardRenderer:
    scenes = card_timeline(project, t, lines)
    return CardRenderer(project, project_dir, t, scenes, lines)


# ---------------------------------------------------------------- 미리보기·인코딩

def build_previews(r: CardRenderer, out_dir: Path) -> SceneBuildResult:
    """장면마다 움직임이 끝난 상태 + 그 장면에서 처음 다 나온 자막의 PNG (Streamlit·CapCut 키트용)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    built = []
    for s in r.scenes:
        sec = s.start + min(r.layers[s.index].motion.length + 0.2, s.duration * 0.9)
        for l in r.lines:  # 장면 안에서 시작한 첫 가사가 다 나온 시각
            if s.start <= l.start < s.end:
                sec = max(sec, min(char_times(l)[-1] + 0.05, s.end - 1 / r.t.fps))
                break
        path = out_dir / f"scene{s.index:02d}.png"
        r.frame_at(sec).save(path)
        built.append(SceneLayers(scene=s, base=path, preview=path))
    return SceneBuildResult(built, list(r.warnings))


def render_video(r: CardRenderer, output: Path, *, progress: ProgressFn | None = None) -> None:
    p, t = r.project, r.t
    music = (r.project_dir / p.music) if p.music else None
    n_frames = round(p.duration * t.fps)
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{t.width}x{t.height}", "-r", str(t.fps), "-i", "-"]
    if music:
        cmd += ["-ss", _f(p.music_start), "-i", str(music)]
    else:
        cmd += ["-f", "lavfi", "-t", _f(p.duration), "-i", "anullsrc=r=44100:cl=stereo"]
    fade = min(t.effects.music_fade_out, p.duration)
    cmd += ["-filter_complex",
            f"[0:v]{TO_TV_YUV420}[vout];[1:a]apad,atrim=0:{_f(p.duration)},asetpts=PTS-STARTPTS,"
            f"afade=t=out:st={_f(p.duration - fade)}:d={_f(fade)}[aout]",
            "-map", "[vout]", "-map", "[aout]", "-r", str(t.fps), "-t", _f(p.duration),
            *FINAL_VCODEC, *FINAL_ACODEC, "-movflags", "+faststart", str(output)]
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=err)
        try:
            last_key, data = None, b""
            for i in range(n_frames):
                key = r.state(i)
                if key != last_key:
                    data = r.frame(i).tobytes()
                    last_key = key
                proc.stdin.write(data)
                if progress and i % t.fps == 0:
                    progress(i / n_frames, f"프레임 {i}/{n_frames}")
            proc.stdin.close()
        except BrokenPipeError:
            pass
        code = proc.wait()
        if code != 0:
            err.seek(0)
            raise FFmpegError(cmd, err.read().decode("utf-8", "replace"))
