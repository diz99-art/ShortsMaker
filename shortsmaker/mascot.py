"""채널 마스코트 '한장이' — 화면 아래 구석에서 표정으로 반응하고, 노래가 쉬는 구간에만 말풍선.

표정 그림은 assets/mascot/<표정>.png (question, surprise, shock, laugh, clap, thumbsup, wave, think, sad, cheer)
+ default.png. 흰 배경 그림이면 불러올 때 배경을 지운다 (원본 파일은 그대로).
파일이 없으면 default.png, 그것도 없으면 임시 도형(야구공 얼굴). 파일이 들어오면 다음 렌더부터 자동으로 쓴다.

흐름: 인트로 question → 순위 장면 분위기 표정 + 박자마다 까딱 → 1위 surprise→cheer → 그래프 think →
보너스 shock → 마지막 2초 wave. 가사 추임새 '(와아!)' 를 부르는 순간 0.8초 반응.
말풍선(3~8글자, 1~2초)은 노래가 0.9초 이상 쉬는 구간에만: 인트로 / 1위 / 엔딩.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw

from .scenes import _font

if TYPE_CHECKING:
    from .effects import BeatMap
    from .models import LyricLine
    from .timeline import Scene

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
MASCOT_DIR = ASSETS_DIR / "mascot"        # 야구한장 '한장이' (정보 채널은 assets/mascot_info, 템플릿 mascot_dir)
EXPRESSIONS = ("question", "surprise", "shock", "laugh", "clap", "thumbsup", "wave", "think", "sad", "cheer")
DEFAULT = "default"        # 기본 표정 (어떤 표정 그림이 없을 때 대신)
SIZE = 220                 # 화면 속 크기(px)
REST_MIN = 0.9             # 말풍선을 띄울 수 있는 최소 쉼(초)
BUBBLE_MAX = 2.0
WAVE_LEN = 2.0             # 마지막 손 흔들기
TOP_SURPRISE = 1.5         # 1위 장면 처음 놀람
REACT_LEN = 0.8            # 추임새 반응
BOB_DEG = 7.0              # 박자 까딱 각도

MOOD_RANK = {"record": ("clap", "thumbsup"), "humiliation": ("sad", "shock"), "money": ("surprise", "thumbsup"),
             "rivalry": ("think", "surprise"), "retro": ("laugh", "think"), "live": ("cheer", "clap")}
MOOD_BUBBLES = {  # 말풍선이 없을 때 자동 문구 (인트로 / 1위 / 엔딩)
    "record": ("몇 개일까?", "역시 1위!", "구독 꾹!"),
    "humiliation": ("얼마나?", "이건 너무해", "구독 꾹!"),
    "money": ("얼마일까?", "억 소리 나네", "구독 꾹!"),
    "rivalry": ("누가 이길까", "승자는 너!", "구독 꾹!"),
    "retro": ("기억나요?", "전설이다!", "구독 꾹!"),
    "live": ("지금 몇 위?", "새 기록!", "구독 꾹!"),
}
SLOTS = ("intro", "top", "ending")


def bubble_problem(text: str) -> str | None:
    n = len(text.replace(" ", ""))
    if not 3 <= n <= 8:
        return f"말풍선 '{text}'은(는) {n}글자입니다 (3~8글자)"
    return None


def auto_bubbles(mood: str, given: dict[str, str] | None = None) -> dict[str, str]:
    """사람이 준 문구가 우선, 없는 칸은 분위기 기본 문구."""
    base = dict(zip(SLOTS, MOOD_BUBBLES.get(mood or "record", MOOD_BUBBLES["record"])))
    return {k: (given or {}).get(k) or base[k] for k in SLOTS}


# ---------------------------------------------------------------- 그림

def _placeholder(expr: str, size: int) -> Image.Image:
    """임시 마스코트: 야구공 얼굴 + 표정 + 작은 손짓 기호 (진짜 그림이 들어오면 안 씀)."""
    S = 400
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy, r = S / 2, S / 2 + 20, 150
    d.ellipse([cx - r + 6, cy - r + 12, cx + r + 6, cy + r + 12], fill=(0, 0, 0, 60))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 255), outline=(30, 30, 30, 255), width=10)
    for sx in (-1, 1):  # 야구공 실밥
        d.arc([cx + sx * 95 - 120, cy - 150, cx + sx * 95 + 120, cy + 150], 100 if sx > 0 else -80,
              260 if sx > 0 else 80, fill=(214, 40, 40, 255), width=8)
    eye_y = cy - 25
    red = (214, 40, 40, 255)
    ink = (30, 30, 30, 255)

    def eyes(kind: str) -> None:
        for sx in (-1, 1):
            x = cx + sx * 50
            if kind == "dot":
                d.ellipse([x - 14, eye_y - 18, x + 14, eye_y + 18], fill=ink)
            elif kind == "big":
                d.ellipse([x - 24, eye_y - 28, x + 24, eye_y + 28], fill=(255, 255, 255, 255), outline=ink, width=6)
                d.ellipse([x - 10, eye_y - 10, x + 10, eye_y + 10], fill=ink)
            elif kind == "happy":
                d.arc([x - 24, eye_y - 16, x + 24, eye_y + 24], 200, 340, fill=ink, width=9)
            elif kind == "sad":
                d.arc([x - 22, eye_y, x + 22, eye_y + 30], 200, 340, fill=ink, width=8)
                d.line([x - 20 * sx, eye_y - 30, x + 14 * sx, eye_y - 20], fill=ink, width=7)
            elif kind == "x":
                d.line([x - 16, eye_y - 16, x + 16, eye_y + 16], fill=ink, width=9)
                d.line([x - 16, eye_y + 16, x + 16, eye_y - 16], fill=ink, width=9)

    def mouth(kind: str) -> None:
        my = cy + 55
        if kind == "smile":
            d.arc([cx - 55, my - 45, cx + 55, my + 25], 20, 160, fill=ink, width=9)
        elif kind == "open":
            d.ellipse([cx - 30, my - 20, cx + 30, my + 35], fill=red, outline=ink, width=6)
        elif kind == "wide":
            d.chord([cx - 60, my - 40, cx + 60, my + 45], 0, 180, fill=red, outline=ink, width=6)
        elif kind == "flat":
            d.line([cx - 35, my + 5, cx + 35, my + 5], fill=ink, width=8)
        elif kind == "frown":
            d.arc([cx - 45, my, cx + 45, my + 50], 200, 340, fill=ink, width=9)
        elif kind == "o":
            d.ellipse([cx - 16, my - 8, cx + 16, my + 26], fill=ink)

    face = {"question": ("dot", "flat"), "surprise": ("big", "o"), "shock": ("x", "open"), "laugh": ("happy", "wide"),
            "clap": ("happy", "smile"), "thumbsup": ("dot", "smile"), "wave": ("happy", "smile"),
            "think": ("dot", "flat"), "sad": ("sad", "frown"), "cheer": ("happy", "wide")}[expr]
    eyes(face[0])
    mouth(face[1])
    f = _font(str(Path(__file__).resolve().parent.parent / "assets" / "fonts" / "Pretendard-Black.otf"), 110)
    sign = {"question": "?", "surprise": "!", "shock": "!!", "think": "…", "sad": "💧"}.get(expr)
    if expr in ("clap", "cheer", "wave", "thumbsup"):  # 손 (동그라미 두 개)
        hy = cy - 120 if expr in ("cheer", "wave") else cy + 90
        for sx in ((1,) if expr in ("wave", "thumbsup") else (-1, 1)):
            hx = cx + sx * (r + 20)
            d.ellipse([hx - 38, hy - 38, hx + 38, hy + 38], fill=(255, 224, 189, 255), outline=ink, width=7)
        if expr == "thumbsup":
            d.rounded_rectangle([cx + r + 5, cy + 30, cx + r + 35, cy + 70], radius=10, fill=(255, 224, 189, 255),
                                outline=ink, width=6)
    if sign and sign != "💧":
        d.text((S - 60, 70), sign, font=f, fill=red, anchor="mm", stroke_width=8, stroke_fill=(255, 255, 255))
    elif sign == "💧":
        d.ellipse([cx + 70, eye_y + 10, cx + 100, eye_y + 50], fill=(90, 160, 255, 255))
    return img.resize((size, size), Image.LANCZOS)


@lru_cache(maxsize=64)
def _load(path: str, mtime: float, size: int) -> Image.Image:
    """그림 파일 → 투명 배경 RGBA (흰 배경 그림이면 테두리에서 배경을 지움. 원본 파일은 그대로)."""
    from .bg_remove import has_transparency, remove_background
    with Image.open(path) as im:
        src = im.copy()
    img = src.convert("RGBA") if has_transparency(src) else remove_background(src, method="flood", tolerance=28)[0]
    img = img.crop(img.getbbox() or (0, 0, *img.size))
    img.thumbnail((size, size), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.alpha_composite(img, ((size - img.width) // 2, size - img.height))
    return out


def expression_image(expr: str, size: int = SIZE, folder: Path | None = None) -> Image.Image:
    """assets/mascot/<표정>.png → 없으면 default.png → 그것도 없으면 임시 도형."""
    folder = folder or MASCOT_DIR
    for name in (expr, DEFAULT):
        p = folder / f"{name}.png"
        if p.is_file():
            return _load(str(p), p.stat().st_mtime, size)
    return _placeholder_cached(expr, size)


@lru_cache(maxsize=32)
def _placeholder_cached(expr: str, size: int) -> Image.Image:
    return _placeholder(expr, size)


def missing_files(folder: Path = MASCOT_DIR) -> list[str]:
    return [e for e in EXPRESSIONS if not (folder / f"{e}.png").is_file()]


def bubble_image(text: str, font_path: Path, tail_right: bool = True) -> Image.Image:
    f = _font(str(font_path), 50)
    tw = f.getlength(text)
    w, h = int(tw + 70), 96
    img = Image.new("RGBA", (w, h + 30), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([3, 3, w - 4, h - 4], radius=40, fill=(255, 255, 255, 255), outline=(17, 17, 17, 255), width=6)
    tx = w - 70 if tail_right else 70
    d.polygon([(tx - 18, h - 8), (tx + 18, h - 8), (tx + (14 if tail_right else -14), h + 26)],
              fill=(255, 255, 255, 255), outline=(17, 17, 17, 255))
    d.line([(tx - 16, h - 6), (tx + 16, h - 6)], fill=(255, 255, 255, 255), width=8)
    d.text((w / 2, h / 2), text, font=f, fill=(17, 17, 17, 255), anchor="mm")
    return img


# ---------------------------------------------------------------- 시간표

@dataclass
class MascotPlan:
    exprs: list[tuple[float, float, str]] = field(default_factory=list)
    bubbles: list[tuple[float, float, str]] = field(default_factory=list)
    reactions: list[tuple[float, float, str]] = field(default_factory=list)
    rank_spans: list[tuple[float, float]] = field(default_factory=list)  # 박자 까딱하는 구간

    def expr_at(self, t: float) -> str:
        for a, b, e in self.reactions:
            if a <= t < b:
                return e
        for a, b, e in self.exprs:
            if a <= t < b:
                return e
        return self.exprs[-1][2] if self.exprs else "question"

    def bubble_at(self, t: float) -> tuple[int, float] | None:
        for i, (a, b, _) in enumerate(self.bubbles):
            if a <= t < b:
                return i, t - a
        return None

    def bobbing(self, t: float) -> bool:
        return any(a <= t < b for a, b in self.rank_spans)


def rests(lines: list[LyricLine], duration: float, min_gap: float = REST_MIN) -> list[tuple[float, float]]:
    """노래가 쉬는 구간 (단어 타이밍 기준, 없으면 줄 기준)."""
    spans = []
    for l in lines:
        if l.words:
            spans += [(w.start, w.end) for w in l.words]
        else:
            spans.append((l.start, l.end))
    spans.sort()
    out, prev = [], 0.0
    for a, b in spans:
        if a - prev >= min_gap:
            out.append((round(prev, 3), round(a, 3)))
        prev = max(prev, b)
    if duration - prev >= min_gap:
        out.append((round(prev, 3), round(duration, 3)))
    return out


def adlib_times(lines: list[LyricLine]) -> list[float]:
    """가사 추임새 '(와아!)' 를 부르는 시각 (괄호로 시작하는 단어)."""
    out = []
    for l in lines:
        for w in l.words:
            if w.text.strip().startswith("("):
                out.append(w.start)
        if not l.words and re.match(r"^\s*\(", l.text):
            out.append(l.start)
    return out


def make_plan(scenes: list[Scene], lines: list[LyricLine], *, mood: str, bubbles: dict[str, str], hook_len: float,
              duration: float) -> MascotPlan:
    plan = MascotPlan()
    rank_cycle = MOOD_RANK.get(mood or "record", MOOD_RANK["record"])
    k = 0
    for sc in scenes:
        if sc.kind == "intro":
            plan.exprs.append((sc.start, sc.end, "question"))
        elif sc.kind == "rank" and sc.item.shown_rank == 1 and sc.item.rank == 1:
            first, then = ("shock", "sad") if mood == "humiliation" else ("surprise", "cheer")
            plan.exprs.append((sc.start, min(sc.end, sc.start + TOP_SURPRISE), first))
            plan.exprs.append((min(sc.end, sc.start + TOP_SURPRISE), sc.end, then))
            plan.rank_spans.append((sc.start, sc.end))
        elif sc.kind == "rank":
            plan.exprs.append((sc.start, sc.end, rank_cycle[k % len(rank_cycle)]))
            plan.rank_spans.append((sc.start, sc.end))
            k += 1
        elif sc.kind == "chart":
            plan.exprs.append((sc.start, sc.end, "think"))
        elif sc.kind == "bonus":
            plan.exprs.append((sc.start, sc.end, "shock"))
    wave_from = max(0.0, duration - WAVE_LEN)
    kept = [(a, min(b, wave_from), e) for a, b, e in plan.exprs if a < wave_from]
    plan.exprs = kept + [(wave_from, duration, "wave")]
    for t in adlib_times(lines):
        plan.reactions.append((t, t + REACT_LEN, "laugh" if mood == "humiliation" else "cheer"))

    free = list(rests(lines, duration))  # 남은 쉼 구간 (말풍선이 쓴 부분은 빼고 나머지는 다시 쓸 수 있음)
    first_rank = next((s.start for s in scenes if s.kind == "rank"), duration)
    top = next((s for s in scenes if s.kind == "rank" and s.item.shown_rank == 1 and s.item.rank == 1), None)
    top_end = max((s.end for s in scenes if s.kind in ("chart",) or s is top), default=0.0)

    def place(slot: str, lo: float, hi: float, last: bool = False) -> None:
        text = (bubbles or {}).get(slot)
        if not text:
            return
        cands = [(i, max(a, lo), min(b, hi)) for i, (a, b) in enumerate(free)]
        cands = [(i, a, b) for i, a, b in cands if b - a >= REST_MIN]
        if not cands:
            return
        i, a, b = cands[-1] if last else cands[0]
        if last:  # 엔딩은 영상 끝 쪽에
            a = max(a, b - BUBBLE_MAX)
        end = min(b, a + BUBBLE_MAX)
        plan.bubbles.append((a, end, text))
        fa, fb = free.pop(i)
        free[i:i] = [x for x in ((fa, a - 0.2), (end + 0.2, fb)) if x[1] - x[0] > 0]

    place("intro", hook_len, first_rank)
    if top is not None:
        place("top", top.start, top_end)
    place("ending", max(0.0, duration - 6.0), duration, last=True)
    plan.bubbles.sort()
    return plan


def bob_angle(beats: BeatMap | None, t: float) -> float:
    """박자마다 좌우로 까딱 (박자 짝·홀수로 방향 바꿈). 비트가 없으면 0.5초 간격."""
    if beats is not None and beats.beats:
        lb = beats.last_beat(t)
        if lb is None:
            return 0.0
        i, b = lb
    else:
        i, b = int(t / 0.5), int(t / 0.5) * 0.5
    dt = t - b
    return (1 if i % 2 else -1) * BOB_DEG * math.exp(-dt / 0.18)
