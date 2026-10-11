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
MAX_DURATION = 90.0          # 스키마 상한. 템플릿마다 max_duration 으로 더 줄인다 (기존 템플릿 60초)
CLASSIC_MAX_DURATION = 60.0
MIN_SCENE = 1.0  # 장면 시작 시간을 직접 줄 때 장면 하나의 최소 길이(초)


# ---------------------------------------------------------------- 프로젝트

def competition_ranks_ok(ranks: list[int]) -> bool:
    """공동 순위 규칙(1, 2, 2, 4, 5): 각 순위 = 1 + 그보다 앞선 사람 수."""
    return all(r == 1 + sum(1 for o in ranks if o < r) for r in ranks)


def rank_labels(ranks: list[int], fmt: str = "{rank}위") -> list[str]:
    """같은 순위가 둘 이상이면 '공동 2위'."""
    return [("공동 " if ranks.count(r) > 1 else "") + fmt.format(rank=r) for r in ranks]


class Item(BaseModel):
    rank: int = Field(ge=1, le=5)  # 영상 속 자리 (5→1 순서로 한 번씩). 공동 순위면 display_rank 로 보이는 순위를 준다
    name: str = Field(min_length=1)
    value: str = ""
    description: str = ""
    image: str | None = None  # 프로젝트 폴더 기준 상대경로
    start: float | None = Field(default=None, ge=0)  # 이 순위 장면이 시작하는 시각(초). 노래에 맞출 때만
    display_rank: int | None = Field(default=None, ge=1, le=5)  # 화면에 보이는 순위 (공동 2위 2명 → 2, 2 / 3위 없음)
    stat_line: str = ""  # 스티커형: 자막 아래 빨간 기록 줄 (1~2줄, 줄바꿈은 '\n')

    @property
    def shown_rank(self) -> int:
        return self.display_rank or self.rank


class Word(BaseModel):
    """가사 자동 맞추기가 찾은 단어 하나의 시간 (영상 기준 초). 타자 효과에 쓴다."""
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str


class LyricLine(BaseModel):
    start: float = Field(ge=0)
    end: float
    text: str
    words: list[Word] = Field(default_factory=list)  # 자동 맞추기 결과 (없으면 줄 구간을 글자 수로 나눔)
    sung: str = ""  # 실제로 부른 가사 (자막 표기가 다를 때 글자 시각을 이 원문 기준으로 나눈다: '오십육 홈런'→'56홈런')

    @model_validator(mode="after")
    def _end_after_start(self) -> "LyricLine":
        if self.end <= self.start:
            raise ValueError(f"가사 끝 시간({self.end})이 시작 시간({self.start})보다 커야 합니다")
        return self


