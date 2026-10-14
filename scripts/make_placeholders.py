"""assets/placeholders/ 의 단색 도형 임시 그림을 만든다 (git 에 들어가는 유일한 템플릿 그림).

채널 로고·배경·캐릭터 원본은 git 에 넣지 않는다. 원본이 없으면 코드가 이 임시 그림을 쓰고,
임시 그림에는 PNG 글 정보(shortsmaker-placeholder=1)를 단다 (기본 캐릭터로 만든 카드는 최종 렌더에서 막힘).
실행: .venv/bin/python scripts/make_placeholders.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shortsmaker.info_cards import mark_placeholder  # noqa: E402
from shortsmaker.models import PLACEHOLDER_DIR  # noqa: E402

BLUE, YELLOW, RED, SKY, INK = "#2F6FD6", "#FFC21A", "#E5484D", "#CDE7FA", "#1E1E1E"


def _canvas(w: int, h: int, color: str | tuple = (0, 0, 0, 0)) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (w, h), color)
    return img, ImageDraw.Draw(img)


def logo() -> Image.Image:
    img, d = _canvas(600, 600)
    d.ellipse((40, 40, 560, 560), fill=BLUE)
    d.rounded_rectangle((170, 220, 430, 380), radius=40, fill=YELLOW)
    return img


def background() -> Image.Image:
    img, d = _canvas(1080, 1920, SKY)
    d.rectangle((0, 1700, 1080, 1920), fill="#9CCBF0")
    for x, y, r in ((140, 260, 90), (930, 420, 60), (820, 1500, 120)):
        d.ellipse((x - r, y - r, x + r, y + r), fill="#E6F3FD")
    return img


def style_ref() -> Image.Image:
    img, d = _canvas(800, 800, "#FFFFFF")
    d.ellipse((250, 120, 550, 420), fill=YELLOW)
    d.rounded_rectangle((220, 400, 580, 720), radius=80, fill=BLUE)
    return img


def badge_logo() -> Image.Image:
    img, d = _canvas(382, 144)
    d.rounded_rectangle((4, 4, 378, 140), radius=68, fill="#FFFFFF", outline=INK, width=4)
    d.ellipse((30, 42, 90, 102), fill=BLUE)
    d.rounded_rectangle((110, 56, 340, 88), radius=16, fill=INK)
    return img


def badge_outro() -> Image.Image:
    img, d = _canvas(990, 640)
    d.rounded_rectangle((0, 0, 990, 640), radius=48, fill="#FFFFFF", outline=BLUE, width=10)
    d.ellipse((395, 120, 595, 320), fill=YELLOW)
    d.rounded_rectangle((245, 400, 745, 460), radius=30, fill=BLUE)
    return img


def badge_character() -> Image.Image:
    img, d = _canvas(420, 840)
    d.ellipse((110, 20, 310, 220), fill=YELLOW)
    d.rounded_rectangle((60, 240, 360, 820), radius=120, fill=RED)
    return img


MAKERS = {
    "logo.png": logo,
    "background.png": background,
    "style_ref.png": style_ref,
    "sky_badge_logo.png": badge_logo,
    "sky_badge_outro.png": badge_outro,
    "sky_badge_character.png": badge_character,
}


def main() -> None:
    PLACEHOLDER_DIR.mkdir(parents=True, exist_ok=True)
    for name, make in MAKERS.items():
        path = PLACEHOLDER_DIR / name
        make().save(path, optimize=True)
        mark_placeholder(path)
    print("임시 그림:", ", ".join(MAKERS))


if __name__ == "__main__":
    main()
