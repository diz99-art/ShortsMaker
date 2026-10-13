PY ?= .venv/bin/python

.PHONY: setup demo test test-all check-files hooks

## 가상환경 + 패키지 + 폰트·효과음·샘플 준비 (FFmpeg 는 따로: brew install ffmpeg)
setup:
	test -d .venv || python3 -m venv .venv
	$(PY) -m pip install -q -r requirements.txt
	$(PY) scripts/setup_assets.py

## examples/demo 의 가짜 자료로 30초 시험 영상 → output/demo.mp4
demo:
	$(PY) scripts/run_demo.py

## 빠른 테스트 (실제 렌더·모델이 들어가는 slow 테스트 제외)
test:
	$(PY) -m pytest -q -m "not slow"

test-all:
	$(PY) -m pytest -q

## git 에 올리면 안 되는 파일(폰트·오디오·그림·5MB 초과·.env) 검사
check-files:
	$(PY) scripts/check_repo_files.py --all

## 커밋 전에 check-files 를 자동 실행하는 git hook 설치 (worktree 끼리 공유됨)
hooks:
	printf '#!/bin/sh\n# 검사 스크립트가 없는 브랜치(예: 옛 기록의 작업 폴더)에서는 건너뛴다\n[ -f scripts/check_repo_files.py ] || exit 0\nexec .venv/bin/python scripts/check_repo_files.py\n' > "$$(git rev-parse --git-path hooks)/pre-commit"
	chmod +x "$$(git rev-parse --git-path hooks)/pre-commit"
