"""가사 타이밍 자동 맞추기 (2차 기능, 로컬 모델 전용).

음악 + 가사 텍스트(한 줄씩) → 줄마다 시작·끝 시간.
  1) (선택) demucs로 보컬만 분리 — 반주가 크면 인식이 흔들리므로 기본 사용
  2) stable-ts 강제 정렬(align): 이미 아는 가사가 '언제' 불렸는지 찾는다 (faster-whisper, CPU int8)
  3) 후처리: 순서·겹침 정리, 못 찾은 줄 보간, 곡 시간 → 영상 시간(music_start 기준)
  4) LRC 저장 (빈 타임태그로 끝 시간 기록 → parse_lrc로 다시 읽으면 같은 구간)

무거운 패키지(stable-ts, demucs, torch)는 requirements-align.txt로 따로 설치한다.
없으면 import 시점이 아니라 실행할 때 설치 안내 오류를 낸다.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .ffmpeg_builder import ProgressFn, run_ffmpeg
from .lyrics import MAX_HOLD
from .models import LyricLine, Project, Word

DEFAULT_MODEL = "large-v3"
MODEL_CHOICES = ["large-v3", "medium", "small"]
LOW_PROB = 0.5        # 줄 평균 단어 확률이 이보다 낮으면 '확인 필요'
MIN_LINE = 0.3        # 이보다 짧게 잡힌 줄도 '확인 필요'
HOLD_GAP = 0.6        # 다음 줄까지 이 간격 이하면 자막을 이어 붙여 깜빡임 방지
ONSET_DB = -40.0      # 보컬 음량이 이 값(dBFS) 아래에서 위로 올라오는 순간을 단어 시작으로 본다
ONSET_HOLD = 3        # 10ms 프레임 몇 개 이상 계속 커야 시작으로 인정
INSTALL_HINT = ".venv/bin/pip install -r requirements-align.txt"

_SECTION_TAG = re.compile(r"^\s*[\[(（【].*[\])）】]\s*$")  # [Verse], (Chorus) 등


class AlignError(RuntimeError):
    pass


@dataclass
class AlignedLine:
    start: float  # 곡 기준 초
    end: float
    text: str
    prob: float | None = None
    found: bool = True
    words: list[tuple[float, float, str]] = field(default_factory=list)  # 곡 기준 (시작, 끝, 단어)

    @property
    def needs_check(self) -> bool:
        return (not self.found or (self.prob is not None and self.prob < LOW_PROB)
                or self.end - self.start < MIN_LINE)


@dataclass
class AlignResult:
    lines: list[LyricLine]                 # 영상 기준, 바로 project.lyrics로 쓸 수 있음
    aligned: list[AlignedLine]             # 곡 기준 원본 결과
    needs_check: list[str] = field(default_factory=list)
    lrc_path: Path | None = None
    seconds: float = 0.0
    used_vocals: bool = False
    model: str = DEFAULT_MODEL


# ---------------------------------------------------------------- 입력 준비

def clean_lyrics_text(text: str) -> list[str]:
    """한 줄 = 자막 한 줄. 빈 줄·[Verse] 같은 섹션 태그·LRC 타임태그 제거."""
    out = []
    for raw in text.splitlines():
        line = re.sub(r"\[\d+:\d{1,2}(?:\.\d{1,3})?\]", "", raw).strip()
        if line and not _SECTION_TAG.match(line):
            out.append(line)
    return out


def embedded_lyrics(music: Path) -> str | None:
    """음악 파일 메타데이터의 가사(Suno 곡은 lyrics 태그에 들어 있음)."""
    if not shutil.which("ffprobe"):
        return None
    proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", str(music)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    tags = json.loads(proc.stdout or "{}").get("format", {}).get("tags", {})
    for key, value in tags.items():
        if key.lower() in ("lyrics", "unsyncedlyrics", "lyrics-eng") and value.strip():
            return value
    return None


def missing_packages(vocals: bool = True) -> list[str]:
    need = {"stable_whisper": "stable-ts", "faster_whisper": "faster-whisper"}
    if vocals:
        need["demucs"] = "demucs"
    return [pkg for mod, pkg in need.items() if importlib.util.find_spec(mod) is None]


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def separate_vocals(music: Path, work_dir: Path) -> Path:
    """demucs(htdemucs)로 보컬만 분리. 같은 음악이면 이전 결과를 재사용."""
    work_dir.mkdir(parents=True, exist_ok=True)
    out = work_dir / f"vocals_{_file_hash(music)}.wav"
    if out.exists():
        return out
    with tempfile.TemporaryDirectory(dir=work_dir) as tmp:
        src = Path(tmp) / "song.wav"
        run_ffmpeg(["-i", str(music), "-ac", "2", "-ar", "44100", str(src)])
        cmd = [sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs", "-o", tmp, str(src)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        produced = Path(tmp) / "htdemucs" / "song" / "vocals.wav"
        if proc.returncode != 0 or not produced.exists():
            tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
            raise AlignError(f"보컬 분리(demucs) 실패\n명령: {' '.join(cmd)}\n--- stderr (끝부분) ---\n{tail}")
        shutil.move(produced, out)
    return out


# ---------------------------------------------------------------- 정렬

_MODELS: dict[str, object] = {}  # Streamlit 재실행 사이에도 모델을 다시 읽지 않도록


def _load_model(size: str) -> Any:  # stable_whisper 모델 (타입 정보 없음)
    if size not in _MODELS:
        import stable_whisper
        _MODELS[size] = stable_whisper.load_faster_whisper(size, device="cpu", compute_type="int8")
    return _MODELS[size]


def _norm(s: str) -> str:
    return re.sub(r"[\W_]+", "", s).lower()


def align_lines(audio: Path, lines: list[str], model_size: str = DEFAULT_MODEL) -> list[AlignedLine]:
    """stable-ts 강제 정렬. 줄 순서대로 AlignedLine (못 찾은 줄은 found=False, 시간은 보간)."""
    missing = missing_packages(vocals=False)
    if missing:
        raise AlignError(f"가사 자동 맞추기 패키지가 없습니다: {', '.join(missing)}\n설치: {INSTALL_HINT}")
    result = _load_model(model_size).align(str(audio), "\n".join(lines), language="ko",
                                           original_split=True, verbose=None)
    segments = [(s.start, s.end, s.text, [w.probability for w in s.words if w.probability is not None],
                 [(w.start, w.end, w.word) for w in s.words if w.end > w.start])
                for s in result.segments]
    return match_segments(lines, segments)


def match_segments(lines: list[str], segments: list[tuple]) -> list[AlignedLine]:
    """정렬 결과 구간을 입력 가사 줄에 순서대로 대응. 대응 안 된 줄은 앞뒤 사이로 보간.

    segments: (시작, 끝, 글자, 단어 확률들[, (단어 시작, 끝, 단어)들])
    """
    out: list[AlignedLine | None] = [None] * len(lines)
    j = 0
    for i, line in enumerate(lines):
        target = _norm(line)
        for k in range(j, len(segments)):
            start, end, text, probs = segments[k][:4]
            words = segments[k][4] if len(segments[k]) > 4 else []
            if _norm(text) == target and end > start:
                out[i] = AlignedLine(round(start, 2), round(end, 2), line,
                                     round(sum(probs) / len(probs), 2) if probs else None,
                                     words=[(round(a, 3), round(b, 3), w.strip()) for a, b, w in words if w.strip()])
                j = k + 1
                break
    return _interpolate(lines, out)


def _interpolate(lines: list[str], found: list[AlignedLine | None]) -> list[AlignedLine]:
    result: list[AlignedLine] = []
    i = 0
    while i < len(lines):
        if found[i] is not None:
            result.append(found[i])  # type: ignore[arg-type]
            i += 1
            continue
        j = i
        while j < len(lines) and found[j] is None:
            j += 1
        lo = result[-1].end if result else 0.0
        hi = found[j].start if j < len(lines) else lo + 3.0 * (j - i)  # type: ignore[union-attr]
        step = max(hi - lo, 0.0) / (j - i)
        for k in range(i, j):
            s = lo + step * (k - i)
            result.append(AlignedLine(round(s, 2), round(s + step, 2), lines[k], None, found=False))
        i = j
    return result


# ---------------------------------------------------------------- 단어 시작 다듬기

def vocal_db(audio: Path) -> list[float]:
    """10ms 마다 보컬 음량(dBFS)."""
    import numpy as np
    proc = subprocess.run(["ffmpeg", "-v", "error", "-i", str(audio), "-ac", "1", "-ar", "16000",
                           "-f", "s16le", "-"], capture_output=True)
    x = np.frombuffer(proc.stdout, np.int16).astype(np.float32) / 32768
    n = len(x) // 160
    rms = np.sqrt((x[: n * 160].reshape(n, 160) ** 2).mean(1))
    return (20 * np.log10(rms + 1e-6)).tolist()


def refine_onsets(aligned: list[AlignedLine], db: list[float], threshold: float = ONSET_DB,
                  hold: int = ONSET_HOLD) -> list[AlignedLine]:
    """강제 정렬은 단어 시작을 앞쪽 무음까지 끌어 잡는 일이 있다 (예: '5위,' 9.14~11.84초, 실제 소리는 11.4초).
    단어 시작~다음 단어 시작 사이에서 보컬이 조용하다가 커지는 첫 순간으로 시작을 옮긴다.
    조용한 구간이 없으면(앞 단어와 이어 부름) 그대로 둔다. 줄 시작도 첫 단어에 맞춘다."""
    def rise(a: float, b: float) -> float | None:
        i0, i1 = int(a * 100), min(int(b * 100), len(db) - hold)
        quiet = False
        for i in range(max(i0, 0), i1):
            if db[i] < threshold:
                quiet = True
            elif quiet and all(v >= threshold for v in db[i:i + hold]):
                return i / 100
        return None

    flat = [(li, wi) for li, a in enumerate(aligned) for wi in range(len(a.words))]
    for k, (li, wi) in enumerate(flat):
        ws, we, w = aligned[li].words[wi]
        nxt = aligned[flat[k + 1][0]].words[flat[k + 1][1]][0] if k + 1 < len(flat) else we
        t = rise(ws, max(nxt, we))
        if t is not None and t > ws:
            aligned[li].words[wi] = (round(t, 3), max(we, round(t + 0.05, 3)), w)
    for a in aligned:
        if a.words and a.found:
            a.words.sort()
            a.start = max(a.start, a.words[0][0])
            a.end = max(a.end, a.start + 0.05)
    return aligned


# ---------------------------------------------------------------- 영상 시간·LRC

def to_video_lyrics(aligned: list[AlignedLine], music_start: float, duration: float) -> list[LyricLine]:
    """곡 시간 → 영상 시간. 영상 밖 줄은 빼고, 겹침을 정리하고, 짧은 틈은 이어 붙인다."""
    spans = []
    for a in sorted(aligned, key=lambda a: a.start):
        s, e = a.start - music_start, a.end - music_start
        if e <= 0 or s >= duration:
            continue
        s = max(s, 0.0)
        spans.append([s, min(e, duration, s + MAX_HOLD), a.text, a.words])
    for cur, nxt in zip(spans, spans[1:]):
        if nxt[0] < cur[1]:
            cur[1] = nxt[0]
        elif nxt[0] - cur[1] <= HOLD_GAP:
            cur[1] = min(nxt[0], cur[0] + MAX_HOLD)
    out = []
    for s, e, text, words in spans:
        s, e = round(s, 2), round(e, 2)
        if e - s >= 0.05:
            ws = []
            for ws_, we_, w in words:  # 곡 시간 → 영상 시간, 줄 구간 안으로
                a, b = max(s, ws_ - music_start), min(e, we_ - music_start)
                if b > a:
                    ws.append(Word(start=round(a, 3), end=round(b, 3), text=w))
            out.append(LyricLine(start=s, end=e, text=text, words=ws))
    return out


def _lrc_time(t: float) -> str:
    cs = round(t * 100)
    m, cs = divmod(cs, 6000)
    return f"[{m:02d}:{cs // 100:02d}.{cs % 100:02d}]"


def lrc_text(lines: list[LyricLine]) -> str:
    """끝 시간은 빈 타임태그로 남겨 parse_lrc 왕복 시 같은 구간이 나오게 한다."""
    rows = []
    ordered = sorted(lines, key=lambda l: l.start)
    for i, l in enumerate(ordered):
        rows.append(_lrc_time(l.start) + l.text.replace("\n", " "))
        nxt = ordered[i + 1].start if i + 1 < len(ordered) else None
        if nxt is None or nxt - l.end > 0.005:
            rows.append(_lrc_time(l.end))
    return "\n".join(rows) + "\n"


def write_lrc(lines: list[LyricLine], path: Path) -> Path:
    path.write_text(lrc_text(lines), encoding="utf-8")
    return path


# ---------------------------------------------------------------- 전체 흐름

Aligner = Callable[[Path, list[str], str], list[AlignedLine]]


def auto_align(project: Project, project_dir: Path, lyrics_text: str | None, *, vocals: bool = True,
               model_size: str = DEFAULT_MODEL, work_dir: Path | None = None,
               progress: ProgressFn | None = None, aligner: Aligner = align_lines,
               lrc_name: str = "lyrics_auto.lrc") -> AlignResult:
    """CLI·화면 공통 진입점. lyrics_text가 비면 음악 파일 내장 가사를 쓴다."""
    report = progress or (lambda f, m: None)
    t0 = time.time()
    if not project.music:
        raise AlignError("음악 파일이 없습니다")
    music = project_dir / project.music
    if not music.exists():
        raise AlignError(f"음악 파일을 찾을 수 없습니다: {music}")
    lines = clean_lyrics_text(lyrics_text or "") or clean_lyrics_text(embedded_lyrics(music) or "")
    if not lines:
        raise AlignError("가사 텍스트가 없습니다. 한 줄씩 입력하거나, 가사가 들어 있는 음악 파일을 쓰세요")
    if aligner is align_lines and (missing := missing_packages(vocals)):
        raise AlignError(f"가사 자동 맞추기 패키지가 없습니다: {', '.join(missing)}\n설치: {INSTALL_HINT}")

    audio = music
    if vocals:
        report(0.05, "보컬 분리 중 (demucs)")
        from .pipeline import work_dir_for
        audio = separate_vocals(music, work_dir or work_dir_for(project_dir))
    report(0.35, f"가사 정렬 중 ({model_size}, 처음엔 모델 다운로드로 오래 걸림)")
    aligned = aligner(audio, lines, model_size)
    if vocals and audio != music:
        aligned = refine_onsets(aligned, vocal_db(audio))
    report(0.9, "결과 정리")
    video = to_video_lyrics(aligned, project.music_start, project.duration)
    lrc = write_lrc(video, project_dir / lrc_name)
    shown = {l.text for l in video}
    needs = [f"{a.text} ({'못 찾음, 보간' if not a.found else f'신뢰도 {a.prob}'})"
             for a in aligned if a.needs_check and a.text in shown]
    report(1.0, "완료")
    return AlignResult(video, aligned, needs, lrc, round(time.time() - t0, 1), vocals, model_size)
