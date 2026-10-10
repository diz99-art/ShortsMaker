"""참고 영상과 내 결과의 같은 시점 프레임을 나란히 붙인 비교 이미지.

    .venv/bin/python scripts/compare_frames.py <참고 영상> <비교 대상: project.json 또는 mp4> <출력.png> [초 ...]

비교 대상이 project.json 이면 렌더 없이 CardRenderer 로 그 시점 프레임을 바로 그린다 (배치 맞추기용).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shortsmaker.lyrics import resolve_lyrics  # noqa: E402
from shortsmaker.models import FONTS_DIR, load_project, load_template  # noqa: E402
from shortsmaker.scenes import _font  # noqa: E402

DEFAULT_TIMES = [0.1, 0.5, 2.0, 5.0, 11.1, 11.5, 15.0, 21.9, 25.0, 33.0, 40.0]
W = 360  # 한 칸 너비


def video_frame(path: Path, sec: float) -> Image.Image:
    out = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{sec:.3f}", "-i", str(path), "-frames:v", "1",
                          "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, check=True).stdout
    from io import BytesIO
    return Image.open(BytesIO(out)).convert("RGB")


def main() -> None:
    ref, target, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    # '참고초:결과초' 로 서로 다른 시점을 짝지을 수 있다 (참고 영상이 곡 일부를 잘라 낸 뒤 구간)
    pairs = [tuple(float(v) for v in x.split(":")) if ":" in x else (float(x), float(x)) for x in sys.argv[4:]]
    pairs = pairs or [(x, x) for x in DEFAULT_TIMES]
    if target.suffix == ".json":
        from shortsmaker.card_mode import make_renderer
        project, pdir = load_project(target)
        t = load_template(project.channel)
        r = make_renderer(project, pdir, t, resolve_lyrics(project, pdir))
        mine = lambda s: r.frame_at(s)  # noqa: E731
    else:
        mine = lambda s: video_frame(target, s)  # noqa: E731
    h = round(W * 1920 / 1080)
    font = _font(str(FONTS_DIR / "Pretendard-Bold.otf"), 22)
    sheet = Image.new("RGB", (len(pairs) * (2 * W + 16), h + 40), "white")
    d = ImageDraw.Draw(sheet)
    for i, (sec, my) in enumerate(pairs):
        x = i * (2 * W + 16)
        a = video_frame(ref, sec).resize((W, round(W * 1916 / 1080)))
        b = mine(my).resize((W, h))
        sheet.paste(a, (x, 40))
        sheet.paste(b, (x + W, 40))
        label = f"{sec:.2f}s  참고 | 결과" if sec == my else f"참고 {sec:.2f}s | 결과 {my:.2f}s"
        d.text((x + 6, 8), label, font=font, fill="black")
        d.line([x + W, 40, x + W, 40 + h], fill="red", width=2)
    sheet.save(out)
    print(out)


if __name__ == "__main__":
    main()
