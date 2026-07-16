"""FTP connector tests through the generic action executor."""

from __future__ import annotations

import asyncio
import ftplib
import json
import posixpath
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest
from sqlmodel import Session

from stackos.actions import ActionRepository
from stackos.auth_providers import AuthRepository
from stackos.repositories.base import ConflictError
from stackos.repositories.projects import IntegrationCredentialRepository


class _FakeFTP:
    instances: ClassVar[list[_FakeFTP]] = []
    server_dirs: ClassVar[set[str]] = {"/"}
    server_files: ClassVar[dict[str, bytes]] = {}
    server_symlinks: ClassVar[set[str]] = set()
    malicious_children: ClassVar[dict[str, list[str]]] = {}

    def __init__(
        self,
        *,
        timeout: float | None = None,
        encoding: str = "utf-8",
        **_kwargs: Any,
    ) -> None:
        self.timeout = timeout
        self.encoding = encoding
        self.cwd_path = "/"
        self.calls: list[tuple[Any, ...]] = []
        self.__class__.instances.append(self)

    @classmethod
    def reset(cls) -> None:
        cls.instances.clear()
        cls.server_dirs = {"/"}
        cls.server_files = {}
        cls.server_symlinks = set()
        cls.malicious_children = {}

    def _path(self, value: str) -> str:
        if value.startswith("/"):
            return posixpath.normpath(value)
        return posixpath.normpath(posixpath.join(self.cwd_path, value))

    def connect(self, host: str, port: int, timeout: float | None = None) -> str:
        self.calls.append(("connect", host, port, timeout))
        return "220 ready"

    def auth(self) -> str:
        self.calls.append(("auth",))
        return "234 AUTH TLS"

    def login(self, username: str, password: str) -> str:
        self.calls.append(("login", username, password))
        return "230 logged in"

    def prot_p(self) -> str:
        self.calls.append(("prot_p",))
        return "200 protected"

    def set_pasv(self, value: bool) -> None:
        self.calls.append(("set_pasv", value))

    def pwd(self) -> str:
        self.calls.append(("pwd",))
        return self.cwd_path

    def cwd(self, path: str) -> str:
        resolved = self._path(path)
        self.calls.append(("cwd", resolved))
        if resolved not in self.__class__.server_dirs:
            raise ftplib.error_perm("550 directory unavailable")
        self.cwd_path = resolved
        return "250 changed"

    def mkd(self, path: str) -> str:
        resolved = self._path(path)
        self.calls.append(("mkd", resolved))
        parent = posixpath.dirname(resolved) or "/"
        if parent not in self.__class__.server_dirs:
            raise ftplib.error_perm("550 parent unavailable")
        self.__class__.server_dirs.add(resolved)
        return resolved

    def storbinary(
        self,
        command: str,
        file_obj: Any,
        blocksize: int = 8192,
        callback: Any | None = None,
    ) -> str:
        _, raw_path = command.split(" ", 1)
        path = self._path(raw_path)
        self.calls.append(("storbinary", path))
        chunks: list[bytes] = []
        while chunk := file_obj.read(blocksize):
            chunks.append(chunk)
            if callback is not None:
                callback(chunk)
        self.__class__.server_files[path] = b"".join(chunks)
        return "226 stored"

    def retrbinary(
        self,
        command: str,
        callback: Any,
        blocksize: int = 8192,
    ) -> str:
        del blocksize
        _, raw_path = command.split(" ", 1)
        path = self._path(raw_path)
        self.calls.append(("retrbinary", path))
        try:
            payload = self.__class__.server_files[path]
        except KeyError as exc:
            raise ftplib.error_perm("550 file unavailable") from exc
        callback(payload)
        return "226 retrieved"

    def mlsd(
        self,
        path: str = "",
        facts: list[str] | None = None,
    ) -> Iterator[tuple[str, dict[str, str]]]:
        del facts
        resolved = self._path(path or self.cwd_path)
        self.calls.append(("mlsd", resolved))
        if resolved not in self.__class__.server_dirs:
            raise ftplib.error_perm("550 directory unavailable")
        for name in self.__class__.malicious_children.get(resolved, []):
            yield name, {"type": "file", "size": "4", "modify": "20260715000000"}
        prefix = resolved.rstrip("/") + "/"
        children: dict[str, dict[str, str]] = {}
        for directory in self.__class__.server_dirs:
            if directory == resolved or not directory.startswith(prefix):
                continue
            remainder = directory[len(prefix) :]
            if "/" not in remainder:
                children[remainder] = {"type": "dir", "modify": "20260715000000"}
        for file_path, payload in self.__class__.server_files.items():
            if not file_path.startswith(prefix):
                continue
            remainder = file_path[len(prefix) :]
            if "/" not in remainder:
                children[remainder] = {
                    "type": "file",
                    "size": str(len(payload)),
                    "modify": "20260715000000",
                }
        for link_path in self.__class__.server_symlinks:
            if not link_path.startswith(prefix):
                continue
            remainder = link_path[len(prefix) :]
            if "/" not in remainder:
                children[remainder] = {
                    "type": "OS.unix=slink",
                    "modify": "20260715000000",
                }
        yield from sorted(children.items())

    def nlst(self, path: str = "") -> list[str]:
        resolved = self._path(path or self.cwd_path)
        self.calls.append(("nlst", resolved))
        return [name for name, _facts in self.mlsd(resolved)]

    def size(self, path: str) -> int | None:
        resolved = self._path(path)
        self.calls.append(("size", resolved))
        if resolved in self.__class__.server_files:
            return len(self.__class__.server_files[resolved])
        raise ftplib.error_perm("550 file unavailable")

    def sendcmd(self, command: str) -> str:
        verb, raw_path = command.split(" ", 1)
        assert verb == "MLST"
        path = self._path(raw_path)
        self.calls.append(("mlst", path))
        if path in self.__class__.server_dirs:
            return f"250-Listing\n type=dir; {posixpath.basename(path)}\n250 End"
        if path in self.__class__.server_files:
            size = len(self.__class__.server_files[path])
            return f"250-Listing\n type=file;size={size}; {posixpath.basename(path)}\n250 End"
        if path in self.__class__.server_symlinks:
            return f"250-Listing\n type=OS.unix=slink; {posixpath.basename(path)}\n250 End"
        raise ftplib.error_perm("550 path unavailable")

    def quit(self) -> str:
        self.calls.append(("quit",))
        return "221 bye"

    def close(self) -> None:
        self.calls.append(("close",))