class Bonus(BaseModel):
    """1위 뒤 엔딩 보너스 한 장면: 순위 밖 기록(kind=rank, 예: 6위 디아즈 50홈런) 또는 다음 편 예고(kind=teaser)."""
    kind: Literal["rank", "teaser"] = "rank"
    rank: int | None = Field(default=None, ge=1, le=99)
    name: str = ""
    team: str = ""
    value: str = ""
    year: str = ""
    text: str = ""            # 예고 문구 ('다음 편: KBO 단일 시즌 도루 TOP 5')
    image: str | None = None  # 보너스 카드 PNG (프로젝트 폴더 기준). 없으면 렌더러가 글자 카드를 그린다

    @model_validator(mode="after")
    def _filled(self) -> "Bonus":
        if self.kind == "rank" and not (self.rank and self.name and self.value):
            raise ValueError("보너스 순위 장면은 순위·이름·기록이 필요합니다")
        if self.kind == "teaser" and not self.text.strip():
            raise ValueError("다음 편 예고 문구가 비어 있습니다")
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
    outro_start: float | None = Field(default=None, ge=0)  # 아웃트로 시작 시각(초). 없으면 비례 배분
    intro_image: str | None = None  # 카드 모드 인트로 캐릭터 (프로젝트 폴더 기준). 없으면 템플릿 채널 로고
    variation: dict | None = None   # 카드 모드 연출 {transition, accent, subtitle, intro, …} (shortsmaker/variation.py)
    hook: str = ""                  # 첫 0~2초 후킹 문구 ('50홈런이 6위?!'). 비우면 로고 인트로만
    bonus: Bonus | None = None      # 1위 뒤 엔딩 보너스 장면
    gauge_base: str = ""            # 막대그래프 기준선 ('50홈런'). 비우면 기준선 없이 (예전 게이지 바는 삭제됨)
    bubbles: dict[str, str] = Field(default_factory=dict)  # 마스코트 말풍선 {intro, top, ending} (3~8글자)
    mood: str = ""                  # 카드 분위기 (마스코트 표정 고를 때)

    @field_validator("items")
    @classmethod
    def _five_unique_ranks(cls, items: list[Item]) -> list[Item]:
        if len(items) != 5:
            raise ValueError(f"items는 정확히 5개여야 합니다 (현재 {len(items)}개)")
        ranks = sorted(i.rank for i in items)
        if ranks != [1, 2, 3, 4, 5]:
            raise ValueError(f"rank는 1~5가 한 번씩 있어야 합니다 (현재 {ranks})")
        shown = [i.shown_rank for i in sorted(items, key=lambda i: i.rank)]
        if shown != sorted(shown) or not competition_ranks_ok(shown):
            raise ValueError(f"보이는 순위(display_rank)가 공동 순위 규칙에 맞지 않습니다 (현재 1→5 자리: {shown}). "
                             "예: 공동 2위 2명이면 1, 2, 2, 4, 5")
        return items

    @model_validator(mode="after")
    def _one_lyrics_source(self) -> "Project":
        if self.lyrics and self.lyrics_lrc:
            raise ValueError("lyrics(직접 입력)와 lyrics_lrc 중 하나만 사용하세요")
        return self

    @model_validator(mode="after")
    def _scene_starts(self) -> "Project":
        """장면 시작 시간(items[].start, outro_start)은 모두 주거나 모두 비워야 하고, 순서대로 커져야 한다."""
        starts = [i.start for i in self.ordered_items]
        given = [s is not None for s in starts]
        if not any(given):
            if self.outro_start is not None:
                raise ValueError("outro_start 는 items 의 start 를 모두 줄 때만 쓸 수 있습니다")
            return self
        if not all(given):
            raise ValueError("items 의 start 는 5개 모두 주거나 모두 비워야 합니다")
        bounds = [0.0, *starts]
        if self.outro_start is not None:
            bounds.append(self.outro_start)
        bounds.append(self.duration)
        labels = ["인트로", "5위", "4위", "3위", "2위", "1위"] + (["아웃트로"] if self.outro_start is not None else [])
        for label, a, b in zip(labels, bounds, bounds[1:]):
            if b - a < MIN_SCENE:
                raise ValueError(f"{label} 장면이 {b - a:.2f}초로 너무 짧습니다 (최소 {MIN_SCENE:g}초, "
                                 "start 는 5위→1위→아웃트로 순서로 커지고 영상 길이 안이어야 합니다)")
        return self

    @property
    def has_scene_starts(self) -> bool:
        return self.items[0].start is not None

    def rank_label(self, item: Item, fmt: str = "{rank}위") -> str:
        """'2위' 또는 공동이면 '공동 2위'."""
        same = sum(1 for i in self.items if i.shown_rank == item.shown_rank)
        return ("공동 " if same > 1 else "") + fmt.format(rank=item.shown_rank)

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


# ---------------------------------------------------------------- 카드 모드 (yagu_v2)
# 순위 이미지가 정보가 다 들어간 완성 카드일 때. 이름·값 텍스트는 그리지 않는다.
# 좌표·크기·색·움직임 곡선은 참고 영상 프레임에서 측정한 값 (templates/yagu_v2/MEASUREMENTS.md)


