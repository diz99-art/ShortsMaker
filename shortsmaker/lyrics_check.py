"""가사 검사기 — 단순 나열형·데이터 불일치·이전 영상과의 표현 겹침.

오류(저장 불가)
  - 나열형: 'N위 + 이름 + 숫자'뿐인 줄이 절반을 넘거나, 해설 줄(순위·숫자 없는 문장)이 4줄 미만
  - 반전·해설·비교 표현이 하나도 없음 (각각 따로 검사)
  - 데이터에 없는 숫자, 데이터에 없는 구단, 순위와 이름이 어긋남 ('5위 양의지' 인데 데이터 5위는 오지환)
경고(사람이 확인하면 저장 가능)
  - 이전 영상 가사와 같은 표현(어절 3개 이상 연속)이 겹치는 줄
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .lyrics_align import clean_lyrics_text
from .models import PROJECTS_DIR
from .rank_extract import find_team, load_teams, normalize_numbers

TWIST = ("하지만", "그런데", "근데", "그러나", "반전", "의외로", "오히려", "사실은", "알고 보면", "뜻밖", "놀랍게도", "그래도")
COMPARE = ("보다", "만큼", "비교", "차이", "두 배", "절반", "더 많", "더 적", "넘었", "넘어", "앞질", "뒤집", "맞먹")
COMMENT = ("왜", "이유", "덕분", "때문", "의미", "생각", "기억", "결국", "그래서", "그만큼", "말해", "보여",
           "믿음", "기대", "가치", "증명", "무게", "크기", "상징", "뜻", "꿈", "자존심", "역사")
_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_RANK = re.compile(r"(\d)\s*위")
NGRAM = 3


@dataclass
class LyricsReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    overlaps: list[dict] = field(default_factory=list)  # {line, phrase, slug}
    stats: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {"passed": self.passed, "errors": self.errors, "warnings": self.warnings,
                "overlaps": self.overlaps, "stats": self.stats}


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUM.findall(normalize_numbers(text))}


def _is_list_line(line: str) -> bool:
    """순위·이름·숫자 말고 내용이 거의 없는 줄 ('5위 오지환 124억')."""
    t = normalize_numbers(line)
    if not (_RANK.search(t) or _NUM.search(t)):
        return False
    rest = re.sub(r"\d+[\w%]*|[,.!?·]", " ", t)
    words = [w for w in rest.split() if w not in ("공동", "위", "억", "연패")]
    return len(words) <= 3


def _has(lines: list[str], words: tuple[str, ...]) -> list[str]:
    return [w for w in words if any(w in l for l in lines)]


def _eojeol_ngrams(line: str, n: int = NGRAM, data_words: set[str] | None = None) -> set[str]:
    """어절 n개 묶음. 데이터 자체(순위·이름·숫자)로만 된 묶음('공동 2위 나성범')은 같은 주제면 피할 수 없어 뺀다."""
    words = [re.sub(r"[^\w]", "", w) for w in normalize_numbers(line).split()]
    words = [w for w in words if w]
    out = set()
    for i in range(len(words) - n + 1):
        chunk = words[i:i + n]
        datalike = sum(1 for w in chunk if re.fullmatch(r"\d+\w{0,2}|공동|\d위", w) or (data_words and w in data_words))
        if datalike < n - 1:
            out.add(" ".join(chunk))
    return out


def _derived(values: list[float]) -> set[str]:
    """데이터 숫자끼리의 차이 (비교 해설에 쓰는 '이억 차이', '십육억 더')."""
    out = set()
    for a in values:
        for b in values:
            if a > b:
                d = round(a - b, 3)
                out.add(str(int(d)) if d == int(d) else str(d))
    return out


def history_lyrics(root: Path = PROJECTS_DIR, exclude: str | None = None) -> list[tuple[str, list[str]]]:
    """이전 프로젝트의 가사 (project.json 자막, _briefs 의 고른 가사)."""
    out = []
    if not root.exists():
        return out
    for pj in root.glob("*/project.json"):
        if pj.parent.name == exclude:
            continue
        try:
            lines = [l["text"] for l in json.loads(pj.read_text(encoding="utf-8")).get("lyrics", [])]
        except (ValueError, KeyError):
            continue
        if lines:
            out.append((pj.parent.name, lines))
    for bj in (root / "_briefs").glob("*/brief.json"):
        if bj.parent.name == exclude:
            continue
        try:
            chosen = json.loads(bj.read_text(encoding="utf-8")).get("chosen_lyrics") or ""
        except ValueError:
            continue
        if chosen:
            out.append((f"가사:{bj.parent.name}", clean_lyrics_text(chosen)))
    return out


def check_lyrics_text(text: str, rows: list[dict] | None = None, *,
                      history: list[tuple[str, list[str]]] | None = None) -> LyricsReport:
    """rows: 확정 데이터 [{rank, team, player, record, year, notes?, stat_line?}] — 주면 숫자·이름 일치도 검사."""
    lines = clean_lyrics_text(text)
    r = LyricsReport()
    if not lines:
        r.errors.append("가사가 비어 있습니다")
        return r

    # ---- 나열형
    list_lines = [l for l in lines if _is_list_line(l)]
    comment_lines = [l for l in lines if not _RANK.search(normalize_numbers(l)) and not _NUM.search(normalize_numbers(l))]
    ratio = len(list_lines) / len(lines)
    r.stats.update(lines=len(lines), list_lines=len(list_lines), comment_lines=len(comment_lines),
                   list_ratio=round(ratio, 2))
    if ratio > 0.5:
        r.errors.append(f"순위 나열형입니다: 'N위 이름 숫자'뿐인 줄이 {len(list_lines)}/{len(lines)}줄 ({ratio:.0%})")
    if len(comment_lines) < 4:
        r.errors.append(f"해설 줄(순위·숫자 없는 문장)이 {len(comment_lines)}줄뿐입니다 (4줄 이상)")
    for words, name in ((TWIST, "반전"), (COMMENT, "해설·관점"), (COMPARE, "비교")):
        found = _has(lines, words)
        r.stats[name] = found
        if not found:
            r.errors.append(f"{name} 표현이 없습니다 (예: {', '.join(words[:4])})")

    # ---- 데이터 일치
    if rows:
        allowed = {str(n) for n in range(1, 6)}
        for row in rows:
            for key in ("rank", "record", "year", "notes", "stat_line", "value"):
                allowed |= _numbers(str(row.get(key) or ""))
        records = [float(n) for row in rows for n in _numbers(str(row.get("record") or ""))]
        allowed |= _derived(records)
        for l in lines:
            bad = sorted(_numbers(l) - allowed, key=lambda x: float(x))
            if bad:
                r.errors.append(f"데이터에 없는 숫자 {', '.join(bad)}: \"{l}\"")
        data_teams = {find_team(str(row.get("team") or "")).name for row in rows if find_team(str(row.get("team") or ""))}
        teams, _ = load_teams()
        for l in lines:
            for t in teams:
                if t.name in data_teams:
                    continue
                if any(re.search(rf"(?<![가-힣A-Za-z]){re.escape(a)}(?![A-Za-z])", l) for a in (t.name, *t.aliases)
                       if len(a) >= 2):
                    r.errors.append(f"데이터에 없는 구단 '{t.name}': \"{l}\"")
                    break
        players: dict[int, set[str]] = {}
        teams_by: dict[int, set[str]] = {}
        for row in rows:
            k = int(row["rank"])
            if str(row.get("player") or "").strip():
                players.setdefault(k, set()).add(str(row["player"]).strip())
            team = find_team(str(row.get("team") or ""))
            if team:
                teams_by.setdefault(k, set()).update({team.name, *team.aliases})
        all_players = set().union(*players.values()) if players else set()
        all_teams = set().union(*teams_by.values()) if teams_by else set()
        for l in lines:
            m = _RANK.search(normalize_numbers(l))
            if not m:
                continue
            n = int(m.group(1))
            # 이 줄에 나온 데이터 선수(팀) 중 그 순위의 선수(팀)가 하나도 없으면 어긋남 ('5위 양의지')
            for names, own, what in ((all_players, players.get(n, set()), "선수"), (all_teams, teams_by.get(n, set()), "팀")):
                here = {x for x in names if len(x) >= 2 and x in l}
                if here and not here & own:
                    r.errors.append(f"{n}위와 {what} 이름이 데이터와 다릅니다: \"{l}\"")
        for row in rows:
            p = str(row.get("player") or "").strip()
            if p and not any(p in l for l in lines):
                r.warnings.append(f"데이터의 {row['rank']}위 {p} 가 가사에 없습니다")

    # ---- 이전 영상과 겹침
    data_words = set()
    for row in rows or []:
        for key in ("player", "team"):
            data_words |= {re.sub(r"[^\w]", "", w) for w in str(row.get(key) or "").split()}
        t = find_team(str(row.get("team") or ""))
        if t:
            data_words |= {re.sub(r"[^\w]", "", w) for a in (t.name, *t.aliases) for w in a.split()}
    data_words.discard("")
    for slug, old in history or []:
        old_grams = set().union(*(_eojeol_ngrams(o, data_words=data_words) for o in old)) if old else set()
        for l in lines:
            same = _eojeol_ngrams(l, data_words=data_words) & old_grams
            for phrase in sorted(same):
                r.overlaps.append({"line": l, "phrase": phrase, "slug": slug})
    if r.overlaps:
        lines_hit = sorted({o["line"] for o in r.overlaps})
        r.warnings.append(f"이전 영상 가사와 표현이 겹치는 줄 {len(lines_hit)}개: "
                          + " / ".join(f"\"{o['phrase']}\"({o['slug']})" for o in r.overlaps[:5]))
    return r
