"""렌더 결과 확인용 도구: 스트림 정보 출력 + 지정 시각 프레임을 한 장으로 이어 붙이기.

  .venv/bin/python scripts/probe.py output/x.mp4 --at 1 3.2 10.9 --out sheet.png
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def frames_sheet(video: Path, times: list[float], out: Path, width: int = 270) -> Path:
    font = ImageFont.truetype(str(ROOT / "assets/fonts/Pretendard-Bold.otf"), 22)
    thumbs = []
    with tempfile.TemporaryDirectory() as td:
        for i, t in enumerate(times):
            png = Path(td) / f"{i}.png"
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", str(video),
                 "-frames:v", "1", "-vf", f"scale={width}:-1", str(png)],
                check=True,
            )
            im = Image.open(png).convert("RGB")
            ImageDraw.Draw(im).text((8, 6), f"{t:g}s", font=font, fill="#00ff66",
                                    stroke_width=2, stroke_fill="black")
            thumbs.append(im)
    gap = 8
    h = thumbs[0].height
    sheet = Image.new("RGB", (len(thumbs) * (width + gap) + gap, h + 2 * gap), "#222")
    for i, im in enumerate(thumbs):
        sheet.paste(im, (gap + i * (width + gap), gap))
    sheet.save(out)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("--at", type=float, nargs="*", default=[])
    ap.add_argument("--out", type=Path, default=Path("build/frames.png"))
    ap.add_argument("--width", type=int, default=270)
    a = ap.parse_args()
    info = probe(a.video)
    for s in info["streams"]:
        if s["codec_type"] == "video":
            print(f"video: {s['codec_name']} {s['width']}x{s['height']} {s['pix_fmt']} "
                  f"{s['r_frame_rate']}fps profile={s.get('profile')}")
        else:
            print(f"audio: {s['codec_name']} {s['sample_rate']}Hz {s['channels']}ch "
                  f"{int(s.get('bit_rate', 0)) // 1000}kbps")
    print(f"duration: {float(info['format']['duration']):.3f}s")
    if a.at:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        print("frames →", frames_sheet(a.video, a.at, a.out, a.width))
