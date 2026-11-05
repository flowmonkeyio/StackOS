"""One-brain lifecycle service for host MCP registrations."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, Protocol

from stackos.host_mcp.adapters import claude_code, claude_desktop, codex, gemini_cli, grok, hermes
from stackos.host_mcp.bridge import MCP_SERVER_NAME, default_home, token_preflight
from stackos.host_mcp.result import (
    AgentCapabilities,
    AgentRunResult,
    AgentSandbox,
    HostMcpConnectionState,
    HostMcpResult,
    HostMcpSetupPolicy,
)

Action = Literal["inspect", "register", "repair", "remove"]
AdapterFn = Callable[[Path], HostMcpResult]
ForceAdapterFn = Callable[[Path, bool], HostMcpResult]
ProfileAdapterFn = Callable[[Path, str | None], HostMcpResult]


class HostMcpAdapter(Protocol):
    HOST_KEY: str

    def inspect(self, home: Path) -> HostMcpResult: ...

    def register(self, home: Path) -> HostMcpResult: ...

    def remove(self, home: Path) -> HostMcpResult: ...


class AgentRunner(Protocol):
    def __call__(
        self,
        project_dir: Path,
        instructions: str,
        prompt: str,
        output_schema: dict[str, Any],
        *,
        sandbox: AgentSandbox,
        model: str | None = None,
        provider: str | None = None,
        profile: str | None = None,
    ) -> AgentRunResult: ...


@dataclass(frozen=True)
class HostMcpPolicy:
    host_key: str
    display_name: str
    setup_policy: HostMcpSetupPolicy
    agent_execution: AgentCapabilities = field(default_factory=AgentCapabilities)


@dataclass(frozen=True)
class HostMcpDefinition:
    adapter: HostMcpAdapter
    policy: HostMcpPolicy
    repair: AdapterFn | None = None
    force_register: ForceAdapterFn | None = None
    inspect_profile: ProfileAdapterFn | None = None
    register_profile: ProfileAdapterFn | None = None
    remove_profile: ProfileAdapterFn | None = None
    run_agent: AgentRunner | None = None


@dataclass(frozen=True)
class HostMcpAggregate:
    ok: bool
    results: list[HostMcpResult]

    @property
    def blocking_results(self) -> list[HostMcpResult]:
        return [result for result in self.results if result.blocking]

    def to_info(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "hosts": [result.to_info() for result in self.results],
            "blocking_hosts": [result.host_key for result in self.blocking_results],
        }

    def summary_lines(self) -> list[str]:
        return [
            f"{result.host_key}: {result.message}"
            + (f" Repair: {result.repair}" if result.repair and not result.ok else "")
            for result in self.results
        ]


def run_agent(
    host_key: str,
    project_dir: str | Path,
    instructions: str,
    prompt: str,
    output_schema: dict[str, Any],
    *,
    sandbox: AgentSandbox = "workspace-write",
    model: str | None = None,
    provider: str | None = None,
    profile: str | None = None,
) -> AgentRunResult:
    """Run a project agent through the same host registry used by Overview."""
    if not instructions.strip():
        return AgentRunResult(None, error="Project-agent instructions must not be empty.")
    error = agent_selection_error(
        host_key,
        sandbox=sandbox,
        model=model,
        provider=provider,
        profile=profile,
    )
    if error:
        return AgentRunResult(None, error=error)
    definition = _definition_by_key(host_key)
    if definition is None or definition.run_agent is None:
        return AgentRunResult(None, error=f"Host {host_key!r} does not support agent execution.")
    return definition.run_agent(
        Path(project_dir).expanduser().resolve(),
        instructions,
        prompt,
        output_schema,
        sandbox=sandbox,
        **{
            key: value
            for key, value in {"model": model, "provider": provider, "profile": profile}.items()
            if value is not None
        },
    )


def agent_selection_error(
    host_key: str,
    *,
    sandbox: AgentSandbox,
    model: str | None = None,
    provider: str | None = None,
    profile: str | None = None,
) -> str | None:
    """Validate declared native choices without claiming installation or model access."""
    definition = _definition_by_key(host_key)
    if definition is None or definition.run_agent is None:
        return f"Host {host_key!r} does not support headless agent execution."
    capability = definition.policy.agent_execution
    if sandbox not in capability.sandboxes:
        return f"Host {host_key!r} does not support {sandbox} execution. {capability.notes}"
    for key, value in {"model": model, "provider": provider, "profile": profile}.items():
        if value is not None and (not value.strip() or value.startswith("-")):
            return f"{key} must be a nonempty native selection, not a CLI option."
    if provider is not None and not capability.provider_selection:
        return f"Host {host_key!r} does not support a native provider selector."
    if profile is not None and not capability.profile_selection:
        return f"Host {host_key!r} does not support a native execution profile selector."
    if capability.requires_provider_model and (not model or not provider):
        return f"Host {host_key!r} requires explicit provider and model."
    if (
        model is not None
        and model not in capability.models
        and not (capability.custom_models or provider is not None)
    ):
        return f"Model {model!r} is not in the current {host_key} native model catalog."
    return None


def inspect_all(*, home: Path | None = None) -> HostMcpAggregate:
    home_dir = home or default_home()
    definitions = _definitions()
    with ThreadPoolExecutor(
        max_workers=len(definitions),
        thread_name_prefix="stackos-host-inspect",
    ) as executor:
        futures = [
            executor.submit(
                _call_adapter,
                definition.adapter.inspect,
                home_dir,
                definition.policy.host_key,
            )
            for definition in definitions
        ]
        results = [
            normalize_result(future.result(), definition.policy)
            for future, definition in zip(futures, definitions, strict=True)
        ]
    return _aggregate(results)


def repair_all(*, home: Path | None = None) -> HostMcpAggregate:
    home_dir = home or default_home()
    token_error = token_preflight(home_dir)
    if token_error:
        policy = HostMcpPolicy("all-hosts", "AI tool connections", "automatic")
        return _aggregate(
            [
                normalize_result(
                    HostMcpResult(
                        host_key="all-hosts",
                        surface="local-lifecycle",
                        status="token_missing",
                        message=token_error,
                        ok=False,
                        available=True,
                        blocking=True,
                        repair=(
                            "Run `stackos install` or desktop Repair before MCP host registration."
                        ),
                    ),
                    policy,
                )
            ]
        )
    results: list[HostMcpResult] = []
    for definition in _definitions():
        current = normalize_result(
            _call_adapter(
                definition.adapter.inspect,
                home_dir,
                definition.policy.host_key,
            ),
            definition.policy,
        )
        should_connect = (
            current.connection_state == "available"
            and definition.policy.setup_policy == "automatic"
        )
        should_repair = current.connection_state == "repair_needed" and current.repairable
        if should_connect or should_repair:
            lifecycle_action: Action = "register"
            if should_repair and definition.repair is not None:
                lifecycle_action = "repair"
            current = normalize_result(
                _invoke_adapter(definition, lifecycle_action, home_dir),
                definition.policy,
            )
        results.append(current)
    return _aggregate(results)


def remove_all(*, home: Path | None = None) -> HostMcpAggregate:
    home_dir = home or default_home()
    results = []
    for definition in _definitions():
        current = normalize_result(
            _call_adapter(
                definition.adapter.inspect,
                home_dir,
                definition.policy.host_key,
            ),
            definition.policy,
        )
        if current.selected and current.managed:
            current = normalize_result(
                _invoke_adapter(definition, "remove", home_dir),
                definition.policy,
            )
        results.append(current)
    return _aggregate(results)


def register_host(
    host_key: str,
    *,
    home: Path | None = None,
    force: bool = False,
    profile: str | None = None,
) -> HostMcpResult:
    return _single(host_key, action="register", home=home, force=force, profile=profile)


def remove_host(
    host_key: str,
    *,
    home: Path | None = None,
    profile: str | None = None,
) -> HostMcpResult:
    return _single(host_key, action="remove", home=home, profile=profile)


def inspect_host(
    host_key: str,
    *,
    home: Path | None = None,
    profile: str | None = None,
) -> HostMcpResult:
    return _single(host_key, action="inspect", home=home, profile=profile)


def _single(
    host_key: str,
    *,
    action: Action,
    home: Path | None,
    force: bool = False,
    profile: str | None = None,
) -> HostMcpResult:
    home_dir = home or default_home()
    definition = _definition_by_key(host_key)
    if definition is None:
        return normalize_result(
            HostMcpResult(
                host_key=host_key,
                surface="unknown",
                status="register_failed",
                message=f"Unknown MCP host {host_key!r}.",
                ok=False,
                available=False,
                blocking=True,
                repair="Use one of: codex, claude-code, claude-desktop, gemini-cli, hermes, grok.",
            ),
            HostMcpPolicy(host_key, host_key, "automatic"),
        )
    if profile is not None and definition.inspect_profile is None:
        return normalize_result(
            HostMcpResult(
                host_key=host_key,
                surface="host-lifecycle",
                status="register_failed",
                message=f"{definition.policy.display_name} does not support profile targeting.",
                ok=False,
                available=True,
                blocking=True,
                repair="Remove the profile selector and retry.",
            ),
            definition.policy,
        )
    if action == "inspect":
        return normalize_result(
            _invoke_adapter(definition, action, home_dir, profile=profile),
            definition.policy,
        )
    if action == "remove":
        current = normalize_result(
            _invoke_adapter(definition, "inspect", home_dir, profile=profile),
            definition.policy,
        )
        if current.selected and not current.managed:
            return current
        return normalize_result(
            _invoke_adapter(definition, action, home_dir, profile=profile),
            definition.policy,
        )
    return normalize_result(
        _invoke_adapter(
            definition,
            action,
            home_dir,
            force=force,
            profile=profile,
        ),
        definition.policy,
    )


def _definitions() -> tuple[HostMcpDefinition, ...]:
    return (
        HostMcpDefinition(
            codex,
            HostMcpPolicy(
                "codex",
                "ChatGPT / Codex",
                "automatic",
                AgentCapabilities(
                    sandboxes=("workspace-write", "read-only"),
                    models=("gpt-6-astra", "gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-luna"),
                    provider_selection=True,
                    auth_status_command=("codex", "login", "status"),
                    notes="Native sandbox; login status establishes credential presence only.",
                    sources=(
                        "https://learn.chatgpt.com/docs/models",
                        "https://learn.chatgpt.com/docs/cli/reference",
                    ),
                ),
            ),
            force_register=lambda home, force: codex.register(home, force=force),
            run_agent=codex.run_agent,
        ),
        HostMcpDefinition(
            claude_code,
            HostMcpPolicy(
                "claude-code",
                "Claude Code",
                "automatic",
                AgentCapabilities(
                    sandboxes=("workspace-write", "read-only"),
                    models=(
                        "claude-opus-5-5",
                        "claude-sonnet-5-5",
                        "claude-haiku-5-5",
                        "claude-fable-5-1",
                    ),
                    auth_status_command=("claude", "auth", "status"),
                    notes=(
                        "Review is file-only Read/Glob/Grep with MCP denied; "
                        "native tool controls, not an OS sandbox."
                    ),
                    sources=(
                        "https://code.claude.com/docs/en/cli-reference",
                        "https://code.claude.com/docs/en/headless",
                        "https://platform.claude.com/docs/en/models/overview",
                    ),
                ),
            ),
            run_agent=claude_code.run_agent,
        ),
        HostMcpDefinition(
            claude_desktop,
            HostMcpPolicy(
                "claude-desktop",
                "Claude Desktop",
                "automatic",
                AgentCapabilities(
                    notes="Claude Desktop does not support scripting, --print or output-format.",
                    sources=("https://code.claude.com/docs/en/desktop",),
                ),
            ),
        ),
        HostMcpDefinition(
            gemini_cli,
            HostMcpPolicy(
                "gemini-cli",
                "Gemini CLI",
                "automatic",
                AgentCapabilities(
                    sandboxes=("workspace-write",),
                    models=(
                        "gemini-3.1-pro-preview",
                        "gemini-3-pro-preview",
                        "gemini-3-flash-preview",
                        "gemini-2.5-pro",
                        "gemini-2.5-flash",
                    ),
                    notes=(
                        "Delivery uses auto_edit; unapproved shell calls are denied. "
                        "Strict read-only review and auth-status CLI are unverified."
                    ),
                    sources=(
                        "https://geminicli.com/docs/cli/headless/",
                        "https://geminicli.com/docs/cli/model/",
                        "https://geminicli.com/docs/cli/plan-mode/#non-interactive-execution",
                    ),
                ),
            ),
            run_agent=gemini_cli.run_agent,
        ),
        HostMcpDefinition(
            hermes,
            HostMcpPolicy(
                "hermes",
                "Hermes",
                "explicit",
                AgentCapabilities(
                    sandboxes=("workspace-write",),
                    custom_models=True,
                    provider_selection=True,
                    profile_selection=True,
                    requires_provider_model=True,
                    auth_status_command=("hermes", "auth", "status"),
                    notes=(
                        "Explicit provider/model; native approvals retained. "
                        "Strict read-only review is unsupported. "
                        "Installed shims may not be runnable."
                    ),
                    sources=(
                        "https://hermes-agent.nousresearch.com/docs/reference/cli-commands",
                        "https://hermes-agent.nousresearch.com/docs/user-guide/security",
                    ),
                ),
            ),
            run_agent=hermes.run_agent,
            repair=hermes.repair,
            inspect_profile=lambda home, profile: hermes.inspect_profile(
                home,
                profile=profile,
            ),
            register_profile=lambda home, profile: hermes.register(
                home,
                profile=profile,
            ),
            remove_profile=lambda home, profile: hermes.remove(
                home,
                profile=profile,
            ),
        ),
        HostMcpDefinition(
            grok,
            HostMcpPolicy(
                "grok",
                "Grok",
                "automatic",
                AgentCapabilities(
                    sandboxes=("workspace-write", "read-only"),
                    models=("grok-build",),
                    custom_models=True,
                    notes=(
                        "Native sandbox. Use grok models for installed CLI selections, "
                        "including configured aliases; grok-build is not the grok-4.7 API ID. "
                        "Auth presence is unverified."
                    ),
                    sources=(
                        "https://docs.x.ai/build/cli/reference",
                        "https://docs.x.ai/build/cli/headless-scripting",
                        "https://docs.x.ai/build/features/sandbox",
                        "https://docs.x.ai/build/features/permissions",
                    ),
                ),
            ),
            run_agent=grok.run_agent,
        ),
    )


def _definition_by_key(host_key: str) -> HostMcpDefinition | None:
    for definition in _definitions():
        if definition.policy.host_key == host_key:
            return definition
    return None


def _call_adapter(
    fn: AdapterFn,
    home: Path,
    host_key: str | None = None,
) -> HostMcpResult:
    try:
        return fn(home)
    except Exception as exc:
        resolved_host_key = host_key or (
            getattr(fn, "__module__", "host").rsplit(".", maxsplit=1)[-1].replace("_", "-")
        )
        return HostMcpResult(
            host_key=resolved_host_key,
            surface="unknown",
            status="register_failed",
            message=f"Host MCP adapter failed: {type(exc).__name__}.",
            ok=False,
            available=True,
            blocking=True,
            repair=(
                "Run `stackos install --mcp-only` or inspect the "
                f"{MCP_SERVER_NAME} MCP host config."
            ),
        )


def _invoke_adapter(
    definition: HostMcpDefinition,
    action: Action,
    home: Path,
    *,
    force: bool = False,
    profile: str | None = None,
) -> HostMcpResult:
    adapter = definition.adapter
    if action == "repair":
        if definition.repair is None:
            raise ValueError(f"{definition.policy.host_key} has no bulk repair capability")
        return _call_adapter(
            definition.repair,
            home,
            definition.policy.host_key,
        )
    inspect_profile = definition.inspect_profile
    if action == "inspect" and profile is not None and inspect_profile is not None:
        return _call_adapter(
            lambda current_home: inspect_profile(current_home, profile),
            home,
            definition.policy.host_key,
        )
    register_profile = definition.register_profile
    if action == "register" and register_profile is not None:
        return _call_adapter(
            lambda current_home: register_profile(current_home, profile),
            home,
            definition.policy.host_key,
        )
    remove_profile = definition.remove_profile
    if action == "remove" and remove_profile is not None:
        return _call_adapter(
            lambda current_home: remove_profile(current_home, profile),
            home,
            definition.policy.host_key,
        )
    force_register = definition.force_register
    if action == "register" and force_register is not None:
        return _call_adapter(
            lambda current_home: force_register(current_home, force),
            home,
            definition.policy.host_key,
        )
    fn = {
        "inspect": adapter.inspect,
        "register": adapter.register,
        "remove": adapter.remove,
    }[action]
    return _call_adapter(fn, home, definition.policy.host_key)


def _aggregate(results: list[HostMcpResult]) -> HostMcpAggregate:
    return HostMcpAggregate(
        ok=not any(result.blocking for result in results),
        results=results,
    )


def normalize_result(result: HostMcpResult, policy: HostMcpPolicy) -> HostMcpResult:
    """Apply the shared host lifecycle policy to adapter-native facts."""

    configured_statuses = {
        "registered_current",
        "registered",
        "registered_stale",
        "registered_unsafe",
        "registered_unmanaged",
        "restart_required",
        "shadowed",
    }
    selected = (
        result.selected if result.selected is not None else result.status in configured_statuses
    )
    managed = (
        result.managed
        if result.managed is not None
        else result.status in {"registered_current", "registered", "restart_required"}
    )

    state: HostMcpConnectionState
    if result.status in {"registered_current", "registered"}:
        state = "connected"
    elif result.status in {"available_unregistered", "removed"}:
        state = "available"
    elif result.status == "absent":
        state = "unavailable"
    elif result.status == "restart_required":
        state = "restart_required"
    elif result.status == "unsupported_host_version":
        state = "update_required"
    elif result.status == "registered_stale" and managed:
        state = "repair_needed"
    elif result.status in {
        "registered_stale",
        "registered_unsafe",
        "registered_unmanaged",
        "shadowed",
        "config_unreadable",
    }:
        state = "review_required"
    else:
        state = "error"

    labels: dict[HostMcpConnectionState, str] = {
        "connected": "Connected",
        "available": "Available",
        "repair_needed": "Repair needed",
        "review_required": "Review required",
        "update_required": "Update needed",
        "restart_required": "Restart needed",
        "unavailable": "Not detected",
        "error": "Setup failed",
    }
    repairable = state == "repair_needed" and bool(selected and managed)
    blocking = state in {
        "repair_needed",
        "review_required",
        "update_required",
        "restart_required",
        "error",
    } and bool(selected or result.host_key == "all-hosts" or (state == "error" and result.blocking))
    ok = not blocking
    return replace(
        result,
        agent_execution={
            **asdict(policy.agent_execution),
            "credential_status": "not_checked",
            "model_access": "not_checked",
        },
        display_name=policy.display_name,
        connection_state=state,
        status_label=labels[state],
        selected=selected,
        managed=managed,
        repairable=repairable,
        setup_policy=policy.setup_policy,
        ok=ok,
        blocking=blocking,
        advisory=not blocking and state != "connected",
    )
