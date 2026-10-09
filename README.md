# ShortsMaker

macOS용 Python 쇼츠 자동 편집 프로젝트. 현재는 **1단계 CapCut 호환성 검증만** 진행했다.
Streamlit 화면, 50초 영상 제작, 채널별 편집 기능은 아직 구현하지 않았다.

## 검증 결론

2026-10-09, macOS 26.3.1 / Python 3.12.15 / pycapcut 0.0.3 / CapCut 5.9.1에서
초안 JSON 생성은 성공했다. Mac 파일명·버전 정보를 보정하면 앱에서 3초 타임라인과
한글 자막 트랙이 열리지만, 이미지·MP3가 접근 불가로 표시된다.
**완전히 편집 가능한 CapCut 프로젝트 생성 성공으로 판정하지 않는다.**
자세한 조사·실제 앱 시험 결과는 [검증 보고서](docs/capcut-compatibility.md)를 참고한다.

## 설치 및 시험

Python 3.11 이상, FFmpeg와 ffprobe가 필요하다. 시험 환경은 기존 `.venv`를 사용했다.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/capcut_probe.py output/my-new-probe
.venv/bin/python -m pytest -q
```

시험 도구는 파란색 합성 PNG, 440Hz 사인파 MP3, 한글 텍스트 트랙과 UTF-8 SRT를 생성한다.
기존 출력 경로가 있으면 중단한다. 사용자 입력 파일은 받지 않으며 외부 업로드 기능도 없다.
`probe_summary.json`의 생성 상태는 앱 호환성 성공을 뜻하지 않는다.

## 구조

- `scripts/capcut_probe.py`: 3초 최소 초안 생성
- `scripts/adapt_capcut_probe.py`: Mac 5.9.1용 실험적 파일명·버전·소재 등록 보정
- `tests/`: 생성 데이터, 한글 경로, 덮어쓰기 거부, 보정 시 원본 보존 검증
- `docs/`: 조사와 실제 시험 결과
- `output/`: 로컬 생성물; Git 제외
- `shortsmaker/`, `projects/`, `templates/`, `samples/`: 후속 개발용 기존 디렉터리

실험적 보정 도구는 완성된 내보내기 기능이 아니다. 다른 CapCut 버전에 사용하지 않는다.
다음과 같이 **새 경로에만** 보정본을 만들며 CapCut 폴더 설치는 별도 단계다.

```sh
.venv/bin/python scripts/adapt_capcut_probe.py \
  output/my-new-probe/ShortsMaker_호환성검증 \
  output/my-new-adapted \
  "$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft/ShortsMaker_new_probe"
```

스크립트는 앱 저장소에 자동 설치하거나 기존 사용자 프로젝트를 변경하지 않는다.
시험 초안은 삭제하지 않고 남겨 두었다. 4개의 `ShortsMaker_probe_20261009_*` 항목은 합성 테스트 전용이다.
