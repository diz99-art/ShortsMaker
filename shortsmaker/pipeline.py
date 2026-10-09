"""렌더 전체 흐름: 검증 → 장면 PNG → 장면 클립 → 최종 MP4 → 결과 검증.

CLI와 Streamlit이 같은 함수를 쓴다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .ffmpeg_builder import ProgressFn, TimedOverlay, final_args, render_scene_clips, run_ffmpeg
from .lyrics import build_ass, check_lyrics, has_libass, render_lyric_pngs, resolve_lyrics, subtitles_filter
from .models import OUTPUT_DIR, ROOT, Project, Template, load_template
from .scenes import SceneBuildResult, build_scenes
from .timeline import Scene, build_timeline

BUILD_DIR = ROOT / "build"


@dataclass
class RenderResult:
    output: Path
    scenes: list[Scene]
    warnings: list[str] = field(default_factory=list)
    subtitle_mode: str = "none"  # "ass" | "png" | "none"


class LyricsError(ValueError):
    pass


def prepare_lyrics(project: Project, project_dir: Path, t: Template, work: Path, mode: str = "auto"
                   ) -> tuple[list[TimedOverlay], str | None, str, list[str]]:
    """가사 자막 레이어 준비. (PNG overlay 목록, ASS 필터, 사용한 방식, 경고)"""
    lines = resolve_lyrics(project, project_dir)
    if not lines:
        return [], None, "none", []
    errors = check_lyrics(lines, project.duration)
    if errors:
        raise LyricsError("\n".join(errors))
    use_ass = mode == "ass" or (mode == "auto" and has_libass())
    if use_ass:
        ass = work / "lyrics.ass"
        ass.parent.mkdir(parents=True, exist_ok=True)
        ass.write_text(build_ass(lines, t), encoding="utf-8")
        return [], subtitles_filter(ass), "ass", []
    overlays, warnings = render_lyric_pngs(lines, t, work / "lyrics")
    return overlays, None, "png", warnings


def work_dir_for(project_dir: Path) -> Path:
    return BUILD_DIR / project_dir.name


def prepare(project: Project, project_dir: Path, template: Template | None = None
            ) -> tuple[Template, list[Scene], SceneBuildResult]:
    """타임라인과 장면 PNG까지만 만든다 (미리보기용)."""
    t = template or load_template(project.channel)
    scenes = build_timeline(project, fps=t.fps, transition=t.effects.transition)
    built = build_scenes(project, project_dir, t, scenes, work_dir_for(project_dir) / "scenes")
    return t, scenes, built


def render(
    project: Project,
    project_dir: Path,
    output: Path | None = None,
    progress: ProgressFn | None = None,
    subtitle_mode: str = "auto",  # "auto" | "ass" | "png"
) -> RenderResult:
    def report(frac: float, msg: str) -> None:
        if progress:
            progress(frac, msg)

    report(0.0, "장면 이미지 생성 중")
    t, scenes, built = prepare(project, project_dir)
    work = work_dir_for(project_dir)

    report(0.05, "가사 자막 준비 중")  # 가사 오류는 오래 걸리는 렌더 전에 알린다
    overlays, ass_vf, sub_mode, lyric_warnings = prepare_lyrics(project, project_dir, t, work, subtitle_mode)

    report(0.1, "장면 클립 렌더 중")
    clips = render_scene_clips(
        built.scenes, t, work / "clips",
        progress=lambda f, m: report(0.1 + 0.6 * f, m),
    )

    report(0.75, "최종 합성 중 (전환·가사·음악)")
    output = output or OUTPUT_DIR / f"{project_dir.name}.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    music = (project_dir / project.music) if project.music else None
    run_ffmpeg(final_args(
        clips, [s.start for s in scenes], t, project.duration, output,
        music=music, music_start=project.music_start, overlays=overlays, extra_vf=ass_vf,
    ))
    report(1.0, "완료")
    return RenderResult(output=output, scenes=scenes, warnings=built.warnings + lyric_warnings,
                        subtitle_mode=sub_mode)
