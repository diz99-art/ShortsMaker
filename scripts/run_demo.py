"""examples/demo 의 가짜 자료로 30초 시험 영상을 만든다 (make demo).

자료 양식(form.txt) 읽기 → 그림·비프음·LRC 가사 연결 → 렌더 → ffprobe 로 결과 확인.
원본 examples/ 는 그대로 두고, projects/_demo 에 복사해서 작업한다 (projects/ 는 git 제외).
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shortsmaker.models import OUTPUT_DIR, PROJECTS_DIR  # noqa: E402
from shortsmaker.pipeline import render  # noqa: E402
from shortsmaker.quick_parse_info import parse_quick_info  # noqa: E402
from shortsmaker.validate import post_render  # noqa: E402

DEMO = ROOT / "examples" / "demo"
DURATION = 30.0
STARTS = [4.0, 9.0, 14.0, 19.0, 24.0]  # 5위 → 1위 장면 시작 (lyrics.lrc 와 맞춤)


def build(work: Path):
    project = parse_quick_info((DEMO / "form.txt").read_text(encoding="utf-8"))
    items = [it.model_copy(update={"image": f"item{i}.png", "start": STARTS[i - 1]})
             for i, it in enumerate(project.items, 1)]
    project = project.model_copy(update={
        "items": items, "duration": DURATION, "music": "beep.mp3", "lyrics_lrc": "lyrics.lrc"})
    (work / "project.json").write_text(project.model_dump_json(indent=2), encoding="utf-8")
    return project


def main() -> None:
    work = PROJECTS_DIR / "_demo"
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(DEMO, work)
    project = build(work)
    out = OUTPUT_DIR / "demo.mp4"
    t0 = time.perf_counter()
    res = render(project, work, out, require_rights=False,
                 progress=lambda f, msg: print(f"[{f * 100:5.1f}%] {msg}", flush=True))
    took = time.perf_counter() - t0
    check = post_render(res.output, DURATION)
    print(f"완성: {res.output.relative_to(ROOT)} ({took:.1f}초 걸림)")
    print("확인: 1080×1920·30초·오디오 " + ("통과" if check.ok else "실패 — " + "; ".join(check.errors)))
    for w in res.warnings:
        print(" -", w)
    if not check.ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
