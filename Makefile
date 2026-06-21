# StackOS Makefile
# All targets idempotent unless documented otherwise.
# Reserved targets print a clear placeholder and exit 0.

SHELL := /bin/bash
PYTHON ?= uv run python
UV ?= uv

.DEFAULT_GOAL := help

.PHONY: help install serve dev-ui build-ui signoff register-codex register-claude \
        install-agent-assets install-skills-codex install-skills-claude \
        install-plugins \
        install-launchd doctor test test-ui-unit test-ui-e2e migrate lint \
        format typecheck gen-types clean uninstall backup restore \
        rotate-seed rotate-token

help: ## Show this help with all targets
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-26s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Full dev install — deps + migrate + UI bundle + plugin + MCP + doctor
	@echo "==> Installing Python deps"
	$(UV) sync --all-extras
	@echo "==> Bootstrapping daemon state (creates seed + auth.token if absent)"
	$(PYTHON) -m stackos init
	@echo "==> Running migrations"
	$(PYTHON) -m stackos migrate
	@echo "==> Installing Playwright Chromium browser runtime"
	$(PYTHON) -m playwright install chromium
	@echo "==> Verifying committed UI bundle (stackos/ui_dist/ is committed)"
	@if [ -f stackos/ui_dist/index.html ]; then \
	  echo "  ui_dist/index.html present"; \
	else \
	  echo "  ui_dist/ missing — running \`make build-ui\` to regenerate"; \
	  $(MAKE) build-ui; \
	fi
	@echo "==> Registering bridge MCP for Codex CLI"
	@bash scripts/register-mcp-codex.sh
	@echo "==> Registering bridge MCP for Claude Code"
	@bash scripts/register-mcp-claude.sh
	@echo "==> Installing runtime skill mirrors"
	@bash scripts/install-codex.sh
	@bash scripts/install-claude.sh
	@echo "==> Installing plugins"
	@bash scripts/install-plugins.sh
	@echo "==> Running doctor (daemon-down is allowed before \`make serve\`; hard failures stop install)"
	@bash scripts/install-doctor.sh
	@echo "==> install complete"

serve: ## Run the daemon foreground on 127.0.0.1:5180
	$(PYTHON) -m stackos serve

dev-ui: ## Run Vite dev server alongside the daemon
	@if [ -d ui ]; then cd ui && pnpm dev; else echo "ui/ not available in this checkout"; exit 0; fi

build-ui: ## Build Vue UI into stackos/ui_dist/
	@if [ -d ui ]; then cd ui && pnpm install && pnpm build; else echo "ui/ not available in this checkout"; exit 0; fi

signoff: lint typecheck ## Before commit/release: setup docs, actions, MCP/REST/CLI, and UI checks
	$(UV) run pytest tests/unit \
		tests/integration/test_routes/test_operations_routes.py \
		tests/integration/test_routes/test_auth_provider_routes.py \
		tests/integration/test_routes/test_cli_mock_provider.py \
		tests/integration/test_routes/test_telegram_setup_to_action_routes.py \
		tests/integration/test_mcp/test_mcp_actions.py \
		tests/integration/test_mcp/test_mcp_readiness.py \
		tests/integration/test_mcp/test_mcp_run_plans.py \
		tests/integration/test_mcp/test_mcp_communications.py \
		tests/integration/test_mcp/test_mcp_agent_requests.py \
		tests/integration/test_repositories/test_actions.py \
		tests/integration/test_repositories/test_video_provider_actions.py \
		tests/integration/test_repositories/test_agent_requests.py \
		tests/integration/test_repositories/test_auth_providers.py \
		tests/integration/test_repositories/test_smtp_actions.py \
		tests/integration/test_repositories/test_imap_actions.py \
		tests/integration/test_repositories/test_telegram_bot_actions.py \
		tests/integration/test_repositories/test_workflow_templates.py \
		tests/integration/test_integrations \
		-q
	$(MAKE) test-ui-unit
	$(MAKE) build-ui

migrate: ## Run alembic migrations forward
	$(UV) run alembic upgrade head

test: test-ui-unit ## Run pytest + UI unit tests
	$(UV) run pytest

test-ui-unit: ## Run UI unit tests
	@if [ -d ui ]; then cd ui && pnpm install --silent && pnpm test; else echo "ui/ not available in this checkout"; exit 0; fi

test-ui-e2e: ## Run UI E2E tests
	@if [ -d ui ]; then cd ui && pnpm install --silent && pnpm e2e; else echo "ui/ not available in this checkout"; exit 0; fi

lint: ## Run ruff check + format-check
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format: ## Apply ruff format + auto-fix lints
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

typecheck: ## Run mypy on the package
	$(UV) run mypy stackos

gen-types: ## Regenerate ui/src/api.ts from the source OpenAPI spec
	bash scripts/gen-types.sh

doctor: ## Diagnose local install state
	$(PYTHON) -m stackos doctor

clean: ## Remove caches and build artifacts (preserves DB at ~/.local/share/stackos/)
	rm -rf __pycache__ .ruff_cache .mypy_cache .pytest_cache build dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name '*.egg-info' -prune -exec rm -rf {} +

register-codex: ## Register bridge MCP server with Codex CLI
	@bash scripts/register-mcp-codex.sh

register-claude: ## Register bridge MCP server with Claude Code
	@bash scripts/register-mcp-claude.sh

install-agent-assets: ## Refresh StackOS skills and plugin assets for local agent runtimes
	@echo "==> Installing runtime skill mirrors"
	@bash scripts/install-codex.sh
	@bash scripts/install-claude.sh
	@echo "==> Installing plugins"
	@bash scripts/install-plugins.sh

install-skills-codex: ## Install skills into ~/.codex/skills/stackos/
	@bash scripts/install-codex.sh

install-skills-claude: ## Install skills into ~/.claude/skills/stackos/
	@bash scripts/install-claude.sh

install-plugins: ## Install StackOS plugin into ~/.codex/plugins and ~/.agents marketplace
	@bash scripts/install-plugins.sh

install-launchd: ## Install launchd autostart for the daemon
	@bash scripts/install-launchd.sh

uninstall: ## Remove installed skills/plugins/MCP entries; preserve DB + seed
	@echo "==> Booting out launchd job (if loaded)"
	@bash scripts/install-launchd.sh --uninstall || true
	@echo "==> Removing skills"
	@rm -rf "$(HOME)/.codex/skills/stackos" "$(HOME)/.claude/skills/stackos"
	@echo "==> Removing plugins"
	@bash scripts/install-plugins.sh --remove || true
	@echo "==> Unregistering bridge MCP for Codex CLI"
	@bash scripts/register-mcp-codex.sh --remove || true
	@echo "==> Unregistering bridge MCP for Claude Code"
	@bash scripts/register-mcp-claude.sh --remove || true
	@echo "==> Note: ~/.local/share/stackos/ (DB) and ~/.local/state/stackos/ (seed + token) preserved."
	@echo "==> uninstall complete"

backup: ## Reserved backup command
	@echo "Not yet implemented (backup/restore jobs)"

restore: ## Reserved restore command
	@echo "Not yet implemented (backup/restore jobs)"

rotate-seed: ## Re-encrypt all integration_credentials with a new seed
	$(PYTHON) -m stackos rotate-seed --reencrypt

rotate-token: ## Generate new auth token and update MCP configs
	$(PYTHON) -m stackos rotate-token --yes
