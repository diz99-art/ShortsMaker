<!-- CLAUDE.md 와 같은 내용 (Codex 는 AGENTS.md 를 읽는다). 고칠 때는 두 파일을 같이 고친다. -->
# ShortsMaker — 유튜브 쇼츠 자동 제작 프로그램

## 목표
유튜브 채널 '야구한장'과 정보 채널의 TOP 5 쇼츠를 만드는 도구다.
순위 표·곡·그림을 넣으면 가사에 맞춰 카드가 넘어가는 1080×1920 MP4 와 CapCut 편집 초안, 업로드 문구를 만든다.
CapCut 에서 카드·가사를 손으로 배치하던 반복 작업을 대신하고, 사람은 자료가 맞는지와 결과물만 확인한다.

## 범위
- 제작 흐름 3가지 (Streamlit 왼쪽 메뉴)
  - **노래로 자동 제작**: 곡 → 가사 맞추기 → 순위 표 추출(사용자 확인·수정 필수) → 위키미디어 공용 사진 검색(없으면 사용자 이미지)
    → 캐리커처(수동 무료 / OpenAI 유료는 비용 표시 후 사용자 승인 필수) → 카드 합성(구단 로고 금지, 팀 색·이름만) → 렌더 + CapCut 초안 + 업로드 문구
  - **가사 만들기**: 주제·확정 데이터 → 해설·비교가 들어간 가사 초안 → 가사 검사 통과 시 Suno 브리프 저장
  - **빠른 제작**: 양식 답변 + 곡 + 그림 5장 → 시험 렌더까지 한 번에. 문제가 있을 때만 멈추고 묻는다
- API 키 없이 Mock 모드로 전체 흐름이 돌아야 한다.
- YouTube 업로드는 하지 않는다 (업로드 문구만 만든다).

## 기술 스택
- Python 3.11+, 가상환경 `.venv`
- Pydantic v2(스키마·검증), Pillow(프레임 그리기), FFmpeg/ffprobe(Homebrew 설치, `subprocess` 로 호출)
- Streamlit(화면), librosa(박자 — 연출 효과에만 사용), pyCapCut(CapCut 초안)
- 가사 맞추기: demucs(보컬 분리) + stable-ts(faster-whisper) 강제 정렬. `requirements-align.txt` 로 분리하고, 없어도 나머지 기능은 동작해야 한다
- 개발 도구: pytest(+ xdist, cov), ruff, mypy, pre-commit, GitHub Actions
- 외부 유료 API 금지. 예외: 캐리커처 생성(OpenAI 이미지 편집)만, 예상 비용을 보여주고 사용자가 승인한 요청에 한해 허용
- 가사 초안·분위기 판단은 Claude Code 헤드리스(`claude -p`, 사용자 구독)만 쓴다. 실패하면 프롬프트 복사 수동 모드 / 키워드 규칙

## 폴더 구조
```
ShortsMaker/
├── app.py, pages/            # Streamlit 화면 (노래로 자동 제작 · 가사 만들기 · 빠른 제작)
├── shortsmaker/              # 본체
│   ├── models.py             # pydantic 스키마 (프로젝트·템플릿)
│   ├── quick.py, quick_parse*.py   # 빠른 제작, 양식 답변 → 표 (줄 번호 오류)
│   ├── rank_extract.py       # 가사 → 순위 표, 한글 숫자 변환, 열 밀림 검사
│   ├── lyrics.py, lyrics_align.py, lyrics_gen.py, lyrics_check.py   # 자막 · 가사 맞추기 · 가사 초안 · 가사 검사
│   ├── auto_pipeline.py      # 노래로 자동 제작 흐름·체크리스트
│   ├── card_timing.py        # 장면·글자 시각 계산 (그리지 않음)
│   ├── card_layers.py, card_*.py, effects.py, mascot.py, sfx.py   # 화면 요소 그리기 · 효과 · 효과음
│   ├── card_mode.py          # 프레임 렌더 → FFmpeg 인코딩 (지금 쓰는 방식)
│   ├── timeline.py, scenes.py, ffmpeg_builder.py, pipeline.py      # 첫 버전(classic) 렌더
│   ├── variation.py          # 영상마다 연출 조합 바꾸기
│   ├── production_log.py     # 사람이 고친 내용 기록
│   ├── upload.py             # 업로드용 제목·설명·태그 (업로드는 하지 않음)
│   ├── capcut_export.py      # CapCut 초안 (비공식 라이브러리)
│   ├── validate.py           # 렌더 전후 검증
│   └── cli.py                # 명령줄 진입점
├── templates/<채널>/         # template.json (좌표·색·글꼴), template.local.json 은 내 컴퓨터 전용 (git 제외)
├── assets/                   # 폰트·효과음·마스코트·임시 그림 (폰트는 make setup 이 받음)
├── examples/demo/            # 저작권 문제 없는 데모 자료 (make demo)
├── samples/                  # 테스트용 샘플 프로젝트
├── scripts/                  # 자산 준비·데모·프레임 비교·파일 검사
├── tests/                    # pytest (실제 MP4 를 만드는 테스트는 @pytest.mark.slow)
├── docs/                     # architecture · decisions · usage · CapCut 호환성 기록
├── projects/<이름>/          # 영상마다 project.json·그림·곡 (git 제외)
└── output/                   # 완성 MP4 (git 제외)
```
자세한 모듈 구조는 `docs/architecture.md`, 사용법은 `docs/usage.md`.

