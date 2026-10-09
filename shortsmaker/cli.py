"""명령줄 진입점.

  python -m shortsmaker.cli timeline projects/x/project.json   # 장면 시간표
  python -m shortsmaker.cli scenes   projects/x/project.json   # 장면 PNG만 생성
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .models import ROOT, load_project, load_template
from .scenes import build_scenes, contact_sheet
from .timeline import build_timeline, format_timeline

BUILD_DIR = ROOT / "build"


def _load(path: str):
    project, project_dir = load_project(path)
    template = load_template(project.channel)
    scenes = build_timeline(project, fps=template.fps, transition=template.effects.transition)
    return project, project_dir, template, scenes


def _work_dir(project_dir: Path) -> Path:
    return BUILD_DIR / project_dir.name


def cmd_timeline(args: argparse.Namespace) -> int:
    project, _, template, scenes = _load(args.project)
    print(f"{project.title}  ({project.duration:g}초, 템플릿: {template.name})")
    print(format_timeline(scenes))
    return 0


def cmd_scenes(args: argparse.Namespace) -> int:
    project, project_dir, template, scenes = _load(args.project)
    work = _work_dir(project_dir) / "scenes"
    res = build_scenes(project, project_dir, template, scenes, work)
    sheet = contact_sheet([s.preview for s in res.scenes], work.parent / "contact_sheet.png")
    for w in res.warnings:
        print("경고:", w)
    print(f"장면 {len(res.scenes)}개 생성 → {work}")
    print(f"미리보기 시트 → {sheet}")
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
