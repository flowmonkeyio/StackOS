# content-stack Makefile
# All targets idempotent unless documented otherwise.
# Stubs that land in later milestones print the milestone tag and exit 0.

SHELL := /bin/bash
PYTHON ?= uv run python
UV ?= uv

.DEFAULT_GOAL := help

.PHONY: help install serve dev-ui build-ui register-codex register-claude \
        install-skills-codex install-skills-claude \
        install-plugins \
        install-launchd doctor test test-ui-unit test-ui-e2e migrate lint \
        format typecheck gen-types clean uninstall backup restore \
        rotate-seed rotate-token

help: ## Show this help with all targets
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-26s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## (M9) Full install pipeline — deps + migrate + UI + plugin + MCP + doctor
	@echo "==> Installing Python deps"
	$(UV) sync --all-extras
	@echo "==> Bootstrapping daemon state (creates seed + auth.token if absent)"
	$(PYTHON) -m content_stack init
	@echo "==> Running migrations"
	$(PYTHON) -m content_stack migrate
	@echo "==> Verifying committed UI bundle (D8: content_stack/ui_dist/ is committed)"
	@if [ -f content_stack/ui_dist/index.html ]; then \
	  echo "  ui_dist/index.html present"; \
	else \
	  echo "  ui_dist/ missing — running \`make build-ui\` to regenerate"; \
	  $(MAKE) build-ui; \
	fi
	@echo "==> Registering bridge MCP for Codex CLI"
	@bash scripts/register-mcp-codex.sh
	@echo "==> Registering bridge MCP for Claude Code"
	@bash scripts/register-mcp-claude.sh
	@echo "==> Installing plugins"
	@bash scripts/install-plugins.sh
	@echo "==> Running doctor (post-install diagnose; daemon-up checks may FAIL until \`make serve\` runs)"
	@bash scripts/doctor.sh || echo "  (doctor surfaced issues — see output above)"
	@echo "==> install complete"

serve: ## (M0) Run the daemon foreground on 127.0.0.1:5180
	$(PYTHON) -m content_stack serve

dev-ui: ## (M6) Run Vite dev server alongside daemon (UI source lands in M6)
	@if [ -d ui ]; then cd ui && pnpm dev; else echo "ui/ not yet scaffolded — lands in M6"; exit 0; fi

build-ui: ## (M6) Build Vue UI into content_stack/ui_dist/
	@if [ -d ui ]; then cd ui && pnpm install && pnpm build; else echo "ui/ not yet scaffolded — lands in M6"; exit 0; fi

migrate: ## (M0/M1) Run alembic migrations forward (schema lands in M1)
	$(UV) run alembic upgrade head

test: test-ui-unit ## (M0) Run pytest + UI unit tests (vitest)
	$(UV) run pytest

test-ui-unit: ## (M5) Run UI unit tests (vitest, jsdom env)
	@if [ -d ui ]; then cd ui && pnpm install --silent && pnpm test; else echo "ui/ not yet scaffolded"; exit 0; fi

test-ui-e2e: ## (M5) Run UI E2E tests (Playwright + axe; spawns daemon on :5181)
	@if [ -d ui ]; then cd ui && pnpm install --silent && pnpm e2e; else echo "ui/ not yet scaffolded"; exit 0; fi

lint: ## (M0) Run ruff check + format-check
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format: ## (M0) Apply ruff format + auto-fix lints
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

typecheck: ## (M0) Run mypy on the package
	$(UV) run mypy content_stack

gen-types: ## (M2) Regenerate ui/src/api.ts from the source OpenAPI spec
	bash scripts/gen-types.sh

doctor: ## (M0) Diagnose install (M0 minimal — full check list grows through M10)
	$(PYTHON) -m content_stack doctor

clean: ## (M0) Remove caches and build artifacts (preserves DB at ~/.local/share/content-stack/)
	rm -rf __pycache__ .ruff_cache .mypy_cache .pytest_cache build dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name '*.egg-info' -prune -exec rm -rf {} +

register-codex: ## (M9) Register bridge MCP server with Codex CLI
	@bash scripts/register-mcp-codex.sh

register-claude: ## (M9) Register bridge MCP server with Claude Code (.mcp.json upsert)
	@bash scripts/register-mcp-claude.sh

install-skills-codex: ## (M9) Install skills into ~/.codex/skills/content-stack/
	@bash scripts/install-codex.sh

install-skills-claude: ## (M9) Install skills into ~/.claude/skills/content-stack/
	@bash scripts/install-claude.sh

install-plugins: ## Install content-stack plugin into ~/.codex/plugins and ~/.agents marketplace
	@bash scripts/install-plugins.sh

install-launchd: ## (M9) Write launchd plist for auto-start (macOS, optional)
	@bash scripts/install-launchd.sh

uninstall: ## (M9) Remove installed skills/plugins/MCP entries; preserve DB + seed
	@echo "==> Booting out launchd job (if loaded)"
	@bash scripts/install-launchd.sh --uninstall || true
	@echo "==> Removing skills"
	@rm -rf "$(HOME)/.codex/skills/content-stack" "$(HOME)/.claude/skills/content-stack"
	@echo "==> Removing plugins"
	@bash scripts/install-plugins.sh --remove || true
	@echo "==> Unregistering bridge MCP for Codex CLI"
	@bash scripts/register-mcp-codex.sh --remove || true
	@echo "==> Unregistering bridge MCP for Claude Code"
	@bash scripts/register-mcp-claude.sh --remove || true
	@echo "==> Note: ~/.local/share/content-stack/ (DB) and ~/.local/state/content-stack/ (seed + token) preserved."
	@echo "==> uninstall complete"

backup: ## (M9) Atomic SQLite .backup + copy seed.bin and auth.token
	@echo "Not yet implemented (M9: jobs/scheduling; auto-backup job)"

restore: ## (M9) Halt daemon, copy backup file over current DB, restart
	@echo "Not yet implemented (M9: jobs/scheduling)"

rotate-seed: ## (M4) Re-encrypt all integration_credentials with a new seed
	$(PYTHON) -m content_stack rotate-seed --reencrypt

rotate-token: ## (M10) Generate new auth token and update MCP configs
	$(PYTHON) -m content_stack rotate-token --yes
