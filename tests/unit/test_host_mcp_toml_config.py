from __future__ import annotations

import os
import stat
import tomllib
from pathlib import Path

import pytest

from stackos.host_mcp import toml_config

ENTRY = {"command": "stackos", "args": ["mcp-bridge", "--runtime", "grok"]}
ENTRY_LINES = 'command = "stackos"\nargs = ["mcp-bridge", "--runtime", "grok"]\n'


@pytest.mark.parametrize(
    "contents",
    [
        '# global\n["mcp_servers"."stackos"]\n'
        + ENTRY_LINES
        + '\n# Other server\n[mcp_servers."other.server"]\ncommand = "other" # inline\n',
        '# global\n[mcp_servers]\nstackos = {command = "stackos", '
        'args = ["mcp-bridge", "--runtime", "grok"]}\n'
        '# Other server\n"other.server" = { command = "other" } # inline\n',
        '# global\nmcp_servers = {stackos = {command = "stackos", '
        'args = ["mcp-bridge", "--runtime", "grok"]}, '
        '"other.server" = {command = "other"}} # inline\n# Other server\n',
        "# global\n[mcp_servers.stackos]\n"
        + ENTRY_LINES
        + "# User interface\n[ui]\ncompact = true\n"
        '# Other server\n[mcp_servers."other.server"]\ncommand = "other" # inline\n',
        '# global\nmcp_servers.stackos.command = "stackos"\n'
        'mcp_servers.stackos.args = ["mcp-bridge", "--runtime", "grok"]\n'
        '# Other server\nmcp_servers."other.server".command = "other" # inline\n',
        "# global\n[mcp_servers.stackos]\n"
        + ENTRY_LINES
        + '[mcp_servers.stackos.env]\nTOKEN = "synthetic-token"\n'
        '# Other server\n[mcp_servers."other.server"]\ncommand = "other" # inline\n',
    ],
)
def test_removes_only_expected_entry_preserving_comments(tmp_path: Path, contents: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(contents)
    expected = tomllib.loads(contents)
    inspected_entry = expected["mcp_servers"]["stackos"]
    del expected["mcp_servers"]["stackos"]

    assert toml_config.remove_mcp_server(path, "stackos", expected_entry=inspected_entry) == (
        True,
        None,
        True,
    )

    result = path.read_text()
    assert tomllib.loads(result) == expected
    for comment in ("# global", "# Other server", "# inline"):
        assert result.count(comment) == 1
    if "# User interface" in contents:
        assert "# User interface\n[ui]" in result
    if '[mcp_servers."other.server"]' in contents:
        assert '# Other server\n[mcp_servers."other.server"]' in result


@pytest.mark.parametrize(
    "contents", [None, "# empty\n", '[mcp_servers.other]\ncommand = "other"\n']
)
def test_absent_file_or_entry_is_a_read_only_noop(tmp_path: Path, contents: str | None) -> None:
    path = tmp_path / "config.toml"
    if contents is not None:
        path.write_text(contents)
    before = path.stat() if path.exists() else None

    assert toml_config.remove_mcp_server(path, "stackos", expected_entry=ENTRY) == (
        True,
        None,
        False,
    )
    assert (path.stat() if path.exists() else None) == before
    if contents is not None:
        assert path.read_text() == contents


def test_removal_is_idempotent_and_preserves_permissions(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[mcp_servers.stackos]\n" + ENTRY_LINES)
    path.chmod(0o640)
    assert toml_config.remove_mcp_server(path, "stackos", expected_entry=ENTRY) == (
        True,
        None,
        True,
    )
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    before = path.stat()
    assert toml_config.remove_mcp_server(path, "stackos", expected_entry=ENTRY) == (
        True,
        None,
        False,
    )
    assert path.stat() == before


@pytest.mark.parametrize(
    "contents",
    [
        b'[broken = "private-config-value"',
        b"\xffprivate-config-value",
        b'mcp_servers = "private-config-value"',
        b'[mcp_servers]\nstackos = "private-config-value"',
        b'[[mcp_servers.stackos]]\ncommand = "private-config-value"',
    ],
)
def test_invalid_config_is_preserved_and_errors_are_sanitized(
    tmp_path: Path, contents: bytes
) -> None:
    path = tmp_path / "config.toml"
    path.write_bytes(contents)
    ok, error, changed = toml_config.remove_mcp_server(path, "stackos", expected_entry=ENTRY)
    assert not ok and not changed and error
    assert "private-config-value" not in error
    assert path.read_bytes() == contents


@pytest.mark.parametrize("extra", ['command = "other"\n', "enabled = 1\n"])
def test_changed_entry_is_not_removed(tmp_path: Path, extra: str) -> None:
    path = tmp_path / "config.toml"
    contents = "[mcp_servers.stackos]\n" + extra
    path.write_text(contents)
    expected = {"enabled": True} if "enabled" in extra else ENTRY
    ok, error, changed = toml_config.remove_mcp_server(path, "stackos", expected_entry=expected)
    assert not ok and not changed and "changed since inspection" in error
    assert path.read_text() == contents


def test_config_symlink_updates_target_and_keeps_link(tmp_path: Path) -> None:
    target = tmp_path / "stored.toml"
    target.write_text("[mcp_servers.stackos]\n" + ENTRY_LINES)
    target.chmod(0o600)
    path = tmp_path / "config.toml"
    path.symlink_to(target.name)
    link_stat = path.lstat()

    assert toml_config.remove_mcp_server(path, "stackos", expected_entry=ENTRY) == (
        True,
        None,
        True,
    )
    assert path.is_symlink() and path.lstat() == link_stat
    assert "stackos" not in tomllib.loads(target.read_text()).get("mcp_servers", {})
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.parametrize("symlink", [False, True])
def test_read_access_time_changes_do_not_block_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, symlink: bool
) -> None:
    target = tmp_path / "stored.toml"
    target.write_text("[mcp_servers.stackos]\n" + ENTRY_LINES)
    path = tmp_path / "config.toml" if symlink else target
    if symlink:
        path.symlink_to(target.name)
    original_read_bytes = Path.read_bytes
    original_lstat = Path.lstat
    reads = 0

    def read_bytes_with_access_time(self: Path) -> bytes:
        nonlocal reads
        contents = original_read_bytes(self)
        if self == target:
            reads += 1
        return contents

    def lstat_with_access_time(self: Path) -> os.stat_result:
        info = original_lstat(self)
        if self not in (path, target) or not reads:
            return info
        # Simulate a filesystem read updating atime only, without using utime
        # (which itself changes ctime and would correctly look like an edit).
        fields = list(info)
        fields[7] += reads
        return os.stat_result(
            fields,
            {
                "st_atime_ns": info.st_atime_ns + reads * 1_000_000_000,
                "st_mtime_ns": info.st_mtime_ns,
                "st_ctime_ns": info.st_ctime_ns,
            },
        )

    monkeypatch.setattr(Path, "read_bytes", read_bytes_with_access_time)
    monkeypatch.setattr(Path, "lstat", lstat_with_access_time)

    assert toml_config.remove_mcp_server(path, "stackos", expected_entry=ENTRY) == (
        True,
        None,
        True,
    )
    assert reads >= 2
    assert "stackos" not in tomllib.loads(target.read_text()).get("mcp_servers", {})
    assert path.is_symlink() == symlink


@pytest.mark.parametrize("mutation", ["contents", "target", "link"])
def test_concurrent_changes_are_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    target = tmp_path / "stored.toml"
    original = "[mcp_servers.stackos]\n" + ENTRY_LINES
    target.write_text(original)
    path = tmp_path / "config.toml"
    path.symlink_to(target.name)
    other = tmp_path / "other.toml"
    other.write_text(original)
    original_mkstemp = toml_config.tempfile.mkstemp

    def concurrent_mkstemp(*args, **kwargs):
        result = original_mkstemp(*args, **kwargs)
        if mutation == "contents":
            target.write_text(original + "# concurrent user edit\n")
        elif mutation == "target":
            os.replace(other, target)
        else:
            path.unlink()
            path.symlink_to(other.name)
        return result

    monkeypatch.setattr(toml_config.tempfile, "mkstemp", concurrent_mkstemp)
    ok, error, changed = toml_config.remove_mcp_server(path, "stackos", expected_entry=ENTRY)
    assert not ok and not changed and "changed during cleanup" in error
    assert target.read_text() == original + (
        "# concurrent user edit\n" if mutation == "contents" else ""
    )
    assert path.is_symlink()
    if mutation == "link":
        assert path.resolve() == other
    assert not list(tmp_path.glob(".stored.toml.*"))


def test_dangling_symlink_is_preserved(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.symlink_to("missing.toml")
    ok, error, changed = toml_config.remove_mcp_server(path, "stackos", expected_entry=ENTRY)
    assert not ok and not changed and error
    assert path.is_symlink()


def test_atomic_replace_failure_preserves_original_and_removes_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    original = "[mcp_servers.stackos]\n" + ENTRY_LINES
    path.write_text(original)

    def fail_replace(*_args):
        raise OSError("private-config-value")

    monkeypatch.setattr(toml_config.os, "replace", fail_replace)
    ok, error, changed = toml_config.remove_mcp_server(path, "stackos", expected_entry=ENTRY)
    assert not ok and not changed and error and "private-config-value" not in error
    assert path.read_text() == original
    assert not list(tmp_path.glob(".config.toml.*"))
