"""카드 모드(yagu_v2) 타이밍 — 가사의 글자별 시각, 순위 장면 시작, 보너스·그래프·엔딩 시작, 효과음 시점.

그림은 그리지 않는 계산만 둔다 (card_mode.CardRenderer 와 auto_pipeline·capcut_export 가 같이 쓴다).
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from .effects import BONUS_LEN, BONUS_MIN_TOP, CHART_LEN, HOOK_IMPACT
from .models import MIN_SCENE, LyricLine, Project, Template
from .sfx import Cue
from .timeline import Scene, snap
from .variation import (
    Variation,
)


class CardModeError(ValueError):
    pass


def _norm(s: str) -> str:
    return re.sub(r"[\W_]+", "", s).lower()


# ---------------------------------------------------------------- 가사 → 글자별 시각

def char_times(line: LyricLine) -> list[float]:
    """줄의 글자마다 나타나는 시각. 문장 부호는 앞 글자와, 공백은 다음 글자와 함께 나타난다.

    자막 표기가 부른 가사와 다르면(line.sung: '오십육 홈런' → 자막 '56홈런') 부른 가사로 글자 시각을 구한 뒤
    같은 글자는 그 시각을, 바뀐 부분('오십육 '→'56')은 원래 부른 구간 안에서 고르게 나눠 쓴다.
    """
    if line.sung and line.sung != line.text:
        return _mapped_times(line)
    return _own_times(line)


def _mapped_times(line: LyricLine) -> list[float]:
    src = line.model_copy(update={"text": line.sung, "sung": ""})
    st = _own_times(src)
    n = len(src.text)
    tail = max([line.end] + [w.end for w in line.words]) if line.words else line.end
    word_ends = sorted(w.end for w in line.words)
    ends = []  # 부른 글자마다 끝나는 시각: 다음 글자 시각 (단어 끝을 넘지 않게)
    for i in range(n):
        nxt = st[i + 1] if i + 1 < n else tail
        cap = next((e for e in word_ends if e >= st[i]), tail)
        ends.append(max(st[i], min(nxt, cap) if not src.text[i].isspace() else nxt))
    chars = list(line.text)
    times = [line.start] * len(chars)
    for op, i1, i2, j1, j2 in SequenceMatcher(None, src.text, line.text, autojunk=False).get_opcodes():
        if op == "equal":
            times[j1:j2] = st[i1:i2]
            continue
        if j1 == j2:
            continue
        a = st[i1] if i1 < n else tail
        b = ends[i2 - 1] if i2 > i1 else a
        vis = [j for j in range(j1, j2) if _norm(chars[j])]
        for k, j in enumerate(vis):
            times[j] = a + (b - a) * k / len(vis)
    return _settle(chars, set(j for j, c in enumerate(chars) if _norm(c)), times, line.start)


def _own_times(line: LyricLine) -> list[float]:
    """char_times 의 기본: 자막 글자를 부른 단어 구간에 비율로 나눈다.

    단어 타이밍(words)이 있으면 그 안에서 글자를 나눈다: 화면 글자(글자·숫자만) k번째를
    단어들의 글자 수 누적 비율로 대응시켜 해당 단어 구간 안에서 보간한다.
    (화면 글자가 부른 가사와 조금 달라도 — 예: '케이비오'→'KBO' — 비율로 맞춘다.)
    단어 타이밍이 없으면 줄 구간을 글자 수로 고르게 나눈다.
    """
    chars = list(line.text)
    visible = [i for i, c in enumerate(chars) if _norm(c)]  # 글자·숫자만 센다 (단어 길이와 같은 기준)
    n = len(visible)
    times = [line.start] * len(chars)
    if n == 0:
        return times
    words = [(w.start, w.end, max(1, len(_norm(w.text)) or len(w.text.strip()))) for w in line.words
             if w.end > w.start]
    if words:
        total = sum(w[2] for w in words)
        for k, ci in enumerate(visible):
            p = k / n * total
            acc = 0
            for ws, we, wl in words:
                if p < acc + wl or (ws, we, wl) == words[-1]:
                    frac = min(max((p - acc) / wl, 0.0), 1.0)
                    times[ci] = ws + frac * (we - ws)
                    break
                acc += wl
    else:
        span = max(line.end - line.start, 1e-3)
        for k, ci in enumerate(visible):
            times[ci] = line.start + span * k / n
    return _settle(chars, set(visible), times, line.start)


def _settle(chars: list[str], counted: set[int], times: list[float], start: float) -> list[float]:
    """문장 부호는 바로 앞 글자와 함께, 공백은 다음 글자와 함께 나타나게 하고 단조 증가로 맞춘다."""
    for i, ch in enumerate(chars):
        if i not in counted and not ch.isspace():
            times[i] = times[i - 1] if i else start
    nxt = None
    for i in range(len(chars) - 1, -1, -1):
        if chars[i].isspace():
            times[i] = nxt if nxt is not None else times[i - 1] if i else start
        else:
            nxt = times[i]
    # 단조 증가 보장
    for i in range(1, len(times)):
        times[i] = max(times[i], times[i - 1])
    return times


def rank_starts_from_lyrics(lines: list[LyricLine], ranks: list[int], word_format: str = "{rank}위"
                            ) -> dict[int, float]:
    """rank_times 의 순위별 사전판 (순위가 겹치지 않을 때)."""
    return dict(zip(ranks, rank_times(lines, ranks, word_format)))


def rank_times(lines: list[LyricLine], ranks: list[int], word_format: str = "{rank}위") -> list[float]:
    """가사에서 각 순위 낱말("5위")을 부르는 시각. ranks 는 화면 순서(5→1, 공동이면 2, 2 처럼 겹침),
    앞에서부터 차례로 찾는다. 같은 순위가 또 나오면 앞 사람 다음에 나오는 줄을 찾는다."""
    out: list[float] = []
    after = -1.0
    for rank in ranks:
        word = word_format.format(rank=rank)
        target = _norm(word)
        found = None
        for line in lines:
            if line.start < after - 1e-6:
                continue
            idx = _norm(line.text).find(target)
            if idx < 0:
                continue
            # 정규화 글자 위치 → 원문 글자 위치
            seen = -1
            for ci, ch in enumerate(line.text):
                if _norm(ch):
                    seen += 1
                    if seen == idx:
                        found = char_times(line)[ci]
                        break
            if found is not None and (found > after or (not out and found >= after)):
                break
            found = None
        if found is None:
            raise CardModeError(f"가사에서 '{word}' 를 부르는 줄을 찾지 못했습니다. "
                                "가사를 확인하거나 items[].start 로 장면 시작 시각을 직접 주세요")
        out.append(round(found, 3))
        after = found
    return out


# ---------------------------------------------------------------- 타임라인

def card_timeline(project: Project, t: Template, lines: list[LyricLine]) -> list[Scene]:
    """인트로 + 5위~1위 (+ 엔딩 보너스). 1위 카드가 끝까지 남고, 보너스가 있으면 마지막 몇 초를 보너스 장면으로."""
    items = project.ordered_items
    if project.has_scene_starts:
        starts = {it.rank: it.start for it in items}
    else:  # 가사에서 보이는 순위("공동 2위"면 2위를 두 번)를 부르는 순간
        times = rank_times(lines, [it.shown_rank for it in items], t.card.rank_word)
        starts = {it.rank: tm for it, tm in zip(items, times)}
    fps = t.fps
    bounds = [0.0] + [snap(starts[it.rank], fps) for it in items] + [snap(project.duration, fps)]
    labels = ["인트로"] + [project.rank_label(it) for it in items]
    for label, a, b in zip(labels, bounds, bounds[1:]):
        if b - a < MIN_SCENE:
            raise CardModeError(f"{label} 장면이 {b - a:.2f}초로 너무 짧습니다 (장면 시작 시각을 확인하세요)")
    kinds = ["intro"] + ["rank"] * len(items)
    scene_items: list = [None] + list(items)
    top_start = bounds[-2]
    b_start = bonus_start(project, top_start, fps)
    c_start = chart_start(project, top_start, b_start, fps)
    if c_start is not None:  # 1위 뒤 기록 막대그래프 (보너스 직전 2초)
        bounds.insert(-1, c_start)
        kinds.append("chart")
        scene_items.append(None)
    if b_start is not None:
        bounds.insert(-1, b_start)
        kinds.append("bonus")
        scene_items.append(None)
    o_start = outro_start(project, t, top_start, b_start, c_start, fps)
    if o_start is not None:  # 엔딩 카드 (하늘 배지형 '알면 알수록 신기한 세상')
        bounds.insert(-1, o_start)
        kinds.append("outro")
        scene_items.append(None)
    scenes = []
    for i, (a, b) in enumerate(zip(bounds, bounds[1:])):
        scenes.append(Scene(index=i, kind=kinds[i], start=a, end=b, clip_duration=b - a, item=scene_items[i]))
    return scenes


def outro_start(project: Project, t: Template, top_start: float, b_start: float | None, c_start: float | None,
                fps: int) -> float | None:
    """엔딩 카드 시작: 영상 끝 outro_len 초. 보너스·그래프가 있거나 1위를 3초 못 보여 주면 넣지 않음."""
    if not t.card.outro_image or b_start is not None or c_start is not None:
        return None
    if t.brand_asset(t.card.outro_image) is None:
        return None
    start = snap(project.duration - t.card.outro_len, fps)
    return start if start - top_start >= 3.0 else None


def bonus_start(project: Project, top_start: float, fps: int) -> float | None:
    """보너스 장면 시작: 1위를 BONUS_MIN_TOP 초 이상 보여 준 뒤, 영상 끝 BONUS_LEN 초. 자리가 없으면 None."""
    if project.bonus is None:
        return None
    start = snap(max(top_start + BONUS_MIN_TOP, project.duration - BONUS_LEN), fps)
    return start if project.duration - start >= 2.5 else None


def chart_start(project: Project, top_start: float, b_start: float | None, fps: int) -> float | None:
    """그래프 장면 시작: 보너스 직전 CHART_LEN 초 (보너스가 없으면 영상 끝 CHART_LEN 초). 1위는 3초 이상 남김."""
    v = Variation.model_validate(project.variation or {})
    if v.datagfx == "off":
        return None
    end = b_start if b_start is not None else project.duration
    start = snap(end - CHART_LEN, fps)
    return start if start - top_start >= 3.0 else None


def hook_length(project: Project, scenes: list[Scene]) -> float:
    """후킹 문구 시간: 인트로 전체 (후킹 다음 로고 인트로 없이 바로 5위). 문구가 없으면 0 (로고 인트로)."""
    if not project.hook.strip() or not scenes:
        return 0.0
    return round(scenes[0].duration, 3) if scenes[0].duration >= 1.0 else 0.0  # 후킹 → 바로 5위 (로고 인트로 없음)


def sfx_cues(project: Project, t: Template, scenes: list[Scene]) -> list[Cue]:
    """효과음 위치: 후킹 '쾅', 장면 전환 '휙', 카드 착지 '쿵', 1위 함성."""
    v = Variation.model_validate(project.variation or {})
    if v.sfx == "off":
        return []
    cues: list[Cue] = []
    if hook_length(project, scenes):
        cues.append(Cue(HOOK_IMPACT, "slam"))
    land = 0.12 if v.transition in ("flash", "pop") else 0.2
    for sc in scenes:
        if sc.kind == "intro":
            continue
        cues.append(Cue(max(0.0, sc.start - 0.12), "whoosh"))
        if sc.kind == "chart":
            continue
        cues.append(Cue(sc.start + land, "thud"))
        if sc.kind == "rank" and sc.item.shown_rank == 1 and sc.item.rank == 1:
            cues.append(Cue(sc.start + 0.25, "cheer"))
    return sorted(cues, key=lambda c: c.at)
