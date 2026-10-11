"""효과음 — 전환 '휙', 순위 공개 '쿵', 후킹 '쾅', 1위 '함성'.

휙·쿵·쾅은 numpy 로 직접 합성한다 (자체 제작이라 권리 문제가 없다).
함성은 위키미디어 공용에서 퍼블릭 도메인·CC0 로 확인한 녹음만 쓰고, 출처는 assets/sfx/credits.json 에 둔다.
영상마다 쓴 효과음의 출처는 credits.txt 에 적는다. 음악보다 작게 섞는다 (MIX_GAIN).

세트(variation.sfx): a / b 는 소리 결이 다른 두 벌, off 는 효과음 없음.
"""
from __future__ import annotations

import json
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .ffmpeg_builder import ffmpeg_bin

SFX_DIR = Path(__file__).resolve().parent.parent / "assets" / "sfx"
CREDITS_FILE = SFX_DIR / "credits.json"
RATE = 44100
SETS = ("a", "b")
OK_LICENSES = ("자체 제작", "Public domain", "CC0 1.0")  # 수익화용 렌더에 쓸 수 있는 라이선스
# 음악 대비 크기 (효과음 파일은 최고 -1 dBFS 로 맞춰 두고 이 배율로 섞는다)
MIX_GAIN = {"whoosh": 0.22, "thud": 0.35, "slam": 0.4, "cheer": 0.3}
CHEER_LEN = 3.2  # 1위 함성 길이(초)


class SfxError(RuntimeError):
    pass


@dataclass(frozen=True)
class Cue:
    at: float   # 영상 기준 초
    kind: str   # whoosh | thud | slam | cheer


# ---------------------------------------------------------------- 합성 (자체 제작)

def _env(n: int, attack: float, decay: float) -> np.ndarray:
    t = np.arange(n) / RATE
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    return a * np.exp(-np.maximum(t - attack, 0) / decay)


def _norm(x: np.ndarray, peak: float = 0.89) -> np.ndarray:
    m = float(np.max(np.abs(x))) or 1.0
    return x / m * peak


def _lowpass(x: np.ndarray, cutoff: np.ndarray | float) -> np.ndarray:
    """한 극 저역 통과 (차단 주파수가 시간에 따라 바뀔 수 있음)."""
    c = np.broadcast_to(np.asarray(cutoff, dtype=np.float64), x.shape)
    alpha = 1 - np.exp(-2 * np.pi * c / RATE)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):
        acc += alpha[i] * (x[i] - acc)
        y[i] = acc
    return y


def synth_whoosh(variant: str = "a", seed: int = 7) -> np.ndarray:
    """바람 가르는 '휙': 걸러 낸 잡음의 차단 주파수를 올렸다 내린다."""
    dur = 0.42 if variant == "a" else 0.32
    n = int(dur * RATE)
    rnd = np.random.default_rng(seed)
    noise = rnd.standard_normal(n)
    t = np.linspace(0, 1, n)
    sweep = (300 + 5200 * np.sin(np.pi * t) ** 2) if variant == "a" else (600 + 7000 * t ** 0.6 * (1 - t) * 4)
    x = _lowpass(noise, sweep) - _lowpass(noise, sweep * 0.25)  # 띠 통과
    amp = np.sin(np.pi * t) ** (1.6 if variant == "a" else 1.1)
    return _norm(x * amp)


def synth_thud(variant: str = "a") -> np.ndarray:
    """카드가 떨어지는 '쿵': 아래로 내려가는 저음 + 짧은 타격 잡음."""
    dur = 0.55
    n = int(dur * RATE)
    t = np.arange(n) / RATE
    f0, f1 = (95, 45) if variant == "a" else (130, 60)
    freq = f1 + (f0 - f1) * np.exp(-t / 0.08)
    body = np.sin(2 * np.pi * np.cumsum(freq) / RATE) * _env(n, 0.002, 0.16)
    click = _lowpass(np.random.default_rng(3).standard_normal(n), 2500) * _env(n, 0.0005, 0.012)
    return _norm(body + 0.6 * click)


