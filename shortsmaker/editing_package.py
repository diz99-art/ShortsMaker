"""Build offline CapCut import materials without touching CapCut's storage."""
import argparse
import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
from pydantic import BaseModel, Field, model_validator


class Scene(BaseModel):
    kind: str
    title: str
    image: str
    duration: float = Field(gt=0)
    rank: int | None = None


class Lyric(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(min_length=1)


class Composition(BaseModel):
    title: str
    music: str
    scenes: list[Scene] = Field(min_length=1)
    lyrics: list[Lyric] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_timing(self) -> "Composition":
        if [s.kind for s in self.scenes] != ["intro", *(["rank"] * 5), "outro"]:
            raise ValueError("장면은 인트로, 5개 순위, 아웃트로 순서여야 합니다")
        if [s.rank for s in self.scenes[1:6]] != [5, 4, 3, 2, 1]:
            raise ValueError("순위는 5위부터 1위 순서여야 합니다")
        if self.scenes[0].rank is not None or self.scenes[-1].rank is not None:
            raise ValueError("인트로와 아웃트로에는 순위가 없습니다")
        if any(not math.isfinite(s.duration) for s in self.scenes):
            raise ValueError("장면 길이는 유한한 값이어야 합니다")
        # A 30fps frame is the smallest scene unit.
        if any(abs(s.duration * 30 - round(s.duration * 30)) > 1e-6 for s in self.scenes):
            raise ValueError("장면 길이는 1/30초 단위여야 합니다")
        total = sum(round(s.duration * 30) for s in self.scenes) / 30
        previous = 0
        seen = set()
        for line in self.lyrics:
            if not math.isfinite(line.start) or not math.isfinite(line.end):
                raise ValueError("가사 시간은 유한한 값이어야 합니다")
            if line.end <= line.start or line.start < previous or line.end > total:
                raise ValueError("가사 순서, 시간 충돌 또는 영상 길이 초과")
            if round(line.end * 1000) <= round(line.start * 1000):
                raise ValueError("SRT 가사 길이는 최소 1밀리초입니다")
            if not line.text.strip() or "\n\n" in line.text.replace("\r\n", "\n"):
                raise ValueError("빈 가사 또는 빈 줄은 허용하지 않습니다")
            key = (line.start, line.end, line.text)
            if key in seen:
                raise ValueError("중복 가사")
            seen.add(key)
            previous = line.end
        return self


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"{command[0]} 실행 실패: {error.stderr[-2000:]}") from error


def probe(path: Path) -> dict:
    return json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                           "-of", "json", str(path)]).stdout)


def timestamp(seconds: float) -> str:
    ms = round(seconds * 1000)
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


def srt_text(lyrics: list[Lyric]) -> str:
    return "".join(f"{i}\n{timestamp(l.start)} --> {timestamp(l.end)}\n{l.text.strip()}\n\n"
                   for i, l in enumerate(lyrics, 1))


