from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import pytest
from pydantic import SecretStr

from stackos.host_mcp.adapters import codex
from stackos.local_runtime.connection import LocalBinding, LocalConnectionStore
from stackos.local_runtime.source import check_codex_source


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def source(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Source proof")
    git(root, "config", "user.email", "proof@example.invalid")
    (root / "source.txt").write_text("accepted\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "accepted")
    info = root.stat()
    binding = LocalBinding(
        server_url="http://127.0.0.1:19876",
        environment_id="disposable-source-proof",
        user_id="synthetic-owner",
        project_id=1,
        connection_id="synthetic-computer",
        credential=SecretStr("synthetic-credential"),
        root=str(root),
        root_device=info.st_dev,
        root_inode=info.st_ino,
    )
    store = LocalConnectionStore(tmp_path / "private-state")
    rows = [
        {
            "name": "stackos",
            "enabled": True,
            "transport": {
                "type": "stdio",
                "command": sys.executable,
                "args": [
                    "-m",
                    "stackos",
                    "mcp-bridge",
                    "--runtime",
                    "codex",
                    "--workspace-root",
                    str(root),
                ],
                "cwd": str(root),
                "env": {},
            },
        },
        {
            "name": "serena",
            "transport": {
                "type": "stdio",
                "command": sys.executable,
                "args": [],
                "cwd": str(root),
                "env": {"SERENA_PROJECT": str(root)},
            },
        },
    ]
    config = tmp_path / "native-servers.json"
    config.write_text(json.dumps(rows))
    native = tmp_path / "native-codex"
    native.write_text(
        f"#!{sys.executable}\nimport pathlib, sys\n"
        f"assert pathlib.Path.cwd() == pathlib.Path({str(root)!r})\n"
        "assert sys.argv[-3:] == ['mcp', 'list', '--json']\n"
        f"print(pathlib.Path({str(config)!r}).read_text())\n"
    )
    native.chmod(0o700)
    monkeypatch.setattr(codex, "_select_codex_bin", lambda: str(native))
    return root, binding, store, config, rows, native


def check(source, **kwargs):
    _, binding, store, *_ = source
    return check_codex_source(store, binding, **kwargs)


def test_local_source_measures_actual_root_git_host_and_routes(source):
    root, _, _, _, _, native = source
    sha = git(root, "rev-parse", "HEAD")
    result = check(source, candidate={"base_commit_sha": sha, "commit_sha": sha})
    assert result["project_dir"] == str(root)
    assert result["candidate"] == {"base_commit_sha": sha, "commit_sha": sha}
    assert result["native_executable"] == str(native)
    assert result["git_common_dir"] == str(root / ".git")
    assert result["mcp_servers"] == ["serena", "stackos"]
    assert "synthetic-credential" not in json.dumps(result)


@pytest.mark.parametrize("change", ["dirty", "head", "wrong-sha", "bad-sha", "bad-base"])
def test_candidate_drift_and_invalid_evidence_refuse(source, change):
    root, *_ = source
    sha = git(root, "rev-parse", "HEAD")
    candidate = {"base_commit_sha": sha, "commit_sha": sha}
    check(source, candidate=candidate)
    if change in {"dirty", "head"}:
        (root / "source.txt").write_text("changed\n")
        if change == "head":
            git(root, "commit", "-qam", "new head")
    elif change == "wrong-sha":
        candidate["commit_sha"] = "f" * 40
    else:
        candidate["base_commit_sha" if change == "bad-base" else "commit_sha"] = "--all"
    with pytest.raises(ValueError):
        check(source, candidate=candidate)


@pytest.mark.parametrize("change", ["missing", "replaced", "subdirectory"])
def test_missing_replaced_or_wrong_git_root_refuses(source, change):
    root, binding, store, *_ = source
    if change == "missing":
        root.rename(root.with_name("original"))
    elif change == "replaced":
        root.rename(root.with_name("original"))
        root.mkdir()
    else:
        root = root / "subdirectory"
        root.mkdir()
        info = root.stat()
        binding = binding.model_copy(
            update={
                "root": str(root),
                "root_device": info.st_dev,
                "root_inode": info.st_ino,
            }
        )
    with pytest.raises(ValueError):
        check_codex_source(store, binding)


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "disabled",
        "http",
        "command",
        "cwd",
        "workspace",
        "duplicate-workspace",
        "missing-workspace",
        "serena",
        "tool-missing",
    ],
)
def test_actual_native_tool_identity_refuses_wrong_or_missing_routes(source, change):
    root, _, _, config, rows, _ = source
    check(source)
    bridge = rows[0]
    if change == "missing":
        rows.pop(0)
    elif change == "disabled":
        bridge["enabled"] = False
    elif change == "http":
        bridge["transport"] = {"type": "streamable_http", "url": "http://localhost:1"}
    elif change == "command":
        bridge["transport"]["args"] = []
    elif change == "cwd":
        bridge["transport"]["cwd"] = str(root.parent)
    elif change == "workspace":
        bridge["transport"]["args"][-1] = str(root.parent)
    elif change == "duplicate-workspace":
        bridge["transport"]["args"].extend(["--workspace-root", str(root)])
    elif change == "missing-workspace":
        del bridge["transport"]["args"][-2:]
    elif change == "serena":
        rows[1]["transport"]["env"]["SERENA_PROJECT"] = str(root.parent)
    else:
        bridge["transport"]["command"] = str(root / "missing-stackos")
    config.write_text(json.dumps(rows))
    with pytest.raises(ValueError):
        check(source)