class PillLogo(Box):
    """왼쪽 위 '야구한장 )(' 알약 모양 로고 (코드로 그림)."""
    text: str = "야구한장"
    font: str = "black"
    size: int = 34
    text_color: str = "#111111"
    fill: str = "#FFFFFF"
    border_width: int = 4
    border_color: str = "#111111"
    icon_color: str = "#E23B2E"  # 오른쪽 야구공 실밥 ')(' 모양
    icon: Literal["seam", "dot", "none"] = "seam"  # 정보한장은 dot


class Rule(BaseModel):
    """가로 구분선."""
    y: int
    h: int = Field(gt=0)
    color: str = "#000000"


class SubscribeButton(Box):
    text: str = "구독"
    font: str = "bold"
    size: int = 36
    fill: str = "#F53A0D"
    text_color: str = "#FFFFFF"


class Footer(BaseModel):
    handle: TextBox                  # "@야구한장"
    handle_text: str = "@야구한장"
    separator: Box                   # 세로 막대
    separator_color: str = "#CFCFCF"
    button: SubscribeButton


class Motion(BaseModel):
    """크게 시작해서 제자리(배율 1)로 줄어드는 움직임.

    curve: [[초, 배율], ...] — 참고 영상에서 프레임마다 잰 값. 사이는 선형 보간.
    blur: 처음 blur 초 동안 확대 방향 모션 블러 (프레임 사이 배율 변화만큼 번짐).
    """
    curve: list[tuple[float, float]]
    blur: float = Field(default=0.3, ge=0)
    blur_samples: int = Field(default=8, ge=1, le=32)
    gap_frames: int = Field(default=0, ge=0)  # 등장 전 빈 프레임 수 (이전 카드가 사라지는 순간)
    shutter: float = Field(default=1.0, gt=0, le=8)  # 블러에 담을 움직임 길이 (프레임 수). 클수록 많이 번짐

    @field_validator("curve")
    @classmethod
    def _curve_ok(cls, c: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if len(c) < 2 or c[0][0] != 0 or any(b[0] <= a[0] for a, b in zip(c, c[1:])):
            raise ValueError("curve 는 0초부터 시간 순서대로 2개 이상이어야 합니다")
        if abs(c[-1][1] - 1.0) > 1e-6:
            raise ValueError("curve 의 마지막 배율은 1 이어야 합니다")
        return c

    @property
    def length(self) -> float:
        return self.curve[-1][0]

    def scale_ext(self, t: float) -> float:
        """scale_at 과 같지만 0초 이전은 첫 구간 기울기로 연장 (등장 첫 프레임의 모션 블러용)."""
        if t >= 0:
            return self.scale_at(t)
        (t0, s0), (t1, s1) = self.curve[0], self.curve[1]
        return s0 + (s1 - s0) / (t1 - t0) * t

    def scale_at(self, t: float) -> float:
        if t <= 0:
            return self.curve[0][1]
        for (t0, s0), (t1, s1) in zip(self.curve, self.curve[1:]):
            if t <= t1:
                return s0 + (s1 - s0) * (t - t0) / (t1 - t0)
        return 1.0


class TypingSubtitle(TextBox):
    """노래에 맞춰 한 글자씩 나타나는 자막. 박스 세로 가운데에 줄 블록을 맞춘다."""
    intro_center_y: int | None = None  # 인트로 장면에서만 쓰는 세로 중심 (없으면 박스 중심)
    intro_align: Literal["left", "center"] = "center"  # 인트로 장면 자막 정렬
    wide_w: int | None = None           # 최소 크기로도 max_lines 를 넘으면 이 폭까지 넓혀 다시 맞춤 (가운데 기준)
    line_pitch: float = 1.08            # 줄 간격 (글자 크기 배수)
    shadow_offset: tuple[int, int] = (5, 7)
    shadow_color: str = "#000000"
    shadow_opacity: float = Field(default=0.45, ge=0, le=1)
    shadow_blur: float = Field(default=3.0, ge=0)


class StatLine(TextBox):
    """스티커형: 자막 아래 빨간 굵은 기록 줄. 순위 장면이 시작되고 delay 뒤부터 초당 rate 글자씩 나타난다."""
    center_y: int = 880
    line_pitch: float = 0.9
    rate: float = Field(default=27.0, gt=0)
    delay: float = Field(default=0.2, ge=0)


class CardLayout(BaseModel):
    style: Literal["card", "sticker"] = "card"  # card: 정보가 다 든 카드 / sticker: 글씨 없는 치비 스티커 + 빨간 기록 줄
    background_color: str = "#FCFCFC"
    pill: PillLogo
    title: TextBox                     # 파란 2줄 제목 (project.title, 줄바꿈은 '\n' 또는 자동)
    title_line_pitch: float = 0.98
    divider: Rule
    footer: Footer
    logo: str = "logo.png"             # 템플릿 폴더 기준 채널 로고 (인트로)
    logo_box: Box                      # 로고 전체 이미지가 놓이는 자리 (배율 1)
    intro_box: Box | None = None       # 프로젝트 인트로 캐릭터(intro_image) 자리. 없으면 logo_box
    card_box: Box                      # 순위 카드가 놓이는 자리 (비율 유지해 박스 안에 맞춤)
    content_top: int                   # 움직이는 로고·카드를 이 y 위로는 그리지 않음 (구분선 아래)
    intro_motion: Motion
    card_motion: Motion
    subtitle: TypingSubtitle
    stat_line: StatLine | None = None  # 스티커형 빨간 기록 줄
    rank_word: str = "{rank}위"        # 가사에서 장면 전환 시점을 찾을 낱말
    auto_cards: bool = False           # 항목(이름·값·이미지)으로 클린 카드를 자동 합성 (정보한장)
    default_look: str = "classic"      # 프로젝트 연출에 look 이 없을 때 쓰는 스타일


class Template(BaseModel):
    name: str
    mode: Literal["classic", "card"] = "classic"
    width: int = 1080
    height: int = 1920
    fps: int = 30
    max_duration: float = Field(default=CLASSIC_MAX_DURATION, ge=MIN_DURATION, le=MAX_DURATION)
    background: str | None = None          # 템플릿 폴더 기준 이미지
    gradient: list[str] = Field(default_factory=lambda: ["#101820", "#2a3a50"])
    fonts: dict[str, str]                  # 키 → assets/fonts 안의 파일명
    intro: IntroLayout | None = None       # classic 모드 전용
    rank: RankLayout | None = None
    outro: OutroLayout | None = None
    lyrics: LyricsBox | None = None
    card: CardLayout | None = None         # card 모드 전용
    effects: Effects = Field(default_factory=Effects)

    @model_validator(mode="after")
    def _mode_blocks(self) -> "Template":
        if self.mode == "classic":
            missing = [k for k in ("intro", "rank", "outro", "lyrics") if getattr(self, k) is None]
            if missing:
                raise ValueError(f"classic 템플릿에 {', '.join(missing)} 가 필요합니다")
        elif self.card is None:
            raise ValueError("card 모드 템플릿에는 card 블록이 필요합니다")
        return self

    _template_dir: Path | None = PrivateAttr(default=None)

    @property
    def template_dir(self) -> Path | None:
        return self._template_dir

    def background_path(self) -> Path | None:
        if self.background and self._template_dir:
            p = self._template_dir / self.background
            return p if p.exists() else None
        return None

    def asset_path(self, name: str) -> Path | None:
        """템플릿 폴더 안의 파일 (없으면 None)."""
        if self._template_dir:
            p = self._template_dir / name
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
