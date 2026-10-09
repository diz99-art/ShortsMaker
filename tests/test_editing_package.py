import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from shortsmaker.editing_package import Composition, Lyric, build, srt_text


def config(**kwargs):
    scenes = [{"kind": "intro", "title": "인트로", "image": "이미지.jpg", "duration": .4}]
    scenes.extend({"kind": "rank", "title": f"{rank}위", "rank": rank, "image": "이미지.jpg", "duration": .4}
                  for rank in range(5, 0, -1))
    scenes.append({"kind": "outro", "title": "아웃트로", "image": "이미지.jpg", "duration": .6})
    result = {"title": "검증", "music": "음악.mp3", "scenes": scenes}
    return result | kwargs


@pytest.mark.parametrize("lyrics", [
    [{"start": 1, "end": 1, "text": "가사"}],
    [{"start": 0, "end": 2, "text": "가사"}, {"start": 1, "end": 3, "text": "충돌"}],
    [{"start": 0, "end": 4, "text": "초과"}],
    [{"start": 0, "end": 1, "text": "가사"}] * 2,
    [{"start": 0, "end": 1, "text": "   "}],
])
def test_invalid_lyrics(lyrics):
    with pytest.raises(ValueError):
        Composition.model_validate(config(lyrics=lyrics))


def test_utf8_srt():
    assert srt_text([Lyric(start=.5, end=2.5, text="한글")]) == "1\n00:00:00,500 --> 00:00:02,500\n한글\n\n"


def test_rank_order():
    data = config()
    data["scenes"][1]["rank"] = 1
    with pytest.raises(ValueError):
        Composition.model_validate(data)


def test_package(tmp_path):
    import subprocess
    image = tmp_path / "이미지.jpg"
    music = tmp_path / "음악.mp3"
    Image.new("RGB", (320, 160), "red").save(image)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=1",
                    str(music)], check=True)
    inputs = [image, music]
    hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs]
    path = tmp_path / "input.json"
    path.write_text(json.dumps(config(lyrics=[{"start": .5, "end": 2.5, "text": "한글"}]), ensure_ascii=False))
    destination = tmp_path / "패키지"
    report = build(path, destination, Path(__file__).parents[1] / "assets/fonts/Pretendard-Regular.otf")
    assert report["music_shorter_than_video"]
    assert report["duration"] == 3
    assert not report["capcut_verified"]
    with Image.open(destination / "prepared/01_장면.png") as prepared:
        assert prepared.size == (1080, 1920)
        assert prepared.getpixel((540, 100)) == (0, 0, 0)
        assert prepared.getpixel((540, 960))[0] > 240
    assert (destination / "자막.srt").read_text().endswith("한글\n\n")
    assert hashes == [hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs]
    with pytest.raises(FileExistsError):
        build(path, destination, Path(__file__).parents[1] / "assets/fonts/Pretendard-Regular.otf")
