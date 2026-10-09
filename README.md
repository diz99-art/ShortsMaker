# ShortsMaker

'야구한장', '정보 TOP 5' 채널용 **TOP 5 유튜브 쇼츠 자동 제작** 프로그램 (1차).
제목·5개 항목·이미지·음악·가사 타이밍을 입력하면 1080×1920 / 30fps MP4(기본 50초)를 만든다.

## 설치 (macOS)

```sh
brew install ffmpeg                      # ffmpeg·ffprobe
python3.12 -m venv .venv                 # Python 3.11 이상
.venv/bin/pip install -r requirements.txt
```

- 한글 폰트: `assets/fonts/`에 Pretendard(OFL, `Pretendard-LICENSE.txt`)가 들어 있다.
  다른 폰트를 쓰려면 파일을 넣고 `templates/<채널>/template.json`의 `fonts`를 바꾼다.
- 테스트용 샘플 생성 (placeholder 캐리커처 + 직접 합성한 음악):
  ```sh
  .venv/bin/python scripts/make_samples.py   # samples/yagu_demo (50초, 직접 입력 가사), samples/info_demo (40초, LRC)
  ```

## 실행

### 화면 (Streamlit)

```sh
.venv/bin/streamlit run app.py
```

1. 사이드바 **불러오기**로 `samples/…`나 `projects/…`를 열거나 새로 입력
2. 채널·제목·부제 → 5위~1위 카드(이름·값·설명·이미지) → 음악·시작 위치·길이(30~60초)
3. 가사: 표에 직접 입력(시작/끝/가사) 또는 LRC 업로드
4. 출처 입력 + **권리 확인 체크** → 체크해야 제작 버튼이 켜진다
5. **장면 미리보기** → **MP4로 완성**(진행률·재생·다운로드) / **CapCut 초안으로 보내기**

입력은 `projects/<저장 폴더 이름>/project.json`과 이미지·음악 파일로 저장된다. 완성 영상은 `output/<폴더>.mp4`.

### 명령줄

```sh
.venv/bin/python -m shortsmaker.cli check    samples/yagu_demo/project.json  # 렌더 전 검증
.venv/bin/python -m shortsmaker.cli timeline samples/yagu_demo/project.json  # 장면 시간표
.venv/bin/python -m shortsmaker.cli scenes   samples/yagu_demo/project.json  # 장면 PNG + 미리보기 시트
.venv/bin/python -m shortsmaker.cli render   samples/yagu_demo/project.json  # MP4 → output/yagu_demo.mp4
.venv/bin/python -m shortsmaker.cli capcut   samples/yagu_demo/project.json  # CapCut 초안 (비공식)
.venv/bin/python -m shortsmaker.cli kit      samples/yagu_demo/project.json  # CapCut 수동 가져오기 재료
```

## 영상 구성

| 구간 | 50초 기준 | 내용 |
|---|---|---|
| 인트로 | 3초 | TOP 5 배지(팝) · 제목 · 부제 |
| 5위~2위 | 각 8초 | 순위 숫자(팝) · 캐리커처(천천히 줌인) · 이름 · 값 · 설명 |
| 1위 | 10초 | 1위 색 강조 |
| 아웃트로 | 5초 | 구독 문구 · 출처 |

- 길이를 바꾸면 모든 구간이 비례해서 늘거나 줄고, 프레임 단위로 맞춘다. 장면 사이 fade 전환은 0.4초.
- 음악은 `music_start`부터 잘라 길이에 맞추고 끝 1.5초 페이드아웃. 음악이 없으면 무음 트랙.
- 가사는 화면 하단 고정 영역에 시간 구간에만 표시. FFmpeg에 libass가 있으면 ASS 자막,
  없으면 Pillow PNG overlay를 쓴다 (현재 Homebrew FFmpeg 9.0.2는 libass 없음 → PNG 방식).
- 출력: H.264 High / yuv420p, AAC 192k, `+faststart`.

## project.json

```json
{
  "channel": "yagu",                 // templates/ 폴더 이름
  "title": "KBO 역대 FA 계약 TOP 5",
  "subtitle": "총액 기준",
  "duration": 50,                    // 30~60초
  "source_note": "출처: KBO 공식 기록 (2026.10 기준)",
  "items": [ {"rank": 5, "name": "선수명", "value": "80억", "description": "한 줄 설명", "image": "img5.png"} ],
  "music": "music.mp3",
  "music_start": 0.0,
  "lyrics": [ {"start": 3.0, "end": 6.5, "text": "가사 한 줄"} ],
  "lyrics_lrc": null,                // lyrics 와 둘 중 하나만
  "rights_confirmed": false
}
```

items는 정확히 5개(rank 1~5 각 한 번). 파일 경로는 project.json이 있는 폴더 기준.

## 템플릿 (채널 추가)

`templates/<채널>/template.json` 하나로 채널이 추가된다. 코드에는 좌표가 없다.

