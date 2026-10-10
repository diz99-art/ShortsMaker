"""유튜브 업로드 준비 — 제목·설명·태그 초안 (업로드는 하지 않는다).

설명에는 데이터 출처, credits.txt(사진 출처), '음악: Suno로 제작', 캐리커처 제작 방식을 자동으로 넣는다.
"""
from __future__ import annotations

import json
from pathlib import Path

from .rank_extract import RankRow, find_team, label

TITLE_MAX = 100
TAGS_MAX = 500
BASE_TAGS = ["야구한장", "KBO", "프로야구", "야구", "쇼츠", "TOP5"]
MAKING = {
    "manual": "캐리커처: 선수 사진·장면 설명을 바탕으로 직접 프롬프트를 써서 AI 이미지 도구로 그린 뒤 사람이 골라 편집",
    "openai": "캐리커처: 선수 사진을 바탕으로 AI(OpenAI 이미지 편집)로 만든 뒤 사람이 확인·승인",
    "mock": "MOCK 시험용 — 업로드하지 마세요",
}


def titles(title: str, rows: list[RankRow]) -> list[str]:
    one = title.replace("\n", " ").strip()
    main = title.split("\n")[-1].strip() or one  # 2줄 제목이면 둘째 줄(주제)
    top = next((r for r in rows if r.rank == 1), None)
    cands = [one]
    if top:
        cands.append(f"{main} | 1위는 {top.player or top.team}?")
        cands.append(f"{top.record}의 주인공은? {main}")
    out = []
    for c in cands:
        c = " ".join(c.split())
        if len(c) > TITLE_MAX:
            c = c[: TITLE_MAX - 1] + "…"
        if c not in out:
            out.append(c)
    return out[:3]


def tags(rows: list[RankRow], extra: list[str] | None = None) -> list[str]:
    out: list[str] = []
    for t in BASE_TAGS + list(extra or []):
        if t and t not in out:
            out.append(t)
    for r in rows:
        team = find_team(r.team)
        for t in (r.player, team.name if team else r.team, team.short if team else ""):
            if t and t not in out:
                out.append(t)
    keep, size = [], 0
    for t in out:  # 유튜브 태그 합계 500자 이내 (쉼표 포함)
        if size + len(t) + 1 > TAGS_MAX:
            break
        keep.append(t)
        size += len(t) + 1
    return keep


def description(title: str, rows: list[RankRow], *, data_source: str, credits: str, gen_mode: str,
                suno_note: str = "음악: Suno로 제작") -> str:
    lines = [title.replace("\n", " "), ""]
    for r in sorted(rows, key=lambda r: -(r.slot or r.rank)):
        who = " ".join(x for x in (r.player, f"({r.team})" if r.team else "") if x)
        lines.append(f"{label(rows, r)} {who} — {r.record}" + (f" ({r.year})" if r.year else ""))
    lines += ["", f"데이터 출처: {data_source}" if data_source else "데이터 출처: (입력 필요)", "",
              credits.strip() if credits else "[사진 출처] 없음", "", suno_note, MAKING.get(gen_mode, ""), "",
              "#야구한장 #KBO #프로야구"]
    return "\n".join(lines).strip() + "\n"


def build(title: str, rows: list[RankRow], *, data_source: str, credits: str, gen_mode: str,
          extra_tags: list[str] | None = None) -> dict:
    return {"titles": titles(title, rows), "description": description(title, rows, data_source=data_source,
                                                                        credits=credits, gen_mode=gen_mode),
            "tags": tags(rows, extra_tags)}


def write(d: Path, draft: dict) -> tuple[Path, Path]:
    j = d / "upload.json"
    j.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    t = d / "upload.txt"
    t.write_text("[제목 후보]\n" + "\n".join(f"- {x}" for x in draft["titles"]) + "\n\n[설명]\n" + draft["description"]
                 + "\n[태그]\n" + ", ".join(draft["tags"]) + "\n", encoding="utf-8")
    return j, t
