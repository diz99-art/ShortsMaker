"""명령줄 진입점.

  python -m shortsmaker.cli timeline projects/x/project.json
"""
from __future__ import annotations

import argparse
import sys

from .models import load_project, load_template
from .timeline import build_timeline, format_timeline


def cmd_timeline(args: argparse.Namespace) -> int:
    project, _ = load_project(args.project)
    template = load_template(project.channel)
    scenes = build_timeline(project, fps=template.fps, transition=template.effects.transition)
    print(f"{project.title}  ({project.duration:g}초, 템플릿: {template.name})")
    print(format_timeline(scenes))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="shortsmaker")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("timeline", help="장면 시간표 출력")
    p.add_argument("project")
    p.set_defaults(func=cmd_timeline)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
