"""Stage 1 only: generate synthetic, offline compatibility fixtures."""
import argparse
import json
import subprocess
from pathlib import Path

import pycapcut as cc
from PIL import Image


def generate(destination: Path) -> Path:
    destination = destination.resolve()
    # Refuse reuse: never overwrite user files or previous evidence.
    destination.mkdir(parents=True, exist_ok=False)
    media = destination / "media"
    media.mkdir()
    image = media / "검증 이미지.png"
    music = media / "검증 음악.mp3"
    Image.new("RGB", (1080, 1920), (25, 90, 150)).save(image)
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-n", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=3", "-codec:a", "libmp3lame", str(music)
    ], check=True)
    script = cc.DraftFolder(str(destination)).create_draft("ShortsMaker_호환성검증", 1080, 1920, 30)
    script.add_track(cc.TrackType.video).add_track(cc.TrackType.audio).add_track(cc.TrackType.text)
    span = cc.Timerange(0, 3_000_000)
    script.add_segment(cc.VideoSegment(str(image), span))
    script.add_segment(cc.AudioSegment(str(music), span, volume=0.15))
    script.add_segment(cc.TextSegment("한글 자막 검증", cc.Timerange(500_000, 2_000_000),
                                      style=cc.TextStyle(size=12)))
    script.save()
    (media / "검증 자막.srt").write_text(
        "1\n00:00:00,500 --> 00:00:02,500\n한글 자막 검증\n", encoding="utf-8")
    draft = destination / "ShortsMaker_호환성검증"
    payload = json.loads((draft / "draft_content.json").read_text())
    (destination / "probe_summary.json").write_text(json.dumps({
        "status": "generated_only_not_app_verified", "duration_us": payload["duration"],
        "track_types": [t["type"] for t in payload["tracks"]],
        "canvas": payload["canvas_config"], "fps": payload["fps"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return draft


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    print(generate(parser.parse_args().destination))
