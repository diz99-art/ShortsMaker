"""영상마다 연출 바꾸기 — 템플릿으로 찍어낸 반복형 영상이 되지 않게.

연출 요소(장면 전환·강조색·자막 등장·인트로·그림 움직임·후킹·비트·숫자·효과음·진행 표시·엔딩)를 영상마다 고른다.
제목 파랑·채널 로고는 채널 정체성이라 고정.
모든 프로젝트의 조합을 projects/_history.json 에 남기고, 최근 5편과 3요소 이상 다른 조합만 허용한다.
예전 기록·프로젝트에 없는 요소는 '끄기'로 본다 (예전 영상은 그대로 렌더된다).
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, field_validator

from .models import PROJECTS_DIR, Motion

TRANSITIONS = {"punch": "확대+모션 블러 '쾅'", "slide": "옆에서 미끄러져 들어오기", "flash": "흰 섬광 후 바로 바뀜",
               "pop": "작게 시작해 튀어 오르기"}
ACCENTS = {  # 빨간 기록 줄 / 카드 순위 글자 그라데이션 (제목 파랑은 고정)
    "red_gold": {"label": "빨강 + 금색", "stat": "#EB1B1B", "rank": ("#FFF27A", "#F7B500", "#C86A00")},
    "orange_silver": {"label": "주황 + 은색", "stat": "#F26A00", "rank": ("#FFFFFF", "#D9E1EA", "#8A97A6")},
    "green_gold": {"label": "진녹 + 금색", "stat": "#0A8F3C", "rank": ("#FFF6B0", "#E9C34A", "#9C7A12")},
    "magenta_cyan": {"label": "자홍 + 하늘", "stat": "#D10F6F", "rank": ("#E9FDFF", "#7FE3F2", "#1597B0")},
}
SUBTITLES = {"typing": "한 글자씩", "word": "단어 단위로 톡톡", "line": "줄 단위로 떠오르기"}
INTROS = {"shrink": "크게 시작해 줄어들기", "slide": "옆에서 들어오기", "title_card": "제목 카드 → 로고"}
MOTIONS = {"kenburns": "천천히 확대 (켄 번즈)", "float": "위아래 둥실둥실", "bounce": "등장 후 통통 튀기",
           "wobble": "살짝 흔들림", "none": "움직임 없음"}
HOOKS = {"slam": "크게 '쾅' 찍히기", "drop": "위에서 떨어져 튕기기", "pop_words": "단어씩 팡팡"}
BEATS = {"pulse": "박자마다 카드 펄스", "pulse_flash": "펄스 + 강한 구간 번쩍·흔들림", "off": "비트 효과 없음"}
COUNTS = {"countup": "기록 숫자 카운트업", "static": "숫자 고정"}  # 예전 'countup_gauge'(게이지)는 없앰 → countup
SFX = {"a": "효과음 세트 A", "b": "효과음 세트 B", "off": "효과음 없음"}
PROGRESS = {"dots": "진행 점 ●●●○○", "fraction": "숫자 3/5", "bar": "진행 막대", "none": "진행 표시 없음"}
ENDINGS = {"stamp": "보너스 '쾅' 찍기", "slide": "보너스 옆에서 들어오기", "pop": "보너스 튀어 오르기"}
LOOKS = {"clean_a": "클린 A (기본)", "clean_b": "클린 B (흰 카드·라인)", "classic": "화려한 스타일 (예전, 선택)"}
DEFAULT_LOOK = "clean_a"   # 새 영상 기본 스타일 (2026-10-11 사용자 결정). 예전 프로젝트는 look 이 없으면 classic
MASCOTS = {"right": "마스코트 오른쪽 아래", "left": "마스코트 왼쪽 아래", "off": "마스코트 없음"}
DATAGFX = {"bars": "1위 뒤 기록 막대그래프", "bars_years": "막대그래프 + 연도 타임라인", "off": "그래프 없음"}
DEPTHS = {"pan": "2.5D 옆으로 흐르기", "dolly": "2.5D 다가가기", "off": "입체 없음"}
BURSTS = {"zoom": "키워드 크게 터뜨리기", "stamp": "키워드 도장 쾅", "off": "키워드 없음"}
# 카드 디자인 (분위기별 3종, shortsmaker/card_design.py). '' 은 예전 붓 터치 카드
CARD_DESIGNS = {"": "기본 붓 터치", "stadium": "스타디움 조명", "gold_rays": "금색 광선", "medal": "메달·리본",
                "fall": "추락 그래프", "rain": "어두운 비", "glass": "금 간 유리", "bills": "지폐 비", "coins": "금화 더미",
                "receipt": "계약서 영수증", "vs_split": "VS 분할", "lightning": "번개", "flames": "불꽃 대결",
                "newspaper": "신문 지면", "film": "옛날 필름", "polaroid": "빛바랜 사진", "scoreboard": "전광판",
                "neon": "네온", "breaking": "속보 LIVE"}
AXES = {"transition": TRANSITIONS, "accent": ACCENTS, "subtitle": SUBTITLES, "intro": INTROS, "motion": MOTIONS,
        "hook": HOOKS, "beat": BEATS, "count": COUNTS, "sfx": SFX, "progress": PROGRESS, "ending": ENDINGS,
        "design": CARD_DESIGNS, "mascot": MASCOTS, "datagfx": DATAGFX, "depth": DEPTHS, "burst": BURSTS,
        "look": LOOKS}
OFF = {"motion": "none", "beat": "off", "count": "static", "sfx": "off", "progress": "none", "mascot": "off",
       "datagfx": "off", "depth": "off", "burst": "off"}  # 추천에서 빼는 값
FIXED = ("design", "mascot", "look")  # 랜덤으로 고르지 않는 요소 (디자인은 분위기에 맞춰, 마스코트는 기본 오른쪽 아래)
RECENT = 5
MIN_DIFF = 3  # 최근 영상들과 적어도 이만큼의 요소가 달라야 함


class Variation(BaseModel):
    transition: str = "punch"
    accent: str = "red_gold"
    subtitle: str = "typing"
    intro: str = "shrink"
    motion: str = "none"  # 순위 장면 그림 움직임 (예전 프로젝트는 움직임 없음)
    hook: str = "slam"     # 후킹 문구 등장 (문구가 없으면 쓰지 않음)
    beat: str = "off"
    count: str = "static"
    sfx: str = "off"
    progress: str = "none"
    ending: str = "stamp"  # 보너스 장면 등장 (보너스가 없으면 쓰지 않음)
    design: str = ""       # 카드 디자인 (비우면 예전 붓 터치 카드)
    mascot: str = "off"    # 마스코트 '한장이' 위치
    datagfx: str = "off"
    depth: str = "off"
    burst: str = "off"
    look: str = "classic"  # 전체 스타일. 새 영상은 suggest() 가 DEFAULT_LOOK(클린 A) — 값이 없는 예전 기록은 classic

    @field_validator("count", mode="before")
    @classmethod
    def _no_gauge(cls, v: str) -> str:
        return "countup" if v == "countup_gauge" else v  # 게이지 바는 삭제됨 (예전 기록·프로젝트 호환)

    def describe(self) -> str:
        return " · ".join(f"{AXES[k][getattr(self, k)] if k != 'accent' else ACCENTS[self.accent]['label']}"
                          for k in AXES)


def history_path(root: Path = PROJECTS_DIR) -> Path:
    return root / "_history.json"


def load_history(root: Path = PROJECTS_DIR) -> list[dict]:
    p = history_path(root)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def recent(root: Path = PROJECTS_DIR, exclude: str | None = None) -> list[Variation]:
    """최근 RECENT 편 (같은 프로젝트를 다시 렌더한 기록은 한 번만)."""
    seen, out = set(), []
    for h in reversed(load_history(root)):
        if h["slug"] in seen or h["slug"] == exclude:
            continue
        seen.add(h["slug"])
        out.append(Variation.model_validate(h["variation"]))
        if len(out) == RECENT:
            break
    return out


def differences(a: Variation, b: Variation) -> int:
    return sum(getattr(a, k) != getattr(b, k) for k in AXES)


def conflicts(v: Variation, root: Path = PROJECTS_DIR, exclude: str | None = None) -> list[str]:
    """최근 5편 중 너무 비슷한 것 (다른 요소가 MIN_DIFF 개 미만)."""
    out = []
    for i, r in enumerate(recent(root, exclude), 1):
        if differences(v, r) < MIN_DIFF:
            out.append(f"최근 {i}번째 영상과 {differences(v, r)}개 요소만 다릅니다 ({r.describe()})")
    return out


def suggest_design(candidates: list[str], root: Path = PROJECTS_DIR, exclude: str | None = None) -> str:
    """분위기 디자인 후보(추천 순서) 중 최근 5편에서 쓰지 않은 첫 번째. 모두 썼으면 가장 오래전에 쓴 것."""
    if not candidates:
        return ""
    used = [r.design for r in recent(root, exclude)]
    fresh = [c for c in candidates if c not in used]
    if fresh:
        return fresh[0]
    return max(candidates, key=lambda c: used.index(c))


def suggest(root: Path = PROJECTS_DIR, exclude: str | None = None, seed: int | None = None) -> Variation:
    """최근 5편과 충분히 다른 조합 중 하나 (덜 쓰인 요소를 우선). '끄기' 값은 추천하지 않는다."""
    rnd = random.Random(seed)
    past = recent(root, exclude)
    choices = {k: [v for v in AXES[k] if OFF.get(k) != v] for k in AXES if k not in FIXED}
    combos = [Variation(**{k: rnd.choice(vs) for k, vs in choices.items()}, mascot="right", look=DEFAULT_LOOK)
              for _ in range(4000)]
    ok = [c for c in combos if all(differences(c, r) >= MIN_DIFF for r in past)]
    counts = {(k, getattr(r, k)) for r in past for k in AXES}
    ok.sort(key=lambda c: (sum((k, getattr(c, k)) in counts for k in AXES), rnd.random()))
    return ok[0] if ok else combos[0]


def record(slug: str, v: Variation, root: Path = PROJECTS_DIR, kind: str = "render") -> None:
    h = load_history(root)
    h.append({"slug": slug, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind,
              "variation": v.model_dump()})
    root.mkdir(parents=True, exist_ok=True)
    history_path(root).write_text(json.dumps(h, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- 렌더러용 움직임

TITLE_CARD = 1.6      # 제목 카드를 보여주는 시간(초)
FLASH = 0.25          # 흰 섬광이 사라지는 시간(초)
LINE_RISE = 0.25      # 줄 단위 자막이 떠오르는 시간(초)


def transition_motion(name: str, base: Motion) -> tuple[Motion, list[tuple[float, float]] | None, float]:
    """(카드 움직임, 가로 이동 곡선[초, px], 섬광 시간)."""
    if name == "slide":
        return (Motion(curve=[(0, 1.0), (0.45, 1.0)], blur=0.3, blur_samples=10, shutter=2.0),
                [(0, 1150), (0.1, 520), (0.2, 190), (0.3, 50), (0.45, 0)], 0.0)
    if name == "flash":
        return Motion(curve=[(0, 1.06), (0.3, 1.0)], blur=0), None, FLASH
    if name == "pop":
        return Motion(curve=[(0, 0.25), (0.12, 1.12), (0.22, 0.95), (0.32, 1.02), (0.4, 1.0)], blur=0), None, 0.0
    return base, None, 0.0


def intro_motion(name: str, base: Motion) -> tuple[Motion, list[tuple[float, float]] | None]:
    if name == "slide":
        return (Motion(curve=[(0, 1.0), (0.6, 1.0)], blur=0.3, blur_samples=10, shutter=2.0),
                [(0, -1150), (0.15, -500), (0.3, -150), (0.45, -30), (0.6, 0)])
    return base, None


# ---------------------------------------------------------------- 그림 움직임 (순위 장면 내내)

IDLE_RAMP = 0.5   # 등장 효과가 끝난 뒤 움직임이 자리 잡는 시간(초)
TOP_GAIN = 1.5    # 1위 장면 움직임 배율
TOP_FLASH = 0.2   # 1위 등장 흰 섬광(초)
TOP_SHAKE = 0.6   # 1위 등장 흔들림(초)


def idle_transform(kind: str, t: float, gain: float = 1.0) -> tuple[float, float, float, float]:
    """등장 후 t초의 (배율 곱, dx, dy, 회전 각도). t<0 이면 움직임 없음."""
    import math
    if t < 0 or kind == "none":
        return 1.0, 0.0, 0.0, 0.0
    ramp = min(1.0, t / IDLE_RAMP)
    if kind == "kenburns":
        return 1.0 + min(0.08, 0.012 * t) * gain, 0.0, 0.0, 0.0
    if kind == "float":
        return 1.0, 0.0, -14 * gain * ramp * math.sin(2 * math.pi * t / 2.4), 0.0
    if kind == "bounce":
        return 1.0, 0.0, -55 * gain * math.exp(-2.8 * t) * abs(math.sin(2 * math.pi * t / 0.5)), 0.0
    if kind == "wobble":
        return 1.0, 0.0, 0.0, 2.2 * gain * ramp * math.sin(2 * math.pi * t / 1.8)
    return 1.0, 0.0, 0.0, 0.0


def top_shake(t: float) -> float:
    """1위 등장 흔들림 dx (px)."""
    import math
    if t < 0 or t > TOP_SHAKE:
        return 0.0
    return 16 * math.exp(-5 * t) * math.sin(2 * math.pi * t * 11)
