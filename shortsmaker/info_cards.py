"""정보 채널(하늘 배지형·info_v2): 항목(이름·값·설명·이미지)으로 클린 카드를 자동 합성한다.

야구한장은 '노래로 자동 제작'이 캐리커처로 카드를 만들지만, 정보 TOP 5 는 나라·물건 이미지(배경 있음)가 대부분이라
렌더할 때 항목 값으로 바로 카드를 만든다. 결과는 projects/<이름>/cards_auto/ 에 두고, 입력이 같으면 다시 만들지 않는다.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image, PngImagePlugin

from .card_clean import compose_clean, soft_text
from .models import Project, Template
from .variation import ACCENTS, Variation

PLACEHOLDER_KEY = "shortsmaker-placeholder"  # PNG 글 정보: 임시 그림 (수익화용 렌더에서 막음)


def is_placeholder(path: Path) -> bool:
    try:
        with Image.open(path) as im:
            return str(im.info.get(PLACEHOLDER_KEY, "")) == "1"
    except OSError:
        return False


def mark_placeholder(path: Path) -> None:
    """이미 있는 그림에 '임시' 표시를 단다 (그림은 그대로)."""
    with Image.open(path) as im:
        img = im.copy()
    info = PngImagePlugin.PngInfo()
    info.add_text(PLACEHOLDER_KEY, "1")
    img.save(path, pnginfo=info)


def placeholder_art(name: str) -> Image.Image:
    """그림이 없을 때 임시 글자 카드 (이름 앞 글자). 시험 렌더용으로만."""
    img = Image.new("RGB", (900, 700), (226, 231, 238))
    t = soft_text((name or "?")[:3], 260, (120, 130, 146), shadow=0)
    img.paste(t, ((900 - t.width) // 2, (700 - t.height) // 2), t)
    return img


def placeholder_problems(project: Project, project_dir: Path) -> list[str]:
    """그림이 없거나 임시 글자 카드인 항목 (수익화용 렌더 차단 사유)."""
    out = []
    for it in project.ordered_items:
        p = project_dir / it.image if it.image else None
        if p is None or not p.is_file():
            out.append(f"[{it.shown_rank}위 {it.name}] 그림이 없습니다 (임시 그림으로 시험 렌더만 가능)")
        elif is_placeholder(p):
            out.append(f"[{it.shown_rank}위 {it.name}] 그림이 임시 그림입니다 — 실제 그림으로 바꾸세요")
    return out


ZIP_CALM = {"count": "static", "datagfx": "off", "burst": "off", "progress": "none", "mascot": "off"}


def _prepare_zip(project: Project, project_dir: Path, t: Template, vd: dict) -> Project:
    """하늘 배지형: 배지 + 캐릭터 장면 그림. 캐릭터가 없으면 기준 캐릭터(임시, 수익화용 렌더에서 막힘)."""
    from .card_zip import compose_zip
    out = project_dir / "cards_auto"
    out.mkdir(exist_ok=True)
    default = t.brand_asset(t.card.default_character)
    items = []
    for it in project.items:
        src = project_dir / it.image if it.image and (project_dir / it.image).is_file() else None
        if src is not None and src.parent.name == "cards_auto":
            src = None
        icon = project_dir / it.icon if it.icon and (project_dir / it.icon).is_file() else None
        tied = sum(1 for o in project.items if o.shown_rank == it.shown_rank) > 1
        label = ("공동 " if tied else "") + f"{it.shown_rank}위"
        key = json.dumps(["zip", label, it.rank, it.name, it.value, it.description, it.year, project.value_label,
                          str(src), src.stat().st_mtime if src else 0, str(icon), icon.stat().st_mtime if icon else 0,
                          str(default)], ensure_ascii=False)
        h = hashlib.sha1(key.encode()).hexdigest()[:10]
        card_p = out / f"zip{it.rank}_{h}.png"
        if not card_p.exists():
            compose_zip(src or default, rank_label=label, name=it.name, value=it.value,
                        value_label=project.value_label, category=it.description, year=it.year, icon=icon,
                        top=it.shown_rank == 1).save(card_p)
            if src is None:  # 기준 캐릭터로 채운 장면은 임시 표시
                mark_placeholder(card_p)
        items.append(it.model_copy(update={"image": f"cards_auto/{card_p.name}"}))
    return project.model_copy(update={"items": items, "variation": {**vd, **ZIP_CALM}})


def prepare_cards(project: Project, project_dir: Path, t: Template) -> Project:
    vd = dict(project.variation or {})
    vd.setdefault("look", t.card.default_look)
    if t.card.scene_style == "zip":
        return _prepare_zip(project, project_dir, t, vd)
    v = Variation.model_validate(vd)
    look = v.look if v.look in ("clean_a", "clean_b") else "clean_a"
    accent = t.card.accent_color or ACCENTS.get(v.accent, ACCENTS["red_gold"])["stat"]
    main = t.card.main_color or "#0043C8"
    out = project_dir / "cards_auto"
    out.mkdir(exist_ok=True)
    items = []
    for it in project.items:
        src = project_dir / it.image if it.image and (project_dir / it.image).is_file() else None
        if src is not None and src.parent.name == "cards_auto":
            src = None  # 이미 자동 카드로 바뀐 프로젝트를 다시 넣은 경우
        tied = sum(1 for o in project.items if o.shown_rank == it.shown_rank) > 1
        key = json.dumps([look, accent, main, it.shown_rank, it.rank, it.name, it.value, it.description, it.year, tied,
                          str(src), src.stat().st_mtime if src else 0], ensure_ascii=False)
        h = hashlib.sha1(key.encode()).hexdigest()[:10]
        card_p = out / f"card{it.rank}_{h}.png"
        if not card_p.exists():
            # 이름 아래 작은 글씨: '구분 · 2024년 기준'
            kw = dict(rank=it.shown_rank, record=it.value, year=f"{it.year}년 기준" if it.year else "",
                      team=it.description, player=it.name, tied=tied, accent=accent, top=it.shown_rank == 1, main=main)
            art = src if src is not None else placeholder_art(it.name)  # 그림이 없으면 임시 글자 카드
            compose_clean(art, look, **kw).save(card_p)
            meta, layers = {}, {}
            compose_clean(art, look, **kw, omit_record=True, meta=meta, layers=layers).save(
                out / f"card{it.rank}_{h}_base.png")
            meta["base"] = f"cards_auto/card{it.rank}_{h}_base.png"
            meta["layers"] = {}
            for n, im in layers.items():
                im.save(out / f"card{it.rank}_{h}_L_{n}.png")
                meta["layers"][n] = f"cards_auto/card{it.rank}_{h}_L_{n}.png"
            card_p.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        items.append(it.model_copy(update={"image": f"cards_auto/{card_p.name}"}))
    vd["look"] = look
    return project.model_copy(update={"items": items, "variation": vd})
