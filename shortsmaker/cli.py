"""명령줄 진입점.

  python -m shortsmaker.cli timeline projects/x/project.json   # 장면 시간표
  python -m shortsmaker.cli scenes   projects/x/project.json   # 장면 PNG만 생성
  python -m shortsmaker.cli render   projects/x/project.json   # MP4 렌더
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .ffmpeg_builder import FFmpegError
from .models import load_project, load_template
from .pipeline import prepare, render, work_dir_for
from .scenes import contact_sheet
from .timeline import build_timeline, format_timeline


def cmd_timeline(args: argparse.Namespace) -> int:
    project, _ = load_project(args.project)
    template = load_template(project.channel)
    scenes = build_timeline(project, fps=template.fps, transition=template.effects.transition)
    print(f"{project.title}  ({project.duration:g}초, 템플릿: {template.name})")
    print(format_timeline(scenes))
    return 0


def cmd_scenes(args: argparse.Namespace) -> int:
    project, project_dir = load_project(args.project)
    _, _, built = prepare(project, project_dir)
    sheet = contact_sheet([s.preview for s in built.scenes], work_dir_for(project_dir) / "contact_sheet.png")
    for w in built.warnings:
        print("경고:", w)
    print(f"장면 {len(built.scenes)}개 생성, 미리보기 시트 → {sheet}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    project, project_dir = load_project(args.project)
    t0 = time.time()
    try:
        res = render(project, project_dir, Path(args.output) if args.output else None,
                     progress=lambda f, m: print(f"[{f * 100:5.1f}%] {m}"))
    except FFmpegError as e:
        print(e, file=sys.stderr)
        return 1
    for w in res.warnings:
        print("경고:", w)
    print(f"완성: {res.output}  ({time.time() - t0:.1f}초 소요)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="shortsmaker")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("timeline", help="장면 시간표 출력")
    p.add_argument("project")
    p.set_defaults(func=cmd_timeline)

    p = sub.add_parser("scenes", help="장면 PNG 레이어만 생성")
    p.add_argument("project")
    p.set_defaults(func=cmd_scenes)

    p = sub.add_parser("render", help="MP4 렌더")
    p.add_argument("project")
    p.add_argument("-o", "--output", help="출력 경로 (기본: output/<폴더명>.mp4)")
    p.set_defaults(func=cmd_render)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
