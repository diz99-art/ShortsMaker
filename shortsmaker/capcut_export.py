"""CapCut 초안(draft) 내보내기 — 비공식(pyCapCut) 기능.

MP4 렌더와 완전히 분리되어 있다. 여기서 실패해도 MP4 기능에는 영향이 없다.

Mac CapCut 5.9.1 대응 (docs/capcut-compatibility.md 시험 결과 반영)
  - 초안 본문은 draft_content.json 뿐 아니라 draft_info.json 에도 쓴다.
  - new_version / platform 값을 설치된 앱의 기존 초안에서 읽어 맞춘다.
  - CapCut은 샌드박스 앱이라 ~/Movies 밖의 파일을 열지 못한다("Unsupported Media").
    → 모든 미디어를 초안 폴더(~/Movies/CapCut/...) 안의 media/ 로 복사해서 참조한다.

초안을 못 쓰는 경우를 위해 수동 가져오기 키트(장면 PNG + 자른 음악 + SRT)도 만들 수 있다.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .lyrics import check_lyrics, resolve_lyrics
from .models import OUTPUT_DIR, LyricLine, Project
from .pipeline import prepare
from .validate import media_duration

DEFAULT_DRAFTS_DIR = Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
DRAFT_PREFIX = "ShortsMaker_"
US = 1_000_000  # CapCut 시간 단위: 마이크로초

# 설치 앱 정보를 못 읽을 때 쓰는 값 (CapCut Mac 5.9.1에서 확인)
FALLBACK_NEW_VERSION = "132.0.0"
FALLBACK_PLATFORM = {"app_id": 359289, "app_source": "cc", "app_version": "5.9.1", "os": "mac"}


class CapCutExportError(RuntimeError):
    pass


@dataclass
class DraftResult:
    name: str
    path: Path
    warnings: list[str] = field(default_factory=list)


def drafts_dir() -> Path:
    """CapCut 초안 폴더. 환경변수 SHORTSMAKER_CAPCUT_DIR 로 바꿀 수 있다."""
    return Path(os.environ.get("SHORTSMAKER_CAPCUT_DIR", DEFAULT_DRAFTS_DIR)).expanduser()


def us(seconds: float) -> int:
    return round(seconds * US)


def detect_app_format(root: Path) -> tuple[str, dict]:
    """설치된 CapCut이 만든 가장 최근 초안에서 (new_version, platform) 을 읽는다."""
    candidates = []
    if root.exists():
        for d in root.iterdir():
            info = d / "draft_info.json"
            if d.is_dir() and not d.name.startswith(DRAFT_PREFIX) and info.exists():
                candidates.append(info)
    for info in sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(info.read_text(encoding="utf-8"))
            if data.get("new_version") and isinstance(data.get("platform"), dict):
                platform = {k: data["platform"][k] for k in ("app_id", "app_source", "app_version", "os")
                            if k in data["platform"]}
                return data["new_version"], platform
        except (OSError, ValueError):
            continue
    return FALLBACK_NEW_VERSION, dict(FALLBACK_PLATFORM)


def _rgb01(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _adapt_for_mac(draft: Path, new_version: str, platform: dict) -> None:
    """pyCapCut 생성물(Windows 형식)을 Mac CapCut이 여는 형식으로 보정."""
    data = json.loads((draft / "draft_content.json").read_text(encoding="utf-8"))
    data["new_version"] = new_version
    data["platform"] = platform
    data["last_modified_platform"] = platform
    text = json.dumps(data, ensure_ascii=False)
    for name in ("draft_content.json", "draft_info.json"):
        (draft / name).write_text(text, encoding="utf-8")

    now_us = int(time.time() * US)
    meta = json.loads((draft / "draft_meta_info.json").read_text(encoding="utf-8"))
    meta.update(
        draft_id=str(uuid.uuid4()).upper(), draft_name=draft.name,
        draft_fold_path=str(draft), draft_root_path=str(draft.parent),
        tm_duration=data["duration"], tm_draft_create=now_us, tm_draft_modified=now_us,
    )
    entries = []
    for kind in ("videos", "audios"):
        for m in data["materials"][kind]:
            entries.append({
                "ai_group_type": "", "create_time": -1,
                "duration": m.get("duration", 0) if kind == "audios" else 0,
                "enter_from": 0, "extra_info": Path(m["path"]).name, "file_Path": m["path"],
                "height": m.get("height", 0), "width": m.get("width", 0), "id": m["id"],
                "import_time": -1, "import_time_ms": -1, "item_source": 1, "material_color_tag": "",
                "md5": "", "metetype": "photo" if kind == "videos" else "music",
                "roughcut_time_range": {"duration": -1, "start": -1},
                "sub_time_range": {"duration": -1, "start": -1}, "type": 0,
            })
    for group in meta.get("draft_materials", []):
        if group.get("type") == 0:
            group["value"] = entries
    (draft / "draft_meta_info.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def _lyrics_or_raise(project: Project, project_dir: Path) -> list[LyricLine]:
    lines = resolve_lyrics(project, project_dir)
    errors = check_lyrics(lines, project.duration)
    if errors:
        raise CapCutExportError("가사 오류: " + " / ".join(errors))
    return lines


def export_draft(
    project: Project,
    project_dir: Path,
    *,
    root: Path | None = None,
    name: str | None = None,
) -> DraftResult:
    """CapCut 초안 폴더에 새 초안을 만든다. 같은 이름이 있으면 실패(덮어쓰지 않음)."""
    try:
        import pycapcut as cc
    except ImportError as e:  # pragma: no cover
        raise CapCutExportError(f"pycapcut을 불러올 수 없습니다: {e}") from e

    root = (root or drafts_dir()).expanduser()
    if not root.exists():
        raise CapCutExportError(f"CapCut 초안 폴더가 없습니다: {root} (CapCut 설정의 초안 위치를 확인하세요)")
    lines = _lyrics_or_raise(project, project_dir)
    t, scenes, built = prepare(project, project_dir)
    warnings = list(built.warnings)

    name = name or f"{DRAFT_PREFIX}{project_dir.name}_{datetime.now():%Y%m%d_%H%M%S}"
    draft = root / name
    if draft.exists():
        raise CapCutExportError(f"같은 이름의 초안이 이미 있습니다: {draft}")

    new_version, platform = detect_app_format(root)
    try:
        script = cc.DraftFolder(str(root)).create_draft(name, t.width, t.height, t.fps)
        media = draft / "media"
        media.mkdir()

        # 장면: 완성 상태 미리보기 PNG를 장면 구간에 배치 (전환·줌은 CapCut에서 추가)
        script.add_track(cc.TrackType.video, "장면")
        for s in built.scenes:
            dst = media / f"{s.scene.index:02d}_{s.scene.label}.png"
            shutil.copy2(s.preview, dst)
            script.add_segment(
                cc.VideoSegment(str(dst), cc.Timerange(us(s.scene.start), us(s.scene.duration))), "장면")

        # 음악: music_start 부터 영상 길이만큼, 끝 페이드아웃
        if project.music:
            src = project_dir / project.music
            dst = media / f"music{src.suffix.lower()}"
            shutil.copy2(src, dst)
            length = media_duration(dst) or 0
            use = min(project.duration, max(0.0, length - project.music_start))
            if use <= 0:
                raise CapCutExportError("음악 시작 위치가 음악 길이보다 깁니다")
            if use < project.duration:
                warnings.append(f"음악이 영상보다 짧아 {use:.1f}초까지만 배치했습니다")
            script.add_track(cc.TrackType.audio, "음악")
            seg = cc.AudioSegment(str(dst), cc.Timerange(0, us(use)),
                                  source_timerange=cc.Timerange(us(project.music_start), us(use)))
            fade = min(t.effects.music_fade_out, use)
            if fade > 0:
                seg.add_fade(0, us(fade))
            script.add_segment(seg, "음악")

        # 가사: 편집 가능한 텍스트 트랙 (템플릿 가사 영역 위치)
        if lines:
            box = t.lyrics
            cy = box.y + box.h / 2
            transform_y = (t.height / 2 - cy) / (t.height / 2)  # 위가 +, 화면 반 높이 단위
            script.add_track(cc.TrackType.text, "가사")
            for line in lines:
                script.add_segment(cc.TextSegment(
                    line.text, cc.Timerange(us(line.start), us(line.end - line.start)),
                    style=cc.TextStyle(size=9.0, bold=True, color=_rgb01(box.color), align=1,
                                       auto_wrapping=True, max_line_width=0.85),
                    border=cc.TextBorder(color=_rgb01(box.stroke_color), width=30.0),
                    clip_settings=cc.ClipSettings(transform_y=round(transform_y, 4)),
                ), "가사")

        script.save()
        _adapt_for_mac(draft, new_version, platform)
    except Exception as e:
        shutil.rmtree(draft, ignore_errors=True)  # 반쯤 만든 초안은 남기지 않는다
        if isinstance(e, CapCutExportError):
            raise
        raise CapCutExportError(f"CapCut 초안 생성 실패: {type(e).__name__}: {e}") from e

    warnings.append("CapCut 초안 기능은 비공식입니다. CapCut에서 열어 이미지·음악·가사를 확인하세요.")
    return DraftResult(name=name, path=draft, warnings=warnings)


# ---------------------------------------------------------------- 수동 가져오기 키트

def _srt_time(t: float) -> str:
    ms = round(t * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(lines: list[LyricLine]) -> str:
    return "\n".join(
        f"{i}\n{_srt_time(l.start)} --> {_srt_time(l.end)}\n{l.text}\n" for i, l in enumerate(lines, 1)
    )


def export_manual_kit(project: Project, project_dir: Path, out_dir: Path | None = None) -> Path:
    """CapCut에 손으로 가져올 재료: 장면 PNG(시작 시각 이름), 영상 길이로 자른 음악, 가사 SRT."""
    lines = _lyrics_or_raise(project, project_dir)
    t, scenes, built = prepare(project, project_dir)
    out = out_dir or OUTPUT_DIR / f"{project_dir.name}_capcut_kit"
    if out.exists():
        shutil.rmtree(out)
    (out / "scenes").mkdir(parents=True)
    rows = []
    for s in built.scenes:
        fn = f"{s.scene.index:02d}_{s.scene.start:05.2f}s_{s.scene.label}.png"
        shutil.copy2(s.preview, out / "scenes" / fn)
        rows.append(f"{fn}\t{s.scene.start:.2f}\t{s.scene.end:.2f}")
    (out / "scenes" / "timeline.tsv").write_text("파일\t시작(초)\t끝(초)\n" + "\n".join(rows) + "\n",
                                                 encoding="utf-8")
    if project.music:
        fade = min(t.effects.music_fade_out, project.duration)
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", str(project.music_start), "-i",
             str(project_dir / project.music), "-t", str(project.duration),
             "-af", f"afade=t=out:st={project.duration - fade}:d={fade}",
             "-c:a", "aac", "-b:a", "192k", str(out / "music.m4a")],
            check=True, capture_output=True,
        )
    if lines:
        (out / "lyrics.srt").write_text(build_srt(lines), encoding="utf-8")
    return out
