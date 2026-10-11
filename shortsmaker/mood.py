"""영상 분위기 판단 → 카드 디자인 추천.

제목·주제·메모·순위 표를 보고 6가지 분위기 중 하나를 고른다.
먼저 `claude -p`(사용자 Claude 구독 안에서)로 판단하고, 실패하면 키워드 규칙으로 정한다.
판단 결과와 이유는 화면에 보여 주고 사람이 바꿀 수 있다.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass

from .card_design import MOODS, designs_for

TIMEOUT = 120

# 키워드 규칙 (위에서부터 먼저 맞는 것, 정규식)
RULES: list[tuple[str, tuple[str, ...]]] = [
    ("humiliation", ("연패", "최악", "굴욕", "불운", "패배", "최다패", "실책", "꼴찌", "최하위", "흑역사", "망한", "먹튀")),
    ("money", (r"\d+\s*억", "억 원", "연봉", "계약", "FA", "몸값", "총액")),
    ("rivalry", ("VS", "vs", "라이벌", "대결", "맞대결", "더비", "경쟁")),
    ("live", ("올해", "이번 시즌", "현재", "실시간", "신기록 경신", "진행 중", "2026")),
]


@dataclass
class MoodResult:
    mood: str
    reason: str
    via: str  # claude | rules

    @property
    def designs(self) -> list[str]:
        return designs_for(self.mood)


MAIN_ONLY = ("rivalry", "live")  # 메모에 지나가듯 나온 '경쟁'·'현재'로는 정하지 않음 (제목·주제에서만)


def rule_mood(text: str, years: list[int] | None = None, now_year: int = 2026, extra: str = "") -> MoodResult:
    """text: 제목·주제, extra: 메모·기록 (굴욕·돈 키워드만 봄)."""
    for mood, words in RULES:
        src = text if mood in MAIN_ONLY else f"{text} {extra}"
        hit = [m.group(0) for w in words for m in [re.search(w, src)] if m]
        if hit:
            return MoodResult(mood, f"키워드 '{hit[0]}'", "rules")
    if years:
        old = sum(1 for y in years if y < 2000)
        if old * 2 > len(years):
            return MoodResult("retro", f"기록 연도 {len(years)}개 중 {old}개가 2000년 이전", "rules")
        if all(y >= now_year - 1 for y in years):
            return MoodResult("live", "기록이 모두 최근 시즌", "rules")
    return MoodResult("record", "특별한 키워드가 없어 기본(기록·최고)", "rules")


def mood_prompt(title: str, topic: str, memos: list[str], rows: list[dict]) -> str:
    table = "\n".join(f"- {r.get('rank')}위 {r.get('player', '')} {r.get('team', '')} {r.get('record', '')} "
                      f"{r.get('year', '')}".rstrip() for r in rows)
    choices = "\n".join(f"- {k}: {v}" for k, v in MOODS.items())
    return f"""유튜브 쇼츠 'KBO TOP 5' 영상 카드 디자인의 분위기를 하나 골라줘.

제목: {title.replace(chr(10), ' / ')}
주제: {topic}
메모: {' / '.join(memos)}
순위 표:
{table}

분위기 (이 중 하나):
{choices}

판단 기준: 영상이 시청자에게 주는 느낌. 대단한 기록은 record, 불명예·연패는 humiliation, 계약·연봉은 money,
두 선수·팀 경쟁이 핵심이면 rivalry, 오래된 기록의 향수가 핵심이면 retro, 지금 시즌·새 기록이면 live.
답은 JSON 하나만: {{"mood": "record", "reason": "한 줄 이유"}}"""


def claude_mood(prompt: str, *, runner=subprocess.run, timeout: int = TIMEOUT) -> MoodResult:
    cmd = ["claude", "-p", "--output-format", "json", "--tools", "", "--no-session-persistence"]
    proc = runner(cmd, input=prompt, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError("claude -p 실패")
    body = proc.stdout
    try:
        env = json.loads(body)
        if isinstance(env, dict) and "result" in env:
            if env.get("is_error"):
                raise RuntimeError(str(env.get("result"))[:200])
            body = env["result"]
    except ValueError:
        pass
    m = re.search(r"\{.*\}", body, re.S)
    data = json.loads(m.group(0)) if m else {}
    mood = data.get("mood")
    if mood not in MOODS:
        raise RuntimeError(f"알 수 없는 분위기: {mood}")
    return MoodResult(mood, str(data.get("reason", "")).strip()[:120], "claude")


def detect_mood(title: str, topic: str = "", memos: list[str] | None = None, rows: list[dict] | None = None, *,
                use_claude: bool = True, runner=subprocess.run) -> MoodResult:
    rows = rows or []
    memos = memos or []
    if use_claude:
        try:
            import shutil
            if runner is not subprocess.run or shutil.which("claude"):
                return claude_mood(mood_prompt(title, topic, memos, rows), runner=runner)
        except Exception:  # noqa: BLE001 — 키워드 규칙으로
            pass
    years = [int(y) for r in rows for y in [str(r.get("year", ""))] if re.fullmatch(r"\d{4}", y)]
    extra = " ".join([*memos] + [str(r.get("record", "")) for r in rows])
    return rule_mood(f"{title} {topic}", years, extra=extra)