## 설계 원칙
- 좌표·색·글꼴은 템플릿에 둔다. 코드에 좌표를 하드코딩하지 않는다. 채널 추가 = 템플릿 폴더 추가로 끝나야 한다.
- 템플릿 이미지·로고·캐릭터가 없으면 단색 도형 임시 그림으로 대체해 실행은 되게 한다.
- CapCut 초안은 비공식 라이브러리라 `capcut_export.py` 로 분리하고, 실패해도 MP4 기능에는 영향이 없게 한다.
  시간 단위는 마이크로초, 장면·가사 타이밍은 MP4 렌더와 같은 계산 결과를 재사용한다.
- 출력: 1080×1920, 30fps, H.264 yuv420p, AAC 192k, `-movflags +faststart`

## 검증 규칙
- 렌더 전: 스키마, 파일 존재, 한글 폰트, 가사 구간 겹침·영상 길이 초과, 글자 넘침(자동 축소, 최소 크기에서도 넘치면 경고)
- 렌더 후: ffprobe 로 해상도 1080×1920, 길이 ±0.5초, 오디오 스트림 확인
- FFmpeg 실패 시 실행한 명령과 stderr 마지막 부분을 화면에 보여준다

## 콘텐츠 품질·정확성 체크리스트
- 영상마다 구성·해설을 다르게 만든다: `variation.py` 의 연출 18요소를 최근 5편과 3요소 이상 다르게. 제목 색·채널 로고는 고정
- 가사는 순위 나열만 하지 않고 해설·비교·반전을 넣는다. 확정 데이터에 있는 숫자·이름만 쓴다. 가사 검사를 통과해야 저장
- 사람이 확인·수정한 내용(데이터 수정·가사 선택·캐리커처 승인·최종 확인)을 `production_log.json` 에 기록한다
- 최종 렌더 전 확인: 데이터 출처, 사진 출처·사용 권한, 음악 사용 권리, 캐리커처에 로고·글씨 없음, 가사 검사, 연출 중복.
  하나라도 안 되면 시험 렌더(`_DRAFT`)만 만든다

## AI 에게 일을 맡길 때의 규칙
- 큰 작업은 먼저 계획을 보여주고 승인받은 뒤 진행한다.
- 테스트 먼저: 버그는 실패하는 재현 테스트를 먼저 커밋(`test:`)하고 고친다(`fix:`).
- 렌더 비교: 리팩터링처럼 결과가 바뀌면 안 되는 변경은 전후로 같은 프로젝트를 렌더해 프레임 해시(framemd5)와 오디오 해시가 같은지 확인한다.
  결과가 바뀌는 변경은 `samples/` 또는 `examples/demo` 로 실제 렌더해서 눈으로 확인한다.
- 모든 변경은 브랜치 → PR 로 올리고 CI(ruff → mypy → pytest)가 통과해야 merge 한다. `main` 에 직접 push 하지 않는다.
- git 검사(hook)를 건너뛰지 않는다: `git commit --no-verify`, `git push --no-verify`(`-n`), `core.hooksPath` 바꾸기는 절대 쓰지 않는다.
  검사에 걸리면 원인을 고치고, 고칠 수 없으면 멈추고 사용자에게 묻는다.
- 폰트·노래·그림·비밀 파일은 커밋하지 않는다 (`make hooks` 의 검사가 막는다).
- 사용자에게 설명할 때는 한국어로 한다.

## 협업 규칙 (Claude Code + Codex)
같은 저장소를 Claude Code(메인 폴더 `~/Projects/ShortsMaker`, `main` 브랜치)와
Codex(작업 폴더 `~/Projects/ShortsMaker-codex`, `codex/work` 브랜치, git worktree)가 함께 개발한다.
이 규칙은 CLAUDE.md 와 AGENTS.md 에 똑같이 들어 있다. 하나를 고치면 다른 하나도 같이 고친다.

- Codex 는 자기 브랜치(`codex/work`)에서만 작업한다. `main` 에 직접 커밋·병합·리베이스하지 않는다 (병합은 사용자가 정한다).
- 맡은 파일 외에는 건드리지 않는다. 다른 파일을 꼭 고쳐야 하면 고치기 전에 이유를 적고 사용자에게 묻는다.
- 새 기능은 가능하면 새 파일(모듈)로 만든다. 기존 파일에는 새 모듈을 연결하는 최소한의 수정만 한다.
- 가상환경은 같이 쓴다: 작업 폴더의 `.venv` 는 `~/Projects/ShortsMaker/.venv` 를 가리키는 링크다.
  패키지 설치·삭제는 두 쪽 모두에 영향을 주므로 사용자에게 먼저 묻는다.
- `projects/`, `output/`, `reference/` 는 git 에 없어서 작업 폴더마다 따로다. 실제 프로젝트로 렌더할 때는 메인 폴더의 프로젝트를 복사해서 쓴다 (원본은 고치지 않는다).
- 작업이 끝나면 `.venv/bin/python -m pytest -q` 가 모두 통과하는지 확인하고, 답변 맨 끝에 아래 요약을 출력한다.
  표는 쓰지 말고 줄글과 목록만, 15줄 이내, 한국어.

```
━━━━━━━━ 📋 Claude 전달용 (여기부터 복사) ━━━━━━━━
[프로젝트] 이름 / 브랜치 / 커밋 해시
[한 일] 3줄 이내
[결과] 테스트 수, 만든·고친 파일, 만든 영상 위치
[문제·확인 못 한 것] 있으면
[나에게 묻는 것] 선택지가 있으면 번호로
━━━━━━━━ 📋 여기까지 ━━━━━━━━
```
