"""공통 테스트 설정."""
import importlib.util
import os

import pytest

# SHORTSMAKER_TEST_FAKE_NO_DEMUCS=1: demucs 가 깔린 컴퓨터에서 CI(미설치) 상황을 흉내 낼 때
HAS_DEMUCS = importlib.util.find_spec("demucs") is not None and not os.environ.get("SHORTSMAKER_TEST_FAKE_NO_DEMUCS")


@pytest.fixture(autouse=True)
def _no_demucs_fallback(request, monkeypatch):
    """demucs(requirements-align.txt)가 없는 환경(새로 받은 저장소·CI)에서는 보컬 분리를 원곡 복사로 대신한다.
    가짜 정렬기를 쓰는 흐름 테스트(렌더까지 가는 slow 테스트 포함)가 무거운 모델 없이 돌게 하려는 것.
    실제 모델 테스트는 stable_whisper 가 없으면 스스로 건너뛰고, demucs 가 있으면 이 대체는 쓰지 않는다."""
    if HAS_DEMUCS:
        return
    from shortsmaker import lyrics_align

    def copy_as_vocals(music, work_dir):
        work_dir.mkdir(parents=True, exist_ok=True)
        out = work_dir / f"vocals_{music.stem}.wav"
        lyrics_align.run_ffmpeg(["-i", str(music), "-ac", "1", "-ar", "44100", str(out)])
        return out

    monkeypatch.setattr(lyrics_align, "separate_vocals", copy_as_vocals)
