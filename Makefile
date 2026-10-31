.PHONY: setup dev test lint seed eval-quick eval demo-reset postman

ifeq ($(OS),Windows_NT)
  VENV_BIN := backend/.venv/Scripts
else
  VENV_BIN := backend/.venv/bin
endif
PY := $(VENV_BIN)/python
HAS_FRONTEND := $(wildcard frontend/package.json)

# Python tooling uses uv (https://docs.astral.sh/uv/).
setup:
	uv venv --python 3.11 backend/.venv
	uv pip install --python $(PY) -e "backend[dev]" pre-commit
	$(VENV_BIN)/pre-commit install
ifneq ($(HAS_FRONTEND),)
	cd frontend && npm ci
endif

dev:
	cd backend && ../$(PY) -m uvicorn app.main:app --reload --port 8000
# The frontend dev server (vite :5173) is added by the frontend PRs: run `npm run dev` in frontend/.

test:
	cd backend && ../$(PY) -m pytest
ifneq ($(HAS_FRONTEND),)
	cd frontend && npm test --if-present
endif

lint:
	cd backend && ../$(PY) -m ruff check .
	cd backend && ../$(PY) -m ruff format --check .
	cd backend && ../$(PY) -m mypy app
ifneq ($(HAS_FRONTEND),)
	cd frontend && npm run lint --if-present && npx tsc --noEmit
endif

# Targets below are filled in by later PRs (scripts/ and eval/ belong to the platform session).
seed:
	@test -f scripts/seed_sandbox.py && $(PY) scripts/seed_sandbox.py || echo "seed: scripts/seed_sandbox.py not added yet"

eval-quick:
	@test -f eval/run_eval.py && $(PY) eval/run_eval.py --quick || echo "eval-quick: eval/run_eval.py not added yet"

eval:
	@test -f eval/run_eval.py && $(PY) eval/run_eval.py || echo "eval: eval/run_eval.py not added yet"

demo-reset:
	@test -f scripts/demo_reset.py && $(PY) scripts/demo_reset.py || echo "demo-reset: scripts/demo_reset.py not added yet"

postman:
	@echo "postman: export from /openapi.json is added with the postman PR"
