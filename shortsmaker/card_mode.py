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
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .ffmpeg_builder import FINAL_ACODEC, FINAL_VCODEC, TO_TV_YUV420, FFmpegError, _f, ffmpeg_bin
from .models import MIN_SCENE, Box, CardLayout, LyricLine, Motion, Project, StatLine, Template, TypingSubtitle
from .scenes import SceneBuildResult, SceneLayers, _font, _hex, _wrap
from .timeline import Scene, snap
from . import effects as fx
from .effects import BONUS_LEN, BONUS_MIN_TOP, HOOK_IMPACT, HOOK_LEN
from .sfx import Cue, mix_filter
from .variation import (ACCENTS, FLASH, LINE_RISE, TITLE_CARD, TOP_FLASH, TOP_GAIN, TOP_SHAKE, Variation, idle_transform,
                        intro_motion, top_shake, transition_motion)

ProgressFn = Callable[[float, str], None]


class CardModeError(ValueError):
    pass


def _norm(s: str) -> str:
    return re.sub(r"[\W_]+", "", s).lower()


# ---------------------------------------------------------------- 가사 → 글자별 시각

def char_times(line: LyricLine) -> list[float]:
    """줄의 글자마다 나타나는 시각. 문장 부호는 앞 글자와, 공백은 다음 글자와 함께 나타난다.

    자막 표기가 부른 가사와 다르면(line.sung: '오십육 홈런' → 자막 '56홈런') 부른 가사로 글자 시각을 구한 뒤
    같은 글자는 그 시각을, 바뀐 부분('오십육 '→'56')은 원래 부른 구간 안에서 고르게 나눠 쓴다.
    """
    if line.sung and line.sung != line.text:
        return _mapped_times(line)
    return _own_times(line)


def _mapped_times(line: LyricLine) -> list[float]:
    src = line.model_copy(update={"text": line.sung, "sung": ""})
    st = _own_times(src)
    n = len(src.text)
    tail = max([line.end] + [w.end for w in line.words]) if line.words else line.end
    word_ends = sorted(w.end for w in line.words)
    ends = []  # 부른 글자마다 끝나는 시각: 다음 글자 시각 (단어 끝을 넘지 않게)
    for i in range(n):
        nxt = st[i + 1] if i + 1 < n else tail
        cap = next((e for e in word_ends if e >= st[i]), tail)
        ends.append(max(st[i], min(nxt, cap) if not src.text[i].isspace() else nxt))
    chars = list(line.text)
    times = [line.start] * len(chars)
    for op, i1, i2, j1, j2 in SequenceMatcher(None, src.text, line.text, autojunk=False).get_opcodes():
        if op == "equal":
            times[j1:j2] = st[i1:i2]
            continue
        if j1 == j2:
            continue
        a = st[i1] if i1 < n else tail
        b = ends[i2 - 1] if i2 > i1 else a
        vis = [j for j in range(j1, j2) if _norm(chars[j])]
        for k, j in enumerate(vis):
            times[j] = a + (b - a) * k / len(vis)
    return _settle(chars, set(j for j, c in enumerate(chars) if _norm(c)), times, line.start)


