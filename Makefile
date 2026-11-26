PYTHON ?= python3
JOBS ?= 10
CLANG_FORMAT ?= clang-format-21
export PYTHON JOBS

.PHONY: check fetch build verify

check:
	shellcheck scripts/*.sh
	$(PYTHON) -m ruff format --check scripts
	$(PYTHON) -m ruff check scripts
	$(PYTHON) -m mypy --strict --scripts-are-modules scripts
	$(CLANG_FORMAT) --dry-run --Werror tests/jni-app/src/main/jni/greeting.cpp

fetch build verify: | out

out:
	mkdir -p out

fetch:
	flock out/build.lock $(PYTHON) scripts/inputs.py

build:
	flock out/build.lock bash scripts/build.sh

verify:
	flock out/build.lock $(PYTHON) scripts/verify.py