class _FakeFTPTLS(_FakeFTP):
    instances: ClassVar[list[_FakeFTPTLS]] = []
    server_dirs: ClassVar[set[str]] = {"/"}
    server_files: ClassVar[dict[str, bytes]] = {}
    server_symlinks: ClassVar[set[str]] = set()
    malicious_children: ClassVar[dict[str, list[str]]] = {}


class _FallbackFTPTLS(_FakeFTPTLS):
    instances: ClassVar[list[_FallbackFTPTLS]] = []
    server_dirs: ClassVar[set[str]] = {"/"}
    server_files: ClassVar[dict[str, bytes]] = {}
    server_symlinks: ClassVar[set[str]] = set()
    malicious_children: ClassVar[dict[str, list[str]]] = {}

    def mlsd(
        self,
        path: str = "",
        facts: list[str] | None = None,
    ) -> Iterator[tuple[str, dict[str, str]]]:
        del path, facts
        raise ftplib.error_perm("500 MLSD unsupported")

    def sendcmd(self, command: str) -> str:
        self.calls.append(("mlst_unsupported", command))
        raise ftplib.error_perm("500 MLST unsupported")

    def nlst(self, path: str = "") -> list[str]:
        resolved = self._path(path or self.cwd_path)
        self.calls.append(("nlst", resolved))
        prefix = resolved.rstrip("/") + "/"
        children = {
            item
            for item in (*self.__class__.server_dirs, *self.__class__.server_files)
            if item != resolved and item.startswith(prefix) and "/" not in item[len(prefix) :]
        }
        return sorted(children)

    def retrlines(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("LIST must never be used or parsed")


class _FallbackCycleFTPTLS(_FallbackFTPTLS):
    instances: ClassVar[list[_FallbackCycleFTPTLS]] = []
    server_dirs: ClassVar[set[str]] = {"/", "/cycle"}
    server_files: ClassVar[dict[str, bytes]] = {}
    server_symlinks: ClassVar[set[str]] = set()
    malicious_children: ClassVar[dict[str, list[str]]] = {}

    def cwd(self, path: str) -> str:
        resolved = self._path(path)
        self.calls.append(("cwd", resolved))
        if resolved == "/cycle/loop":
            self.cwd_path = "/cycle"
            return "250 changed"
        if resolved not in self.__class__.server_dirs:
            raise ftplib.error_perm("550 directory unavailable")
        self.cwd_path = resolved
        return "250 changed"

    def nlst(self, path: str = "") -> list[str]:
        resolved = self._path(path or self.cwd_path)
        self.calls.append(("nlst", resolved))
        return ["/cycle/loop"] if resolved == "/cycle" else []


class _FailingDownloadFTPTLS(_FakeFTPTLS):
    instances: ClassVar[list[_FailingDownloadFTPTLS]] = []
    server_dirs: ClassVar[set[str]] = {"/"}
    server_files: ClassVar[dict[str, bytes]] = {}
    server_symlinks: ClassVar[set[str]] = set()
    malicious_children: ClassVar[dict[str, list[str]]] = {}

    def retrbinary(
        self,
        command: str,
        callback: Any,
        blocksize: int = 8192,
    ) -> str:
        del blocksize
        _, raw_path = command.split(" ", 1)
        path = self._path(raw_path)
        self.calls.append(("retrbinary", path))
        callback(b"partial")
        raise ftplib.error_temp("426 transfer aborted")


def _credential_ref(
    session: Session,
    project_id: int,
    *,
    tls_mode: str = "explicit",
) -> str:
    ActionRepository(session).describe(action_ref="utils.ftp.directory.list")
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="ftp",
        secret_payload=json.dumps({"password": "ftp-secret"}).encode(),
        config_json={
            "host": "ftp.example.test",
            "port": 21,
            "tls_mode": tls_mode,
            "username": "deploy",
            "passive_mode": True,
        },
    )
    status = AuthRepository(session).status(project_id=project_id, provider_key="ftp")
    return status.connections[0].credential_ref


