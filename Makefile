.DEFAULT_GOAL := help
SHELL := /bin/bash

PYTHON := .venv/bin/python
PYTEST := .venv/bin/pytest
RUFF   := .venv/bin/ruff
MYPY   := .venv/bin/mypy
LINT_IMPORTS := .venv/bin/lint-imports

# Every application module, in dependency order. Named explicitly rather than passing '.':
# that would pull in the test suite, whose looser rules produce enough noise to hide a real
# error in the application itself.
#
# **One list, and CI uses this one.** It lived in two places until S17 and had drifted —
# `approvals`, `reporting` and `imports_exports` were type-checked locally and not in CI, so
# three modules were unchecked on the branch that mattered. The workflow now calls these
# targets with the tools overridden, which is why the variables above exist.
TYPED_MODULES := config operations audit accounts specifications inventory beams calculations \
                 spectrum satnets satnet_paths approvals reporting imports_exports

INTO ?= vsat_restore_drill

BOOTSTRAP_VERSION := 5.3.3
HTMX_VERSION      := 2.0.4

.PHONY: help
help:  ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# The single command of specification section 22.1
# ---------------------------------------------------------------------------
.PHONY: up
up:  ## Start everything: database, migrations, demo accounts, health check
	# --wait blocks until both containers report healthy. Without it `up -d` returns as
	# soon as they are *started*, and the migrate below races the application's own
	# boot — which fails with "init process is not running" if the container exited,
	# a message that says nothing about why.
	docker compose up --build -d --wait
	docker compose exec -T web python manage.py migrate --noinput
	docker compose exec -T web python manage.py seed_demo
	@$(MAKE) --no-print-directory health

.PHONY: down
down:  ## Stop the stack, keeping the database volume
	docker compose down

.PHONY: reset
reset:  ## Stop the stack and DESTROY the database volume
	docker compose down --volumes

.PHONY: health
health:  ## Verify the running application reports ready
	@echo "Waiting for readiness..."
	@for i in $$(seq 1 30); do \
		if curl --fail --silent http://127.0.0.1:8000/health/ready > /dev/null; then \
			echo "ready: $$(curl --silent http://127.0.0.1:8000/health/ready)"; exit 0; \
		fi; sleep 2; \
	done; \
	echo "not ready after 60s:"; curl --silent http://127.0.0.1:8000/health/ready; exit 1

# ---------------------------------------------------------------------------
# Native development (no Docker) against a local PostgreSQL cluster
# ---------------------------------------------------------------------------
.PHONY: install
install:  ## Install dependencies from the lock file
	uv sync --python 3.12

.PHONY: migrate
migrate:  ## Apply database migrations
	$(PYTHON) manage.py migrate

.PHONY: run
run:  ## Run the development server
	$(PYTHON) manage.py runserver 127.0.0.1:8000

# ---------------------------------------------------------------------------
# Quality gates — `make check` is what CI runs
# ---------------------------------------------------------------------------
.PHONY: check
check: lint types imports  ## Run every static quality gate

.PHONY: lint
lint:  ## Ruff lint and format check
	$(RUFF) check .
	$(RUFF) format --check .

.PHONY: format
format:  ## Apply Ruff formatting
	$(RUFF) format .
	$(RUFF) check --fix .

.PHONY: types
types:  ## Type check
	$(MYPY) $(TYPED_MODULES)

.PHONY: imports
imports:  ## Enforce the module dependency direction of docs/design/01
	$(LINT_IMPORTS)

.PHONY: intake
intake:  ## Regenerate the S0 RF confirmation sheets from the models
	# The sheets carry the exact columns the S15 importer will expect, so they are
	# generated rather than maintained by hand. tests/rf_confirmation fails if the
	# committed package and the models have parted, which is the reminder to run this.
	$(PYTHON) manage.py export_intake_templates

.PHONY: test
test:  ## Run the test suite against real PostgreSQL
	$(PYTEST)

.PHONY: test-db
test-db:  ## Run only the database constraint tests
	$(PYTEST) tests/db -v

# ---------------------------------------------------------------------------
# Operations — backup, restore verification and the release smoke check (section 22)
# ---------------------------------------------------------------------------
.PHONY: backup
backup:  ## Dump the database and write a manifest beside it (section 22.4)
	$(PYTHON) manage.py backup_database

.PHONY: verify-restore
verify-restore:  ## Restore DUMP into a scratch database and verify it (section 22.4)
	# A backup nobody has restored is a file, not a backup. docs/runbooks/restore.md.
	@test -n "$(DUMP)" || { echo "Usage: make verify-restore DUMP=/var/backups/vsat/xxx.dump"; exit 2; }
	$(PYTHON) manage.py verify_restore --dump $(DUMP) --into $(INTO)

.PHONY: smoke
smoke:  ## Verify a deployment is serving (section 22.3)
	$(PYTHON) manage.py smoke

# ---------------------------------------------------------------------------
# Vendored front-end assets (specification section 19.4 — no CDN dependency)
# ---------------------------------------------------------------------------
.PHONY: vendor
vendor:  ## Re-fetch vendored front-end assets into static/vendor
	@set -euo pipefail; \
	tmp=$$(mktemp -d); \
	echo "Fetching Bootstrap $(BOOTSTRAP_VERSION) and HTMX $(HTMX_VERSION)..."; \
	curl -sSL "https://registry.npmjs.org/bootstrap/-/bootstrap-$(BOOTSTRAP_VERSION).tgz" \
		| tar xz -C $$tmp; \
	install -m 644 $$tmp/package/dist/css/bootstrap.min.css static/vendor/bootstrap/; \
	install -m 644 $$tmp/package/dist/js/bootstrap.bundle.min.js static/vendor/bootstrap/; \
	install -m 644 $$tmp/package/LICENSE static/vendor/bootstrap/LICENSE; \
	rm -rf $$tmp/package; \
	curl -sSL "https://registry.npmjs.org/htmx.org/-/htmx.org-$(HTMX_VERSION).tgz" \
		| tar xz -C $$tmp; \
	install -m 644 $$tmp/package/dist/htmx.min.js static/vendor/htmx/; \
	install -m 644 $$tmp/package/LICENSE static/vendor/htmx/LICENSE; \
	rm -rf $$tmp; \
	echo "Vendored assets updated. Commit them — they are part of the repository."
