"""빠른 제작: ChatGPT 답변(정해진 양식) → 제목·출처·순위 표·장면 설명·메모.

양식
  제목: 1줄 / 2줄
  출처: 이름 | URL
  순위 | 선수 | 팀 | 기록 | 연도 | 장면 설명
  5 | 박병호 | 넥센 히어로즈 | 52홈런 | 2014 | 힘차게 풀스윙하는 타격 자세
  …
  메모: 가사·해설에 쓸 사실

틀린 줄은 줄 번호와 이유를 알려 준다. 마크다운 표(| … |), 코드 블록(```), 굵게(**), 목록 기호(-)는 알아서 걸러 읽는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .rank_extract import RankRow, column_problems, find_team, rows_complete

COLUMNS = ["순위", "선수", "팀", "기록", "연도", "장면 설명"]
UNSURE = ("확인 필요", "확인필요", "모름", "unknown", "N/A")

EXAMPLE = """제목: 50홈런도 6위라고?! / KBO 단일 시즌 홈런 TOP 5
출처: 뉴시스 | https://www.newsis.com/view/NISX20251004_0003355623
순위 | 선수 | 팀 | 기록 | 연도 | 장면 설명
5 | 박병호 | 넥센 히어로즈 | 52홈런 | 2014 | 힘차게 풀스윙하는 타격 자세
3 | 박병호 | 넥센 히어로즈 | 53홈런 | 2015 | 홈런 후 한 손을 번쩍 드는 모습
3 | 심정수 | 현대 유니콘스 | 53홈런 | 2003 | 배트를 어깨에 걸치고 묵직하게 선 자세
2 | 이승엽 | 삼성 라이온즈 | 54홈런 | 1999 | 젊은 시절, 외다리 타법으로 타격 준비
1 | 이승엽 | 삼성 라이온즈 | 56홈런 | 2003 | 두 팔을 번쩍 들고 환호하는 모습
메모: 2003년 이승엽 56개와 심정수 53개는 같은 시즌의 홈런 경쟁이었다"""


@dataclass
class QuickData:
    title: str = ""
    source_name: str = ""
    source_url: str = ""
    rows: list[RankRow] = field(default_factory=list)
    memos: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)    # "7번째 줄: …"
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def request_prompt(topic: str) -> str:
    """ChatGPT 에 이 양식으로 답하게 하는 프롬프트."""
    return f"""주제: {topic or '(주제를 적어 주세요)'}
위 주제의 KBO TOP 5 를 유튜브 쇼츠 '야구한장'용으로 정리해줘. 아래 양식 그대로, 다른 설명 없이 답해.

규칙
- 공식 기록(KBO 기록실 등)이나 믿을 만한 기사로 확인된 값만 쓴다. 확인하지 못한 칸은 '확인 필요'라고 적는다.
- 출처는 실제로 확인한 페이지 1개 (이름 | URL).
- 공동 순위는 같은 숫자로 적고 다음 순위는 건너뛴다 (예: 5, 3, 3, 2, 1). 항목은 정확히 5줄.
- 팀은 그 기록 당시 구단 정식 이름 (예: 넥센 히어로즈, 현대 유니콘스).
- 기록은 숫자+단위 (예: 52홈런, 16연패, 152억). 연도는 4자리.
- 장면 설명은 캐리커처 그림용 동작·표정만 (구단 로고·글씨·등번호는 넣지 말 것).
- 메모는 가사·해설에 쓸 사실 2~5줄 (출처에 있는 사실만, 줄마다 '메모:'로 시작).

양식
제목: 1줄 / 2줄
출처: 이름 | URL
순위 | 선수 | 팀 | 기록 | 연도 | 장면 설명
5 | 선수 | 팀 | 기록 | 연도 | 장면 설명
(5줄)
메모: 가사·해설에 쓸 사실"""


def _clean(line: str) -> str:
    line = line.strip().replace("**", "")
    line = re.sub(r"^[-*•]\s+", "", line)
    return line.strip()


def parse_quick(text: str) -> QuickData:
    q = QuickData()
    for no, raw in enumerate(text.splitlines(), start=1):
        line = _clean(raw)
        if not line or line.startswith("```") or re.fullmatch(r"\|?[\s:\-|]+\|?", line):
            continue  # 빈 줄, 코드 블록 표시, 마크다운 표 구분선
        if any(u in line for u in UNSURE):
            q.errors.append(f"{no}번째 줄: '확인 필요' 칸이 있습니다 — 직접 확인해 채운 뒤 다시 붙여 넣으세요: {line[:50]}")
            continue
        m = re.match(r"^(제목|출처|메모)\s*[:：]\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if key == "제목":
                parts = [p.strip() for p in re.split(r"\s+/\s+|\s*\|\s*", val) if p.strip()]
                if not parts or len(parts) > 2:
                    q.errors.append(f"{no}번째 줄: 제목은 '1줄 / 2줄' 형식이어야 합니다 (1~2줄)")
                else:
                    q.title = "\n".join(parts)
            elif key == "출처":
                parts = [p.strip() for p in val.split("|")]
                if len(parts) != 2 or not parts[0]:
                    q.errors.append(f"{no}번째 줄: 출처는 '이름 | URL' 형식이어야 합니다")
                elif not re.match(r"^https?://\S+$", parts[1]):
                    q.errors.append(f"{no}번째 줄: 출처 URL 이 http(s):// 로 시작하지 않습니다: {parts[1][:40]}")
                else:
                    q.source_name, q.source_url = parts
            else:
                if val:
                    q.memos.append(val)
            continue
        if "|" in line:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if cells[:2] == ["순위", "선수"]:
                if cells != COLUMNS:
                    q.warnings.append(f"{no}번째 줄: 머리줄이 '{' | '.join(COLUMNS)}' 와 다릅니다 (순서대로 읽습니다)")
                continue
            if len(cells) != len(COLUMNS):
                q.errors.append(f"{no}번째 줄: 칸이 {len(cells)}개입니다 ({len(COLUMNS)}개 필요: {' | '.join(COLUMNS)})")
                continue
            rank, player, team, record, year, scene = cells
            if not re.fullmatch(r"(공동\s*)?[1-5](위)?", rank):
                q.errors.append(f"{no}번째 줄: 순위 '{rank}' 는 1~5 숫자여야 합니다")
                continue
            if year and not re.fullmatch(r"\d{4}", year):
                q.errors.append(f"{no}번째 줄: 연도 '{year}' 는 4자리 숫자여야 합니다")
                continue
            if team and not find_team(team):
                q.warnings.append(f"{no}번째 줄: 팀 '{team}' 이 구단 사전에 없습니다 (맞으면 그대로 진행)")
            q.rows.append(RankRow(rank=int(re.search(r"[1-5]", rank).group(0)), player=player, team=team,
                                  record=record, year=year, scene=scene))
            continue
        q.errors.append(f"{no}번째 줄: 양식에 없는 줄입니다 (제목:/출처:/표/메모: 중 하나): {line[:50]}")
    if not q.title:
        q.errors.append("제목: 줄이 없습니다")
    if not q.source_url:
        q.errors.append("출처: 줄이 없습니다")
    if q.rows:
        q.errors += rows_complete(q.rows)
        q.errors += [p for p in column_problems(q.rows, allow_unknown_team=True)]
    else:
        q.errors.append("순위 표 줄이 없습니다")
    return q
