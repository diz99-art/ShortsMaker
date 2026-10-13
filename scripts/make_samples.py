"""테스트용 샘플 프로젝트 생성.

samples/yagu_demo  : 야구한장 템플릿, 50초, 가사 직접 입력
samples/info_demo  : 정보 TOP 5 템플릿, 40초, LRC 가사

이미지는 Pillow로 그린 placeholder 캐리커처, 음악은 직접 합성한 화음+비트(저작권 없음).
실행: .venv/bin/python scripts/make_samples.py
"""
from __future__ import annotations

import json
import math
import struct
import subprocess
import sys
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
FONT = ROOT / "assets" / "fonts" / "Pretendard-Bold.otf"

PALETTE = ["#E85D5D", "#F2A541", "#4DB37E", "#4A90D9", "#9B6DD6"]


def placeholder_face(path: Path, label: str, color: str, size: int = 800, variant: int = 0) -> None:
    """단순한 캐리커처 얼굴 placeholder."""
    img = Image.new("RGB", (size, size), color)
    d = ImageDraw.Draw(img)
    # 배경 줄무늬
    for i in range(0, size, 60):
        d.line([(i, 0), (0, i)], fill=_shade(color, 0.9), width=18)
        d.line([(size, i), (i, size)], fill=_shade(color, 0.9), width=18)
    cx, cy, r = size // 2, int(size * 0.47), int(size * 0.30)
    # 머리
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill="#F6D3B3", outline="#3a2a20", width=8)
    # 모자(야구모자 느낌)
    d.chord([cx - r - 6, cy - r - 30, cx + r + 6, cy + r // 2], 180, 360,
            fill=_shade(color, 0.6), outline="#3a2a20", width=8)
    d.rectangle([cx - 10, cy - r // 4 - 4, cx + r + 70, cy - r // 4 + 18], fill=_shade(color, 0.5))
    # 눈
    eye_dy = r // 5
    for ex in (cx - r // 2.6, cx + r // 2.6):
        d.ellipse([ex - 22, cy + eye_dy - 26, ex + 22, cy + eye_dy + 26], fill="white", outline="#3a2a20", width=5)
        d.ellipse([ex - 10 + variant * 3, cy + eye_dy - 8, ex + 10 + variant * 3, cy + eye_dy + 12], fill="#222")
    # 입
    d.arc([cx - r // 2.2, cy + r // 3, cx + r // 2.2, cy + r // 1.3], 10, 170, fill="#7a2a2a", width=9)
    # 라벨
    font = ImageFont.truetype(str(FONT), 70)
    d.rounded_rectangle([60, size - 150, size - 60, size - 40], radius=30, fill=(0, 0, 0))
    d.text((size / 2, size - 95), label, font=font, fill="white", anchor="mm")
    img.save(path)


def _shade(hex_color: str, k: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"#{int(r * k):02x}{int(g * k):02x}{int(b * k):02x}"


def synth_music(path: Path, seconds: float, bpm: int = 100) -> None:
    """화음 진행(2박마다 변경) + 킥/하이햇 비트를 합성해 mp3로 저장."""
    sr = 22050
    chords = [  # C - G - Am - F
        (261.63, 329.63, 392.00),
        (196.00, 246.94, 293.66),
        (220.00, 261.63, 329.63),
        (174.61, 220.00, 261.63),
    ]
    beat = 60 / bpm
    n = int(seconds * sr)
    frames = bytearray()
    for i in range(n):
        t = i / sr
        chord = chords[int(t / (beat * 4)) % len(chords)]
        tone = sum(math.sin(2 * math.pi * f * t) for f in chord) / len(chord)
        tone *= 0.22 * (0.85 + 0.15 * math.sin(2 * math.pi * 0.5 * t))
        bt = t % beat
        kick = math.sin(2 * math.pi * (55 + 90 * math.exp(-bt * 30)) * bt) * math.exp(-bt * 12) * 0.5
        ht = (t + beat / 2) % beat
        hat = (((i * 1103515245 + 12345) >> 8) % 2000 / 1000 - 1) * math.exp(-ht * 60) * 0.08
        s = max(-1.0, min(1.0, tone + kick + hat))
        frames += struct.pack("<h", int(s * 32000))
    wav = path.with_suffix(".wav")
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-ac", "2", "-ar", "44100",
         "-b:a", "192k", str(path)],
        check=True,
    )
    wav.unlink()


def make_yagu_demo() -> None:
    d = SAMPLES / "yagu_demo"
    d.mkdir(parents=True, exist_ok=True)
    names = {5: "테스트 선수 마", 4: "테스트 선수 라", 3: "테스트 선수 다", 2: "테스트 선수 나", 1: "테스트 선수 가"}
    values = {5: "80억", 4: "90억", 3: "100억", 2: "115억", 1: "152억"}
    descs = {
        5: "샘플 설명 문구입니다",
        4: "4년 계약 · 옵션 포함 총액",
        3: "아주 긴 설명이 들어가면 글자 크기가 자동으로 줄어드는지 확인하기 위한 샘플 문장입니다",
        2: "구단 역사상 최고 대우",
        1: "역대 최고액 (샘플 데이터)",
    }
    items = []
    for rank in (5, 4, 3, 2, 1):
        fn = f"img{rank}.png"
        placeholder_face(d / fn, f"샘플 {rank}", PALETTE[rank - 1], variant=rank)
        items.append({"rank": rank, "name": names[rank], "value": values[rank],
                      "description": descs[rank], "image": fn})
    synth_music(d / "music.mp3", 56)
    lyrics = [
        {"start": 0.5, "end": 2.8, "text": "오늘의 TOP 5 시작합니다"},
        {"start": 4.0, "end": 8.0, "text": "첫 번째 가사 줄 — 샘플 테스트"},
        {"start": 12.0, "end": 16.5, "text": "두 번째 가사 줄"},
        {"start": 20.0, "end": 24.0, "text": "세 번째 가사는 조금 더 길게 써서 두 줄로 나뉘는지 확인합니다"},
        {"start": 28.0, "end": 32.0, "text": "네 번째 가사 줄"},
        {"start": 36.0, "end": 40.0, "text": "드디어 1위 발표!"},
        {"start": 41.0, "end": 44.5, "text": "다섯 번째 가사 줄"},
        {"start": 45.5, "end": 49.0, "text": "끝까지 봐주셔서 감사합니다"},
    ]
    project = {
        "channel": "yagu",
        "title": "KBO 역대 FA 계약 TOP 5 (샘플)",
        "subtitle": "총액 기준",
        "duration": 50,
        "source_note": "출처: 샘플 데이터 — 실제 기록 아님 (2026.10 기준)",
        "items": items,
        "music": "music.mp3",
        "music_start": 2.0,
        "lyrics": lyrics,
        "lyrics_lrc": None,
        "rights_confirmed": True,
    }
    (d / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")


def make_info_demo() -> None:
    d = SAMPLES / "info_demo"
    d.mkdir(parents=True, exist_ok=True)
    names = {5: "다섯 번째 항목", 4: "네 번째 항목", 3: "세 번째 항목", 2: "두 번째 항목", 1: "첫 번째 항목"}
    items = []
    for rank in (5, 4, 3, 2, 1):
        fn = f"item{rank}.png"
        placeholder_face(d / fn, f"항목 {rank}", PALETTE[(rank + 2) % 5], variant=-rank)
        items.append({"rank": rank, "name": names[rank], "value": f"{rank * 17}%",
                      "description": "정보 TOP 5 샘플 설명", "image": fn})
    synth_music(d / "music.mp3", 45, bpm=120)
    (d / "lyrics.lrc").write_text(
        "[ti:샘플]\n"
        "[00:00.50]LRC 가사 첫 줄\n"
        "[00:04.00]두 번째 줄\n"
        "[00:09.00]\n"
        "[00:12.00]세 번째 줄은 빈 줄 다음에 나와요\n"
        "[00:18.50]네 번째 줄\n"
        "[00:25.00]다섯 번째 줄\n"
        "[00:31.00]마지막 줄\n",
        encoding="utf-8",
    )
    project = {
        "channel": "info_top5",
        "title": "알아두면 쓸모있는 정보 TOP 5 (샘플)",
        "subtitle": "40초 길이 테스트",
        "duration": 40,
        "source_note": "출처: 샘플 데이터",
        "items": items,
        "music": "music.mp3",
        "music_start": 0.0,
        "lyrics": [],
        "lyrics_lrc": "lyrics.lrc",
        "rights_confirmed": True,
    }
    (d / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    if not FONT.exists():
        sys.exit(f"폰트가 없습니다: {FONT}")
    make_yagu_demo()
    make_info_demo()
    print("샘플 생성 완료:", ", ".join(p.name for p in sorted(SAMPLES.iterdir()) if p.is_dir()))