def _patch_ftps(monkeypatch: pytest.MonkeyPatch) -> None:
    import stackos.actions.ftp as ftp_module

    _FakeFTPTLS.reset()
    monkeypatch.setattr(ftp_module.ftplib, "FTP_TLS", _FakeFTPTLS)


def test_ftp_browse_upload_and_download_recursive_arbitrary_paths(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_ftps(monkeypatch)
    credential_ref = _credential_ref(session, project_id)
    source = tmp_path / "operator-selected" / "site"
    (source / "assets").mkdir(parents=True)
    (source / "empty").mkdir()
    (source / "index.html").write_text("home", encoding="utf-8")
    (source / "assets" / "app.js").write_text("app", encoding="utf-8")
    unrelated_asset_dir = tmp_path / "generated-assets-not-used"
    unrelated_asset_dir.mkdir()
    repo = ActionRepository(session, asset_dir=unrelated_asset_dir)

    uploaded = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="utils.ftp.file.upload",
            input_json={
                "items": [{"local_path": str(source), "remote_path": "/public/site"}],
                "conflict_policy": "overwrite",
                "error_policy": "stop",
                "follow_symlinks": False,
            },
            credential_ref=credential_ref,
        )
    ).data

    assert _FakeFTPTLS.server_files["/public/site/index.html"] == b"home"
    assert _FakeFTPTLS.server_files["/public/site/assets/app.js"] == b"app"
    assert "/public/site/empty" in _FakeFTPTLS.server_dirs
    assert uploaded.output_json["completed_count"] == 2
    calls = _FakeFTPTLS.instances[0].calls
    assert ("auth",) in calls
    assert ("prot_p",) in calls
    assert calls.index(("prot_p",)) < next(
        index for index, call in enumerate(calls) if call[0] == "storbinary"
    )

    browsed = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="utils.ftp.directory.list",
            input_json={"remote_path": "/public/site"},
            credential_ref=credential_ref,
        )
    ).data
    by_name = {item["name"]: item for item in browsed.output_json["entries"]}
    assert by_name["assets"]["type"] == "directory"
    assert by_name["index.html"]["type"] == "file"
    assert by_name["assets"]["remote_path"] == "/public/site/assets"
    assert all(call[0] != "retrbinary" for call in _FakeFTPTLS.instances[1].calls)

    destination = tmp_path / "agent-selected-download"
    downloaded = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="utils.ftp.file.download",
            input_json={
                "items": [{"remote_path": "/public/site", "local_path": str(destination)}],
                "conflict_policy": "overwrite",
                "error_policy": "stop",
            },
            credential_ref=credential_ref,
        )
    ).data

    assert (destination / "index.html").read_text(encoding="utf-8") == "home"
    assert (destination / "assets" / "app.js").read_text(encoding="utf-8") == "app"
    assert (destination / "empty").is_dir()
    assert downloaded.output_json["completed_count"] == 2
    rendered = json.dumps(downloaded.model_dump(mode="json"))
    assert "ftp-secret" not in rendered


