"""FFmpeg 명령 조립·실행.

1) 장면마다 클립 렌더: base 배경 + 캐리커처 줌인(zoompan→고정 마스크) + 순위 숫자 팝
2) 최종 합성: 클립들을 xfade로 잇고, 가사 PNG를 시간 구간 overlay, 음악 자르기·페이드아웃
"""
from __future__ import annotations

import shlex
import shutil
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .models import Template
from .scenes import SceneLayers

ProgressFn = Callable[[float, str], None]

# PNG 입력은 풀 레인지(pc)로 들어오므로 표준 TV 레인지 yuv420p로 명시 변환
TO_TV_YUV420 = "scale=out_range=tv:flags=bicubic,format=yuv420p"
CLIP_VCODEC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", "-color_range", "tv"]
FINAL_VCODEC = ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-color_range", "tv",
                "-profile:v", "high"]
FINAL_ACODEC = ["-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2"]


class FFmpegError(RuntimeError):
    def __init__(self, cmd: list[str], stderr: str):
        self.cmd = cmd
        self.stderr_tail = "\n".join(stderr.strip().splitlines()[-25:])
        super().__init__(f"FFmpeg 실패\n명령: {self.command_line}\n--- stderr (끝부분) ---\n{self.stderr_tail}")

    @property
    def command_line(self) -> str:
        return shlex.join(self.cmd)


def ffmpeg_bin() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise FileNotFoundError("ffmpeg를 찾을 수 없습니다. `brew install ffmpeg`로 설치하세요.")
    return path


def run_ffmpeg(args: list[str]) -> None:
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(cmd, proc.stderr)


def _f(x: float) -> str:
    return f"{x:.4f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------- 장면 클립

def scene_clip_args(layers: SceneLayers, t: Template, out: Path) -> list[str]:
    D = layers.scene.clip_duration
    fps = t.fps
    fx = t.effects
    loop = ["-loop", "1", "-framerate", str(fps), "-t", _f(D)]
    inputs = [*loop, "-i", str(layers.base)]
    chains: list[str] = []
    cur = "[0:v]"
    idx = 1

    if layers.image:
        img = layers.image
        nframes = max(2, round(D * fps))
        z = fx.zoom_to - 1
        inputs += [*loop, "-i", str(img.path), *loop, "-i", str(img.mask)]
        chains.append(
            f"[{idx}:v]zoompan=z='1+{_f(z)}*on/{nframes - 1}'"
            f":x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={img.box.w}x{img.box.h}:fps={fps},"
            f"format=rgba[ph];[{idx + 1}:v]format=gray[mk];[ph][mk]alphamerge[card];"
            f"{cur}[card]overlay=x={img.box.x}:y={img.box.y}:shortest=1[v{idx}]"
        )
        cur = f"[v{idx}]"
        idx += 2

    if layers.pop:
        pop = layers.pop
        P = max(fx.rank_pop, 1 / fps)
        delay = fx.transition / 2  # xfade 중간쯤부터 튀어나오게
        cx, cy = pop.box.center
        inputs += [*loop, "-i", str(pop.path)]
        s = f"(1+0.45*pow(max(0\\,1-max(0\\,t-{_f(delay)})/{_f(P)})\\,2))"
        chains.append(
            f"[{idx}:v]format=rgba,scale=w='iw*{s}':h='ih*{s}':eval=frame,"
            f"fade=t=in:st={_f(delay)}:d={_f(P * 0.6)}:alpha=1[pp];"
            f"{cur}[pp]overlay=x='{_f(cx)}-overlay_w/2':y='{_f(cy)}-overlay_h/2':shortest=1[v{idx}]"
        )
        cur = f"[v{idx}]"
        idx += 1

    chains.append(f"{cur}{TO_TV_YUV420}[vout]")
    return [
        *inputs,
        "-filter_complex", ";".join(chains),
        "-map", "[vout]", "-r", str(fps), "-t", _f(D), *CLIP_VCODEC, "-an", str(out),
    ]


def render_scene_clips(
    scene_layers: list[SceneLayers], t: Template, clip_dir: Path,
    progress: ProgressFn | None = None, workers: int = 3,
) -> list[Path]:
    clip_dir.mkdir(parents=True, exist_ok=True)
    outs = [clip_dir / f"clip{s.scene.index:02d}.mp4" for s in scene_layers]

    # progress는 호출한 스레드에서만 부른다 (Streamlit 위젯은 작업 스레드에서 갱신할 수 없음)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(run_ffmpeg, scene_clip_args(s, t, o)): s for s, o in zip(scene_layers, outs)}
        for done, f in enumerate(as_completed(futures), 1):
            f.result()
            if progress:
                progress(done / len(outs), f"장면 클립 {done}/{len(outs)} 완료 ({futures[f].scene.label})")
    return outs


# ---------------------------------------------------------------- 최종 합성

@dataclass
class TimedOverlay:
    """시간 구간에만 보이는 PNG (가사 등)."""
    path: Path
    x: int
    y: int
    start: float
    end: float


def final_args(
    clips: list[Path],
    starts: list[float],
    t: Template,
    duration: float,
    out: Path,
    *,
    music: Path | None = None,
    music_start: float = 0.0,
    overlays: list[TimedOverlay] | None = None,
    extra_vf: str | None = None,
) -> list[str]:
    """extra_vf: 전환·overlay 뒤에 붙일 필터 (예: ASS subtitles)."""
    overlays = overlays or []
    fx = t.effects
    inputs: list[str] = []
    for c in clips:
        inputs += ["-i", str(c)]
    chains: list[str] = []

    # xfade 체인: k번째 전환 offset = (k+1)번째 장면 시작 시각
    cur = "[0:v]"
    for k in range(1, len(clips)):
        nxt = f"[x{k}]"
        if fx.transition > 0:
            chains.append(
                f"{cur}[{k}:v]xfade=transition=fade:duration={_f(fx.transition)}:offset={_f(starts[k])}{nxt}"
            )
        else:
            chains.append(f"{cur}[{k}:v]concat=n=2:v=1:a=0{nxt}")
        cur = nxt

    idx = len(clips)
    for n, ov in enumerate(overlays):
        inputs += ["-i", str(ov.path)]
        nxt = f"[o{n}]"
        chains.append(
            f"{cur}[{idx}:v]overlay=x={ov.x}:y={ov.y}:enable='gte(t\\,{_f(ov.start)})*lt(t\\,{_f(ov.end)})'{nxt}"
        )
        cur = nxt
        idx += 1
    if extra_vf:
        chains.append(f"{cur}{extra_vf}[sub]")
        cur = "[sub]"
    chains.append(f"{cur}{TO_TV_YUV420}[vout]")

    # 오디오: 음악이 없으면 무음 트랙 (오디오 스트림은 항상 존재)
    if music:
        inputs += ["-ss", _f(music_start), "-i", str(music)]
    else:
        inputs += ["-f", "lavfi", "-t", _f(duration), "-i", "anullsrc=r=44100:cl=stereo"]
    fade = min(fx.music_fade_out, duration)
    chains.append(
        f"[{idx}:a]apad,atrim=0:{_f(duration)},asetpts=PTS-STARTPTS,"
        f"afade=t=out:st={_f(duration - fade)}:d={_f(fade)}[aout]"
    )

    return [
        *inputs,
        "-filter_complex", ";".join(chains),
        "-map", "[vout]", "-map", "[aout]",
        "-r", str(t.fps), "-t", _f(duration),
        *FINAL_VCODEC, *FINAL_ACODEC, "-movflags", "+faststart", str(out),
    ]
