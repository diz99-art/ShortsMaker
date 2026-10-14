# 문서용 캡처

README 와 docs 에 넣을 그림을 두는 곳. 이 폴더의 그림·GIF 만 git 에 올릴 수 있다 (`.gitignore` 예외).
파일 하나는 5MB 를 넘지 않게 한다 (`scripts/check_repo_files.py` 가 막음).

## 찍을 목록

아래 이름으로 저장하면 README 에서 바로 쓸 수 있다. 다 찍을 필요는 없고, 1\~3번이 가장 중요하다.

1. `demo.gif` — (완료) 새 폴더에서 만든 `make demo` 결과의 1.5\~11초 (후킹 → 5위 → 공동 3위), 360×640, 약 230KB, 핸들은 가짜 `@demo`
   ```sh
   ffmpeg -ss 1.5 -t 9.5 -i output/demo.mp4 -filter_complex \
     "fps=12,scale=360:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" \
     docs/images/demo.gif
   ```
2. `quick-make.png` — 빠른 제작 화면: 양식 답변을 붙여넣고 틀린 줄에 오류가 뜬 상태 (줄 번호 오류 메시지가 보이게)
3. `rank-table.png` — 순위 표 확인 화면: 열 밀림 경고 또는 '공동 2위'가 보이는 표
4. `frames.png` — 완성 영상의 장면 3\~4개를 나란히 (인트로·5위·1위·엔딩)
   ```sh
   ffmpeg -i output/demo.mp4 -vf "select='eq(n\,60)+eq(n\,150)+eq(n\,750)+eq(n\,870)',scale=270:-1,tile=4x1" \
     -frames:v 1 -fps_mode vfr docs/images/frames.png
   ```
5. `checklist.png` — 노래로 자동 제작의 '최종 렌더 체크리스트' (품질·정확성 항목의 ✅/❌ 가 보이게)
6. `capcut-draft.png` — CapCut 에서 연 초안의 타임라인 (트랙이 나뉜 모습)
7. `ci.png` — GitHub Actions 의 PR 실행 화면 (약 3분)

## 주의

- **영상 캡처(1·4번)는 새로 받은 저장소에서 찍는다.** 내 작업 폴더에는 `template.local.json` 과 채널 로고 원본이 있어서
  `make demo` 에도 실제 채널 로고·핸들이 찍힌다. 새 폴더에서 `git clone` → `make setup` → `make demo` 로 만들면
  임시 그림·가짜 이름으로 나온다
- 실제 선수 사진·캐리커처, 다른 채널의 영상·로고가 들어간 화면은 찍지 않는다. `examples/demo` 자료나 임시 그림으로 찍는다
- 화면에 개인 정보(파일 경로의 사용자 이름, 이메일, API 키 설정 표시 등)가 보이지 않게 자른다
- 노래가 들어간 영상 파일은 올리지 않는다 (GIF 는 소리가 없어서 괜찮음)