def test_ftp_plain_mode_and_conflict_policies_are_agent_selected(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.ftp as ftp_module

    _FakeFTP.reset()
    monkeypatch.setattr(ftp_module.ftplib, "FTP", _FakeFTP)
    credential_ref = _credential_ref(session, project_id, tls_mode="none")
    source = tmp_path / "source.txt"
    source.write_text("new", encoding="utf-8")
    _FakeFTP.server_dirs = {"/", "/target"}
    _FakeFTP.server_files = {"/target/source.txt": b"old"}

    skipped = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="utils.ftp.file.upload",
            input_json={
                "items": [{"local_path": str(source), "remote_path": "/target/source.txt"}],
                "conflict_policy": "skip",
                "error_policy": "stop",
            },
            credential_ref=credential_ref,
        )
    ).data

    assert _FakeFTP.server_files["/target/source.txt"] == b"old"
    assert skipped.output_json["skipped_count"] == 1
    assert all(call[0] not in {"auth", "prot_p"} for call in _FakeFTP.instances[0].calls)

    overwritten = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="utils.ftp.file.upload",
            input_json={
                "items": [{"local_path": str(source), "remote_path": "/target/source.txt"}],
                "conflict_policy": "overwrite",
                "error_policy": "stop",
            },
            credential_ref=credential_ref,
        )
    ).data
    assert _FakeFTP.server_files["/target/source.txt"] == b"new"
    assert overwritten.output_json["completed_count"] == 1


def test_ftp_continue_preserves_partial_results_and_stop_audits_partial_effects(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_ftps(monkeypatch)
    credential_ref = _credential_ref(session, project_id)
    good = tmp_path / "good.txt"
    good.write_text("good", encoding="utf-8")
    missing = tmp_path / "missing.txt"

    partial = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="utils.ftp.file.upload",
            input_json={
                "items": [
                    {"local_path": str(missing), "remote_path": "/batch/missing.txt"},
                    {"local_path": str(good), "remote_path": "/batch/good.txt"},
                ],
                "conflict_policy": "overwrite",
                "error_policy": "continue",
            },
            credential_ref=credential_ref,
        )
    ).data
    assert partial.output_json["status"] == "partial"
    assert partial.output_json["failed_count"] == 1
    assert partial.output_json["completed_count"] == 1
    assert _FakeFTPTLS.server_files["/batch/good.txt"] == b"good"

    _FakeFTPTLS.reset()
    with pytest.raises(ConflictError) as excinfo:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="utils.ftp.file.upload",
                input_json={
                    "items": [
                        {"local_path": str(good), "remote_path": "/batch/good.txt"},
                        {"local_path": str(missing), "remote_path": "/batch/missing.txt"},
                    ],
                    "conflict_policy": "overwrite",
                    "error_policy": "stop",
                },
                credential_ref=credential_ref,
            )
        )
    partial_result = excinfo.value.data["provider_error"]["partial_result"]
    assert partial_result["completed_count"] == 1
    assert partial_result["failed_count"] == 1
    assert partial_result["status"] == "failed"


