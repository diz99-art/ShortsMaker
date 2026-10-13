# 예시 자료 (저작권 걱정 없는 가짜 자료)

`make demo` 가 이 폴더로 30초짜리 시험 영상을 만듭니다 (`output/demo.mp4`).
모든 이름·숫자는 지어낸 값이고, 그림·소리는 `scripts/make_example.py` 가 직접 그린·합성한 것입니다.

- `form.txt` — 빠른 제작 자료 양식 (하늘 배지형 6칸 표). 공동 3위가 들어 있습니다
- `lyrics.lrc` — 자막용 가사 (시간이 붙은 LRC)
- `item1.png` ~ `item5.png` — 단색 도형 그림 5장 (5위 → 1위 순서)
- `beep.mp3` — 30초 비프음 (박자 120 BPM)

그림·소리를 다시 만들려면: `.venv/bin/python scripts/make_example.py`
