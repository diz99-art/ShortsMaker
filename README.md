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
3. 가사: 표에 직접 입력(시작/끝/가사) 또는 LRC 업로드, 또는 **🎙️ 가사 자동 맞추기**(아래 2차 기능)
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
.venv/bin/python -m shortsmaker.cli align    projects/x/project.json         # 가사 타이밍 자동 맞추기 (2차)
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
- `max_duration`: 영상 최대 길이 (기본 60초, 스키마 상한 90초)

### 카드 모드 템플릿 `yagu_v2`

참고 영상(`reference/한_번만_이겼어도__KBO_역대_최장_연패_TOP_5.mov`)에서 잰 배치·움직임을 그대로 옮긴 템플릿.
측정값과 근거는 `templates/yagu_v2/MEASUREMENTS.md`.

- 흰 배경, 위쪽 '야구한장 )(' 알약 로고 + 파란 2줄 제목(`title` 의 `\n` 으로 줄 나눔) + 검은 구분선, 아래쪽 `@야구한장 | 🔔구독`
- 인트로: 채널 로고(`templates/yagu_v2/logo.png`)가 2배 크기로 시작해 약 4초에 걸쳐 제자리로 줄어듦 (처음 0.3초 모션 블러)
- 순위 이미지는 정보가 다 들어간 완성 카드로 보고 이름·값 글자는 그리지 않는다. 카드만 크게 배치
- 장면 전환: 빈 프레임 1개 → 카드가 2.2배에서 '쾅' 들어와 0.3초 동안 모션 블러, 약 1.4초에 걸쳐 제자리
- 전환 시점: `items[].start` 가 없으면 가사 자동 맞추기 결과에서 "5위", "4위"… 를 부르는 순간(단어 타이밍 안 글자 위치)
- 자막: 구분선 아래, 흰 글씨 + 검은 테두리 + 그림자. 문장을 먼저 배치하고 노래에 맞춰 한 글자씩 나타남(단어 타이밍 안에서
  글자를 나눔, 문장 부호는 앞 글자와 함께). 최대 2줄(안 들어가면 폭을 넓힘). 다음 가사가 시작되면 교체
- 영상 길이 최대 90초 (곡 길이에 맞추려면 `duration` 을 곡 길이와 90 중 작은 값으로)
- 렌더는 장면 클립·xfade 대신 Pillow 로 프레임을 직접 그려 FFmpeg 로 인코딩한다 (`shortsmaker/card_mode.py`). 90초 약 2분
- CapCut 초안: 같은 배치(배경 / 로고·카드 / 제목 / 자막 트랙). 확대는 키프레임으로 옮기지만 모션 블러는 없고,
  타자 효과는 CapCut '타자기' 효과로 근사, 글자 그림자는 pyCapCut 미지원으로 빠짐
- 비교 이미지: `.venv/bin/python scripts/compare_frames.py <참고 영상> <결과 mp4 또는 project.json> <출력.png> [초 또는 참고초:결과초 ...]`

가사 자동 맞추기는 이제 단어 시간(`lyrics[].words`)도 저장하고, 보컬 분리를 쓸 때 단어 시작을 보컬이
조용하다가 커지는 순간으로 다듬는다 (강제 정렬이 앞쪽 무음까지 단어에 붙이는 문제 보정).

## 노래로 자동 제작 (yagu_v2)

`.venv/bin/streamlit run app.py` → 왼쪽 메뉴 **노래로 자동 제작**. 가사가 들어 있는 곡 파일(Suno m4a 등) 하나로 시작한다.
단계마다 앞 단계가 끝나야 다음 버튼이 열리고, 진행 상태는 `projects/<이름>/auto.json` 에 저장돼 '이어서 하기'로 계속할 수 있다.

1. **가사 자동 맞추기**: 곡에 든 가사 → 보컬 분리 + 단어 타이밍. 영상 길이 = 곡 길이(최대 90초)
2. **순위 표 확인·수정**: 가사에서 순위·팀·기록·연도·선수를 규칙으로 뽑은 초안(`shortsmaker/rank_extract.py`, 외부 API 없음).
   가사에 없는 값(대개 선수 이름·연도)은 표에서 채운다. 빈칸이 있으면 **표 확정** 불가. 확정할 때 자막 표기도 정리
   ('십육 연패'→'16연패', '케이비오'→'KBO', 순위 줄 + 기록 줄 합치기 선택).
   **공동 순위**: 같은 순위를 두 행에 적으면 '공동 2위'로 표시되고 다음 순위(3위) 칸은 비워도 확정된다
   (규칙 1, 2, 2, 4, 5). 가사의 "공동 N위" 줄은 자동으로 모두 잡는다. 영상 속 자리는 5→1 로 한 번씩 정하고
   보이는 순위는 `items[].display_rank` 에 저장한다
3. **사진**: 위키미디어 공용 검색(검색어는 화면에서 직접 고침, 자동으로 넓히지 않음). 라이선스·저작자 표시, 상업 이용 후보 우선.
   '동일인 확인'과 '이용·변형 권한 확인'을 체크해야 사용. 없으면 직접 이미지 올리기
4. **AI 캐리커처**: 생성 전에 선수별·합계 **예상 비용**을 보여주고, 승인 체크를 해야 생성 버튼이 열린다.
   승인 뒤 사진·설정이 바뀌면 다시 승인해야 하고, 한 번 승인으로 한 번만 실행(선수별 최대 3회, 자동 재시도 없음).
   결과를 보고 선수마다 **승인**
