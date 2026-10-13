"""공통 테스트 설정."""
import importlib.util

import pytest

HAS_DEMUCS = importlib.util.find_spec("demucs") is not None


@pytest.fixture(autouse=True)
def _no_demucs_fallback(request, monkeypatch):
    """demucs(requirements-align.txt)가 없는 환경(새로 받은 저장소·CI)에서는 보컬 분리를 원곡 복사로 대신한다.
    가짜 정렬기를 쓰는 흐름 테스트가 무거운 모델 없이 돌게 하려는 것. 실제 모델 테스트(slow)는 그대로 둔다."""
    if HAS_DEMUCS or request.node.get_closest_marker("slow"):
        return
    from shortsmaker import lyrics_align

    def copy_as_vocals(music, work_dir):
        work_dir.mkdir(parents=True, exist_ok=True)
        out = work_dir / f"vocals_{music.stem}.wav"
        lyrics_align.run_ffmpeg(["-i", str(music), "-ac", "1", "-ar", "44100", str(out)])
        return out

    monkeypatch.setattr(lyrics_align, "separate_vocals", copy_as_vocals)