def prepare_image(source: Path) -> Image.Image:
    """Preserve alpha for CapCut overlays; use black padding for opaque photos."""
    with Image.open(source) as opened:
        oriented = ImageOps.exif_transpose(opened)
        has_alpha = "A" in oriented.getbands() or "transparency" in oriented.info
        resized = ImageOps.contain(oriented.convert("RGBA"), (1080, 1920))
    canvas = Image.new("RGBA" if has_alpha else "RGB", (1080, 1920),
                       (0, 0, 0, 0) if has_alpha else "black")
    position = ((1080 - resized.width) // 2, (1920 - resized.height) // 2)
    if has_alpha:
        canvas.alpha_composite(resized, position)
    else:
        canvas.paste(resized, position, resized)
    return canvas


def build(config_path: Path, destination: Path, font_path: Path) -> dict:
    config_path, destination, font_path = config_path.resolve(), destination.resolve(), font_path.resolve()
    config = Composition.model_validate_json(config_path.read_text(encoding="utf-8"))
    base = config_path.parent
    images = [(base / s.image).resolve() for s in config.scenes]
    music = (base / config.music).resolve()
    if music.suffix.lower() != ".mp3":
        raise ValueError("음악은 MP3 파일이어야 합니다")
    for image in images:
        if image.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            raise ValueError("이미지는 PNG/JPG여야 합니다")
        with Image.open(image) as opened:
            opened.verify()
    font = ImageFont.truetype(str(font_path), 64)
    audio_info = probe(music)
    if not any(s["codec_type"] == "audio" for s in audio_info["streams"]):
        raise ValueError("오디오 스트림이 없습니다")
    run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(music), "-f", "null", "-"])
    audio_duration = float(audio_info["format"]["duration"])
    duration = sum(round(s.duration * 30) for s in config.scenes) / 30
    # Validate all inputs before creating output. Never reuse existing paths.
    destination.mkdir(parents=True, exist_ok=False)
    for directory in ("originals", "prepared", "preview_frames", "audio"):
        (destination / directory).mkdir()
    shutil.copy2(music, destination / "audio" / "음악.mp3")
    timeline = []
    frame_paths = []
    start_frame = 0
    for index, (scene, image) in enumerate(zip(config.scenes, images), 1):
        original = Path("originals") / f"{index:02}_{image.name}"
        prepared = Path("prepared") / f"{index:02}_장면.png"
        shutil.copy2(image, destination / original)
        editable = prepare_image(image)
        editable.save(destination / prepared)
        approval_path = image.with_suffix(".approval.json")
        if approval_path.exists():
            approval = json.loads(approval_path.read_text())
            if approval["sha256"] != hashlib.sha256(image.read_bytes()).hexdigest():
                raise ValueError("승인 캐리커처 이미지가 변경되었습니다")
            shutil.copy2(approval_path, destination / "originals" / f"{index:02}_asset_metadata.json")
        canvas = Image.new("RGB", (1080, 1920), "black")
        canvas.paste(editable, (0, 0), editable.getchannel("A") if editable.mode == "RGBA" else None)
        start = start_frame / 30
        end = (start_frame + round(scene.duration * 30)) / 30
        timeline.append({"index": index, "kind": scene.kind, "title": scene.title, "rank": scene.rank,
                         "start": start, "end": end, "duration": scene.duration,
                         "original": original.as_posix(), "prepared": prepared.as_posix()})
        # Burn subtitles only into preview frames; SRT remains independently editable.
        for n in range(round(scene.duration * 30)):
            instant = start + n / 30
            frame = canvas.copy()
            draw = ImageDraw.Draw(frame)
            for line in config.lyrics:
                if line.start <= instant < line.end:
                    bbox = draw.multiline_textbbox((0, 0), line.text, font=font, align="center", stroke_width=3)
                    width = bbox[2] - bbox[0]
                    if width > 1000:
                        raise ValueError("미리보기 자막이 너무 깁니다. 줄바꿈을 추가하세요")
                    draw.multiline_text((540, 1660), line.text, anchor="mm", font=font,
                                        align="center", fill="white", stroke_width=3, stroke_fill="black")
            frame_path = destination / "preview_frames" / f"{len(frame_paths):06}.png"
            frame.save(frame_path)
            frame_paths.append(frame_path)
        start_frame += round(scene.duration * 30)
    (destination / "자막.srt").write_text(srt_text(config.lyrics), encoding="utf-8")
    command = ["ffmpeg", "-nostdin", "-v", "error", "-n", "-framerate", "30", "-i",
               str(destination / "preview_frames" / "%06d.png"), "-i", str(destination / "audio" / "음악.mp3"),
               "-map", "0:v:0", "-map", "1:a:0", "-af", "apad", "-t", str(duration),
               "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-movflags", "+faststart", str(destination / "미리보기.mp4")]
    run(command)
    rendered = probe(destination / "미리보기.mp4")
    if not any(s["codec_type"] == "audio" for s in rendered["streams"]):
        raise ValueError("미리보기 오디오 스트림 오류")
    video = next(s for s in rendered["streams"] if s["codec_type"] == "video")
    if (video["width"], video["height"], video["r_frame_rate"]) != (1080, 1920, "30/1"):
        raise ValueError("미리보기 영상 규격 오류")
    if abs(float(rendered["format"]["duration"]) - duration) > 0.1:
        raise ValueError("미리보기 길이 오류")
    report = {"schema_version": 1, "title": config.title, "width": 1080, "height": 1920, "fps": 30,
              "duration": duration, "scenes": timeline, "lyrics": [l.model_dump() for l in config.lyrics],
              "music": "audio/음악.mp3", "music_duration": audio_duration,
              "music_decoded": True, "music_shorter_than_video": audio_duration < duration,
              "audio_policy": "짧으면 무음으로 채움, 길면 영상 끝에서 자름; 복사된 MP3는 원본 그대로",
              "preview": "미리보기.mp4", "subtitles": "자막.srt", "capcut_verified": False}
    (destination / "구성.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (destination / "가져오기 안내.md").write_text(
        "# CapCut 가져오기\n\n새 로컬 프로젝트를 만드세요. 클라우드 동기화를 누르지 마세요.\n"
        "prepared의 장면 이미지를 구성.json 순서와 시간대로 타임라인에 배치하세요.\n"
        "audio/음악.mp3를 오디오 트랙에 배치하세요. 짧은 음악은 끝부분이 무음입니다.\n"
        "텍스트 → 로컬 자막에서 자막.srt를 가져오세요. 각 자막을 선택해 수정하세요.\n"
        "미리보기.mp4는 자막이 합성된 비교용 영상입니다. 편집에는 이미지·MP3·SRT를 사용하세요.\n"
        "1080x1920, 30fps로 내보내세요. 앱 가져오기 성공 여부는 별도 실제 시험이 필요합니다.\n"
        "원본 이미지 복사본은 originals에 있습니다. 모든 경로는 구성.json 기준 상대 경로입니다.\n",
        encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--font", type=Path, default=Path(__file__).parents[1] / "assets/fonts/Pretendard-Regular.otf")
    args = parser.parse_args()
    print(json.dumps(build(args.config, args.destination, args.font), ensure_ascii=False, indent=2))
