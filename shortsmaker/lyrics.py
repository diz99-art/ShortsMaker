"""가사: 수동 입력·LRC 파싱 → 자막 타이밍 → 자막 레이어.

자막 표시 방식
  - libass가 있는 FFmpeg: ASS 자막 파일 + subtitles 필터 (fontsdir 지정)
  - 없으면: 가사 줄마다 Pillow PNG를 만들어 시간 구간 overlay
"""
from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw

from .ffmpeg_builder import TimedOverlay, ffmpeg_bin
from .models import FONTS_DIR, LyricLine, LyricsBox, Project, Template
from .scenes import _font, _hex, draw_text, fit_text

_TIME_TAG = re.compile(r"\[(\d+):(\d{1,2}(?:\.\d{1,3})?)\]")

DEFAULT_LAST_HOLD = 4.0  # LRC 마지막 줄 표시 시간
MAX_HOLD = 8.0           # LRC 한 줄 최대 표시 시간 (다음 줄이 멀어도)


# ---------------------------------------------------------------- 타이밍

def parse_lrc(text: str, duration: float | None = None) -> list[LyricLine]:
    """LRC 텍스트 → 가사 줄. 각 줄은 다음 타임태그 직전까지 표시하고, 빈 줄은 '가사 없음' 구간."""
    stamps: list[tuple[float, str]] = []
    for raw in text.splitlines():
        tags = _TIME_TAG.findall(raw)
        if not tags:
            continue  # [ti:], [ar:] 같은 메타 태그·빈 줄
        lyric = _TIME_TAG.sub("", raw).strip()
        for m, s in tags:
            stamps.append((int(m) * 60 + float(s), lyric))
    stamps.sort(key=lambda x: x[0])

    lines: list[LyricLine] = []
    for i, (start, lyric) in enumerate(stamps):
        if not lyric:
            continue
        nxt = stamps[i + 1][0] if i + 1 < len(stamps) else start + DEFAULT_LAST_HOLD
        end = min(nxt, start + MAX_HOLD)
        if duration is not None:
            if start >= duration:
                break
            end = min(end, duration)
        if end > start:
            lines.append(LyricLine(start=round(start, 3), end=round(end, 3), text=lyric))
    return lines


def resolve_lyrics(project: Project, project_dir: Path) -> list[LyricLine]:
    """프로젝트의 가사 원본(직접 입력 또는 LRC)을 시간순 가사 줄로."""
    if project.lyrics_lrc:
        path = project_dir / project.lyrics_lrc
        return parse_lrc(path.read_text(encoding="utf-8-sig"), project.duration)
    return sorted(project.lyrics, key=lambda l: l.start)


def check_lyrics(lines: list[LyricLine], duration: float) -> list[str]:
    """겹침·영상 길이 초과 검사. 문제 목록(비어 있으면 통과)."""
    errors: list[str] = []
    for a, b in zip(lines, lines[1:]):
        if b.start < a.end - 1e-6:
            errors.append(f"가사 구간이 겹칩니다: '{a.text}'({a.start}~{a.end}) / '{b.text}'({b.start}~{b.end})")
    for l in lines:
        if l.end > duration + 1e-6:
            errors.append(f"가사가 영상 길이({duration:g}초)를 넘습니다: '{l.text}'({l.start}~{l.end})")
        if not l.text.strip():
            errors.append(f"빈 가사 줄이 있습니다 ({l.start}~{l.end})")
    return errors


# ---------------------------------------------------------------- PNG 방식

def render_lyric_pngs(lines: list[LyricLine], t: Template, work_dir: Path) -> tuple[list[TimedOverlay], list[str]]:
    """가사 줄마다 박스 크기 RGBA PNG (반투명 배경 + 글자). (overlay 목록, 경고 목록)"""
    box = t.lyrics_box
    work_dir.mkdir(parents=True, exist_ok=True)
    overlays: list[TimedOverlay] = []
    warnings: list[str] = []
    for i, line in enumerate(lines):
        img = Image.new("RGBA", (box.w, box.h), (0, 0, 0, 0))
        ft = fit_text(line.text, box, t.font_path(box.font))
        if ft.overflow:
            warnings.append(f"[가사] 글자가 박스를 넘쳐 잘렸습니다: {line.text}")
        if box.bg_color and box.bg_opacity > 0:
            text_w = max(ft.font.getlength(s) for s in ft.lines) + 2 * box.stroke_width
            pad = box.bg_padding
            w = min(box.w, text_w + 2 * pad * 1.6)
            h = min(box.h, ft.block_height + 2 * pad)
            x0 = {"left": 0, "center": (box.w - w) / 2, "right": box.w - w}[box.align]
            y0 = (box.h - h) / 2
            ImageDraw.Draw(img).rounded_rectangle(
                [x0, y0, x0 + w, y0 + h], radius=min(h / 2, 40),
                fill=_hex(box.bg_color) + (round(255 * box.bg_opacity),),
            )
        draw_text(img, box, line.text, t.font_path(box.font), offset=(box.x, box.y))
        path = work_dir / f"lyric{i:03d}.png"
        img.save(path)
        overlays.append(TimedOverlay(path, box.x, box.y, line.start, line.end))
    return overlays, warnings


# ---------------------------------------------------------------- ASS 방식

@lru_cache(maxsize=1)
def has_libass() -> bool:
    out = subprocess.run([ffmpeg_bin(), "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    return bool(re.search(r"^\s*\S+\s+subtitles\s", out, re.M))


def _ass_color(hex_color: str, opacity: float = 1.0) -> str:
    r, g, b = _hex(hex_color)
    a = round(255 * (1 - opacity))
    return f"&H{a:02X}{b:02X}{g:02X}{r:02X}"


def _ass_time(t: float) -> str:
    cs = round(t * 100)
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def build_ass(lines: list[LyricLine], t: Template) -> str:
    box: LyricsBox = t.lyrics_box
    family, style = _font(str(t.font_path(box.font)), box.size).getname()
    bold = -1 if any(k in (style or "").lower() for k in ("bold", "black", "heavy")) else 0
    align = {"left": 1, "center": 2, "right": 3}[box.align]
    margin_v = t.height - (box.y + box.h)
    border_style = 3 if box.bg_color and box.bg_opacity > 0 else 1
    back = _ass_color(box.bg_color or "#000000", box.bg_opacity)
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {t.width}\nPlayResY: {t.height}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Lyric,{family},{box.size},{_ass_color(box.color)},{_ass_color(box.color)},"
        f"{_ass_color(box.stroke_color) if border_style == 1 else back},{back},{bold},0,0,0,100,100,0,0,"
        f"{border_style},{box.stroke_width if border_style == 1 else box.bg_padding // 2},0,"
        f"{align},{box.x},{t.width - box.x - box.w},{margin_v},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = [
        f"Dialogue: 0,{_ass_time(l.start)},{_ass_time(l.end)},Lyric,,0,0,0,,"
        + l.text.replace("\n", "\\N").replace("{", "(").replace("}", ")")
        for l in lines
    ]
    return header + "\n".join(events) + "\n"


def subtitles_filter(ass_path: Path) -> str:
    def esc(p: Path) -> str:
        return str(p).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    return f"subtitles=filename='{esc(ass_path)}':fontsdir='{esc(FONTS_DIR)}'"