def test_ftp_download_rejects_server_child_traversal(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_ftps(monkeypatch)
    credential_ref = _credential_ref(session, project_id)
    _FakeFTPTLS.server_dirs = {"/", "/unsafe"}
    _FakeFTPTLS.malicious_children = {"/unsafe": ["../escape.txt"]}
    destination = tmp_path / "destination"

    with pytest.raises(ConflictError) as excinfo:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="utils.ftp.file.download",
                input_json={
                    "items": [{"remote_path": "/unsafe", "local_path": str(destination)}],
                    "conflict_policy": "overwrite",
                    "error_policy": "stop",
                },
                credential_ref=credential_ref,
            )
        )

    assert "unsafe remote child name" in json.dumps(excinfo.value.data["provider_error"])
    assert not (tmp_path / "escape.txt").exists()


def test_ftp_validation_rejects_control_characters_and_empty_batches(
    session: Session,
    project_id: int,
) -> None:
    credential_ref = _credential_ref(session, project_id)
    repo = ActionRepository(session)

    browse = repo.validate(
        project_id=project_id,
        action_ref="utils.ftp.directory.list",
        input_json={"remote_path": "/safe\r\nDELE secret"},
        credential_ref=credential_ref,
    )
    upload = repo.validate(
        project_id=project_id,
        action_ref="utils.ftp.file.upload",
        input_json={
            "items": [],
            "conflict_policy": "overwrite",
            "error_policy": "stop",
        },
        credential_ref=credential_ref,
    )

    assert {item.code for item in browse.issues} >= {"format"}
    assert {item.code for item in upload.issues} >= {"required"}


def test_ftp_supports_multiple_mappings_and_all_download_conflict_policies(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_ftps(monkeypatch)
    credential_ref = _credential_ref(session, project_id)
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    repo = ActionRepository(session)

    uploaded = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="utils.ftp.file.upload",
            input_json={
                "items": [
                    {"local_path": str(first), "remote_path": "/one/first.txt"},
                    {"local_path": str(second), "remote_path": "/two/second.txt"},
                ],
                "conflict_policy": "overwrite",
                "error_policy": "stop",
            },
            credential_ref=credential_ref,
        )
    ).data
    assert uploaded.output_json["completed_count"] == 2
    assert _FakeFTPTLS.server_files["/one/first.txt"] == b"first"
    assert _FakeFTPTLS.server_files["/two/second.txt"] == b"second"

    skip_target = tmp_path / "skip.txt"
    skip_target.write_text("keep", encoding="utf-8")
    skipped = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="utils.ftp.file.download",
            input_json={
                "items": [{"remote_path": "/one/first.txt", "local_path": str(skip_target)}],
                "conflict_policy": "skip",
                "error_policy": "stop",
            },
            credential_ref=credential_ref,
        )
    ).data
    assert skipped.output_json["skipped_count"] == 1
    assert skip_target.read_text(encoding="utf-8") == "keep"

    with pytest.raises(ConflictError):
        asyncio.run(
            repo.execute(
                project_id=project_id,
                action_ref="utils.ftp.file.download",
                input_json={
                    "items": [{"remote_path": "/one/first.txt", "local_path": str(skip_target)}],
                    "conflict_policy": "fail",
                    "error_policy": "stop",
                },
                credential_ref=credential_ref,
            )
        )
    assert skip_target.read_text(encoding="utf-8") == "keep"

    other_target = tmp_path / "other.txt"
    downloaded = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="utils.ftp.file.download",
            input_json={
                "items": [
                    {"remote_path": "/one/first.txt", "local_path": str(skip_target)},
                    {"remote_path": "/two/second.txt", "local_path": str(other_target)},
                ],
                "conflict_policy": "overwrite",
                "error_policy": "stop",
            },
            credential_ref=credential_ref,
        )
    ).data
    assert downloaded.output_json["completed_count"] == 2
    assert skip_target.read_text(encoding="utf-8") == "first"
    assert other_target.read_text(encoding="utf-8") == "second"


