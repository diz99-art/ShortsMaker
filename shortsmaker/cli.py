"""명령줄 진입점.

  python -m shortsmaker.cli timeline projects/x/project.json   # 장면 시간표
  python -m shortsmaker.cli scenes   projects/x/project.json   # 장면 PNG만 생성
  python -m shortsmaker.cli check    projects/x/project.json   # 렌더 전 검증
  python -m shortsmaker.cli render   projects/x/project.json   # MP4 렌더
  python -m shortsmaker.cli capcut   projects/x/project.json   # CapCut 초안 (비공식)
  python -m shortsmaker.cli kit      projects/x/project.json   # CapCut 수동 가져오기 재료
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .ffmpeg_builder import FFmpegError
from .models import load_project, load_template
from .pipeline import LyricsError, prepare, render, work_dir_for
from .validate import ValidationError, pre_render
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
                     progress=lambda f, m: print(f"[{f * 100:5.1f}%] {m}"),
                     subtitle_mode=args.subtitles)
    except ValidationError as e:
        print("검증 실패:", file=sys.stderr)
        for err in e.report.errors:
            print(" -", err, file=sys.stderr)
        return 1
    except LyricsError as e:
        print(f"가사 오류:\n{e}", file=sys.stderr)
        return 1
    except FFmpegError as e:
        print(e, file=sys.stderr)
        return 1
    for w in res.warnings:
        print("경고:", w)
    print(f"자막 방식: {res.subtitle_mode}")
    print(f"완성: {res.output}  ({time.time() - t0:.1f}초 소요)")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    project, project_dir = load_project(args.project)
    r = pre_render(project, project_dir)
    for e in r.errors:
        print("오류:", e)
    for w in r.warnings:
        print("경고:", w)
    print("검증 통과" if r.ok else f"검증 실패 (오류 {len(r.errors)}개)")
    return 0 if r.ok else 1


def cmd_capcut(args: argparse.Namespace) -> int:
    from .capcut_export import CapCutExportError, export_draft  # 비공식 기능은 필요할 때만 불러온다
    project, project_dir = load_project(args.project)
    try:
        res = export_draft(project, project_dir, root=Path(args.root) if args.root else None, name=args.name)
    except CapCutExportError as e:
        print(e, file=sys.stderr)
        return 1
    for w in res.warnings:
        print("경고:", w)
    print(f"CapCut 초안: {res.path}")
    return 0


def cmd_kit(args: argparse.Namespace) -> int:
    from .capcut_export import export_manual_kit
    project, project_dir = load_project(args.project)
    print(f"CapCut 수동 가져오기 키트: {export_manual_kit(project, project_dir)}")
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

    p = sub.add_parser("check", help="렌더 전 검증만 실행")
    p.add_argument("project")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("render", help="MP4 렌더")
    p.add_argument("project")
    p.add_argument("-o", "--output", help="출력 경로 (기본: output/<폴더명>.mp4)")
    p.add_argument("--subtitles", choices=["auto", "ass", "png"], default="auto",
                   help="가사 자막 방식 (auto: libass 있으면 ASS, 없으면 PNG)")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("capcut", help="CapCut 초안 만들기 (비공식)")
    p.add_argument("project")
    p.add_argument("--root", help="CapCut 초안 폴더 (기본: ~/Movies/CapCut/User Data/Projects/com.lveditor.draft)")
    p.add_argument("--name", help="초안 이름 (기본: ShortsMaker_<폴더명>_<시각>)")
    p.set_defaults(func=cmd_capcut)

    p = sub.add_parser("kit", help="CapCut 수동 가져오기 재료 (장면 PNG·음악·SRT)")
    p.add_argument("project")
    p.set_defaults(func=cmd_kit)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
