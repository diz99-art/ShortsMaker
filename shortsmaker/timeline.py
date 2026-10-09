"""장면별 시작/끝 시간 계산.

기본 50초 구성(인트로 3 / 5~2위 각 8 / 1위 10 / 아웃트로 5)을 duration 에 비례해
늘리거나 줄인다. 시간은 프레임 단위로 맞춘다.

xfade 를 쓰면 클립이 겹치므로, 각 클립(마지막 제외)은 화면 구간보다 transition 만큼
길게 렌더한다. 그러면 k번째 xfade offset == k+1번째 장면 시작 시각이 되고,
전체 길이는 정확히 duration 이 된다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .models import Item, Project

BASE_TOTAL = 50.0
BASE_INTRO = 3.0
BASE_RANK = 8.0
BASE_RANK_ONE = 10.0
BASE_OUTRO = 5.0

SceneKind = Literal["intro", "rank", "outro"]


@dataclass(frozen=True)
class Scene:
    index: int
    kind: SceneKind
    start: float          # 화면에 나타나기 시작하는 시각(초)
    end: float            # 다음 장면이 나타나기 시작하는 시각(초)
    clip_duration: float  # 렌더할 클립 길이 (transition 겹침 포함)
    item: Item | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def label(self) -> str:
        if self.kind == "rank" and self.item:
            return f"{self.item.rank}위"
        return {"intro": "인트로", "outro": "아웃트로"}.get(self.kind, self.kind)


def snap(t: float, fps: int) -> float:
    """시간을 가장 가까운 프레임 경계로 맞춘다."""
    return round(t * fps) / fps


def build_timeline(project: Project, fps: int = 30, transition: float = 0.4) -> list[Scene]:
    items = project.ordered_items
    base = [BASE_INTRO] + [BASE_RANK_ONE if it.rank == 1 else BASE_RANK for it in items] + [BASE_OUTRO]
    scale = project.duration / BASE_TOTAL

    # 누적 경계를 프레임에 맞춰 반올림 → 오차가 쌓이지 않고 끝이 정확히 duration
    bounds = [0.0]
    acc = 0.0
    for b in base:
        acc += b * scale
        bounds.append(snap(acc, fps))
    bounds[-1] = snap(project.duration, fps)

    transition = snap(transition, fps)
    kinds: list[SceneKind] = ["intro"] + ["rank"] * len(items) + ["outro"]
    scenes: list[Scene] = []
    for i, kind in enumerate(kinds):
        start, end = bounds[i], bounds[i + 1]
        last = i == len(kinds) - 1
        scenes.append(Scene(
            index=i,
            kind=kind,
            start=start,
            end=end,
            clip_duration=snap((end - start) + (0.0 if last else transition), fps),
            item=items[i - 1] if kind == "rank" else None,
        ))
    return scenes


def format_timeline(scenes: list[Scene]) -> str:
    rows = [f"{'#':>2}  {'장면':<5} {'시작':>6} {'끝':>6} {'길이':>6} {'클립':>6}"]
    for s in scenes:
        rows.append(
            f"{s.index:>2}  {s.label:<5} {s.start:6.2f} {s.end:6.2f} {s.duration:6.2f} {s.clip_duration:6.2f}"
        )
    return "\n".join(rows)
