"""Create synthetic input; no user assets required."""
import argparse
import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def create(destination: Path):
    destination.mkdir(parents=True, exist_ok=False)
    font = ImageFont.truetype(str(Path(__file__).parents[1] / "assets/fonts/Pretendard-Bold.otf"), 60)
    image = Image.new("RGB", (720, 1280), (20, 80, 140))
    draw = ImageDraw.Draw(image)
    draw.text((360, 400), "ShortsMaker\n한글 이미지 검증", font=font, anchor="mm", align="center", fill="white")
    image.save(destination / "검증 이미지.png")
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-n", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=3", "-c:a", "libmp3lame", str(destination / "검증 음악.mp3")], check=True)
    scenes = [{"kind": "intro", "title": "인트로", "duration": .4}]
    scenes.extend({"kind": "rank", "title": f"{rank}위", "rank": rank, "duration": .4} for rank in range(5, 0, -1))
    scenes.append({"kind": "outro", "title": "아웃트로", "duration": .6})
    for scene in scenes:
        scene["image"] = "검증 이미지.png"
    config = {"title": "ShortsMaker 가져오기 검증", "music": "검증 음악.mp3", "scenes": scenes,
              "lyrics": [{"start": .5, "end": 1.5, "text": "첫 번째 한글 자막"},
                         {"start": 1.5, "end": 2.5, "text": "두 번째 한글 자막"}]}
    (destination / "input.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination / "input.json"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    print(create(parser.parse_args().destination))