5. **카드 합성**(`shortsmaker/card_compose.py`): 캐리커처 + 금색 순위 + 기록 + 연도 + 팀 이름 띠 + 선수 이름.
   **구단 로고는 넣지 않고** 팀 색(`shortsmaker/data/kbo_teams.json`, 근사값 — 직접 수정 가능)과 이름만 쓴다
6. **렌더 + CapCut 초안**: 권리 확인 체크 후 yagu_v2 로 MP4 + CapCut 초안. `credits.txt` 에 영상 설명용 사진 출처

**Mock 모드**: API 키가 없으면 자동으로 Mock. 비용 0, OpenAI 로 아무것도 보내지 않는다. 사진 대신 팀 색 실루엣을 쓸 수 있고,
카드에 'MOCK 시험용'이 찍히며 출력 이름에 `_MOCK` 이 붙는다. Mock 결과는 실제 프로젝트에 승인·연결할 수 없다.

### OpenAI API 키 설정 (실제 캐리커처 생성용)

1. https://platform.openai.com 에서 API 키를 만들고, **Billing → Usage limits** 에서 월 사용 한도를 먼저 정한다
2. 터미널에서 `~/.zshrc` 끝에 한 줄 추가 후 새 터미널을 연다
   ```sh
   export OPENAI_API_KEY="sk-..."
   ```
3. 그 터미널에서 `.venv/bin/streamlit run app.py`. 화면 왼쪽에 'OPENAI_API_KEY 설정됨'이 보이면 새 프로젝트에서
   '실제 캐리커처 생성(유료) 모드'를 고를 수 있다

키는 채팅·코드·project.json 에 넣지 않는다. 프로그램은 환경변수에서만 읽고 값을 화면에 표시하지 않는다.
예상 비용은 `shortsmaker/player_assets.py` 의 가격표(gpt-image-1.5, 2026-10-09 확인)로 계산한 추정치이며 청구 상한이 아니다.

주의: 실존 선수 캐리커처는 사진 라이선스와 별개로 초상권 문제가 있을 수 있다. CC BY-SA 사진으로 만든 캐리커처는 2차 저작물이라
저작자·라이선스 표시와 동일조건 의무가 따른다 (`credits.txt` 를 영상 설명에 넣을 것).

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

## 가사 타이밍 자동 맞추기 (2차 기능, 로컬 모델)

음악 + 가사 텍스트(한 줄 = 자막 한 줄)를 주면 줄마다 시작·끝 시간을 찾아 `lyrics`에 넣는다.
유료 API 없이 내 Mac에서 돈다(모델은 처음 한 번 Hugging Face에서 무료로 내려받음).

```sh
.venv/bin/pip install -r requirements-align.txt   # stable-ts, faster-whisper, demucs, torch (기본 설치와 분리)
```

- 방식: **stable-ts 강제 정렬** — 받아쓰기가 아니라 이미 아는 가사가 '언제' 불렸는지 찾는다.
  faster-whisper(CPU int8), 기본 모델 `large-v3`(약 3GB, 정확), `medium`·`small`(빠름) 선택 가능.
- 반주가 크면 **demucs로 보컬만 분리**한 뒤 맞춘다(기본 켜짐, 결과는 `build/<폴더>/vocals_*.wav`에 캐시).
- 후처리: 순서·겹침 정리, 0.6초 이하 틈은 이어 붙이기, 한 줄 최대 8초, 영상 시간(= 곡 시간 − 음악 시작 위치)으로 변환,
  영상 밖 줄 제외. 못 찾은 줄은 앞뒤 사이로 보간하고, 신뢰도 낮은 줄과 함께 **확인 필요**로 알려준다.
- 결과는 `projects/<폴더>/lyrics_auto.lrc`로도 저장된다(끝 시간은 빈 타임태그 → 다시 읽어도 같은 구간).
- 화면: 4. 가사 → **🎙️ 가사 자동 맞추기** 펼치기 → 가사 붙여 넣기(또는 **곡에서 가사 불러오기**: Suno 곡은 파일에 가사가 들어 있음)
  → 버튼. 결과가 아래 표에 들어오니 초 단위로 고치고 💾 저장. **표를 LRC로 저장**하면 `lyrics_edited.lrc`가 생긴다.
- 명령줄: `align projects/x/project.json [--lyrics 가사.txt] [--no-vocals] [--model medium] [--lrc-name x.lrc] [--write]`
  (`--lyrics`가 없으면 음악 파일 내장 가사, `--write`면 project.json에도 기록)
- 정확도 평가: `.venv/bin/python scripts/eval_align.py 자동.lrc 정답.lrc` → 시작 시간 ±0.3/0.5/1초 안에 든 줄 수
- 속도(M2 Pro, 64초 곡): 보컬 분리 약 10초 + 정렬(large-v3) 약 15초. 첫 실행은 모델 다운로드로 1~2분 더.

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
  lyrics_align.py          가사 타이밍 자동 맞추기 (2차: demucs 보컬 분리 + stable-ts 정렬, LRC 저장)
  cli.py                   명령줄
templates/{yagu,info_top5} 채널 템플릿
scripts/                   샘플 생성, 렌더 결과 확인(probe.py), CapCut 호환성 프로브, 가사 정렬 평가(eval_align.py)
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