def _own_times(line: LyricLine) -> list[float]:
    """char_times 의 기본: 자막 글자를 부른 단어 구간에 비율로 나눈다.

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
    return _settle(chars, set(visible), times, line.start)


def _settle(chars: list[str], counted: set[int], times: list[float], start: float) -> list[float]:
    """문장 부호는 바로 앞 글자와 함께, 공백은 다음 글자와 함께 나타나게 하고 단조 증가로 맞춘다."""
    for i, ch in enumerate(chars):
        if i not in counted and not ch.isspace():
            times[i] = times[i - 1] if i else start
    nxt = None
    for i in range(len(chars) - 1, -1, -1):
        if chars[i].isspace():
            times[i] = nxt if nxt is not None else times[i - 1] if i else start
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
    """인트로 + 5위~1위 (+ 엔딩 보너스). 1위 카드가 끝까지 남고, 보너스가 있으면 마지막 몇 초를 보너스 장면으로."""
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
    kinds = ["intro"] + ["rank"] * len(items)
    scene_items: list = [None] + list(items)
    b_start = bonus_start(project, bounds[-2], fps)
    if b_start is not None:
        bounds.insert(-1, b_start)
        kinds.append("bonus")
        scene_items.append(None)
    scenes = []
    for i, (a, b) in enumerate(zip(bounds, bounds[1:])):
        scenes.append(Scene(index=i, kind=kinds[i], start=a, end=b, clip_duration=b - a, item=scene_items[i]))
    return scenes


def bonus_start(project: Project, top_start: float, fps: int) -> float | None:
    """보너스 장면 시작: 1위를 BONUS_MIN_TOP 초 이상 보여 준 뒤, 영상 끝 BONUS_LEN 초. 자리가 없으면 None."""
    if project.bonus is None:
        return None
    start = snap(max(top_start + BONUS_MIN_TOP, project.duration - BONUS_LEN), fps)
    return start if project.duration - start >= 2.5 else None


def hook_length(project: Project, scenes: list[Scene]) -> float:
    """후킹 문구 시간. 인트로가 짧으면 줄인다 (로고·제목이 1초는 보이게). 문구가 없으면 0."""
    if not project.hook.strip() or not scenes:
        return 0.0
    room = scenes[0].duration - 1.0
    return round(min(HOOK_LEN, room), 2) if room >= 1.0 else 0.0


def sfx_cues(project: Project, t: Template, scenes: list[Scene]) -> list[Cue]:
    """효과음 위치: 후킹 '쾅', 장면 전환 '휙', 카드 착지 '쿵', 1위 함성."""
    v = Variation.model_validate(project.variation or {})
    if v.sfx == "off":
        return []
    cues: list[Cue] = []
    if hook_length(project, scenes):
        cues.append(Cue(HOOK_IMPACT, "slam"))
    land = 0.12 if v.transition in ("flash", "pop") else 0.2
    for sc in scenes:
        if sc.kind == "intro":
            continue
        cues.append(Cue(max(0.0, sc.start - 0.12), "whoosh"))
        cues.append(Cue(sc.start + land, "thud"))
        if sc.kind == "rank" and sc.item.shown_rank == 1 and sc.item.rank == 1:
            cues.append(Cue(sc.start + 0.25, "cheer"))
    return sorted(cues, key=lambda c: c.at)


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
            (round(self.w * max_s), round(self.h * max_s)), Image.LANCZOS)

    def variant(self, img: Image.Image) -> "MovingLayer":
        """같은 자리·움직임에 그림만 바꾼 레이어 (카운트업 숫자)."""
        return MovingLayer(img, self.box, self.motion, self.fps, self.top, self.canvas, dx=self.dx)

    def _place(self, s: float, dx: float = 0.0, dy: float = 0.0, angle: float = 0.0) -> tuple[Image.Image, int, int]:
        w, h = max(1, round(self.w * s)), max(1, round(self.h * s))
        img = self._big.resize((w, h), Image.BICUBIC if s > 1.01 else Image.LANCZOS)
        if abs(angle) > 0.05:
            img = img.rotate(angle, resample=Image.BICUBIC, expand=True)
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
            acc = None
            for k in range(m.blur_samples):
                f = (k + 1) / m.blur_samples
                img, x, y = self._place(prev_s + (s - prev_s) * f, prev_dx + (dx - prev_dx) * f)
                tmp = Image.new("RGBA", self.canvas, (0, 0, 0, 0))
                self._paste(tmp, img, x, y)
                a = np.asarray(tmp, dtype=np.float32)
                a[..., :3] *= a[..., 3:4] / 255.0  # premultiplied 로 평균해야 가장자리가 검게 번지지 않음
                acc = a if acc is None else acc + a
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
    font: object
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
    c = t.card
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
        self.v = Variation.model_validate(self.project.variation or {})
        card_m, card_dx, self.flash = transition_motion(self.v.transition, c.card_motion)
        intro_m, intro_dx = intro_motion(self.v.intro, c.intro_motion)
        self.title_card = render_title_card(t, self.project) if self.v.intro == "title_card" else None
        self.stat_color = ACCENTS.get(self.v.accent, {}).get("stat")
        self._sub_cache: dict[tuple, Image.Image] = {}
        if self.project.intro_image:  # 프로젝트 인트로 캐릭터
            intro, intro_box = self.project_dir / self.project.intro_image, c.intro_box or c.logo_box
            if not intro.exists():
                raise CardModeError(f"인트로 이미지가 없습니다: {intro}")
        else:
            intro, intro_box = t.asset_path(c.logo), c.logo_box
            if intro is None:
                raise CardModeError(f"채널 로고 이미지가 없습니다: templates/{self.project.channel}/{c.logo}")
        self.layers: dict[int, MovingLayer] = {
            0: MovingLayer(intro, intro_box, intro_m, t.fps, c.content_top, self.canvas, dx=intro_dx)}
        self.accent_rank = ACCENTS.get(self.v.accent, ACCENTS["red_gold"])["rank"]
        self._counts: dict[int, dict] = {}       # 장면 → 카운트업 정보 (바탕 카드, 기록 글자 위치)
        self._count_layers: dict[tuple[int, str], MovingLayer] = {}
        for s in self.scenes[1:]:
            if s.kind == "bonus":
                self.layers[s.index] = self._bonus_layer()
                continue
            if not s.item.image:
                raise CardModeError(f"[{s.item.rank}위] 카드 이미지가 지정되지 않았습니다")
            img_path = self.project_dir / s.item.image
            self.layers[s.index] = MovingLayer(img_path, c.card_box, card_m, t.fps, c.content_top, self.canvas,
                                               dx=card_dx)
            meta_p = img_path.with_suffix(".json")
            if self.v.count != "static" and meta_p.exists():
                import json as _json
                meta = _json.loads(meta_p.read_text(encoding="utf-8"))
                base_p = self.project_dir / meta.get("base", "")
                if meta.get("record") and base_p.is_file() and fx.record_value(meta["record"]) is not None:
                    self._counts[s.index] = {**meta, "base_img": _load_rgba(str(base_p))}
        # 후킹 문구 (인트로 앞 0~2초)
        self.hook_len = hook_length(self.project, self.scenes)
        if self.project.hook.strip() and not self.hook_len:
            self.warnings.append("인트로가 짧아 후킹 문구를 넣지 못했습니다")
        self.hook_img = (fx.hook_image(self.project.hook, self.accent_rank, self.stat_color or "#EB1B1B", t.width)
                         if self.hook_len else None)
        if self.project.bonus is not None and not any(s.kind == "bonus" for s in self.scenes):
            self.warnings.append("1위 장면 뒤 시간이 모자라 보너스 장면을 넣지 못했습니다 (영상 끝을 '반주 끝까지'로 해 보세요)")
        # 비트
        self.beats: fx.BeatMap | None = None
        if self.v.beat != "off" and self.project.music:
            try:
                self.beats = fx.load_beats(self.project_dir, self.project.music).shifted(self.project.music_start)
            except Exception as e:  # noqa: BLE001 — 비트 분석이 안 되면 효과만 빼고 렌더
                self.warnings.append(f"비트 분석 실패로 비트 효과를 뺐습니다: {e}")
        # 게이지
        self.gauge = None
        if self.v.count == "countup_gauge" and self.project.gauge_base:
            vals = [x for x in (fx.record_value(i.value) for i in self.project.items) if x is not None]
            self.gauge = fx.make_gauge(self.project.gauge_base, vals, c.content_top, c.footer.button.y)
            if self.gauge is None:
                self.warnings.append(f"게이지 기준선 '{self.project.gauge_base}'에서 숫자를 찾지 못했습니다")
        self._label_font = t.font_path("black")
        self.cues = sfx_cues(self.project, t, self.scenes)
        st = c.subtitle
        font_path = t.font_path(st.font)
        self._times = [self._reveal_times(l) for l in self.lines]
        self._layouts: dict[tuple[int, bool], SubtitleLayout] = {}
        self._font_path = font_path
        for i, l in enumerate(self.lines):
            for intro in (True, False):
                lay = self._layout(i, intro)
                if lay.overflow:
                    self.warnings.append(f"자막이 {st.max_lines}줄을 넘어 최소 크기로도 다 들어가지 않습니다: {l.text}")
                    break
        self.warnings = list(dict.fromkeys(self.warnings))
        self._stats: dict[int, SubtitleLayout] = {}
        if c.stat_line:
            for s in self.scenes[1:]:
                if s.item and s.item.stat_line.strip():
                    self._stats[s.index] = layout_stat(s.item.stat_line, c.stat_line, t.font_path(c.stat_line.font))
                    if self._stats[s.index].overflow:
                        self.warnings.append(f"빨간 기록 줄이 {c.stat_line.max_lines}줄을 넘습니다: {s.item.stat_line}")
        self._cache_key = None
        self._cache_frame: Image.Image | None = None

    def _bonus_layer(self) -> MovingLayer:
        c, t, bo = self.t.card, self.t, self.project.bonus
        motion, dx, _ = transition_motion({"stamp": "punch", "slide": "slide", "pop": "pop"}.get(self.v.ending, "punch"),
                                          c.card_motion)
        art = self.project_dir / bo.image if bo.image and (self.project_dir / bo.image).is_file() else None
        self.bonus_off: MovingLayer | None = None
        if self.v.design:  # 영상 카드와 같은 디자인 + 반전 ('6위?!', '근데…', 기울기, 조명 꺼짐)
            from .card_design import compose_bonus
            kw = dict(rank=bo.rank, name=bo.name, team=bo.team, value=bo.value, year=bo.year, text=bo.text, art=art)
            on = compose_bonus(self.v.design, bo.kind, **kw)
            self.bonus_off = MovingLayer(compose_bonus(self.v.design, bo.kind, **kw, off=True), c.card_box, motion,
                                         t.fps, c.content_top, self.canvas, dx=dx)
            return MovingLayer(on, c.card_box, motion, t.fps, c.content_top, self.canvas, dx=dx)
        if art is not None:
            src: Path | Image.Image = art
        else:
            src = fx.bonus_card(bo.kind, rank=bo.rank, name=bo.name, team=bo.team, value=bo.value, year=bo.year,
                                text=bo.text, accent=self.accent_rank)
        return MovingLayer(src, c.card_box, motion, t.fps, c.content_top, self.canvas, dx=dx)

    def _count_layer(self, idx: int, text: str) -> MovingLayer:
        key = (idx, text)
        if key not in self._count_layers:
            from .card_design import record_text_for
            m = self._counts[idx]
            card = m["base_img"].copy()
            rec = record_text_for(m.get("design"), text, m["size"], m.get("team", ""), m.get("rank", 5), m.get("top"))
            card.alpha_composite(rec, (m["x"], m["y"] + m["h"] - rec.height))
            if len(self._count_layers) > 40:
                self._count_layers.clear()
            self._count_layers[key] = self.layers[idx].variant(card)
        return self._count_layers[key]

    def _reveal_times(self, line: LyricLine) -> list[float]:
        """자막 등장 방식별 글자 시각: typing=글자마다, word=단어 첫 글자 시각에 단어 전체, line=줄 시작에 전부."""
        times = char_times(line)
        if self.v.subtitle == "line":
            return [line.start] * len(times)
        if self.v.subtitle == "word":
            out, i, text = [], 0, line.text
            while i < len(text):
                j = i
                while j < len(text) and not text[j].isspace():
                    j += 1
                j = j + 1 if j < len(text) else j  # 단어 뒤 공백 포함
                first = times[i]
                out += [first] * (j - i)
                i = j
            return out
        return times

    def _layout(self, i: int, intro: bool) -> SubtitleLayout:
        key = (i, intro)
        if key not in self._layouts:
            st = self.t.card.subtitle
            cy = (st.intro_center_y if intro and st.intro_center_y else st.y + st.h / 2)
            self._layouts[key] = layout_subtitle(self.lines[i].text, st, self._font_path, cy,
                                                 st.intro_align if intro else "center")
        return self._layouts[key]

    def scene_at(self, sec: float) -> Scene:
        for s in self.scenes:
            if sec < s.end - 1e-9:
                return s
        return self.scenes[-1]

    def state(self, frame_no: int) -> tuple:
        """프레임 모양을 결정하는 값. 같으면 이전 프레임을 그대로 쓴다."""
        sec = frame_no / self.t.fps
        fps = self.t.fps
        sc = self.scene_at(sec)
        m = self.layers[sc.index].motion
        since = frame_no - round(sc.start * fps)
        hook = None
        anim: object
        if sc.kind == "intro":  # 후킹 문구 → 제목 카드 → 로고 움직임
            pre = 0
            if self.hook_len:
                hl = round(self.hook_len * fps)
                if since < hl:
                    hook = since
                pre += hl
            if self.title_card is not None:
                pre += round(TITLE_CARD * fps)
            since = since - pre if since >= pre else -1
        if hook is not None:
            anim = "hook"
        elif since < 0:
            anim = "title"
        else:
            anim = since if since / fps < m.length + m.gap_frames / fps else None
        card_scene = sc.kind in ("rank", "bonus")
        flash = since if card_scene and self.flash and 0 <= since < round(self.flash * fps) else None
        top = sc.kind == "rank" and sc.item.shown_rank == 1
        if top and 0 <= since < round(TOP_FLASH * fps):  # 1위: 번쩍
            flash = since if flash is None else flash
        # 그림 움직임: 등장 효과가 끝난 뒤부터 장면 끝까지 프레임마다 (1위는 흔들림도)
        idle = None
        if card_scene and (self.v.motion != "none" or top):
            idle = since
        # 비트 펄스: 카드가 다 들어온 뒤
        pulse = None
        if card_scene and self.beats is not None and anim is None:
            pulse = fx.pulse_state(self.beats, sec, fps, self.v.beat == "pulse_flash")
        # 기록 카운트업 (게이지도 같은 값으로)
        count = None
        if sc.kind == "bonus" and getattr(self, "bonus_off", None) is not None and since >= 0:
            count = "off" if fx.bonus_flicker(since / fps) else None   # 디자인별 반전: 꺼졌다 켜짐
        if sc.index in self._counts and since >= 0:
            frac = fx.count_frac(since / fps)
            count = fx.count_text(self._counts[sc.index]["record"], frac) if frac < 1 else None
        gauge_v = None
        if self.gauge is not None and sc.kind == "rank":
            val = fx.record_value(sc.item.value) or 0.0
            cur = val * fx.count_frac(max(since, 0) / fps) if since >= 0 else 0.0
            gauge_v = float(round(cur)) if val == int(val) else round(cur, 1)  # 정수 기록은 정수로 올라감
        # 진행 표시
        prog = None
        if self.v.progress != "none":
            ranks = [s for s in self.scenes if s.kind == "rank"]
            done = sum(1 for s in ranks if s.start <= sc.start) if sc.kind != "intro" else 0
            pop_n = round(0.25 * fps)
            pop = round(1 - since / pop_n, 2) if sc.kind == "rank" and 0 <= since < pop_n else 0.0
            prog = (done, pop)
        rise = None
        line_i, shown = None, 0
        for i, l in enumerate(self.lines):  # 다음 가사가 시작되면 교체
            if l.start <= sec + 1e-9:
                line_i = i
            else:
                break
        if line_i is not None:
            shown = sum(1 for x in self._times[line_i] if x <= sec + 1e-9)
            if self.v.subtitle == "line" and shown:
                k = frame_no - round(self.lines[line_i].start * fps)
                rise = k if k < round(LINE_RISE * fps) else None
        stat = 0
        if sc.index in self._stats:
            stat = stat_visible(sc.item.stat_line, self.t.card.stat_line, sec - sc.start)
        return (sc.index, anim, line_i, shown, sc.kind == "intro", stat, flash, rise, idle, hook, pulse, count,
                gauge_v, prog)

    def frame(self, frame_no: int) -> Image.Image:
        key = self.state(frame_no)
        if key == self._cache_key and self._cache_frame is not None:
            return self._cache_frame
        idx, anim, line_i, shown, intro, stat, flash, rise, idle, hook, pulse, count, gauge_v, prog = key
        fps = self.t.fps
        img = self.base.convert("RGBA")
        sc = self.scenes[idx]
        ps, pdx, pflash = fx.pulse_transform(pulse, fps)
        if anim == "hook":
            self._draw_hook(img, hook / fps)
        elif anim == "title":
            img.alpha_composite(self.title_card)
        else:
            transform = None
            if idle is not None:
                layer = self.layers[idx]
                top = sc.kind == "rank" and sc.item.shown_rank == 1
                entry = layer.motion.length + layer.motion.gap_frames / fps
                t_idle = idle / fps - entry
                ks, dx, dy, ang = idle_transform(self.v.motion, t_idle, TOP_GAIN if top else 1.0)
                if top:
                    dx += top_shake(idle / fps)
                transform = (ks, dx, dy, ang)
                if anim is not None and t_idle < 0:  # 등장 중에는 1위 흔들림만
                    transform = (1.0, top_shake(idle / fps) if top else 0.0, 0.0, 0.0)
            if pulse is not None:
                ks, dx, dy, ang = transform or (1.0, 0.0, 0.0, 0.0)
                transform = (ks * ps, dx + pdx, dy, ang)
            if count == "off":
                layer = self.bonus_off
            else:
                layer = self._count_layer(idx, count) if count is not None else self.layers[idx]
            layer.draw(img, None if anim is None else anim / fps, transform)
        if gauge_v is not None:
            fx.draw_gauge(img, self.gauge, gauge_v, sc.item.description, self.stat_color or "#EB1B1B", self._label_font)
        if flash is not None or pflash:  # 흰 섬광이 사라짐 (구분선 아래만). 1위 번쩍은 TOP_FLASH 초
            a = 0.0
            if flash is not None:
                span = round((self.flash or TOP_FLASH) * fps)
                a = 1 - flash / max(1, span)
            a = max(a, pflash)
            white = Image.new("RGBA", self.canvas, (255, 255, 255, 0))
            white.paste((255, 255, 255, round(255 * a)), (0, self.t.card.content_top, self.canvas[0], self.canvas[1]))
            img.alpha_composite(white)
        img.alpha_composite(self.head)
        if prog is not None:
            p = self.t.card.pill
            fx.draw_progress(img, self.v.progress, prog[0], sum(1 for s in self.scenes if s.kind == "rank"),
                             right=self.canvas[0] - 42, cy=round(p.y + p.h / 2), hot=self.stat_color or "#EB1B1B",
                             font_path=self._label_font, pop=prog[1])
        if stat:
            ck = ("stat", idx, stat)
            if ck not in self._sub_cache:
                self._sub_cache[ck] = render_stat(self._stats[idx], stat, self.t.card.stat_line, self.canvas,
                                                  self.stat_color)
            img.alpha_composite(self._sub_cache[ck])
        if line_i is not None and shown:
            ck = ("sub", line_i, shown, intro, rise)  # 그림이 프레임마다 움직여도 자막 레이어는 다시 쓰기
            if ck not in self._sub_cache:
                if len(self._sub_cache) > 64:
                    self._sub_cache.clear()
                p = 1.0 if rise is None else (rise + 1) / round(LINE_RISE * fps)
                self._sub_cache[ck] = render_subtitle(self._layout(line_i, intro), shown, self.t.card.subtitle,
                                                      self.canvas, alpha=p, dy=(1 - p) * 40)
            img.alpha_composite(self._sub_cache[ck])
        out = img.convert("RGB")
        self._cache_key, self._cache_frame = key, out
        return out

    def _draw_hook(self, img: Image.Image, t: float) -> None:
        """후킹 문구: 구분선 아래 가운데에 크게. 섬광은 '쾅' 순간."""
        c = self.t.card
        h = self.hook_img
        cx = self.canvas[0] / 2
        cy = (c.content_top + c.footer.button.y) / 2 + 40
        if self.v.hook == "pop_words":  # 단어씩: 왼쪽부터 가려 둔 부분을 연다
            n = max(1, len(self.project.hook.split()))
            k = min(n, int(t / 0.2) + 1)
            src = h.copy()
            if k < n:
                mask = Image.new("L", src.size, 255)
                ImageDraw.Draw(mask).rectangle([round(src.width * (0.12 + 0.76 * k / n)), 0, src.width, src.height],
                                               fill=0)
                src.putalpha(Image.composite(src.getchannel("A"), Image.new("L", src.size, 0), mask))
            s, dx, dy, fl = fx.hook_frame("pop_words", t - (k - 1) * 0.2)
        else:
            src = h
            s, dx, dy, fl = fx.hook_frame(self.v.hook, t)
        w, hh = max(1, round(src.width * s)), max(1, round(src.height * s))
        im = src.resize((w, hh), Image.BICUBIC)
        alpha = fx.hook_alpha(t, self.hook_len)
        if alpha < 1:
            im.putalpha(im.getchannel("A").point(lambda v: round(v * alpha)))
        layer = Image.new("RGBA", self.canvas, (0, 0, 0, 0))
        MovingLayer._paste(layer, im, round(cx - w / 2 + dx), round(cy - hh / 2 + dy))
        layer.paste((0, 0, 0, 0), (0, 0, self.canvas[0], c.content_top))
        img.alpha_composite(layer)
        if fl > 0.01:
            white = Image.new("RGBA", self.canvas, (255, 255, 255, 0))
            white.paste((255, 255, 255, round(255 * fl)), (0, c.content_top, self.canvas[0], self.canvas[1]))
            img.alpha_composite(white)

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
        if s.index in r._stats and r.t.card.stat_line:  # 빨간 줄이 다 나온 뒤
            sl = r.t.card.stat_line
            sec = max(sec, min(s.start + sl.delay + len(s.item.stat_line) / sl.rate + 0.05, s.end - 1 / r.t.fps))
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
    v = r.v
    sfx_args, sfx_graph, _ = mix_filter(r.cues, v.sfx, 2) if v.sfx != "off" else ([], "", [])
    cmd += sfx_args
    music_chain = f"[1:a]aformat=sample_rates=44100:channel_layouts=stereo,apad,atrim=0:{_f(p.duration)},asetpts=PTS-STARTPTS"
    if sfx_graph:  # 효과음은 음악 위에 작게 (sfx.MIX_GAIN)
        audio = (f"{music_chain}[mus];{sfx_graph};[mus][sfx]amix=inputs=2:duration=first:normalize=0:"
                 f"dropout_transition=0,afade=t=out:st={_f(p.duration - fade)}:d={_f(fade)}[aout]")
    else:
        audio = f"{music_chain},afade=t=out:st={_f(p.duration - fade)}:d={_f(fade)}[aout]"
    cmd += ["-filter_complex", f"[0:v]{TO_TV_YUV420}[vout];{audio}",
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
