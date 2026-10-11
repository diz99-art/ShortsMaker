"""시청 유지용 효과 — 후킹 문구, 비트 펄스, 기록 카운트업·게이지, 진행 표시, 엔딩 보너스 카드.

card_mode.CardRenderer 가 프레임마다 부른다. 그리는 함수는 모두 같은 입력이면 같은 그림을 돌려준다
(렌더러가 상태 값으로 프레임을 재사용하므로).
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .card_compose import H as CARD_H, W as CARD_W, brush_band, styled_text
from .rank_extract import team_colors
from .scenes import _font, _hex

HOOK_LEN = 2.0          # 후킹 문구를 보여주는 시간(초)
HOOK_IMPACT = 0.16      # '쾅' 찍히는 순간 (효과음 시각)
COUNT_DELAY = 0.15      # 카드가 들어온 뒤 카운트업 시작
COUNT_LEN = 0.7         # 카운트업 시간
PULSE = 0.035           # 박자 펄스 크기 (배율)
PULSE_DECAY = 0.11      # 펄스가 줄어드는 시간 상수(초)
PULSE_FRAMES = 7        # 펄스를 그리는 프레임 수 (그 뒤는 정지 프레임 재사용)
BONUS_LEN = 4.0         # 엔딩 보너스 장면 길이(초)
BONUS_MIN_TOP = 5.0     # 1위 장면을 적어도 이만큼 보여준 뒤 보너스


# ---------------------------------------------------------------- 비트 분석

@dataclass
class BeatMap:
    tempo: float = 0.0
    beats: list[float] = field(default_factory=list)          # 곡 기준 초
    strong: list[tuple[float, float]] = field(default_factory=list)  # 강한 구간 [시작, 끝]

    def shifted(self, offset: float) -> "BeatMap":
        """영상 기준 시각으로 (music_start 만큼 당김)."""
        return BeatMap(self.tempo, [b - offset for b in self.beats if b >= offset],
                       [(a - offset, b - offset) for a, b in self.strong if b > offset])

    def last_beat(self, t: float) -> tuple[int, float] | None:
        """t 이전 마지막 박자 (번호, 시각)."""
        i = int(np.searchsorted(self.beats, t + 1e-9)) - 1
        return (i, self.beats[i]) if i >= 0 else None

    def is_strong(self, t: float) -> bool:
        return any(a <= t < b for a, b in self.strong)


def analyze_beats(audio: Path, *, sr: int = 22050) -> BeatMap:
    """librosa 로 박자와 강한 구간(소리 세기 상위 25%가 2초 이상 이어지는 곳 — 대개 후렴)을 찾는다."""
    import subprocess

    import librosa

    from .ffmpeg_builder import ffmpeg_bin
    # m4a 등은 librosa 가 직접 못 읽으므로 ffmpeg 로 모노 float32 PCM 으로 풀어서 넘긴다
    raw = subprocess.run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-i", str(audio), "-vn", "-ac", "1",
                          "-ar", str(sr), "-f", "f32le", "-"], capture_output=True, check=True).stdout
    y = np.frombuffer(raw, dtype=np.float32).copy()
    if y.size < sr:
        raise ValueError("곡 소리를 읽지 못했습니다")
    tempo, frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")
    beats = librosa.frames_to_time(frames, sr=sr).tolist()
    hop = 512
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    win = max(1, int(1.0 * sr / hop))
    smooth = np.convolve(rms, np.ones(win) / win, mode="same")
    thr = float(np.percentile(smooth, 75))
    times = librosa.frames_to_time(np.arange(len(smooth)), sr=sr, hop_length=hop)
    segs: list[list[float]] = []
    for t, on in zip(times, smooth >= thr):
        if on:
            if segs and t - segs[-1][1] <= 0.5:
                segs[-1][1] = float(t)
            else:
                segs.append([float(t), float(t)])
    strong = [(round(a, 2), round(b, 2)) for a, b in segs if b - a >= 2.0]
    return BeatMap(float(np.atleast_1d(tempo)[0]), [round(b, 3) for b in beats], strong)


def load_beats(project_dir: Path, music: str) -> BeatMap:
    """projects/<이름>/beats.json 에 저장해 두고 곡이 바뀌지 않았으면 다시 분석하지 않는다."""
    audio = project_dir / music
    cache = project_dir / "beats.json"
    key = f"{music}:{audio.stat().st_size}:{int(audio.stat().st_mtime)}"
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("key") == key:
            return BeatMap(data["tempo"], data["beats"], [tuple(x) for x in data["strong"]])
    bm = analyze_beats(audio)
    cache.write_text(json.dumps({"key": key, "tempo": bm.tempo, "beats": bm.beats, "strong": bm.strong},
                                ensure_ascii=False), encoding="utf-8")
    return bm


def pulse_state(bm: BeatMap | None, t: float, fps: int, flash: bool) -> tuple | None:
    """프레임 상태용 (박자 번호, 박자 뒤 프레임, 강한 박자인가). 펄스가 끝났으면 None."""
    if bm is None:
        return None
    lb = bm.last_beat(t)
    if lb is None:
        return None
    i, b = lb
    k = round((t - b) * fps)
    if k >= PULSE_FRAMES:
        return None
    return (i, k, bool(flash and bm.is_strong(b) and i % 2 == 0))


def pulse_transform(ps: tuple | None, fps: int) -> tuple[float, float, float]:
    """(배율, 흔들림 dx, 번쩍 알파 0~1)."""
    if ps is None:
        return 1.0, 0.0, 0.0
    _, k, strong = ps
    dt = k / fps
    s = 1 + PULSE * math.exp(-dt / PULSE_DECAY) * (1.6 if strong else 1.0)
    if not strong:
        return s, 0.0, 0.0
    shake = 12 * math.exp(-dt / 0.08) * math.sin(dt * 2 * math.pi * 14)
    return s, shake, 0.38 * math.exp(-dt / 0.06)


# ---------------------------------------------------------------- 카운트업

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def record_value(record: str) -> float | None:
    m = _NUM.search(record or "")
    return float(m.group(0).replace(",", "")) if m else None


def count_text(record: str, frac: float) -> str:
    """'56홈런' 의 숫자를 frac(0~1) 만큼 올린 글자. 끝(1.0)은 원래 표기 그대로."""
    m = _NUM.search(record or "")
    if not m or frac >= 1:
        return record
    raw = m.group(0)
    val = float(raw.replace(",", ""))
    dec = len(raw.split(".")[1]) if "." in raw else 0
    cur = val * frac
    num = f"{cur:,.{dec}f}" if "," in raw else f"{cur:.{dec}f}"
    return record[:m.start()] + num + record[m.end():]


def count_frac(since: float) -> float:
    """카드가 들어온 뒤 경과 초 → 카운트 진행 (처음 빠르고 끝에서 느려짐)."""
    x = (since - COUNT_DELAY) / COUNT_LEN
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    return 1 - (1 - x) ** 3


# ---------------------------------------------------------------- 게이지

@dataclass
class Gauge:
    base: float          # 기준선 값 (예: 50)
    base_label: str      # '50홈런'
    top: float           # 게이지 맨 위 값
    x: int
    y0: int              # 위
    y1: int              # 아래
    w: int = 46


def make_gauge(base_label: str, values: list[float], content_top: int, footer_y: int) -> Gauge | None:
    base = record_value(base_label)
    if base is None or not values:
        return None
    top = max(values + [base]) * 1.08
    return Gauge(base, base_label, top, x=150, y0=content_top + 430, y1=footer_y - 110)


def draw_gauge(img: Image.Image, g: Gauge, value: float, team: str, hot: str, font_path: Path) -> None:
    """세로 게이지: 기록만큼 차오르고, 기준선을 넘은 부분은 강조색."""
    d = ImageDraw.Draw(img)
    primary, secondary = team_colors(team)
    h = g.y1 - g.y0

    def y_of(v: float) -> float:
        return g.y1 - h * min(max(v / g.top, 0), 1)

    d.rounded_rectangle([g.x - 5, g.y0 - 5, g.x + g.w + 5, g.y1 + 5], radius=(g.w + 10) // 2,
                        fill=(255, 255, 255, 255), outline=(17, 17, 17, 255), width=5)
    yb = y_of(g.base)
    yv = y_of(value)
    if value > 0:
        lo = max(yv, yb)
        d.rounded_rectangle([g.x + 4, lo, g.x + g.w - 4, g.y1 - 4], radius=(g.w - 8) // 2, fill=_hex(primary) + (255,))
        if yv < yb:  # 기준선을 넘은 부분
            d.rectangle([g.x + 4, yv, g.x + g.w - 4, yb], fill=_hex(hot) + (255,))
    # 기준선
    d.line([g.x - 26, yb, g.x + g.w + 26, yb], fill=(226, 30, 30, 255), width=6)
    f = _font(str(font_path), 34)
    d.text((g.x - 32, yb), g.base_label, font=f, fill=(226, 30, 30, 255), anchor="rm",
           stroke_width=4, stroke_fill=(255, 255, 255))
    fv = _font(str(font_path), 44)
    label = f"{value:g}" if value == int(value) else f"{value:.1f}"
    d.text((g.x + g.w / 2, yv - 14), label, font=fv, fill=(17, 17, 17, 255), anchor="mb",
           stroke_width=5, stroke_fill=(255, 255, 255))


# ---------------------------------------------------------------- 진행 표시

def draw_progress(img: Image.Image, style: str, done: int, total: int, *, right: int, cy: int,
                  hot: str, font_path: Path, pop: float = 0.0) -> None:
    """done 개 채움 (5위 장면이면 1, 1위 장면이면 5). pop: 방금 켜진 점의 커짐(0~1)."""
    if style == "none":
        return
    d = ImageDraw.Draw(img)
    gold = _hex(hot)  # 1위 점·막대는 강조색
    if style == "dots":
        r, gap = 14, 12
        xs =[right - r - (total - 1 - k) * (2 * r + gap) for k in range(total)]
        for k, x in enumerate(xs):
            on = k < done
            rr = r * (1 + 0.5 * pop) if (on and k == done - 1 and pop) else r
            fill = (gold if k == total - 1 else (17, 17, 17)) if on else (255, 255, 255)
            d.ellipse([x - rr, cy - rr, x + rr, cy + rr], fill=fill + (255,), outline=(17, 17, 17, 255), width=4)
    elif style == "fraction":
        f = _font(str(font_path), 42)
        d.text((right, cy), f"{done} / {total}", font=f, fill=(17, 17, 17, 255), anchor="rm")
    elif style == "bar":
        w, h = 220, 16
        d.rounded_rectangle([right - w, cy - h / 2, right, cy + h / 2], radius=h // 2, fill=(255, 255, 255, 255),
                            outline=(17, 17, 17, 255), width=3)
        if done:
            d.rounded_rectangle([right - w + 3, cy - h / 2 + 3, right - w + 3 + (w - 6) * done / total,
                                 cy + h / 2 - 3], radius=(h - 6) // 2, fill=gold + (255,))


# ---------------------------------------------------------------- 후킹 문구

def _ease_out(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def hook_image(text: str, accent: tuple[str, ...], stat_color: str, max_w: int) -> Image.Image:
    """후킹 문구 그림 (터지는 별 모양 배경 + 굵은 글자, 2줄까지)."""
    lines = [l.strip() for l in re.split(r"\n|/", text) if l.strip()][:2] or [text]
    imgs = []
    for l in lines:
        size = 170
        im = styled_text(l, size, accent, stroke="#3B0A00", stroke_w=12, outer="#FFFFFF", outer_w=8, skew=0.1)
        while im.width > max_w - 60 and size > 60:
            size = int(size * 0.9)
            im = styled_text(l, size, accent, stroke="#3B0A00", stroke_w=12, outer="#FFFFFF", outer_w=8, skew=0.1)
        imgs.append(im)
    tw = max(i.width for i in imgs)
    th = sum(i.height for i in imgs) - 30 * (len(imgs) - 1)
    pad = 110
    cw, ch = tw + 2 * pad, th + 2 * pad
    out = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    d = ImageDraw.Draw(out)
    cx, cy = cw / 2, ch / 2
    pts = []
    n = 18
    for k in range(2 * n):
        r = (min(cw, ch) / 2 if k % 2 == 0 else min(cw, ch) / 2 * 0.72) * (1.0 if k % 4 else 0.94)
        a = math.pi * k / n
        pts.append((cx + r * math.cos(a) * cw / min(cw, ch) * 0.95, cy + r * math.sin(a)))
    d.polygon(pts, fill=_hex(stat_color) + (255,), outline=(17, 17, 17, 255), width=8)
    y = pad
    for im in imgs:
        out.alpha_composite(im, (round(cx - im.width / 2), y))
        y += im.height - 30
    return out


def hook_frame(style: str, t: float, words_img: list[Image.Image] | None = None) -> tuple[float, float, float, float]:
    """(배율, dx, dy, 섬광 알파 0~1) — t: 후킹 시작 뒤 초."""
    if style == "drop":
        if t < 0.25:
            return 1.0, 0.0, -900 * (1 - _ease_out(t / 0.25)) ** 1.2, 0.0
        k = t - 0.25
        bounce = -60 * math.exp(-k / 0.12) * abs(math.sin(k * math.pi / 0.18))
        return 1.0 + 0.06 * math.exp(-k / 0.08), 0.0, bounce, 0.45 * math.exp(-k / 0.08)
    if style == "pop_words":
        return 1.0 + 0.25 * math.exp(-t / 0.1), 0.0, 0.0, 0.0
    # slam: 2.6배에서 '쾅'
    if t < HOOK_IMPACT:
        return 2.6 - 1.6 * (t / HOOK_IMPACT) ** 2, 0.0, 0.0, 0.0
    k = t - HOOK_IMPACT
    shake = 22 * math.exp(-k / 0.12) * math.sin(k * 2 * math.pi * 16)
    return 1.0, shake, shake * 0.4, 0.75 * math.exp(-k / 0.1)


def hook_alpha(t: float, length: float = HOOK_LEN) -> float:
    """끝 0.2초 동안 사라짐."""
    return 1.0 if t < length - 0.2 else max(0.0, (length - t) / 0.2)


# ---------------------------------------------------------------- 엔딩 보너스 카드

def bonus_card(kind: str, *, rank: int | None = None, name: str = "", team: str = "", value: str = "",
               year: str = "", text: str = "", accent: tuple[str, ...] = ("#FFF27A", "#F7B500", "#C86A00")
               ) -> Image.Image:
    """보너스 장면 카드 (순위 카드와 같은 941×1672, 투명 배경). 그림 없이 글자로."""
    card = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    primary, secondary = team_colors(team) if team else ("#1F2A44", "#F7B500")
    card.alpha_composite(brush_band((CARD_W, 980), secondary, seed=61, angle=-9, thickness=0.66), (0, 330))
    card.alpha_composite(brush_band((CARD_W, 900), primary, seed=62, angle=-6, thickness=0.6), (0, 380))
    tag = styled_text("BONUS" if kind == "rank" else "NEXT", 120, ("#FFFFFF", "#FFE9A8"), stroke="#111111",
                      stroke_w=10, skew=0.12, shadow=8)
    stamp = Image.new("RGBA", (tag.width + 60, tag.height + 30), (0, 0, 0, 0))
    ImageDraw.Draw(stamp).rounded_rectangle([0, 0, stamp.width - 1, stamp.height - 1], radius=22,
                                            fill=(226, 30, 30, 255), outline=(17, 17, 17, 255), width=8)
    stamp.alpha_composite(tag, (30, 15))
    stamp = stamp.rotate(8, expand=True, resample=Image.BICUBIC)
    card.alpha_composite(stamp, ((CARD_W - stamp.width) // 2, 140))
    if kind == "rank":
        rank_img = styled_text(f"{rank}위", 250, accent, stroke="#3B0A00", stroke_w=12, outer="#FFFFFF", outer_w=6)
        card.alpha_composite(rank_img, ((CARD_W - rank_img.width) // 2, 470))
        nm = styled_text(name, 150, "#FFFFFF", stroke="#111111", stroke_w=10, skew=0.1, shadow=8)
        card.alpha_composite(nm, ((CARD_W - nm.width) // 2, 760))
        rec = styled_text(value, 190, ("#FFFFFF", "#E9EDF2", "#9AA3AE"), stroke="#111111", stroke_w=11,
                          outer=primary, outer_w=7)
        card.alpha_composite(rec, ((CARD_W - rec.width) // 2, 960))
        sub = " · ".join(x for x in (team, year) if x)
        if sub:
            s = styled_text(sub, 70, "#FFFFFF", stroke="#111111", stroke_w=6, skew=0.08, shadow=4)
            if s.width > CARD_W - 60:
                s = s.resize((CARD_W - 60, round(s.height * (CARD_W - 60) / s.width)), Image.LANCZOS)
            card.alpha_composite(s, ((CARD_W - s.width) // 2, 1240))
    else:
        lines = [l.strip() for l in re.split(r"\n|:", text) if l.strip()][:3]
        y = 520
        for i, l in enumerate(lines):
            size = 130 if i == 0 and len(lines) > 1 else 110
            im = styled_text(l, size, "#FFFFFF" if i else accent, stroke="#111111", stroke_w=10, skew=0.08, shadow=6)
            while im.width > CARD_W - 60 and size > 50:
                size = int(size * 0.9)
                im = styled_text(l, size, "#FFFFFF" if i else accent, stroke="#111111", stroke_w=10, skew=0.08,
                                 shadow=6)
            card.alpha_composite(im, ((CARD_W - im.width) // 2, y))
            y += im.height + 30
    return card
