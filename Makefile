PY ?= .venv/bin/python

.PHONY: setup setup-dev demo test test-all cov lint typecheck check check-files hooks

## 가상환경 + 패키지 + 폰트·효과음·샘플 준비 (FFmpeg 는 따로: brew install ffmpeg)
setup:
	test -d .venv || python3 -m venv .venv
	$(PY) -m pip install -q -r requirements.txt
	$(PY) scripts/setup_assets.py

## 개발 도구 (ruff·mypy·pytest-cov·pre-commit) 추가 설치
setup-dev: setup
	$(PY) -m pip install -q -r requirements-dev.txt

## examples/demo 의 가짜 자료로 30초 시험 영상 → output/demo.mp4
demo:
	$(PY) scripts/run_demo.py

## 빠른 테스트 (실제 렌더·모델이 들어가는 slow 테스트 제외)
test:
	$(PY) -m pytest -q -n auto -m "not slow"

test-all:
	$(PY) -m pytest -q -n auto

## 커버리지 포함 전체 테스트 (CI 와 같은 명령)
cov:
	$(PY) -m pytest -q -n auto --cov --cov-report=term-missing:skip-covered

lint:
	$(PY) -m ruff check .

## mypy 설정은 pyproject.toml (핵심 모듈만 엄격 검사, 나머지는 단계적으로)
typecheck:
	$(PY) -m mypy

## CI 와 같은 순서: ruff → mypy → pytest
check: lint typecheck test-all

## git 에 올리면 안 되는 파일(폰트·오디오·그림·5MB 초과·.env) 검사
check-files:
	$(PY) scripts/check_repo_files.py --all

## 커밋 전 검사 hook 설치 (worktree 끼리 공유됨). .pre-commit-config.yaml 이 없는 브랜치에서는 예전 검사만, 그것도 없으면 건너뛴다
hooks:
	printf '#!/bin/sh\n[ -f .pre-commit-config.yaml ] && exec .venv/bin/pre-commit run --hook-stage pre-commit\n[ -f scripts/check_repo_files.py ] || exit 0\nexec .venv/bin/python scripts/check_repo_files.py\n' > "$$(git rev-parse --git-path hooks)/pre-commit"
	chmod +x "$$(git rev-parse --git-path hooks)/pre-commit"