- `gradient` / `background`: 배경 이미지(템플릿 폴더 기준). 파일이 없으면 그라데이션으로 대체
- `fonts`: 키 → `assets/fonts` 파일명
- `intro` / `rank` / `outro` / `lyrics`: 각 요소의 박스(`x, y, w, h`)와 글자 크기(`size` → `min_size`까지 자동 축소),
  색, 정렬, 최대 줄 수, 외곽선. `rank.rank_format`(예: `{rank}위`, `TOP {rank}`), `rank_one_color`
- `effects`: `transition`(전환 초), `zoom_to`(줌 최종 배율), `rank_pop`(팝 시간), `music_fade_out`

## 검증

- 렌더 전(`check`·화면·`render` 시작 시): 스키마, 템플릿·폰트·이미지·음악·LRC 파일 존재, 음악 시작 위치,
  가사 겹침·길이 초과, 글자가 최소 크기에서도 넘치는지(경고), 권리 확인
- 렌더 후: ffprobe로 1080×1920, 길이 ±0.5초, 오디오 스트림 존재 확인
- FFmpeg 실패 시 실행한 명령과 stderr 끝부분을 보여준다

```sh
.venv/bin/python -m pytest -q              # 전체 (실제 렌더 포함, 약 30초)
.venv/bin/python -m pytest -q -m "not slow" # 빠른 테스트만
```

## CapCut 초안 (비공식, 실험적)

`shortsmaker/capcut_export.py`로 분리되어 있어 실패해도 MP4 기능에는 영향이 없다.

- 초안 폴더: `~/Movies/CapCut/User Data/Projects/com.lveditor.draft/` (화면 사이드바 ⚙️ 설정 또는
  환경변수 `SHORTSMAKER_CAPCUT_DIR`로 변경). 이름은 `ShortsMaker_<폴더>_<시각>`, 기존 초안은 덮어쓰지 않는다.
- 트랙: 장면(완성 장면 이미지) / 음악(시작 위치부터, 페이드아웃) / 가사(편집 가능한 텍스트)
- Mac CapCut 5.9.1 보정: `draft_info.json` 작성, 설치 앱 버전 값 반영, 소재 등록 —
  [호환성 시험 기록](docs/capcut-compatibility.md)
- CapCut은 샌드박스 앱이라 `~/Movies` 밖의 파일을 열지 못한다. 그래서 이미지·음악을 **초안 폴더 안 `media/`로 복사**한다.
- 초안이 열리지 않으면 `kit` 명령(화면에서는 실패 시 자동)으로 `output/<폴더>_capcut_kit/`에
  장면 PNG(시작 시각이 파일 이름)·자른 음악·가사 SRT를 만들어 CapCut에 직접 가져온다.

## 구조

```
app.py                     Streamlit 화면
shortsmaker/
  models.py                pydantic 스키마 (Project, Item, LyricLine, Template)
  timeline.py              장면 시작/끝 계산
  scenes.py                Pillow 장면 레이어 (배경·텍스트 / 순위 팝 / 캐리커처 + 마스크)
  lyrics.py                LRC 파싱·검사, 가사 PNG / ASS
  ffmpeg_builder.py        FFmpeg 명령 조립·실행 (장면 클립 → xfade·가사·음악 합성)
  pipeline.py              검증 → 장면 → 클립 → 최종 MP4 → 결과 검증
  validate.py              렌더 전·후 검증
  capcut_export.py         CapCut 초안 / 수동 가져오기 키트
  cli.py                   명령줄
templates/{yagu,info_top5} 채널 템플릿
scripts/                   샘플 생성, 렌더 결과 확인(probe.py), CapCut 호환성 프로브
build/ output/             중간 파일·결과물 (Git 제외)
```

## CapCut 시험 기록 · 편집 패키지 (별도 세션 작업)

- [1단계: Mac CapCut 초안 호환성 시험](docs/capcut-compatibility.md) — pyCapCut 초안의 이미지·음악이
  "Unsupported Media"로 뜬 문제와 샌드박스 원인 분석
- [2단계: CapCut 편집 재료 가져오기 시험](docs/capcut-import-verification.md) — 앱 UI에서 직접 가져온
  PNG·MP3·SRT는 정상 표시·편집됨 (사용자가 고른 파일은 샌드박스 허용 → 위 분석과 일치)
- `shortsmaker/editing_package.py`: 장면 이미지·MP3 복사본·SRT·구성 JSON·미리보기 MP4를 담은 편집 패키지 생성
  (`kit` 명령과 목적이 겹친다. 하나로 합칠지는 다음 단계에서 정한다)

  ```sh
  .venv/bin/python scripts/create_editing_sample.py output/stage2-input-new
  .venv/bin/python -m shortsmaker.editing_package output/stage2-input-new/input.json output/stage2-package-new
  ```
- `scripts/capcut_probe.py`, `scripts/adapt_capcut_probe.py`: 1단계 시험용 최소 초안 생성·보정 (실험용)
