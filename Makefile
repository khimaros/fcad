# fcad, the reusable instrumentation package. `make` installs it editable;
# `make test` runs the end-to-end suite (needs FreeCAD on PATH); `make precommit`
# is the pre-commit gate.
PY      := python3
RESULT  := /tmp/fcad-test-result.txt
FREECAD := freecadcmd

.PHONY: all install test precommit clean

all: install

install:
	uv tool install -e ".[render]"

# the suite drives the real FreeCAD kernel; freecadcmd swallows stdout and exits
# 0 on error, so each test writes its verdict to a file we then assert on.
TESTS := $(wildcard tests/test_*.py)
test:
	@for t in $(TESTS); do \
	  echo "== $$t"; \
	  RESULT_FILE=$(RESULT) $(FREECAD) $$t >/dev/null 2>&1 || true; \
	  cat $(RESULT); \
	  grep -q "RESULT PASS" $(RESULT) || exit 1; \
	done

precommit: install test
	@echo "precommit ok"

clean:
	rm -rf build dist *.egg-info src/*.egg-info $(RESULT)
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
