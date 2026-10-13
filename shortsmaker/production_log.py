"""사람의 기여 기록 — projects/<이름>/production_log.json (덧붙이기만 하고 지우지 않는다).

데이터 확인·수정 내역, 가사 선택·수정, 캐리커처 승인, 연출 선택, 권리 체크, 최종 확인 시간을 남긴다.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

LOG_FILE = "production_log.json"


def now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def log_path(project_dir: Path) -> Path:
    return project_dir / LOG_FILE


def read_log(project_dir: Path) -> list[dict]:
    p = log_path(project_dir)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def append(project_dir: Path, event: str, **data) -> dict:
    """event: data_confirmed, data_source, lyrics_checked, lyrics_chosen, subtitles_edited, caricature_approved,
    variation_chosen, rights_checklist, render, upload_draft ..."""
    entries = read_log(project_dir)
    entry = {"at": now(), "event": event, **data}
    entries.append(entry)
    log_path(project_dir).write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    return entry


def diff_rows(before: list[dict], after: list[dict], fields: tuple[str, ...]) -> list[dict]:
    """자동 초안 표 → 사람이 확정한 표의 차이 (행은 근거 가사 또는 순서로 맞춤)."""
    changes = []
    used = set()
    for i, a in enumerate(after):
        b = next(
            (x for j, x in enumerate(before) if j not in used and x.get("lyric") and x.get("lyric") == a.get("lyric")),
            before[i] if i < len(before) and i not in used else None,
        )
        if b is None:
            changes.append({"row": i, "added": {f: a.get(f) for f in fields}})
            continue
        used.add(before.index(b))
        for f in fields:
            if (b.get(f) or "") != (a.get(f) or ""):
                changes.append({"row": i, "field": f, "from": b.get(f) or "", "to": a.get(f) or ""})
    for j, b in enumerate(before):
        if j not in used and j >= len(after):
            changes.append({"row": j, "removed": {f: b.get(f) for f in fields}})
    return changes