def test_ftp_symlink_policies_detect_local_cycles_and_skip_remote_links(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_ftps(monkeypatch)
    credential_ref = _credential_ref(session, project_id)
    repo = ActionRepository(session)
    real_file = tmp_path / "real.txt"
    real_file.write_text("payload", encoding="utf-8")
    local_link = tmp_path / "file-link"
    local_link.symlink_to(real_file)

    skipped = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="utils.ftp.file.upload",
            input_json={
                "items": [{"local_path": str(local_link), "remote_path": "/links/file.txt"}],
                "conflict_policy": "overwrite",
                "error_policy": "stop",
                "follow_symlinks": False,
            },
            credential_ref=credential_ref,
        )
    ).data
    assert skipped.output_json["skipped"][0]["reason"] == "symlink_not_followed"
    assert "/links/file.txt" not in _FakeFTPTLS.server_files

    followed = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="utils.ftp.file.upload",
            input_json={
                "items": [{"local_path": str(local_link), "remote_path": "/links/file.txt"}],
                "conflict_policy": "overwrite",
                "error_policy": "stop",
                "follow_symlinks": True,
            },
            credential_ref=credential_ref,
        )
    ).data
    assert followed.output_json["completed_count"] == 1
    assert _FakeFTPTLS.server_files["/links/file.txt"] == b"payload"

    cyclic = tmp_path / "cyclic"
    cyclic.mkdir()
    (cyclic / "kept.txt").write_text("kept", encoding="utf-8")
    (cyclic / "loop").symlink_to(cyclic, target_is_directory=True)
    cycle_result = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="utils.ftp.file.upload",
            input_json={
                "items": [{"local_path": str(cyclic), "remote_path": "/cycle"}],
                "conflict_policy": "overwrite",
                "error_policy": "continue",
                "follow_symlinks": True,
            },
            credential_ref=credential_ref,
        )
    ).data
    assert cycle_result.output_json["completed_count"] == 1
    assert cycle_result.output_json["failed_count"] == 1
    assert "symlink cycle detected" in cycle_result.output_json["failed"][0]["message"]

    _FakeFTPTLS.server_dirs.add("/remote")
    _FakeFTPTLS.server_symlinks.update({"/remote-link", "/remote/nested-link"})
    browsed = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="utils.ftp.directory.list",
            input_json={"remote_path": "/remote"},
            credential_ref=credential_ref,
        )
    ).data
    assert browsed.output_json["entries"][0]["type"] == "symlink"

    remote_destination = tmp_path / "remote-download"
    remote_result = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="utils.ftp.file.download",
            input_json={
                "items": [
                    {"remote_path": "/remote-link", "local_path": str(remote_destination / "top")},
                    {"remote_path": "/remote", "local_path": str(remote_destination / "tree")},
                ],
                "conflict_policy": "overwrite",
                "error_policy": "stop",
            },
            credential_ref=credential_ref,
        )
    ).data
    assert remote_result.output_json["skipped_count"] == 2
    assert {item["reason"] for item in remote_result.output_json["skipped"]} == {
        "remote_symlink_not_followed"
    }