def test_source_rechecks_frozen_route_digest_and_host_availability(source, tmp_path, monkeypatch):
    root, *_ = source
    route = tmp_path / "routes.toml"
    route.write_text(f'[mcp_servers.stackos]\ncwd = "{root}"\n')
    reference = {"path": str(route), "sha256": hashlib.sha256(route.read_bytes()).hexdigest()}
    assert check(source, mcp_config_ref=reference)["mcp_config_ref"] == reference
    route.write_text("changed")
    with pytest.raises(ValueError, match="digest mismatch"):
        check(source, mcp_config_ref=reference)
    monkeypatch.setattr(codex, "_select_codex_bin", lambda: None)
    with pytest.raises(ValueError, match="No usable Codex"):
        check(source)


def test_non_git_folder_has_no_invented_commit_and_explicit_candidate_refuses(source):
    root, binding, store, *_ = source
    # Keep the native fixture's cwd assertion and retire only this disposable repo's metadata.
    (root / ".git").rename(root.parent / "retired-git")
    assert check(source)["candidate"] == {}
    assert check(source, candidate={})["candidate"] == {}
    with pytest.raises(ValueError):
        check_codex_source(store, binding, candidate={"commit_sha": "a" * 40})


def test_native_selection_is_validated_without_model_fallback(source):
    with pytest.raises(ValueError, match="catalog"):
        check(source, model="nonexistent-model")


@pytest.mark.parametrize("change", ["head", "dirty", "folder", "route"])
def test_source_is_revalidated_after_native_discovery(source, monkeypatch, tmp_path, change):
    root, _, _, _, _, _ = source
    sha = git(root, "rev-parse", "HEAD")
    route = tmp_path / "routes.toml"
    route.write_text(f'[mcp_servers.stackos]\ncwd = "{root}"\n')
    reference = {"path": str(route), "sha256": hashlib.sha256(route.read_bytes()).hexdigest()}
    native_read = codex._run_codex
    reads = 0

    def mutate_after_read(*args, **kwargs):
        nonlocal reads
        result = native_read(*args, **kwargs)
        reads += 1
        if reads == 1:
            return result
        if change in {"head", "dirty"}:
            (root / "source.txt").write_text("concurrent change")
            if change == "head":
                git(root, "commit", "-qam", "concurrent change")
        elif change == "folder":
            root.rename(root.with_name("original"))
            root.mkdir()
        else:
            route.write_text("concurrent change")
        return result

    monkeypatch.setattr(codex, "_run_codex", mutate_after_read)
    with pytest.raises(ValueError):
        check(source, candidate={"commit_sha": sha}, mcp_config_ref=reference)


def test_missing_declared_tool_after_native_discovery_refuses(source, monkeypatch, tmp_path):
    _, _, _, _, rows, _ = source
    route = tmp_path / "routes.toml"
    route.write_text('[mcp_servers.serena]\nenv = {SERENA_PROJECT_NAME = "source-proof"}')
    reference = {"path": str(route), "sha256": hashlib.sha256(route.read_bytes()).hexdigest()}
    reads = 0

    def native_read(*args, **kwargs):
        nonlocal reads
        reads += 1
        return subprocess.CompletedProcess([], 0, json.dumps(rows if reads == 1 else rows[:1]), "")

    monkeypatch.setattr(codex, "_run_codex", native_read)
    with pytest.raises(ValueError, match="identity"):
        check(source, mcp_config_ref=reference)


def test_unborn_repository_retains_optional_candidate_contract(source):
    root, *_ = source
    git(root, "checkout", "--orphan", "unborn")
    assert check(source)["candidate"] == {}


def test_candidate_must_descend_from_supplied_base(source):
    root, *_ = source
    old = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "--orphan", "other-history")
    git(root, "commit", "-qm", "other history")
    current = git(root, "rev-parse", "HEAD")
    with pytest.raises(ValueError):
        check(source, candidate={"base_commit_sha": old, "commit_sha": current})


def test_current_candidate_accepts_a_real_descendant_and_canonicalizes_short_sha(source):
    root, *_ = source
    old = git(root, "rev-parse", "HEAD")
    (root / "source.txt").write_text("descendant")
    git(root, "commit", "-qam", "descendant")
    current = git(root, "rev-parse", "HEAD")
    assert check(source, candidate={"base_commit_sha": old[:12], "commit_sha": current[:12]})[
        "candidate"
    ] == {"base_commit_sha": old, "commit_sha": current}


@pytest.mark.parametrize("candidate", [[], {"unexpected": "a" * 40}, {"commit_sha": None}])
def test_malformed_candidate_refuses(source, candidate):
    with pytest.raises(ValueError, match="invalid local Git candidate"):
        check(source, candidate=candidate)


@pytest.mark.parametrize("output", ["not-json private-value", "{}", '[{"name": "stackos"}]'])
def test_invalid_native_discovery_never_echoes_config(source, monkeypatch, output):
    monkeypatch.setattr(
        codex,
        "_run_codex",
        lambda *a, **kw: subprocess.CompletedProcess([], 0, output, "private-error"),
    )
    with pytest.raises(ValueError) as error:
        check(source)
    assert "private" not in str(error.value)
