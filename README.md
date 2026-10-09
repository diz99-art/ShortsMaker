# ShortsMaker

macOS용 Python 쇼츠 자동 편집 프로젝트. 현재는 **2단계 CapCut 편집 재료 생성·가져오기 검증**을 진행했다.
Streamlit 화면과 채널별 자동 편집 기능은 아직 구현하지 않았다. 50초 구성 입력 예시는 제공하지만 실제 시험은 3초로 진행했다.

## 2단계: 편집 패키지 생성

CapCut 내부 JSON과 저장소를 건드리지 않는다. 입력 구성에서 장면 시작·종료 시간을 계산하고,
원본 이미지 복사본, 1080×1920 준비 이미지, 원본 MP3 복사본, UTF-8 SRT, 구성 JSON,
FFmpeg 미리보기 MP4와 가져오기 안내서를 새 출력 폴더에 만든다.
미리보기에는 가사를 합성하고, 편집용 SRT는 별도로 유지한다.

```sh
.venv/bin/python scripts/create_editing_sample.py output/stage2-input-new
.venv/bin/python -m shortsmaker.editing_package \
  output/stage2-input-new/input.json output/stage2-package-new
.venv/bin/python -m pytest -q
```

입력 형식은 [50초 구성 예시](samples/editing-input.example.json)를 참고한다.
예시는 스키마 참고용이며 이미지·음악 파일은 함께 제공되지 않는다.
이미지·MP3 경로는 입력 JSON 기준이며, 출력 JSON의 경로는 패키지 루트 기준이다.
인트로 → 5위~1위 → 아웃트로 순서만 허용한다. 가사 시간은 초 단위, 정렬된 순서로 입력한다.
중복·겹침·역전·범위 초과·빈 자막은 오류로 처리한다. 장면 길이는 1/30초 단위다.
PNG/JPG를 원본 비율대로 맞추고 남는 부분은 검게 채운다. EXIF 방향과 투명도도 반영한다.
음악 전체를 FFmpeg로 디코딩해 오류를 검사하며, 짧으면 미리보기 끝부분은 무음으로 채우고
길면 미리보기 길이만큼 사용한다. 복사한 MP3는 자르거나 변경하지 않는다.

현재 합성 3초 샘플만 실험했다. 실제 Suno 음악 청취와 50초 렌더링은 미검증이다.
프레임별 PNG를 생성하는 검증용 구현이므로 긴 영상의 성능 최적화는 후속 범위다.
생성이 중간에 실패하면 부분 출력이 남으며, 재시도는 새로운 폴더를 사용해야 한다.

앱 시험 상태는 [2단계 검증 기록](docs/capcut-import-verification.md)에 별도로 기록한다.
`구성.json`의 `capcut_verified=false`는 자동 생성 시점의 값이다. 앱 시험 결과는 별도 보고서를 근거로 판단한다.
내보내기 시 **공간 동기화 체크를 해제**해 로컬로만 출력해야 한다.

## 1단계 검증 결론 (보존)

2026-10-09, macOS 26.3.1 / Python 3.12.15 / pycapcut 0.0.3 / CapCut 5.9.1에서
초안 JSON 생성은 성공했다. Mac 파일명·버전 정보를 보정하면 앱에서 3초 타임라인과
한글 자막 트랙이 열리지만, 이미지·MP3가 접근 불가로 표시된다.
**완전히 편집 가능한 CapCut 프로젝트 생성 성공으로 판정하지 않는다.**
자세한 조사·실제 앱 시험 결과는 [검증 보고서](docs/capcut-compatibility.md)를 참고한다.

## 설치 및 1단계 시험

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
- `shortsmaker/editing_package.py`: 2단계 편집 패키지 생성 CLI
- `scripts/create_editing_sample.py`: 2단계 합성 샘플 입력 생성
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
