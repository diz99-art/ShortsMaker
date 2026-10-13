"""git 에 들어가면 안 되는 파일을 막는다 (pre-commit 과 CI 에서 실행).

- 폰트·노래·효과음·그림·영상: assets/placeholders/, examples/, docs/images/ 밖에서는 금지
- 5MB 넘는 파일 금지
- .env (예시 .env.example 은 허용)
- local/denylist.txt 가 있으면 (내 컴퓨터에만) 그 안의 글자가 들어간 파일 금지 — 공개하면 안 되는 이름 등

실행: python scripts/check_repo_files.py            # 커밋하려고 올린(staged) 파일
      python scripts/check_repo_files.py --all      # git 이 관리하는 모든 파일
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEDIA = {".ttf", ".otf", ".mp3", ".wav", ".m4a", ".ogg", ".flac", ".mp4", ".mov",
         ".png", ".jpg", ".jpeg", ".webp", ".gif"}
MEDIA_ALLOWED = ("assets/placeholders/", "examples/", "docs/images/")
MAX_BYTES = 5 * 1024 * 1024
DENYLIST = ROOT / "local" / "denylist.txt"


def _git_files(all_files: bool) -> list[str]:
    cmd = ["git", "ls-files"] if all_files else ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"]
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def _denied_words() -> list[str]:
    if not DENYLIST.is_file():
        return []
    lines = DENYLIST.read_text(encoding="utf-8").splitlines()
    return [w.strip() for w in lines if w.strip() and not w.startswith("#")]


def problems(paths: list[str], words: list[str]) -> list[str]:
    out = []
    for rel in paths:
        p = ROOT / rel
        if not p.is_file():
            continue
        if Path(rel).suffix.lower() in MEDIA and not rel.startswith(MEDIA_ALLOWED):
            out.append(f"{rel}: 폰트·오디오·그림·영상은 올리지 않습니다 (허용 폴더: {', '.join(MEDIA_ALLOWED)})")
        if p.stat().st_size > MAX_BYTES:
            out.append(f"{rel}: 5MB 를 넘습니다 ({p.stat().st_size / 1048576:.1f}MB)")
        if Path(rel).name == ".env" or (Path(rel).name.startswith(".env.") and Path(rel).name != ".env.example"):
            out.append(f"{rel}: .env 파일은 올리지 않습니다")
        if words and Path(rel).suffix.lower() not in MEDIA:
            text = p.read_text(encoding="utf-8", errors="ignore").lower()
            hits = [w for w in words if w.lower() in text or w.lower() in rel.lower()]
            if hits:
                out.append(f"{rel}: 공개하면 안 되는 글자가 있습니다 (local/denylist.txt)")
    return out


def main(argv: list[str]) -> int:
    found = problems(_git_files("--all" in argv), _denied_words())
    for line in found:
        print("✗", line)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
