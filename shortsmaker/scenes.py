"""Pillow로 장면 PNG 레이어 생성.

장면마다 다음 레이어를 만든다 (FFmpeg에서 효과를 주기 위해 분리):
  base   : 배경 + 움직이지 않는 텍스트 (1080×1920, RGB)
  pop    : 튀어나오는 강조 요소 — 순위 숫자 / 인트로 TOP 5 배지 (박스 크기, RGBA)
  image  : 줌인할 캐리커처 카드 (박스 크기, RGBA)
  preview: 위 레이어를 최종 상태로 합친 썸네일 (Streamlit 미리보기용)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .models import Box, ImageBox, Project, Template, TextBox
from .timeline import Scene


# ---------------------------------------------------------------- 텍스트 맞춤

@dataclass
class FittedText:
    font: ImageFont.FreeTypeFont
    lines: list[str]
    line_height: int
    block_height: int
    overflow: bool


@lru_cache(maxsize=256)
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _wrap(text: str, font: ImageFont.FreeTypeFont, width: float) -> list[str]:
    """공백 단위로 줄바꿈하고, 한 단어가 너무 길면 글자 단위로 자른다."""
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split(" "):
            cand = f"{cur} {word}" if cur else word
            if font.getlength(cand) <= width:
                cur = cand
                continue
            if cur:
                lines.append(cur)
            cur = ""
            for ch in word:  # 단어 자체가 너무 길면 글자 단위
                if font.getlength(cur + ch) <= width or not cur:
                    cur += ch
                else:
                    lines.append(cur)
                    cur = ch
        lines.append(cur)
    return lines


def fit_text(text: str, box: TextBox, font_path: Path) -> FittedText:
    """박스에 맞을 때까지 글자 크기를 줄인다. 최소 크기에서도 넘치면 overflow=True."""
    width = box.w - 2 * box.stroke_width
    size = box.size
    while True:
        font = _font(str(font_path), size)
        asc, desc = font.getmetrics()
        line_h = round((asc + desc) * box.line_spacing)
        lines = _wrap(text, font, width)
        block_h = (asc + desc) + (len(lines) - 1) * line_h + 2 * box.stroke_width
        fits = len(lines) <= box.max_lines and block_h <= box.h
        if fits or size <= box.min_size:
            break
        size = max(box.min_size, size - 2)
    if not fits:
        lines = lines[: box.max_lines]
        last = lines[-1]
        while last and font.getlength(last + "…") > width:
            last = last[:-1]
        lines[-1] = last + "…"
        block_h = (asc + desc) + (len(lines) - 1) * line_h + 2 * box.stroke_width
    return FittedText(font, lines, line_h, block_h, overflow=not fits)


def draw_text(
    img: Image.Image,
    box: TextBox,
    text: str,
    font_path: Path,
    *,
    color: str | None = None,
    offset: tuple[int, int] = (0, 0),
) -> FittedText:
    """box 안에 text를 세로 가운데 정렬로 그린다. offset은 박스 좌표에서 뺄 값(레이어용)."""
    ft = fit_text(text, box, font_path)
    d = ImageDraw.Draw(img)
    bx, by = box.x - offset[0], box.y - offset[1]
    y = by + (box.h - ft.block_height) / 2 + box.stroke_width
    anchor, x = {
        "left": ("la", bx + box.stroke_width),
        "center": ("ma", bx + box.w / 2),
        "right": ("ra", bx + box.w - box.stroke_width),
    }[box.align]
    for line in ft.lines:
        d.text(
            (x, y), line, font=ft.font, fill=color or box.color, anchor=anchor,
            stroke_width=box.stroke_width, stroke_fill=box.stroke_color,
        )
        y += ft.line_height
    return ft


# ---------------------------------------------------------------- 배경·이미지

def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def make_background(t: Template) -> Image.Image:
    """템플릿 배경 이미지, 없으면 세로 그라데이션."""
    bg_path = t.background_path()
    if bg_path:
        return ImageOps.fit(Image.open(bg_path).convert("RGB"), (t.width, t.height))
    top, bottom = _hex(t.gradient[0]), _hex(t.gradient[-1])
    grad = Image.new("RGB", (1, t.height))
    for y in range(t.height):
        k = y / (t.height - 1)
        grad.putpixel((0, y), tuple(round(a + (b - a) * k) for a, b in zip(top, bottom)))
    return grad.resize((t.width, t.height))


def _shape_mask(size: tuple[int, int], box: ImageBox, inset: int = 0) -> Image.Image:
    mask = Image.new("L", size, 0)
    d = ImageDraw.Draw(mask)
    rect = [inset, inset, size[0] - 1 - inset, size[1] - 1 - inset]
    if box.shape == "circle":
        d.ellipse(rect, fill=255)
    elif box.shape == "rounded":
        d.rounded_rectangle(rect, radius=max(0, box.radius - inset), fill=255)
    else:
        d.rectangle(rect, fill=255)
    return mask


def _placeholder(size: tuple[int, int], label: str, font_path: Path) -> Image.Image:
    img = Image.new("RGB", size, "#555b66")
    d = ImageDraw.Draw(img)
    d.text((size[0] / 2, size[1] / 2), label, font=_font(str(font_path), 56), fill="#dddddd", anchor="mm")
    return img


def make_image_card(src: Path | None, box: ImageBox, font_path: Path) -> tuple[Image.Image, bool]:
    """캐리커처를 박스 크기로 꽉 채우고(cover) 모양·테두리를 입힌 RGBA 카드. (카드, 이미지없음여부)"""
    size = (box.w, box.h)
    bw = box.border_width
    inner = (box.w - 2 * bw, box.h - 2 * bw)
    missing = src is None or not src.exists()
    photo = _placeholder(inner, "이미지 없음", font_path) if missing else ImageOps.fit(
        ImageOps.exif_transpose(Image.open(src)).convert("RGB"), inner, Image.Resampling.LANCZOS
    )
    card = Image.new("RGBA", size, (0, 0, 0, 0))
    if bw:
        border = Image.new("RGBA", size, _hex(box.border_color) + (255,))
        card.paste(border, (0, 0), _shape_mask(size, box))
    inner_mask = _shape_mask(size, box, inset=bw).crop((bw, bw, bw + inner[0], bw + inner[1]))
    card.paste(photo, (bw, bw), inner_mask)
    return card, missing


# ---------------------------------------------------------------- 장면

@dataclass
class Layer:
    path: Path
    box: Box


@dataclass
class SceneLayers:
    scene: Scene
    base: Path
    preview: Path
    pop: Layer | None = None
    image: Layer | None = None


@dataclass
class SceneBuildResult:
    scenes: list[SceneLayers]
    warnings: list[str] = field(default_factory=list)


def _text_layer(box: TextBox, text: str, font_path: Path, color: str | None = None) -> tuple[Image.Image, FittedText]:
    layer = Image.new("RGBA", (box.w, box.h), (0, 0, 0, 0))
    ft = draw_text(layer, box, text, font_path, color=color, offset=(box.x, box.y))
    return layer, ft


def build_scenes(
    project: Project,
    project_dir: Path,
    template: Template,
    scenes: list[Scene],
    work_dir: Path,
) -> SceneBuildResult:
    work_dir.mkdir(parents=True, exist_ok=True)
    t = template
    fp = t.font_path
    background = make_background(t)
    result = SceneBuildResult(scenes=[])

    def text(img: Image.Image, box: TextBox, value: str, what: str, color: str | None = None) -> None:
        if not value:
            return
        ft = draw_text(img, box, value, fp(box.font), color=color)
        if ft.overflow:
            result.warnings.append(f"[{what}] 글자가 박스를 넘쳐 잘렸습니다(최소 {box.min_size}px): {value}")

    for sc in scenes:
        base = background.copy()
        layers: dict[str, Layer] = {}
        stem = f"scene{sc.index:02d}"

        if sc.kind == "intro":
            L = t.intro
            pop_img, ft = _text_layer(L.badge, L.badge_text, fp(L.badge.font))
            pop_path = work_dir / f"{stem}_pop.png"
            pop_img.save(pop_path)
            layers["pop"] = Layer(pop_path, L.badge)
            text(base, L.title, project.title, "제목")
            text(base, L.subtitle, project.subtitle, "부제")

        elif sc.kind == "rank":
            L = t.rank
            item = sc.item
            assert item is not None
            text(base, L.header, project.title, "상단 제목")
            text(base, L.name, item.name, f"{item.rank}위 이름")
            text(base, L.value, item.value, f"{item.rank}위 값")
            text(base, L.description, item.description, f"{item.rank}위 설명")

            color = L.rank_one_color if item.rank == 1 and L.rank_one_color else None
            pop_img, ft = _text_layer(L.rank, L.rank_format.format(rank=item.rank), fp(L.rank.font), color)
            pop_path = work_dir / f"{stem}_pop.png"
            pop_img.save(pop_path)
            layers["pop"] = Layer(pop_path, L.rank)

            src = (project_dir / item.image) if item.image else None
            card, missing = make_image_card(src, L.image, fp("bold"))
            if missing:
                result.warnings.append(f"[{item.rank}위] 이미지가 없어 대체 이미지를 사용합니다: {item.image}")
            img_path = work_dir / f"{stem}_image.png"
            card.save(img_path)
            layers["image"] = Layer(img_path, L.image)

        else:  # outro
            L = t.outro
            text(base, L.cta, L.cta_text, "아웃트로 문구")
            text(base, L.source, project.source_note, "출처")

        base_path = work_dir / f"{stem}_base.png"
        base.save(base_path)

        preview = base.convert("RGBA")
        for key in ("image", "pop"):
            if key in layers:
                lay = layers[key]
                preview.alpha_composite(Image.open(lay.path), (lay.box.x, lay.box.y))
        preview_path = work_dir / f"{stem}_preview.png"
        preview.convert("RGB").save(preview_path)

        result.scenes.append(SceneLayers(
            scene=sc, base=base_path, preview=preview_path,
            pop=layers.get("pop"), image=layers.get("image"),
        ))
    return result


def contact_sheet(paths: list[Path], out: Path, thumb_w: int = 270) -> Path:
    """미리보기 PNG들을 가로로 이어 붙인 한 장짜리 시트."""
    thumbs = [Image.open(p).convert("RGB") for p in paths]
    th = round(thumbs[0].height * thumb_w / thumbs[0].width)
    gap = 12
    sheet = Image.new("RGB", (len(thumbs) * (thumb_w + gap) + gap, th + 2 * gap), "#111111")
    for i, im in enumerate(thumbs):
        sheet.paste(im.resize((thumb_w, th), Image.Resampling.LANCZOS), (gap + i * (thumb_w + gap), gap))
    sheet.save(out)
    return out