def test_ftp_browse_falls_back_to_nlst_cwd_and_size_without_list(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.ftp as ftp_module

    _FallbackFTPTLS.reset()
    monkeypatch.setattr(ftp_module.ftplib, "FTP_TLS", _FallbackFTPTLS)
    credential_ref = _credential_ref(session, project_id)
    _FallbackFTPTLS.server_dirs = {"/", "/fallback", "/fallback/sub"}
    _FallbackFTPTLS.server_files = {"/fallback/file.txt": b"data"}

    result = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="utils.ftp.directory.list",
            input_json={"remote_path": "/fallback"},
            credential_ref=credential_ref,
        )
    ).data

    assert {item["name"]: item["type"] for item in result.output_json["entries"]} == {
        "file.txt": "file",
        "sub": "directory",
    }
    calls = _FallbackFTPTLS.instances[0].calls
    assert ("nlst", "/fallback") in calls
    assert any(call[0] == "size" for call in calls)
    assert any(call[0] == "cwd" for call in calls)
    assert all(call[0] != "retrlines" for call in calls)


def test_ftp_fallback_download_stops_remote_directory_cycle(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.ftp as ftp_module

    _FallbackCycleFTPTLS.reset()
    _FallbackCycleFTPTLS.server_dirs = {"/", "/cycle"}
    monkeypatch.setattr(ftp_module.ftplib, "FTP_TLS", _FallbackCycleFTPTLS)
    credential_ref = _credential_ref(session, project_id)

    with pytest.raises(ConflictError) as excinfo:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="utils.ftp.file.download",
                input_json={
                    "items": [{"remote_path": "/cycle", "local_path": str(tmp_path / "cycle")}],
                    "conflict_policy": "overwrite",
                    "error_policy": "continue",
                },
                credential_ref=credential_ref,
            )
        )

    result = excinfo.value.data["provider_error"]["partial_result"]
    assert result["failed_count"] == 1
    assert "remote directory cycle detected" in result["failed"][0]["message"]
    assert [call for call in _FallbackCycleFTPTLS.instances[0].calls if call[0] == "nlst"] == [
        ("nlst", "/cycle")
    ]


def test_ftp_failed_download_removes_partial_file_and_preserves_existing_target(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.ftp as ftp_module

    _FailingDownloadFTPTLS.reset()
    monkeypatch.setattr(ftp_module.ftplib, "FTP_TLS", _FailingDownloadFTPTLS)
    credential_ref = _credential_ref(session, project_id)
    _FailingDownloadFTPTLS.server_files = {"/broken.txt": b"complete"}
    destination = tmp_path / "broken.txt"

    with pytest.raises(ConflictError):
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="utils.ftp.file.download",
                input_json={
                    "items": [{"remote_path": "/broken.txt", "local_path": str(destination)}],
                    "conflict_policy": "overwrite",
                    "error_policy": "stop",
                },
                credential_ref=credential_ref,
            )
        )

    assert not destination.exists()
    assert list(tmp_path.glob(".*.stackos-ftp-*.part")) == []


def test_ftp_rejects_recursive_command_injection_names_and_redacts_auth_secrets(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_ftps(monkeypatch)
    credential_ref = _credential_ref(session, project_id)
    source = tmp_path / "source"
    source.mkdir()
    (source / "bad\r\nDELE target").write_text("unsafe", encoding="utf-8")

    with pytest.raises(ConflictError) as unsafe_exc:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="utils.ftp.file.upload",
                input_json={
                    "items": [{"local_path": str(source), "remote_path": "/safe"}],
                    "conflict_policy": "overwrite",
                    "error_policy": "stop",
                },
                credential_ref=credential_ref,
            )
        )
    assert "cannot contain NUL, CR, or LF" in json.dumps(unsafe_exc.value.data["provider_error"])
    assert all(call[0] != "storbinary" for call in _FakeFTPTLS.instances[0].calls)

    import stackos.actions.ftp as ftp_module

    class _LeakyLoginFTPTLS(_FakeFTPTLS):
        instances: ClassVar[list[_LeakyLoginFTPTLS]] = []

        def login(self, username: str, password: str) -> str:
            del username
            raise ftplib.error_perm(f"530 rejected password {password}")

    _LeakyLoginFTPTLS.reset()
    monkeypatch.setattr(ftp_module.ftplib, "FTP_TLS", _LeakyLoginFTPTLS)
    with pytest.raises(ConflictError) as auth_exc:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="utils.ftp.directory.list",
                input_json={"remote_path": "/"},
                credential_ref=credential_ref,
            )
        )
    rendered = json.dumps(auth_exc.value.data)
    assert "ftp-secret" not in rendered
    assert "login_rejected" in rendered
    assert "530" in rendered