def synth_slam(variant: str = "a") -> np.ndarray:
    """후킹 문구 '쾅': 깊은 쿵 + 금속성 울림 + 잡음 꼬리."""
    dur = 1.1
    n = int(dur * RATE)
    t = np.arange(n) / RATE
    freq = 40 + 110 * np.exp(-t / 0.06)
    boom = np.sin(2 * np.pi * np.cumsum(freq) / RATE) * _env(n, 0.002, 0.35)
    ring_f = 420 if variant == "a" else 610
    ring = (np.sin(2 * np.pi * ring_f * t) + 0.5 * np.sin(2 * np.pi * ring_f * 2.76 * t)) * _env(n, 0.001, 0.12)
    tail = _lowpass(np.random.default_rng(11).standard_normal(n), 1800) * _env(n, 0.001, 0.25)
    return _norm(boom + 0.25 * ring + 0.35 * tail)


def write_wav(path: Path, x: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(data.tobytes())
    return path


def build_synth(folder: Path = SFX_DIR) -> list[Path]:
    """합성 효과음 파일을 (다시) 만든다. 같은 입력이면 같은 소리."""
    out = []
    for v in SETS:
        out.append(write_wav(folder / f"whoosh_{v}.wav", synth_whoosh(v)))
        out.append(write_wav(folder / f"thud_{v}.wav", synth_thud(v)))
        out.append(write_wav(folder / f"slam_{v}.wav", synth_slam(v)))
    return out


def trim_cheer(src: Path, dst: Path, *, start: float = 0.0, length: float = CHEER_LEN) -> Path:
    """내려받은 함성 녹음에서 앞부분을 잘라 길이를 맞추고 끝을 줄인다 (모노 44.1kHz, 최고 -1 dBFS)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start), "-t", str(length),
           "-i", str(src), "-vn", "-ac", "1", "-ar", str(RATE),
           "-af", f"afade=t=in:d=0.05,afade=t=out:st={length - 0.8}:d=0.8,loudnorm=I=-16:TP=-1",
           str(dst)]
    subprocess.run(cmd, check=True)
    return dst


# ---------------------------------------------------------------- 출처

def load_credits(folder: Path = SFX_DIR) -> dict[str, dict]:
    p = folder / "credits.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def sfx_file(kind: str, variant: str, folder: Path = SFX_DIR) -> Path:
    return folder / f"{kind}_{variant}.wav"


def used_files(cues: list[Cue], variant: str, folder: Path = SFX_DIR) -> list[str]:
    return sorted({sfx_file(c.kind, variant, folder).name for c in cues})


def problems(cues: list[Cue], variant: str, folder: Path = SFX_DIR) -> list[str]:
    """쓸 효과음 중 파일이 없거나 출처·라이선스가 확인되지 않은 것."""
    credits = load_credits(folder)
    out = []
    for name in used_files(cues, variant, folder):
        if not (folder / name).exists():
            out.append(f"효과음 파일이 없습니다: {name}")
            continue
        c = credits.get(name)
        if not c:
            out.append(f"효과음 출처 기록이 없습니다: {name}")
        elif c.get("license") not in OK_LICENSES:
            out.append(f"효과음 라이선스를 확인할 수 없습니다: {name} ({c.get('license')})")
    return out


def credits_lines(cues: list[Cue], variant: str, folder: Path = SFX_DIR) -> list[str]:
    credits = load_credits(folder)
    lines = []
    for name in used_files(cues, variant, folder):
        c = credits.get(name, {})
        if c.get("license") == "자체 제작":
            lines.append(f"{c.get('label', name)}: 자체 제작 (코드로 합성)")
        else:
            lines.append(f"{c.get('label', name)}: {c.get('title', name)} / 저작자 {c.get('author', '미상')} / "
                         f"{c.get('license', '?')} / {c.get('source', '')}".rstrip(" /"))
    return lines


# ---------------------------------------------------------------- 섞기

def mix_filter(cues: list[Cue], variant: str, first_input: int, folder: Path = SFX_DIR
               ) -> tuple[list[str], str, list[str]]:
    """(추가 ffmpeg 입력 인자, 효과음을 [sfx] 로 묶는 filter_complex 조각, 라벨 목록). 큐가 없으면 ([], '', [])."""
    if not cues:
        return [], "", []
    args, parts, labels = [], [], []
    for k, c in enumerate(cues):
        args += ["-i", str(sfx_file(c.kind, variant, folder))]
        lab = f"sfx{k}"
        ms = max(0, round(c.at * 1000))
        parts.append(f"[{first_input + k}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
                     f"volume={MIX_GAIN[c.kind]},adelay={ms}|{ms}[{lab}]")
        labels.append(lab)
    parts.append("".join(f"[{l}]" for l in labels) + f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0[sfx]")
    return args, ";".join(parts), labels
