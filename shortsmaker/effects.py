"""시청 유지용 효과 — 후킹 문구, 비트 펄스, 기록 카운트업, 진행 표시, 엔딩 보너스 카드, 그래프, 키워드.

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

BONUS_FLICKER = [(0.55, 0.65), (0.75, 0.82), (1.25, 1.6)]  # 보너스 반전: 꺼지는 구간 (장면 시작 뒤 초)


def bonus_flicker(t: float) -> bool:
    return any(a <= t < b for a, b in BONUS_FLICKER)


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


# ---------------------------------------------------------------- 데이터 그래픽 (1위 뒤 막대그래프)

CHART_LEN = 2.0          # 보너스 직전 그래프 장면 길이
CHART_GROW = 0.8         # 막대가 자라는 시간


@dataclass
class ChartBar:
    label: str           # '5위'
    name: str
    value: float
    record: str
    team: str
    year: str
    top: bool = False


def chart_bars(items) -> list[ChartBar]:
    """영상 순서(5위→1위)대로. items: Project.ordered_items."""
    bars = []
    for it in items:
        v = record_value(it.value)
        if v is None:
            continue
        year = next(iter(re.findall(r"(?<!\d)(\d{4})(?!\d)", it.description or "")), "")
        team = re.sub(r"\s*\d{4}\s*$", "", it.description or "").strip()
        bars.append(ChartBar(f"{it.shown_rank}위", it.name, v, it.value, team, year, top=it.shown_rank == 1))  # 공동 1위 모두
    return bars


def draw_chart(img: Image.Image, bars: list[ChartBar], t: float, *, base: float | None, base_label: str,
               years: bool, hot: str, accent: tuple[str, ...], font_path: Path, top_y: int, bottom_y: int,
               left: int = 90, right: int | None = None, clean: bool = False) -> None:
    """막대그래프 (기준선 포함). t: 장면 시작 뒤 초 (막대가 차례로 자람)."""
    if not bars:
        return
    W_ = img.width
    d = ImageDraw.Draw(img)
    n = len(bars)
    right = W_ - 90 if right is None else right
    slot = (right - left) / n
    bw = slot * 0.58
    axis_y = bottom_y - (260 if years else 150)
    maxv = max([b.value for b in bars] + ([base] if base else [])) * 1.1
    hmax = axis_y - top_y - 90
    f_val = _font(str(font_path), 48)
    f_name = _font(str(font_path), 40)
    f_small = _font(str(font_path), 32)
    d.line([left - 20, axis_y, right + 20, axis_y], fill=(200, 204, 212, 255) if clean else (17, 17, 17, 255),
           width=3 if clean else 6)
    for i, b in enumerate(bars):
        grow = min(1.0, max(0.0, (t - 0.08 * i) / CHART_GROW))
        grow = 1 - (1 - grow) ** 3
        cx = left + slot * (i + 0.5)
        h = hmax * b.value / maxv * grow
        primary, secondary = team_colors(b.team)
        x0, x1 = cx - bw / 2, cx + bw / 2
        if h > 2:
            if clean:  # 클린: 테두리 없이 채널 파랑 막대, 1위만 강조색
                d.rounded_rectangle([x0, axis_y - h, x1, axis_y], radius=14,
                                    fill=_hex(hot if b.top else "#9DB4E6") + (255,))
            else:
                d.rounded_rectangle([x0, axis_y - h, x1, axis_y], radius=12, fill=_hex(primary) + (255,),
                                    outline=_hex(hot) + (255,) if b.top else (17, 17, 17, 255),
                                    width=10 if b.top else 5)
        val = b.value * grow
        txt = f"{val:.0f}" if b.value == int(b.value) else f"{val:.1f}"
        d.text((cx, axis_y - h - 12), txt, font=f_val, fill=(17, 17, 17, 255), anchor="mb",
               stroke_width=0 if clean else 5, stroke_fill=(255, 255, 255))
        d.text((cx, axis_y + 14), b.name, font=f_name, fill=(17, 17, 17, 255), anchor="mt")
        d.text((cx, axis_y + 62), b.label, font=f_small, fill=_hex(hot) + (255,) if b.top else (90, 90, 90, 255),
               anchor="mt")
    if base:
        yb = axis_y - hmax * base / maxv
        for x in range(left - 20, right + 10, 36):  # 점선
            d.line([x, yb, x + 20, yb], fill=(170, 176, 186, 255) if clean else (226, 30, 30, 255),
                   width=3 if clean else 6)
        red = (150, 156, 166, 255) if clean else (226, 30, 30, 255)
        d.text((right + 24, yb), base_label, font=f_small, fill=red, anchor="lm",  # 그래프 오른쪽 바깥
               stroke_width=0 if clean else 4, stroke_fill=(255, 255, 255))
    if years:  # 연도 타임라인: 축 위에 선수별 점
        ys = [int(b.year) for b in bars if b.year]
        if ys:
            y0, y1 = min(ys), max(ys)
            span = max(1, y1 - y0)
            ty = bottom_y - 90
            d.line([left, ty, right, ty], fill=(120, 120, 120, 255), width=5)
            for b in bars:
                if not b.year:
                    continue
                x = left + (right - left) * (int(b.year) - y0) / span
                primary, _ = team_colors(b.team)
                r = 16 if b.top else 12
                d.ellipse([x - r, ty - r, x + r, ty + r], fill=_hex(primary) + (255,), outline=(17, 17, 17, 255),
                          width=4)
            d.text((left, ty + 24), str(y0), font=f_small, fill=(90, 90, 90, 255), anchor="lt")
            d.text((right, ty + 24), str(y1), font=f_small, fill=(90, 90, 90, 255), anchor="rt")


# ---------------------------------------------------------------- 키워드 터뜨리기

BURST_LEN = 0.6
BURST_GAP = 5.0          # 터뜨림 사이 최소 간격
BURST_MAX = 3


KEYWORDS = ("역대 최고", "역대 최다", "역대 최초", "신기록", "최초", "최고", "최다", "최악", "레전드", "전설", "반전", "충격")


def _norm(t: str) -> str:
    return re.sub(r"[\s?!.,…~]+", "", t)


def pick_bursts(lines, times_of, names: list[str], strong: list[tuple[float, float]],
                blocked: list[tuple[float, float]], on_cards: list[str] | None = None) -> list[tuple[float, str]]:
    """강한 구간(후렴)에서 숫자·순위·핵심 단어('역대 최고' 등)를 부르는 순간. 간격 BURST_GAP 이상, 최대 BURST_MAX 번.

    on_cards: 카드에 이미 크게 나오는 글자(기록·이름·순위·팀·연도) — 같은 말이면 터뜨리지 않는다."""
    words = sorted(KEYWORDS, key=len, reverse=True)
    pat = re.compile(r"(공동\s*\d위[?!]*|\d+위[?!]*|\d[\d,]*(?:\.\d+)?[가-힣%]*[?!]*"
                     + "".join(f"|{re.escape(w)}[?!]*" for w in words)
                     + "".join(f"|{re.escape(n)}" for n in names if n) + ")")
    card_words = {_norm(w) for c in (on_cards or []) for w in [c, *c.split()] if w}
    cands = []
    for li, l in enumerate(lines):
        ts = times_of(li)
        for m in pat.finditer(l.text):
            word = m.group(1)
            if _norm(word) in card_words:
                continue  # 카드 숫자·이름과 같은 말은 생략
            t = ts[m.start()] if m.start() < len(ts) else l.start
            if any(a <= t < b for a, b in strong) and not any(a - 0.3 <= t < b for a, b in blocked):
                cands.append((t, word))
    out: list[tuple[float, str]] = []
    for t, word in sorted(cands):
        if len(out) < BURST_MAX and (not out or t - out[-1][0] >= BURST_GAP):
            out.append((t, word))
    return out


def burst_image(word: str, accent: tuple[str, ...], max_w: int, max_h: int | None = None) -> Image.Image:
    size = 260
    im = styled_text(word, size, accent, stroke="#3B0A00", stroke_w=14, outer="#FFFFFF", outer_w=9, skew=0.12)
    while (im.width > max_w or (max_h and im.height > max_h)) and size > 60:
        size = int(size * 0.9)
        im = styled_text(word, size, accent, stroke="#3B0A00", stroke_w=14, outer="#FFFFFF", outer_w=9, skew=0.12)
    return im


def clean_burst_image(word: str, accent: str, max_w: int, max_h: int) -> Image.Image:
    """클린: 채널 파랑 굵은 글씨 + 강조색 밑줄 하이라이트."""
    return marker_text(word, "#0043C8", accent, max_w, max_h, size=170)


def marker_text(text: str, color: str, accent: str, max_w: int, max_h: int | None = None, size: int = 150,
                progress: float = 1.0, split: bool = False, font: Path | None = None) -> Image.Image:
    """굵은 글씨 + 아래쪽 절반에 형광펜처럼 깔린 강조색 띠 (줄마다).
    progress: 띠가 왼쪽에서 오른쪽으로 그어진 정도 (0~1, 줄마다 차례로)."""
    from .card_clean import FONT_BLACK, soft_text
    font = font or FONT_BLACK
    lines = [l.strip() for l in re.split(r"\n| / ", text) if l.strip()][:2] or [text]
    if split and len(lines) == 1 and " " in lines[0]:  # 한 줄로는 줄여야 하면 가운데 띄어쓰기에서 두 줄로
        one = soft_text(lines[0], size, color, font, shadow=8, alpha=0.15)
        if one.width > max_w:
            words = lines[0].split(" ")
            cut = min(range(1, len(words)), key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))))
            lines = [" ".join(words[:cut]), " ".join(words[cut:])]
    while True:
        imgs = [soft_text(l, size, color, font, shadow=8, alpha=0.15) for l in lines]
        tw = max(i.width for i in imgs)
        th = sum(i.height for i in imgs) + 24 * (len(imgs) - 1)
        if (tw <= max_w and (not max_h or th + 20 <= max_h)) or size <= 50:
            break
        size = int(size * 0.92)
    out = Image.new("RGBA", (tw + 40, th + 20), (0, 0, 0, 0))
    d = ImageDraw.Draw(out)
    y = 0
    n = len(imgs)
    for k, im in enumerate(imgs):
        x = (out.width - im.width) // 2
        hy = y + im.height * 0.55
        p = min(1.0, max(0.0, progress * n - k))  # 줄마다 차례로 그어짐
        if p > 0:
            x1 = x - 14 + (im.width + 28) * p
            d.rounded_rectangle([x - 14, hy, max(x - 13, x1), y + im.height + 8], radius=10,
                                fill=_hex(accent) + (110,))
        out.alpha_composite(im, (x, y))
        y += im.height + 24
    return out


HOOK_WIPE = (0.25, 0.5)   # 클린 후킹: 밑줄이 그어지기 시작하는 시각, 걸리는 시간


def clean_hook_image(text: str, accent: str, width: int, progress: float = 1.0, size: int = 195) -> Image.Image:
    """클린 후킹: 폭발 도형 대신 굵은 글씨(예전의 1.3배) + 강조색 밑줄이 왼쪽→오른쪽으로 그어짐."""
    from .card_clean import FONT_DISPLAY
    return marker_text(text, "#14171C", accent, width - 80, size=size, progress=progress, split=True,
                       font=FONT_DISPLAY)  # 후킹 문구는 G마켓 산스


def hook_wipe(t: float) -> float:
    a, d = HOOK_WIPE
    x = min(1.0, max(0.0, (t - a) / d))
    return 1 - (1 - x) ** 3


def burst_frame(style: str, t: float) -> tuple[float, float, float]:
    """(배율, 각도, 알파) — t: 터뜨린 뒤 초."""
    fade = 1.0 if t < BURST_LEN - 0.15 else max(0.0, (BURST_LEN - t) / 0.15)
    if style == "stamp":
        s = 2.0 - 1.0 * min(1.0, t / 0.12) if t < 0.12 else 1.0
        return s, -8.0, fade
    return 0.6 + 0.6 * min(1.0, t / 0.25) ** 0.5, 0.0, min(1.0, t / 0.08) * fade


# ---------------------------------------------------------------- 2.5D 패럴랙스

LAYER_NAMES = ("back", "art", "front", "text")


def parallax(kind: str, t: float) -> dict[str, tuple[float, float]]:
    """층별 (배율, 가로 이동 px — 카드 원본 941px 기준). t: 등장 효과가 끝난 뒤 초."""
    if kind == "dolly":  # 앞층일수록 더 빨리 다가옴
        k = min(t, 8.0) / 8.0
        return {"back": (1 + 0.02 * k, 0.0), "art": (1 + 0.06 * k, 0.0), "front": (1 + 0.1 * k, 0.0),
                "text": (1.0, 0.0)}
    if kind == "pan":  # 앞층일수록 더 크게 옆으로 흐름 (6초 주기 왕복)
        s = math.sin(2 * math.pi * t / 6.0)
        return {"back": (1.03, -14 * s), "art": (1.03, 18 * s), "front": (1.03, 34 * s), "text": (1.0, 0.0)}
    return {n: (1.0, 0.0) for n in LAYER_NAMES}