def test_ftp_exact_redacts_server_listing_echo_and_marks_entry_non_traversable(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_ftps(monkeypatch)
    credential_ref = _credential_ref(session, project_id)
    _FakeFTPTLS.malicious_children = {"/": ["echo-ftp-secret.txt"]}

    result = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="utils.ftp.directory.list",
            input_json={"remote_path": "/"},
            credential_ref=credential_ref,
        )
    ).data

    entry = result.output_json["entries"][0]
    assert entry["name"] == "echo-[REDACTED].txt"
    assert entry["safe_to_traverse"] is False
    assert entry["remote_path"] is None
    assert "ftp-secret" not in json.dumps(result.model_dump(mode="json"))


def test_ftp_interrupted_upload_reports_unknown_partial_outcome_and_redacts_secret(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.ftp as ftp_module

    class _InterruptedUploadFTPTLS(_FakeFTPTLS):
        instances: ClassVar[list[_InterruptedUploadFTPTLS]] = []

        def storbinary(
            self,
            command: str,
            file_obj: Any,
            blocksize: int = 8192,
            callback: Any | None = None,
        ) -> str:
            del command, blocksize
            chunk = file_obj.read(3)
            if callback is not None:
                callback(chunk)
            raise ftplib.error_temp("426 server echoed ftp-secret after partial STOR")

    _InterruptedUploadFTPTLS.reset()
    monkeypatch.setattr(ftp_module.ftplib, "FTP_TLS", _InterruptedUploadFTPTLS)
    credential_ref = _credential_ref(session, project_id)
    source = tmp_path / "payload.bin"
    source.write_bytes(b"abcdef")

    with pytest.raises(ConflictError) as excinfo:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="utils.ftp.file.upload",
                input_json={
                    "items": [{"local_path": str(source), "remote_path": "/payload.bin"}],
                    "conflict_policy": "overwrite",
                    "error_policy": "stop",
                },
                credential_ref=credential_ref,
            )
        )

    failure = excinfo.value.data["provider_error"]["partial_result"]["failed"][0]
    assert failure["outcome_unknown"] is True
    assert failure["remote_partial_possible"] is True
    assert failure["retry_safe"] is False
    assert failure["attempted_bytes"] == 3
    assert "Inspect the selected remote path" in failure["reconciliation_guidance"]
    assert "ftp-secret" not in json.dumps(excinfo.value.data)


def test_ftp_never_reuses_server_pwd_with_command_control_characters(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.ftp as ftp_module

    class _UnsafePwdFTPTLS(_FallbackFTPTLS):
        instances: ClassVar[list[_UnsafePwdFTPTLS]] = []

        def pwd(self) -> str:
            self.calls.append(("pwd",))
            return "/unsafe\r\nDELE target"

    _UnsafePwdFTPTLS.reset()
    _UnsafePwdFTPTLS.server_files = {"/remote.txt": b"remote"}
    monkeypatch.setattr(ftp_module.ftplib, "FTP_TLS", _UnsafePwdFTPTLS)
    credential_ref = _credential_ref(session, project_id)

    with pytest.raises(ConflictError) as excinfo:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="utils.ftp.file.download",
                input_json={
                    "items": [
                        {"remote_path": "/remote.txt", "local_path": str(tmp_path / "remote.txt")}
                    ],
                    "conflict_policy": "overwrite",
                    "error_policy": "stop",
                },
                credential_ref=credential_ref,
            )
        )

    assert "server PWD cannot contain NUL, CR, or LF" in json.dumps(excinfo.value.data)
    assert all(
        not any(isinstance(value, str) and ("\r" in value or "\n" in value) for value in call)
        for call in _UnsafePwdFTPTLS.instances[0].calls
        if call[0] == "cwd"
    )
