"""프로젝트·템플릿 pydantic 스키마.

project.json 과 templates/<channel>/template.json 의 형식을 정의하고 검증한다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = ROOT / "templates"
FONTS_DIR = ROOT / "assets" / "fonts"
OUTPUT_DIR = ROOT / "output"
PROJECTS_DIR = ROOT / "projects"

MIN_DURATION = 30.0
MAX_DURATION = 60.0


# ---------------------------------------------------------------- 프로젝트

class Item(BaseModel):
    rank: int = Field(ge=1, le=5)
    name: str = Field(min_length=1)
    value: str = ""
    description: str = ""
    image: str | None = None  # 프로젝트 폴더 기준 상대경로


class LyricLine(BaseModel):
    start: float = Field(ge=0)
    end: float
    text: str

    @model_validator(mode="after")
    def _end_after_start(self) -> "LyricLine":
        if self.end <= self.start:
            raise ValueError(f"가사 끝 시간({self.end})이 시작 시간({self.start})보다 커야 합니다")
        return self


class Project(BaseModel):
    channel: str
    title: str = Field(min_length=1)
    subtitle: str = ""
    duration: float = Field(default=50.0, ge=MIN_DURATION, le=MAX_DURATION)
    source_note: str = ""
    items: list[Item]
    music: str | None = None
    music_start: float = Field(default=0.0, ge=0)
    lyrics: list[LyricLine] = Field(default_factory=list)
    lyrics_lrc: str | None = None  # 프로젝트 폴더 기준 .lrc 경로
    rights_confirmed: bool = False

    @field_validator("items")
    @classmethod
    def _five_unique_ranks(cls, items: list[Item]) -> list[Item]:
        if len(items) != 5:
            raise ValueError(f"items는 정확히 5개여야 합니다 (현재 {len(items)}개)")
        ranks = sorted(i.rank for i in items)
        if ranks != [1, 2, 3, 4, 5]:
            raise ValueError(f"rank는 1~5가 한 번씩 있어야 합니다 (현재 {ranks})")
        return items

    @model_validator(mode="after")
    def _one_lyrics_source(self) -> "Project":
        if self.lyrics and self.lyrics_lrc:
            raise ValueError("lyrics(직접 입력)와 lyrics_lrc 중 하나만 사용하세요")
        return self

    @property
    def ordered_items(self) -> list[Item]:
        """화면에 나오는 순서: 5위 → 1위."""
        return sorted(self.items, key=lambda i: -i.rank)


def load_project(path: str | Path) -> tuple[Project, Path]:
    """project.json 을 읽어 (Project, 프로젝트 폴더) 를 돌려준다."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return Project.model_validate(data), path.parent


def save_project(project: Project, project_dir: str | Path) -> Path:
    project_dir = Path(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / "project.json"
    path.write_text(
        json.dumps(project.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


# ---------------------------------------------------------------- 템플릿

class Box(BaseModel):
    x: int
    y: int
    w: int = Field(gt=0)
    h: int = Field(gt=0)

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2


class TextBox(Box):
    font: str = "bold"  # Template.fonts 의 키
    size: int = Field(gt=0)
    min_size: int = Field(gt=0)
    color: str = "#FFFFFF"
    align: Literal["left", "center", "right"] = "center"
    max_lines: int = Field(default=2, ge=1)
    stroke_width: int = 0
    stroke_color: str = "#000000"
    line_spacing: float = 1.15


class ImageBox(Box):
    shape: Literal["rect", "rounded", "circle"] = "rounded"
    radius: int = 40
    border_width: int = 0
    border_color: str = "#FFFFFF"


class LyricsBox(TextBox):
    bg_color: str | None = "#000000"
    bg_opacity: float = Field(default=0.55, ge=0, le=1)
    bg_padding: int = 18


class IntroLayout(BaseModel):
    badge: TextBox
    title: TextBox
    subtitle: TextBox
    badge_text: str = "TOP 5"


class RankLayout(BaseModel):
    header: TextBox  # 영상 제목(작게)
    rank: TextBox    # "5위"
    image: ImageBox  # 캐리커처
    name: TextBox
    value: TextBox
    description: TextBox
    rank_format: str = "{rank}위"
    rank_one_color: str | None = None  # 1위 강조 색


class OutroLayout(BaseModel):
    cta: TextBox
    source: TextBox
    cta_text: str = "구독과 좋아요 부탁드려요!"


class Effects(BaseModel):
    transition: float = Field(default=0.4, ge=0, le=1.5)  # 장면 사이 xfade(초)
    zoom_to: float = Field(default=1.08, ge=1.0, le=1.5)   # 캐리커처 줌인 최종 배율
    rank_pop: float = Field(default=0.35, ge=0, le=2)       # 순위 숫자 튀어나오는 시간(초)
    music_fade_out: float = Field(default=1.5, ge=0)


class Template(BaseModel):
    name: str
    width: int = 1080
    height: int = 1920
    fps: int = 30
    background: str | None = None          # 템플릿 폴더 기준 이미지
    gradient: list[str] = Field(default_factory=lambda: ["#101820", "#2a3a50"])
    fonts: dict[str, str]                  # 키 → assets/fonts 안의 파일명
    intro: IntroLayout
    rank: RankLayout
    outro: OutroLayout
    lyrics: LyricsBox
    effects: Effects = Field(default_factory=Effects)

    _template_dir: Path | None = PrivateAttr(default=None)

    @property
    def template_dir(self) -> Path | None:
        return self._template_dir

    def background_path(self) -> Path | None:
        if self.background and self._template_dir:
            p = self._template_dir / self.background
            return p if p.exists() else None
        return None

    def font_path(self, key: str) -> Path:
        name = self.fonts.get(key)
        if name is None:
            raise KeyError(f"템플릿 '{self.name}'에 폰트 키 '{key}'가 없습니다")
        return FONTS_DIR / name


def list_channels() -> list[str]:
    return sorted(p.name for p in TEMPLATES_DIR.iterdir() if (p / "template.json").exists())


def load_template(channel: str) -> Template:
    tdir = TEMPLATES_DIR / channel
    path = tdir / "template.json"
    if not path.exists():
        raise FileNotFoundError(f"템플릿이 없습니다: {path}")
    t = Template.model_validate(json.loads(path.read_text(encoding="utf-8")))
    t._template_dir = tdir
    return t
