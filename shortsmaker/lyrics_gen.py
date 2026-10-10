"""주제·확정 데이터 → Suno 용 가사 초안 3가지 (반전·해설·관점·비교가 들어간 것).

기본: Claude Code 헤드리스 모드(`claude -p`)로 사용자의 Claude 구독 안에서 생성 (추가 API 비용 없음).
실패하면: 같은 프롬프트를 복사해 claude.ai·ChatGPT 등에 붙여 넣고, 답(JSON)을 다시 붙여 넣는 수동 모드.
고른 가사는 가사 검사기를 통과해야 suno_brief.md (제목, Style, Lyrics) 로 저장된다.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from .lyrics_check import LyricsReport, check_lyrics_text

TIMEOUT = 300
DEFAULT_STYLE = "K-pop hip-hop, energetic, punchy drums, catchy hook, Korean male rap and female hook, 120 BPM"


class LyricsGenError(RuntimeError):
    pass


class Version(BaseModel):
    name: str
    angle: str = ""  # 이 버전의 관점 (반전 중심, 비교 중심 …)
    lyrics: str


class Draft(BaseModel):
    title: str
    style: str = DEFAULT_STYLE
    versions: list[Version] = Field(min_length=1)


def _data_lines(rows: list[dict]) -> str:
    out = []
    for r in sorted(rows, key=lambda r: -int(r["rank"])):
        parts = [f"{r['rank']}위", r.get("player") or "", r.get("team") or "", r.get("record") or "",
                 f"({r['year']})" if r.get("year") else "", r.get("notes") or ""]
        out.append("- " + " ".join(p for p in parts if p))
    return "\n".join(out)


def build_prompt(topic: str, title: str, rows: list[dict], source: str, *, avoid: list[str] | None = None,
                 n: int = 3) -> str:
    avoid_txt = ("\n이미 다른 영상에 쓴 표현이니 피할 것: " + ", ".join(f"\"{a}\"" for a in avoid[:30])) if avoid else ""
    return f"""너는 유튜브 쇼츠 '야구한장' 채널의 작사가야. 아래 '확정 데이터'만 사용해서 Suno로 만들 노래 가사를 {n}가지 버전으로 써줘.

주제: {topic}
영상 제목(참고): {title.replace(chr(10), ' / ')}
데이터 출처: {source}
확정 데이터 (5위 → 1위, 공동 순위 포함):
{_data_lines(rows)}

반드시 지킬 것
1. 위 데이터에 있는 숫자·이름·팀만 쓴다. 데이터에 없는 기록·연도·사실·다른 구단은 절대 지어내지 않는다.
   숫자는 노래로 부르기 쉽게 한글로 읽어 써도 된다 (예: 152억 → 백오십이억). 값은 바꾸지 않는다.
   비교할 때 데이터 숫자끼리의 차이(예: 152억 - 150억 = 이억 차이)는 써도 된다. 그 밖의 새 숫자는 쓰지 않는다.
2. 단순한 순위 나열 금지. 각 버전마다 다음을 모두 넣는다:
   - 반전 (예: '하지만', '알고 보면', 예상과 다른 결과)
   - 해설·관점 (왜 이 기록이 대단한지, 무엇을 의미하는지)
   - 비교 (순위끼리, 또는 기록끼리 '~보다', '~만큼', '차이')
   순위와 숫자만 있는 줄은 전체의 절반 이하, 순위·숫자 없는 해설 줄은 4줄 이상.
3. 5위부터 1위까지 모든 순위를 한 번 이상 부른다. 각 순위를 부르는 줄은 'N위' 로 시작한다 (공동이면 '공동 N위').
4. 구조: [Intro] [Verse] [Pre-Chorus] [Chorus] [Outro] 태그, 60~80초 분량 (14~22줄), 한 줄은 짧게.
5. 세 버전은 관점이 서로 달라야 한다 (예: 반전 중심 / 비교 중심 / 팬의 시선).{avoid_txt}

답은 아래 JSON 하나만 출력 (설명·코드블록 표시 없이):
{{"title": "Suno에 넣을 곡 제목", "style": "Suno Style 문구 (영어, 장르·분위기·BPM)",
 "versions": [{{"name": "버전 이름", "angle": "이 버전의 관점 한 줄", "lyrics": "[Intro]\\n...\\n[Verse]\\n..."}}]}}"""


def parse_response(text: str) -> Draft:
    """모델 답(앞뒤 설명·```json 블록 포함 가능)에서 JSON 을 꺼낸다."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise LyricsGenError("답에서 JSON 을 찾지 못했습니다. 프롬프트의 형식대로 다시 받아 붙여 넣으세요")
    try:
        return Draft.model_validate(json.loads(m.group(0)))
    except (ValueError, TypeError) as e:
        raise LyricsGenError(f"답 JSON 형식이 다릅니다: {e}") from e


def claude_available() -> bool:
    return shutil.which("claude") is not None


def generate_with_claude(prompt: str, *, timeout: int = TIMEOUT, runner=subprocess.run) -> Draft:
    """`claude -p` (도구 없이, 세션 저장 없이). 실패하면 LyricsGenError — 화면은 수동 모드로 넘어간다."""
    if runner is subprocess.run and not claude_available():
        raise LyricsGenError("claude 명령을 찾을 수 없습니다 (Claude Code 설치·로그인 확인)")
    cmd = ["claude", "-p", "--output-format", "json", "--tools", "", "--no-session-persistence"]
    try:
        proc = runner(cmd, input=prompt, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise LyricsGenError(f"claude -p 가 {timeout}초 안에 끝나지 않았습니다") from e
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        raise LyricsGenError("claude -p 실패: " + " / ".join(tail))
    try:
        envelope = json.loads(proc.stdout)
        if envelope.get("is_error"):
            raise LyricsGenError(f"claude -p 오류: {envelope.get('result', '')[:200]}")
        body = envelope.get("result", "")
    except ValueError:
        body = proc.stdout
    return parse_response(body)


def check_versions(draft: Draft, rows: list[dict], history) -> list[LyricsReport]:
    return [check_lyrics_text(v.lyrics, rows, history=history) for v in draft.versions]


def brief_markdown(title: str, style: str, lyrics: str, *, topic: str = "", source: str = "") -> str:
    return "\n".join([
        f"# {title}", "",
        "## 제목 (Title)", "", title, "",
        "## Style", "", style, "",
        "## Lyrics", "", lyrics.strip(), "",
        "---", f"주제: {topic}" if topic else "", f"데이터 출처: {source}" if source else "",
        "Suno 유료 플랜으로 생성하세요 (무료 플랜 곡은 수익화 불가).", "",
    ])


def save_brief(folder: Path, title: str, style: str, lyrics: str, report: LyricsReport, *, topic: str = "",
               source: str = "", overlaps_confirmed: bool = False) -> Path:
    """검사기를 통과한 가사만 저장. 겹침 경고가 있으면 사람이 확인해야 저장."""
    if not report.passed:
        raise LyricsGenError("가사 검사를 통과하지 못해 저장할 수 없습니다: " + "; ".join(report.errors[:3]))
    if report.overlaps and not overlaps_confirmed:
        raise LyricsGenError("이전 영상과 겹치는 표현이 있습니다. 확인 체크 후 저장하세요")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "suno_brief.md"
    path.write_text(brief_markdown(title, style, lyrics, topic=topic, source=source), encoding="utf-8")
    return path
