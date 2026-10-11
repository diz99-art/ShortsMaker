"""렌더 전·후 검증.

렌더 전: 템플릿·폰트·파일 존재, 가사 겹침·길이 초과, 글자 넘침, 음악 길이, 권리 확인
렌더 후: ffprobe로 해상도·길이(±0.5초)·오디오 스트림 확인
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .lyrics import check_lyrics, resolve_lyrics
from .models import Project, Template, TextBox, load_template
from .scenes import fit_text

DURATION_TOLERANCE = 0.5


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class ValidationError(ValueError):
    def __init__(self, report: Report):
        self.report = report
        super().__init__("\n".join(report.errors))


def media_duration(path: Path) -> float | None:
    if not shutil.which("ffprobe"):
        return None
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(json.loads(proc.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError):
        return None


def text_fields(project: Project, t: Template) -> list[tuple[str, TextBox, str]]:
    """(이름, 박스, 글자) — 화면에 들어가는 모든 글자."""
    if t.mode == "card":  # 카드 모드는 이름·값을 그리지 않고 제목만 쓴다
        return [("제목", t.card.title, project.title.replace("\n", " "))]
    fields: list[tuple[str, TextBox, str]] = [
        ("제목", t.intro.title, project.title),
        ("부제", t.intro.subtitle, project.subtitle),
        ("상단 제목", t.rank.header, project.title),
        ("출처", t.outro.source, project.source_note),
    ]
    for it in project.ordered_items:
        fields += [
            (f"{it.rank}위 이름", t.rank.name, it.name),
            (f"{it.rank}위 값", t.rank.value, it.value),
            (f"{it.rank}위 설명", t.rank.description, it.description),
        ]
    return [f for f in fields if f[2]]


def pre_render(project: Project, project_dir: Path, *, require_rights: bool = True) -> Report:
    r = Report()

    if not shutil.which("ffmpeg"):
        r.errors.append("ffmpeg가 설치되어 있지 않습니다 (`brew install ffmpeg`)")

    try:
        t = load_template(project.channel)
    except Exception as e:  # noqa: BLE001 — 템플릿 문제는 모두 사용자에게 보여준다
        r.errors.append(f"템플릿을 읽을 수 없습니다 ({project.channel}): {e}")
        return r

    if project.duration > t.max_duration + 1e-9:
        r.errors.append(f"영상 길이 {project.duration:g}초가 템플릿 '{t.name}' 최대 {t.max_duration:g}초를 넘습니다")

    for key in t.fonts:
        if not t.font_path(key).exists():
            r.errors.append(f"한글 폰트가 없습니다: {t.font_path(key)} (assets/fonts에 넣어주세요)")
    if r.errors:
        return r

    if t.background and not t.background_path():
        r.warnings.append(f"템플릿 배경 이미지가 없어 그라데이션을 사용합니다: {t.background}")

    if t.mode == "card" and t.card and t.card.auto_cards:  # 정보한장: 임시 글자 카드는 시험 렌더용
        from .info_cards import placeholder_problems
        probs = placeholder_problems(project, project_dir)
        (r.errors if require_rights else r.warnings).extend(
            probs if not require_rights else [p + " (수익화용 렌더 불가)" for p in probs])
    for it in project.ordered_items:
        if not it.image:
            if t.mode == "card" and t.card and t.card.auto_cards:
                continue
            r.warnings.append(f"[{it.rank}위] 이미지가 지정되지 않아 대체 이미지를 사용합니다")
        elif not (project_dir / it.image).exists():
            r.errors.append(f"[{it.rank}위] 이미지 파일이 없습니다: {it.image}")

    if project.music:
        music = project_dir / project.music
        if not music.exists():
            r.errors.append(f"음악 파일이 없습니다: {project.music}")
        else:
            length = media_duration(music)
            if length is None:
                r.errors.append(f"음악 파일을 읽을 수 없습니다: {project.music}")
            elif project.music_start >= length:
                r.errors.append(f"음악 시작 위치({project.music_start:g}초)가 음악 길이({length:.1f}초)보다 깁니다")
            elif length - project.music_start < project.duration:
                r.warnings.append(
                    f"음악이 영상보다 짧습니다 ({length - project.music_start:.1f}초 < {project.duration:g}초). "
                    "남는 부분은 무음입니다"
                )
    else:
        r.warnings.append("음악이 없습니다 (무음 영상)")

    if project.lyrics_lrc and not (project_dir / project.lyrics_lrc).exists():
        r.errors.append(f"LRC 파일이 없습니다: {project.lyrics_lrc}")
    else:
        r.errors += check_lyrics(resolve_lyrics(project, project_dir), project.duration)

    for name, box, value in text_fields(project, t):
        ft = fit_text(value, box, t.font_path(box.font))
        if ft.overflow:
            r.warnings.append(f"[{name}] 최소 글자 크기({box.min_size}px)에서도 박스를 넘쳐 잘립니다: {value}")

    if require_rights and not project.rights_confirmed:
        r.errors.append("자료 정확성과 이미지·음악 사용 권리 확인(rights_confirmed)이 필요합니다")
    return r


def post_render(path: Path, duration: float, width: int = 1080, height: int = 1920) -> Report:
    r = Report()
    if not path.exists():
        r.errors.append(f"출력 파일이 없습니다: {path}")
        return r
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    try:
        info = json.loads(proc.stdout)
    except json.JSONDecodeError:
        r.errors.append(f"ffprobe로 읽을 수 없습니다: {proc.stderr.strip()[-300:]}")
        return r
    streams = info.get("streams", [])
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if not video:
        r.errors.append("비디오 스트림이 없습니다")
    else:
        v = video[0]
        if (v.get("width"), v.get("height")) != (width, height):
            r.errors.append(f"해상도가 {v.get('width')}×{v.get('height')} 입니다 (기대: {width}×{height})")
        if v.get("pix_fmt") != "yuv420p":
            r.warnings.append(f"픽셀 포맷이 {v.get('pix_fmt')} 입니다 (기대: yuv420p)")
    if not audio:
        r.errors.append("오디오 스트림이 없습니다")
    actual = float(info.get("format", {}).get("duration", 0))
    if abs(actual - duration) > DURATION_TOLERANCE:
        r.errors.append(f"영상 길이가 {actual:.2f}초 입니다 (기대: {duration:g}초 ±{DURATION_TOLERANCE})")
    return r
