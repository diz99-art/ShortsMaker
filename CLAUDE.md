# ShortsMaker — 유튜브 쇼츠 자동 제작 프로그램

## 목표
'야구한장', '정보 TOP 5' 채널용 TOP 5 쇼츠를 자동으로 만든다.
제목·5개 항목·이미지·음악·가사 타이밍을 입력하면 1080×1920 MP4(기본 50초)를 출력한다.
지금 CapCut에서 이미지·가사를 손으로 배치하던 작업을 대체하는 것이 1차 목표다.

## 개발 범위
- 1차(지금): TOP 5 영상 생성, 길이 조절, 이미지·제목·자막 자동 배치, 음악 삽입, 구간별 가사, MP4 저장
- 2차(나중): AI 주제·대본 생성, 자료 조사·검증 보조, 가사 타이밍 자동 맞추기(로컬 모델 — `shortsmaker/lyrics_align.py`, 진행 중)
- 3차(나중): 유튜브 업로드
- 노래로 자동 제작(진행 중, 2026-10-10 사용자 승인): 곡 → 가사 맞추기 → 순위 표 추출(사용자 확인·수정 필수) → 위키미디어 공용 사진 검색(없으면 사용자 이미지) → AI 캐리커처(비용 표시 후 사용자 승인 필수) → 카드 합성(구단 로고 금지, 팀 색·이름만) → yagu_v2 렌더 + CapCut. API 키 없이 Mock 모드로 전체 흐름이 돌아야 한다.
- 1차에서는 2·3차 기능을 만들지 않는다. 단, 나중에 붙이기 쉽게 모듈을 분리한다.

## 기술 스택
- Python 3.11+, 가상환경 `.venv`
- Streamlit: 입력 화면, 미리보기, 제작 버튼
- Pillow: 장면 이미지·한글 텍스트 합성
- FFmpeg / ffprobe (Homebrew 설치, `subprocess`로 호출): 효과·음악·MP4 출력
- pydantic: 프로젝트 JSON 스키마 검증
- pytest: 테스트
- 외부 유료 API 사용 금지 (1차). 예외: 캐리커처 생성(OpenAI 이미지 편집)만, 예상 비용을 보여주고 사용자가 승인한 요청에 한해 허용

## 폴더 구조
```
ShortsMaker/
├── app.py                    # Streamlit 진입점
├── shortsmaker/
│   ├── models.py             # pydantic 스키마 (Project, Item, LyricLine, Template)
│   ├── timeline.py           # 장면별 시작/끝 시간 계산
│   ├── scenes.py             # Pillow로 장면 PNG 레이어 생성
│   ├── lyrics.py             # 수동 입력·LRC 파싱 → 자막 타이밍
│   ├── ffmpeg_builder.py     # FFmpeg 명령 조립·실행
│   ├── validate.py           # 렌더 전후 검증
│   └── cli.py                # `python -m shortsmaker.cli render projects/x/project.json`
├── templates/
│   ├── yagu/                 # 야구한장: template.json, background.png 등
│   └── info_top5/            # 정보 TOP 5
├── assets/fonts/             # 한글 폰트 (Pretendard 또는 Noto Sans KR, 사용자가 직접 넣음)
├── projects/<slug>/          # project.json + 이 영상에 쓰는 이미지·음악
├── output/                   # 완성 MP4
├── samples/                  # 테스트용 샘플 프로젝트 (자동 생성한 placeholder 이미지, 무음/사인파 오디오)
└── tests/
```

## 프로젝트 JSON (예시)
```json
{
  "channel": "yagu",
  "title": "KBO 역대 FA 계약 TOP 5",
  "subtitle": "총액 기준",
  "duration": 50,
  "source_note": "출처: KBO 공식 기록 (2026.10 기준)",
  "items": [
    {"rank": 5, "name": "선수명", "value": "80억", "description": "한 줄 설명", "image": "img5.png"}
  ],
  "music": "music.mp3",
  "music_start": 0.0,
  "lyrics": [
    {"start": 3.0, "end": 6.5, "text": "가사 한 줄"}
  ],
  "lyrics_lrc": null,
  "rights_confirmed": false
}
```
- items는 정확히 5개, rank 5→1 순서로 보여준다.
- lyrics는 직접 입력한 구간 또는 LRC 파일 중 하나를 쓴다. 박자 자동 분석은 하지 않는다.
- 2차 가사 자동 맞추기는 demucs(보컬 분리) + stable-ts(faster-whisper) 강제 정렬로 lyrics/LRC를 채운다.
  무거운 패키지는 `requirements-align.txt`로 분리하고, 없어도 1차 기능은 그대로 동작해야 한다.

## 영상 구성 (기본 50초, duration에 비례해 자동 조절)
| 구간 | 기본 길이 |
|---|---|
| 인트로 (제목) | 3초 |
| 5위 ~ 2위 | 각 8초 |
| 1위 | 10초 |
| 아웃트로 (출처·구독 문구) | 5초 |

- 장면 효과: 캐리커처 천천히 줌인(zoompan 또는 scale 애니메이션), 장면 사이 fade/xfade 0.4초, 순위 숫자 강조
- 음악: music_start부터 잘라 duration에 맞추고 끝 1.5초 afade out
- 가사 자막: 화면 하단 고정 영역, 시간 구간에만 표시. 기본은 ASS 자막(libass, 폰트 디렉토리 지정), libass가 없으면 Pillow로 만든 PNG를 시간 구간 overlay로 대체
- 출력: 1080×1920, 30fps, H.264 yuv420p, AAC 192k, `-movflags +faststart`

## 템플릿 (templates/<channel>/template.json)
- 배경 이미지, 색상, 폰트 크기, 제목·순위·이름·값·설명·캐리커처·가사 영역 좌표(박스)를 정의한다.
- 코드에 좌표를 하드코딩하지 않는다. 채널 추가 = 템플릿 폴더 추가로 끝나야 한다.
- 템플릿 이미지가 없으면 단색/그라데이션 배경으로 대체해 실행은 되게 한다.

## Streamlit 화면
1. 채널 선택, 제목·부제 입력
2. 5위~1위 입력 카드 (이름, 값, 설명, 이미지 업로드)
3. 음악 업로드, 시작 위치, 영상 길이(30~60초)
4. 가사: 표 형태 수동 입력 또는 LRC 업로드
5. 출처 입력 + "자료 정확성과 이미지·음악 사용 권리를 확인했습니다" 체크 → 체크해야 제작 버튼 활성화
6. 썸네일 미리보기(각 장면 PNG), 제작 버튼, 진행 상황, 완성 영상 재생·다운로드
7. 입력 내용은 projects/<slug>/project.json으로 저장·불러오기

## 출력 방식 2가지
1. **MP4 바로 완성** (FFmpeg) — 기본
2. **CapCut 초안(draft) 생성** (pyCapCut) — CapCut에서 열어 마무리 편집용
   - 비공식 라이브러리이므로 `shortsmaker/capcut_export.py`로 분리하고, 실패해도 MP4 기능에는 영향이 없게 한다.
   - 템플릿 방식 우선: 사용자가 CapCut에서 직접 만든 '야구한장 템플릿' 초안을 복제한 뒤 이미지·텍스트·음악만 교체한다.
   - 템플릿을 못 읽으면(새 버전 CapCut의 암호화된 draft 등) 빈 초안에 트랙을 새로 만들어 이미지·텍스트·음악·가사를 배치한다.
   - Mac CapCut 초안 폴더: `~/Movies/CapCut/User Data/Projects/com.lveditor.draft/` (설정에서 변경 가능하게)
   - 시간 단위는 마이크로초. 장면·가사 타이밍은 timeline.py 결과를 그대로 재사용한다.
   - 먼저 작은 테스트 초안을 만들어 CapCut에서 실제로 열리는지 확인한 뒤 본 기능을 만든다.
   - Streamlit 화면에 "MP4로 완성" / "CapCut 초안으로 보내기" 버튼을 각각 둔다.

## 검증 규칙
- 렌더 전: JSON 스키마, 파일 존재, 가사 구간 겹침·영상 길이 초과, 한글 폰트 존재
- 텍스트가 박스를 넘치면 글자 크기를 자동으로 줄이고, 최소 크기에서도 넘치면 경고
- 렌더 후: ffprobe로 해상도 1080×1920, 길이 ±0.5초, 오디오 스트림 존재 확인
- FFmpeg 실패 시 실행한 명령과 stderr 마지막 부분을 화면에 보여준다

## 작업 원칙
- 큰 작업은 먼저 계획을 보여주고 승인받은 뒤 진행한다.
- 단계마다 samples/ 프로젝트로 실제 렌더링해서 결과를 확인한다.
- 사용자에게 설명할 때는 한국어로 한다.
- 완료 후 README.md에 설치·실행 방법을 정리한다.
