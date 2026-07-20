"""FTP connector tests through the generic action executor."""

from __future__ import annotations

import asyncio
import ftplib
import json
import posixpath
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest
from sqlmodel import Session

from stackos.actions import (
    ActionConnectorError,
    ActionConnectorRequest,
    ActionExecutionOut,
    ActionRepository,
    FtpActionConnector,
)
from stackos.auth_providers import AuthRepository
from stackos.db.models import ActionCallStatus
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
        if (
            resolved in self.__class__.server_dirs
            or resolved in self.__class__.server_files
            or resolved in self.__class__.server_symlinks
        ):
            raise ftplib.error_perm("550 path already exists")
        self.__class__.server_dirs.add(resolved)
        return resolved

    def delete(self, path: str) -> str:
        resolved = self._path(path)
        self.calls.append(("delete", resolved))
        if resolved in self.__class__.server_files:
            del self.__class__.server_files[resolved]
            return "250 deleted"
        if resolved in self.__class__.server_symlinks:
            self.__class__.server_symlinks.remove(resolved)
            return "250 deleted"
        raise ftplib.error_perm("550 file unavailable")

    def rmd(self, path: str) -> str:
        resolved = self._path(path)
        self.calls.append(("rmd", resolved))
        if resolved == "/" or resolved not in self.__class__.server_dirs:
            raise ftplib.error_perm("550 directory unavailable")
        prefix = resolved.rstrip("/") + "/"
        if any(
            item.startswith(prefix)
            for item in (
                *self.__class__.server_dirs,
                *self.__class__.server_files,
                *self.__class__.server_symlinks,
            )
        ):
            raise ftplib.error_perm("550 directory not empty")
        self.__class__.server_dirs.remove(resolved)
        return "250 removed"

    def rename(self, fromname: str, toname: str) -> str:
        source = self._path(fromname)
        destination = self._path(toname)
        self.calls.extend([("rnfr", source), ("rnto", destination)])
        destination_parent = posixpath.dirname(destination) or "/"
        if destination_parent not in self.__class__.server_dirs:
            raise ftplib.error_perm("550 destination parent unavailable")
        if (
            destination in self.__class__.server_dirs
            or destination in self.__class__.server_files
            or destination in self.__class__.server_symlinks
        ):
            raise ftplib.error_perm("550 destination exists")
        if source in self.__class__.server_files:
            self.__class__.server_files[destination] = self.__class__.server_files.pop(source)
            return "250 renamed"
        if source in self.__class__.server_symlinks:
            self.__class__.server_symlinks.remove(source)
            self.__class__.server_symlinks.add(destination)
            return "250 renamed"
        if source not in self.__class__.server_dirs or source == "/":
            raise ftplib.error_perm("550 source unavailable")

        source_prefix = source.rstrip("/") + "/"
        directory_moves = {
            path: destination + path[len(source) :]
            for path in self.__class__.server_dirs
            if path == source or path.startswith(source_prefix)
        }
        file_moves = {
            path: destination + path[len(source) :]
            for path in self.__class__.server_files
            if path.startswith(source_prefix)
        }
        symlink_moves = {
            path: destination + path[len(source) :]
            for path in self.__class__.server_symlinks
            if path.startswith(source_prefix)
        }
        for path in directory_moves:
            self.__class__.server_dirs.remove(path)
        self.__class__.server_dirs.update(directory_moves.values())
        for path, target in file_moves.items():
            self.__class__.server_files[target] = self.__class__.server_files.pop(path)
        for path in symlink_moves:
            self.__class__.server_symlinks.remove(path)
        self.__class__.server_symlinks.update(symlink_moves.values())
        return "250 renamed"

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


class _MlstFallbackFTPTLS(_FakeFTPTLS):
    instances: ClassVar[list[_MlstFallbackFTPTLS]] = []
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

    def nlst(self, path: str = "") -> list[str]:
        resolved = self._path(path or self.cwd_path)
        self.calls.append(("nlst", resolved))
        prefix = resolved.rstrip("/") + "/"
        children = {
            item
            for item in (
                *self.__class__.server_dirs,
                *self.__class__.server_files,
                *self.__class__.server_symlinks,
            )
            if item != resolved and item.startswith(prefix) and "/" not in item[len(prefix) :]
        }
        return sorted(children)


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


def _ftp_connector_request(
    session: Session,
    project_id: int,
    *,
    operation: str,
    input_json: dict[str, Any],
    progress_callback: Any,
) -> ActionConnectorRequest:
    credential_ref = _credential_ref(session, project_id)
    credential = AuthRepository(session).resolve_for_execution(
        project_id=project_id,
        provider_key="ftp",
        credential_ref=credential_ref,
        operation=operation,
    )
    return ActionConnectorRequest(
        project_id=project_id,
        plugin_slug="utils",
        action_key=f"ftp.{operation}",
        action_ref=f"utils.ftp.{operation}",
        provider_key="ftp",
        operation=operation,
        input_json=input_json,
        config_json={},
        credential=credential,
        progress_callback=progress_callback,
    )


def _assert_transfer_progress(
    snapshots: list[dict[str, Any]],
    *,
    source_path: str,
    target_path: str,
    expected_bytes: int,
) -> None:
    transferring = [item for item in snapshots if item.get("phase") == "transferring"]
    assert transferring
    assert transferring[-1]["current_source_path"] == source_path
    assert transferring[-1]["current_target_path"] == target_path
    assert transferring[-1]["bytes_transferred"] == expected_bytes
    assert [item["bytes_transferred"] for item in snapshots] == sorted(
        item["bytes_transferred"] for item in snapshots
    )
    for count_key in ("completed_count", "skipped_count", "failed_count"):
        values = [item[count_key] for item in snapshots]
        assert values == sorted(values)


def _run_action_to_terminal(
    session: Session,
    repository: ActionRepository,
    **execute_kwargs: Any,
) -> Any:
    async def run() -> Any:
        execution = (await repository.execute(**execute_kwargs)).data
        if execution.action_call.status != ActionCallStatus.RUNNING:
            return execution

        call_id = execution.action_call.id
        task_name = f"stackos-action-{call_id}"
        tasks = [task for task in asyncio.all_tasks() if task.get_name() == task_name]
        assert len(tasks) == 1
        await tasks[0]

        with Session(session.get_bind()) as observer:
            calls = ActionRepository(observer).query_calls(
                project_id=int(execute_kwargs["project_id"]),
                limit=100,
            )
            call = next(call for call in calls.items if call.id == call_id)
            return ActionExecutionOut(
                action_call=call,
                output_json=call.response_json or {},
                metadata_json=call.metadata_json,
                cost_cents=call.cost_cents,
                credential_ref=call.credential_ref,
            )

    return asyncio.run(run())


def test_ftp_upload_progress_waits_for_final_server_reply(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.ftp as ftp_module

    class _FinalReplyFTPTLS(_FakeFTPTLS):
        instances: ClassVar[list[_FinalReplyFTPTLS]] = []
        bytes_sent = threading.Event()
        allow_final_reply = threading.Event()

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
            self.__class__.bytes_sent.set()
            self.__class__.allow_final_reply.wait()
            return "226 stored"

    _FinalReplyFTPTLS.reset()
    monkeypatch.setattr(ftp_module.ftplib, "FTP_TLS", _FinalReplyFTPTLS)
    source = tmp_path / "payload.bin"
    payload = b"raw-file-content-must-not-leak"
    source.write_bytes(payload)
    snapshots: list[dict[str, Any]] = []
    request = _ftp_connector_request(
        session,
        project_id,
        operation="file.upload",
        input_json={
            "items": [{"local_path": str(source), "remote_path": "/payload.bin"}],
            "conflict_policy": "overwrite",
            "error_policy": "stop",
        },
        progress_callback=snapshots.append,
    )

    async def execute() -> tuple[bool, list[dict[str, Any]], dict[str, Any]]:
        task = asyncio.create_task(FtpActionConnector().execute(request))
        await asyncio.to_thread(_FinalReplyFTPTLS.bytes_sent.wait)
        done_before_final_reply = task.done()
        progress_before_final_reply = list(snapshots)
        _FinalReplyFTPTLS.allow_final_reply.set()
        result = await task
        return done_before_final_reply, progress_before_final_reply, result.output_json

    done_before_reply, progress_before_reply, output = asyncio.run(execute())

    assert done_before_reply is False
    assert all(item["completed_count"] == 0 for item in progress_before_reply)
    _assert_transfer_progress(
        progress_before_reply,
        source_path=str(source),
        target_path="/payload.bin",
        expected_bytes=len(payload),
    )
    assert output["completed_count"] == 1
    rendered = json.dumps(snapshots)
    assert "raw-file-content-must-not-leak" not in rendered
    assert "ftp-secret" not in rendered


def test_ftp_download_reports_sanitized_monotonic_progress_and_keeps_atomic_placement(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_ftps(monkeypatch)
    payload = b"download-file-content-must-not-leak"
    _FakeFTPTLS.server_files = {"/download.bin": payload}
    destination = tmp_path / "download.bin"
    snapshots: list[dict[str, Any]] = []
    request = _ftp_connector_request(
        session,
        project_id,
        operation="file.download",
        input_json={
            "items": [{"remote_path": "/download.bin", "local_path": str(destination)}],
            "conflict_policy": "overwrite",
            "error_policy": "stop",
        },
        progress_callback=snapshots.append,
    )

    result = asyncio.run(FtpActionConnector().execute(request))

    _assert_transfer_progress(
        snapshots,
        source_path="/download.bin",
        target_path=str(destination),
        expected_bytes=len(payload),
    )
    assert destination.read_bytes() == payload
    assert not list(tmp_path.glob(".download.bin.stackos-ftp-*.part"))
    assert result.output_json["completed_count"] == 1
    rendered = json.dumps(snapshots)
    assert "download-file-content-must-not-leak" not in rendered
    assert "ftp-secret" not in rendered


def test_ftp_upload_full_bytes_then_final_error_remains_outcome_unknown(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.ftp as ftp_module

    class _FinalErrorFTPTLS(_FakeFTPTLS):
        instances: ClassVar[list[_FinalErrorFTPTLS]] = []

        def storbinary(
            self,
            command: str,
            file_obj: Any,
            blocksize: int = 8192,
            callback: Any | None = None,
        ) -> str:
            _, raw_path = command.split(" ", 1)
            path = self._path(raw_path)
            payload = file_obj.read()
            self.__class__.server_files[path] = payload
            if callback is not None:
                callback(payload)
            raise ftplib.error_temp("451 final transfer acknowledgement failed")

    _FinalErrorFTPTLS.reset()
    monkeypatch.setattr(ftp_module.ftplib, "FTP_TLS", _FinalErrorFTPTLS)
    payload = b"all-bytes-were-sent"
    source = tmp_path / "payload.bin"
    source.write_bytes(payload)
    snapshots: list[dict[str, Any]] = []
    request = _ftp_connector_request(
        session,
        project_id,
        operation="file.upload",
        input_json={
            "items": [{"local_path": str(source), "remote_path": "/payload.bin"}],
            "conflict_policy": "overwrite",
            "error_policy": "stop",
        },
        progress_callback=snapshots.append,
    )

    with pytest.raises(ActionConnectorError) as excinfo:
        asyncio.run(FtpActionConnector().execute(request))

    failure = excinfo.value.output_json["failed"][0]
    assert failure["attempted_bytes"] == len(payload)
    assert failure["outcome_unknown"] is True
    assert failure["retry_safe"] is False
    assert excinfo.value.output_json["completed_count"] == 0


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

    uploaded = _run_action_to_terminal(
        session,
        repo,
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
    downloaded = _run_action_to_terminal(
        session,
        repo,
        project_id=project_id,
        action_ref="utils.ftp.file.download",
        input_json={
            "items": [{"remote_path": "/public/site", "local_path": str(destination)}],
            "conflict_policy": "overwrite",
            "error_policy": "stop",
        },
        credential_ref=credential_ref,
    )

    assert (destination / "index.html").read_text(encoding="utf-8") == "home"
    assert (destination / "assets" / "app.js").read_text(encoding="utf-8") == "app"
    assert (destination / "empty").is_dir()
    assert downloaded.output_json["completed_count"] == 2
    rendered = json.dumps(downloaded.model_dump(mode="json"))
    assert "ftp-secret" not in rendered


def test_ftp_remote_management_primitives_use_exact_server_operations(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_ftps(monkeypatch)
    credential_ref = _credential_ref(session, project_id)
    repo = ActionRepository(session)
    _FakeFTPTLS.server_dirs = {
        "/",
        "/archive",
        "/workspace",
        "/workspace/empty",
        "/workspace/source",
        "/workspace/source/nested",
    }
    _FakeFTPTLS.server_files = {
        "/workspace/remove.txt": b"remove",
        "/workspace/old.txt": b"rename",
        "/workspace/source/nested/file.txt": b"nested",
    }

    created = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="utils.ftp.directory.create",
            input_json={"remote_path": "/workspace/new"},
            credential_ref=credential_ref,
        )
    ).data
    deleted_file = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="utils.ftp.file.delete",
            input_json={"remote_path": "/workspace/remove.txt"},
            credential_ref=credential_ref,
        )
    ).data
    renamed_file = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="utils.ftp.path.rename",
            input_json={
                "source_path": "/workspace/old.txt",
                "destination_path": "/archive/renamed.txt",
            },
            credential_ref=credential_ref,
        )
    ).data
    renamed_directory = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="utils.ftp.path.rename",
            input_json={
                "source_path": "/workspace/source",
                "destination_path": "/archive/moved",
            },
            credential_ref=credential_ref,
        )
    ).data
    deleted_directory = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="utils.ftp.directory.delete",
            input_json={"remote_path": "/workspace/empty", "recursive": False},
            credential_ref=credential_ref,
        )
    ).data

    assert created.output_json == {
        "provider": "ftp",
        "operation": "directory.create",
        "status": "success",
        "remote_path": "/workspace/new",
    }
    assert deleted_file.output_json == {
        "provider": "ftp",
        "operation": "file.delete",
        "status": "success",
        "remote_path": "/workspace/remove.txt",
    }
    assert renamed_file.output_json["source_path"] == "/workspace/old.txt"
    assert renamed_file.output_json["destination_path"] == "/archive/renamed.txt"
    assert renamed_directory.output_json["source_path"] == "/workspace/source"
    assert renamed_directory.output_json["destination_path"] == "/archive/moved"
    assert deleted_directory.output_json["deleted_paths"] == [
        {"remote_path": "/workspace/empty", "type": "directory"}
    ]

    assert "/workspace/new" in _FakeFTPTLS.server_dirs
    assert "/workspace/remove.txt" not in _FakeFTPTLS.server_files
    assert _FakeFTPTLS.server_files["/archive/renamed.txt"] == b"rename"
    assert "/workspace/old.txt" not in _FakeFTPTLS.server_files
    assert "/archive/moved" in _FakeFTPTLS.server_dirs
    assert _FakeFTPTLS.server_files["/archive/moved/nested/file.txt"] == b"nested"
    assert "/workspace/source" not in _FakeFTPTLS.server_dirs
    assert "/workspace/empty" not in _FakeFTPTLS.server_dirs

    calls = [call for instance in _FakeFTPTLS.instances for call in instance.calls]
    assert ("mkd", "/workspace/new") in calls
    assert ("delete", "/workspace/remove.txt") in calls
    assert ("rnfr", "/workspace/old.txt") in calls
    assert ("rnto", "/archive/renamed.txt") in calls
    assert ("rnfr", "/workspace/source") in calls
    assert ("rnto", "/archive/moved") in calls
    assert ("rmd", "/workspace/empty") in calls


def test_ftp_management_does_not_create_parents_or_replace_rename_targets(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_ftps(monkeypatch)
    credential_ref = _credential_ref(session, project_id)
    repo = ActionRepository(session)
    _FakeFTPTLS.server_dirs = {"/", "/existing", "/workspace"}
    _FakeFTPTLS.server_files = {
        "/workspace/source.txt": b"source",
        "/workspace/destination.txt": b"destination",
    }

    with pytest.raises(ConflictError):
        asyncio.run(
            repo.execute(
                project_id=project_id,
                action_ref="utils.ftp.directory.create",
                input_json={"remote_path": "/missing/child"},
                credential_ref=credential_ref,
            )
        )
    with pytest.raises(ConflictError):
        asyncio.run(
            repo.execute(
                project_id=project_id,
                action_ref="utils.ftp.directory.create",
                input_json={"remote_path": "/existing"},
                credential_ref=credential_ref,
            )
        )
    with pytest.raises(ConflictError):
        asyncio.run(
            repo.execute(
                project_id=project_id,
                action_ref="utils.ftp.path.rename",
                input_json={
                    "source_path": "/workspace/source.txt",
                    "destination_path": "/workspace/destination.txt",
                },
                credential_ref=credential_ref,
            )
        )

    assert "/missing" not in _FakeFTPTLS.server_dirs
    assert "/missing/child" not in _FakeFTPTLS.server_dirs
    assert "/existing" in _FakeFTPTLS.server_dirs
    assert _FakeFTPTLS.server_files["/workspace/source.txt"] == b"source"
    assert _FakeFTPTLS.server_files["/workspace/destination.txt"] == b"destination"
    rename_calls = _FakeFTPTLS.instances[-1].calls
    assert ("rnfr", "/workspace/source.txt") in rename_calls
    assert ("rnto", "/workspace/destination.txt") in rename_calls
    assert all(call[0] not in {"delete", "mkd"} for call in rename_calls)


def test_ftp_recursive_directory_delete_is_machine_readable_and_postorder(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_ftps(monkeypatch)
    credential_ref = _credential_ref(session, project_id)
    _FakeFTPTLS.server_dirs = {"/", "/tree", "/tree/empty", "/tree/nested"}
    _FakeFTPTLS.server_files = {
        "/tree/root.txt": b"root",
        "/tree/nested/child.txt": b"child",
    }
    _FakeFTPTLS.server_symlinks = {"/tree/link"}

    result = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="utils.ftp.directory.delete",
            input_json={"remote_path": "/tree", "recursive": True},
            credential_ref=credential_ref,
        )
    ).data

    assert result.output_json["status"] == "success"
    assert result.output_json["recursive"] is True
    assert result.output_json["deleted_count"] == 6
    assert result.output_json["file_count"] == 2
    assert result.output_json["directory_count"] == 3
    assert result.output_json["symlink_count"] == 1
    assert result.output_json["deleted_paths"][-1] == {
        "remote_path": "/tree",
        "type": "directory",
    }
    assert not any(path.startswith("/tree") for path in _FakeFTPTLS.server_dirs if path != "/")
    assert not any(path.startswith("/tree") for path in _FakeFTPTLS.server_files)
    assert not any(path.startswith("/tree") for path in _FakeFTPTLS.server_symlinks)

    calls = _FakeFTPTLS.instances[0].calls
    assert calls.index(("delete", "/tree/nested/child.txt")) < calls.index(("rmd", "/tree/nested"))
    assert calls.index(("delete", "/tree/link")) < calls.index(("rmd", "/tree"))
    assert calls.index(("rmd", "/tree/empty")) < calls.index(("rmd", "/tree"))
    assert calls[-3:] == [("rmd", "/tree"), ("quit",), ("close",)]


def test_ftp_nonrecursive_directory_delete_preserves_nonempty_tree(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_ftps(monkeypatch)
    credential_ref = _credential_ref(session, project_id)
    _FakeFTPTLS.server_dirs = {"/", "/tree"}
    _FakeFTPTLS.server_files = {"/tree/file.txt": b"keep"}

    with pytest.raises(ConflictError):
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="utils.ftp.directory.delete",
                input_json={"remote_path": "/tree", "recursive": False},
                credential_ref=credential_ref,
            )
        )

    assert "/tree" in _FakeFTPTLS.server_dirs
    assert _FakeFTPTLS.server_files["/tree/file.txt"] == b"keep"
    calls = _FakeFTPTLS.instances[0].calls
    assert ("rmd", "/tree") in calls
    assert all(call[0] != "delete" for call in calls)


def test_ftp_recursive_delete_fails_before_mutation_without_mlsx_types(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.ftp as ftp_module

    _FallbackFTPTLS.reset()
    _FallbackFTPTLS.server_dirs = {"/", "/tree"}
    _FallbackFTPTLS.server_files = {"/tree/file.txt": b"keep"}
    monkeypatch.setattr(ftp_module.ftplib, "FTP_TLS", _FallbackFTPTLS)
    credential_ref = _credential_ref(session, project_id)

    with pytest.raises(ConflictError) as excinfo:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="utils.ftp.directory.delete",
                input_json={"remote_path": "/tree", "recursive": True},
                credential_ref=credential_ref,
            )
        )

    assert "machine-readable MLSD or MLST" in json.dumps(excinfo.value.data)
    assert _FallbackFTPTLS.server_files["/tree/file.txt"] == b"keep"
    calls = _FallbackFTPTLS.instances[0].calls
    assert all(call[0] not in {"delete", "rmd"} for call in calls)


def test_ftp_recursive_delete_uses_nlst_with_mlst_types_when_mlsd_is_unavailable(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.ftp as ftp_module

    _MlstFallbackFTPTLS.reset()
    _MlstFallbackFTPTLS.server_dirs = {"/", "/tree", "/tree/nested"}
    _MlstFallbackFTPTLS.server_files = {
        "/tree/root.txt": b"root",
        "/tree/nested/child.txt": b"child",
    }
    monkeypatch.setattr(ftp_module.ftplib, "FTP_TLS", _MlstFallbackFTPTLS)
    credential_ref = _credential_ref(session, project_id)

    result = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="utils.ftp.directory.delete",
            input_json={"remote_path": "/tree", "recursive": True},
            credential_ref=credential_ref,
        )
    ).data

    assert result.output_json["deleted_count"] == 4
    assert not any(path.startswith("/tree") for path in _MlstFallbackFTPTLS.server_dirs)
    assert not _MlstFallbackFTPTLS.server_files
    calls = _MlstFallbackFTPTLS.instances[0].calls
    assert ("nlst", "/tree") in calls
    assert ("mlst", "/tree/nested") in calls
    assert ("mlst", "/tree/root.txt") in calls


def test_ftp_recursive_delete_rejects_directory_alias_outside_selected_root(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.ftp as ftp_module

    class _EscapingDirectoryFTPTLS(_FakeFTPTLS):
        instances: ClassVar[list[_EscapingDirectoryFTPTLS]] = []

        def cwd(self, path: str) -> str:
            resolved = self._path(path)
            if resolved == "/tree/escape":
                self.calls.append(("cwd", resolved))
                self.cwd_path = "/outside"
                return "250 changed"
            return super().cwd(path)

        def mlsd(
            self,
            path: str = "",
            facts: list[str] | None = None,
        ) -> Iterator[tuple[str, dict[str, str]]]:
            resolved = self._path(path or self.cwd_path)
            if resolved == "/tree":
                del facts
                self.calls.append(("mlsd", resolved))
                yield "escape", {"type": "dir"}
                return
            yield from super().mlsd(path, facts=facts)

    _EscapingDirectoryFTPTLS.reset()
    _EscapingDirectoryFTPTLS.server_dirs = {"/", "/tree", "/outside"}
    _EscapingDirectoryFTPTLS.server_files = {"/outside/keep.txt": b"keep"}
    monkeypatch.setattr(ftp_module.ftplib, "FTP_TLS", _EscapingDirectoryFTPTLS)
    credential_ref = _credential_ref(session, project_id)

    with pytest.raises(ConflictError) as excinfo:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="utils.ftp.directory.delete",
                input_json={"remote_path": "/tree", "recursive": True},
                credential_ref=credential_ref,
            )
        )

    assert "resolves outside the selected root" in json.dumps(excinfo.value.data)
    assert _EscapingDirectoryFTPTLS.server_files["/outside/keep.txt"] == b"keep"
    calls = _EscapingDirectoryFTPTLS.instances[0].calls
    assert all(call[0] not in {"delete", "rmd"} for call in calls)


def test_ftp_relative_mutation_fails_if_pwd_is_unavailable(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.ftp as ftp_module

    class _NoPwdFTPTLS(_FakeFTPTLS):
        instances: ClassVar[list[_NoPwdFTPTLS]] = []

        def pwd(self) -> str:
            self.calls.append(("pwd",))
            raise ftplib.error_perm("500 PWD unavailable")

    _NoPwdFTPTLS.reset()
    _NoPwdFTPTLS.server_files = {"/relative.txt": b"keep"}
    monkeypatch.setattr(ftp_module.ftplib, "FTP_TLS", _NoPwdFTPTLS)
    credential_ref = _credential_ref(session, project_id)

    with pytest.raises(ConflictError) as excinfo:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="utils.ftp.file.delete",
                input_json={"remote_path": "relative.txt"},
                credential_ref=credential_ref,
            )
        )

    assert "PWD is required to resolve relative remote paths" in json.dumps(excinfo.value.data)
    assert _NoPwdFTPTLS.server_files["/relative.txt"] == b"keep"
    assert all(call[0] != "delete" for call in _NoPwdFTPTLS.instances[0].calls)


def test_ftp_recursive_delete_fails_if_directory_identity_pwd_is_unavailable(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.ftp as ftp_module

    class _NoIdentityPwdFTPTLS(_FakeFTPTLS):
        instances: ClassVar[list[_NoIdentityPwdFTPTLS]] = []

        def pwd(self) -> str:
            self.calls.append(("pwd",))
            if self.cwd_path == "/tree":
                raise ftplib.error_perm("500 PWD unavailable")
            return self.cwd_path

    _NoIdentityPwdFTPTLS.reset()
    _NoIdentityPwdFTPTLS.server_dirs = {"/", "/tree"}
    _NoIdentityPwdFTPTLS.server_files = {"/tree/keep.txt": b"keep"}
    monkeypatch.setattr(ftp_module.ftplib, "FTP_TLS", _NoIdentityPwdFTPTLS)
    credential_ref = _credential_ref(session, project_id)

    with pytest.raises(ConflictError) as excinfo:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="utils.ftp.directory.delete",
                input_json={"remote_path": "/tree", "recursive": True},
                credential_ref=credential_ref,
            )
        )

    assert "PWD is required to verify recursive directory identity" in json.dumps(
        excinfo.value.data
    )
    assert _NoIdentityPwdFTPTLS.server_files["/tree/keep.txt"] == b"keep"
    calls = _NoIdentityPwdFTPTLS.instances[0].calls
    assert all(call[0] not in {"delete", "rmd"} for call in calls)


def test_ftp_recursive_delete_reports_confirmed_partial_effects_and_unknown_outcome(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.ftp as ftp_module

    class _PartialDeleteFTPTLS(_FakeFTPTLS):
        instances: ClassVar[list[_PartialDeleteFTPTLS]] = []

        def delete(self, path: str) -> str:
            resolved = self._path(path)
            if resolved == "/tree/z-fail.txt":
                self.calls.append(("delete", resolved))
                raise ftplib.error_perm("550 ftp-secret cannot delete")
            return super().delete(path)

    _PartialDeleteFTPTLS.reset()
    _PartialDeleteFTPTLS.server_dirs = {"/", "/tree"}
    _PartialDeleteFTPTLS.server_files = {
        "/tree/a-deleted.txt": b"deleted",
        "/tree/z-fail.txt": b"keep",
    }
    monkeypatch.setattr(ftp_module.ftplib, "FTP_TLS", _PartialDeleteFTPTLS)
    credential_ref = _credential_ref(session, project_id)

    with pytest.raises(ConflictError) as partial_exc:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="utils.ftp.directory.delete",
                input_json={"remote_path": "/tree", "recursive": True},
                credential_ref=credential_ref,
            )
        )

    partial = partial_exc.value.data["provider_error"]["partial_result"]
    assert partial["status"] == "failed"
    assert partial["deleted_paths"] == [{"remote_path": "/tree/a-deleted.txt", "type": "file"}]
    assert "ftp-secret" not in json.dumps(partial_exc.value.data)
    assert "/tree/a-deleted.txt" not in _PartialDeleteFTPTLS.server_files
    assert _PartialDeleteFTPTLS.server_files["/tree/z-fail.txt"] == b"keep"

    class _UnknownDeleteFTPTLS(_FakeFTPTLS):
        instances: ClassVar[list[_UnknownDeleteFTPTLS]] = []

        def delete(self, path: str) -> str:
            resolved = self._path(path)
            self.calls.append(("delete", resolved))
            del self.__class__.server_files[resolved]
            raise EOFError("ftp-secret connection closed")

    _UnknownDeleteFTPTLS.reset()
    _UnknownDeleteFTPTLS.server_files = {"/unknown.txt": b"gone"}
    monkeypatch.setattr(ftp_module.ftplib, "FTP_TLS", _UnknownDeleteFTPTLS)

    with pytest.raises(ConflictError) as unknown_exc:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="utils.ftp.file.delete",
                input_json={"remote_path": "/unknown.txt"},
                credential_ref=credential_ref,
            )
        )

    provider_error = unknown_exc.value.data["provider_error"]
    assert provider_error["outcome_unknown"] is True
    assert provider_error["retry_safe"] is False
    assert provider_error["target_path"] == "/unknown.txt"
    assert "Inspect or list the selected remote path" in provider_error["reconciliation_guidance"]
    assert "ftp-secret" not in json.dumps(unknown_exc.value.data)
    assert "/unknown.txt" not in _UnknownDeleteFTPTLS.server_files

    class _UnexpectedReplyFTPTLS(_FakeFTPTLS):
        instances: ClassVar[list[_UnexpectedReplyFTPTLS]] = []

        def delete(self, path: str) -> str:
            resolved = self._path(path)
            self.calls.append(("delete", resolved))
            del self.__class__.server_files[resolved]
            raise ftplib.error_reply("257 ftp-secret unexpected success reply")

    _UnexpectedReplyFTPTLS.reset()
    _UnexpectedReplyFTPTLS.server_files = {"/unexpected.txt": b"gone"}
    monkeypatch.setattr(ftp_module.ftplib, "FTP_TLS", _UnexpectedReplyFTPTLS)

    with pytest.raises(ConflictError) as reply_exc:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="utils.ftp.file.delete",
                input_json={"remote_path": "/unexpected.txt"},
                credential_ref=credential_ref,
            )
        )

    reply_error = reply_exc.value.data["provider_error"]
    assert reply_error["outcome_unknown"] is True
    assert reply_error["retry_safe"] is False
    assert reply_error["target_path"] == "/unexpected.txt"
    assert "ftp-secret" not in json.dumps(reply_exc.value.data)
    assert "/unexpected.txt" not in _UnexpectedReplyFTPTLS.server_files


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

    skipped = _run_action_to_terminal(
        session,
        ActionRepository(session),
        project_id=project_id,
        action_ref="utils.ftp.file.upload",
        input_json={
            "items": [{"local_path": str(source), "remote_path": "/target/source.txt"}],
            "conflict_policy": "skip",
            "error_policy": "stop",
        },
        credential_ref=credential_ref,
    )

    assert _FakeFTP.server_files["/target/source.txt"] == b"old"
    assert skipped.output_json["skipped_count"] == 1
    assert all(call[0] not in {"auth", "prot_p"} for call in _FakeFTP.instances[0].calls)

    overwritten = _run_action_to_terminal(
        session,
        ActionRepository(session),
        project_id=project_id,
        action_ref="utils.ftp.file.upload",
        input_json={
            "items": [{"local_path": str(source), "remote_path": "/target/source.txt"}],
            "conflict_policy": "overwrite",
            "error_policy": "stop",
        },
        credential_ref=credential_ref,
    )
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

    partial = _run_action_to_terminal(
        session,
        ActionRepository(session),
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
    assert partial.output_json["status"] == "partial"
    assert partial.output_json["failed_count"] == 1
    assert partial.output_json["completed_count"] == 1
    assert _FakeFTPTLS.server_files["/batch/good.txt"] == b"good"

    _FakeFTPTLS.reset()
    failed = _run_action_to_terminal(
        session,
        ActionRepository(session),
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
    assert failed.action_call.status == ActionCallStatus.FAILED
    partial_result = failed.output_json["provider_error"]["partial_result"]
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

    failed = _run_action_to_terminal(
        session,
        ActionRepository(session),
        project_id=project_id,
        action_ref="utils.ftp.file.download",
        input_json={
            "items": [{"remote_path": "/unsafe", "local_path": str(destination)}],
            "conflict_policy": "overwrite",
            "error_policy": "stop",
        },
        credential_ref=credential_ref,
    )

    assert failed.action_call.status == ActionCallStatus.FAILED
    assert "unsafe remote child name" in json.dumps(failed.output_json["provider_error"])
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
    delete_file = repo.validate(
        project_id=project_id,
        action_ref="utils.ftp.file.delete",
        input_json={"remote_path": "/safe\r\nDELE secret"},
        credential_ref=credential_ref,
    )
    delete_directory = repo.validate(
        project_id=project_id,
        action_ref="utils.ftp.directory.delete",
        input_json={"remote_path": "/safe", "recursive": "yes"},
        credential_ref=credential_ref,
    )
    rename = repo.validate(
        project_id=project_id,
        action_ref="utils.ftp.path.rename",
        input_json={
            "source_path": "/safe",
            "destination_path": "/unsafe\nRNTO target",
        },
        credential_ref=credential_ref,
    )

    assert {item.code for item in browse.issues} >= {"format"}
    assert {item.code for item in upload.issues} >= {"required"}
    assert {item.code for item in delete_file.issues} >= {"format"}
    assert {item.code for item in delete_directory.issues} >= {"type_error"}
    assert {item.code for item in rename.issues} >= {"format"}


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

    uploaded = _run_action_to_terminal(
        session,
        repo,
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
    assert uploaded.output_json["completed_count"] == 2
    assert _FakeFTPTLS.server_files["/one/first.txt"] == b"first"
    assert _FakeFTPTLS.server_files["/two/second.txt"] == b"second"

    skip_target = tmp_path / "skip.txt"
    skip_target.write_text("keep", encoding="utf-8")
    skipped = _run_action_to_terminal(
        session,
        repo,
        project_id=project_id,
        action_ref="utils.ftp.file.download",
        input_json={
            "items": [{"remote_path": "/one/first.txt", "local_path": str(skip_target)}],
            "conflict_policy": "skip",
            "error_policy": "stop",
        },
        credential_ref=credential_ref,
    )
    assert skipped.output_json["skipped_count"] == 1
    assert skip_target.read_text(encoding="utf-8") == "keep"

    failed = _run_action_to_terminal(
        session,
        repo,
        project_id=project_id,
        action_ref="utils.ftp.file.download",
        input_json={
            "items": [{"remote_path": "/one/first.txt", "local_path": str(skip_target)}],
            "conflict_policy": "fail",
            "error_policy": "stop",
        },
        credential_ref=credential_ref,
    )
    assert failed.action_call.status == ActionCallStatus.FAILED
    assert skip_target.read_text(encoding="utf-8") == "keep"

    other_target = tmp_path / "other.txt"
    downloaded = _run_action_to_terminal(
        session,
        repo,
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

    skipped = _run_action_to_terminal(
        session,
        repo,
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
    assert skipped.output_json["skipped"][0]["reason"] == "symlink_not_followed"
    assert "/links/file.txt" not in _FakeFTPTLS.server_files

    followed = _run_action_to_terminal(
        session,
        repo,
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
    assert followed.output_json["completed_count"] == 1
    assert _FakeFTPTLS.server_files["/links/file.txt"] == b"payload"

    cyclic = tmp_path / "cyclic"
    cyclic.mkdir()
    (cyclic / "kept.txt").write_text("kept", encoding="utf-8")
    (cyclic / "loop").symlink_to(cyclic, target_is_directory=True)
    cycle_result = _run_action_to_terminal(
        session,
        repo,
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
    remote_result = _run_action_to_terminal(
        session,
        repo,
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

    failed = _run_action_to_terminal(
        session,
        ActionRepository(session),
        project_id=project_id,
        action_ref="utils.ftp.file.download",
        input_json={
            "items": [{"remote_path": "/cycle", "local_path": str(tmp_path / "cycle")}],
            "conflict_policy": "overwrite",
            "error_policy": "continue",
        },
        credential_ref=credential_ref,
    )

    assert failed.action_call.status == ActionCallStatus.FAILED
    result = failed.output_json["provider_error"]["partial_result"]
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

    failed = _run_action_to_terminal(
        session,
        ActionRepository(session),
        project_id=project_id,
        action_ref="utils.ftp.file.download",
        input_json={
            "items": [{"remote_path": "/broken.txt", "local_path": str(destination)}],
            "conflict_policy": "overwrite",
            "error_policy": "stop",
        },
        credential_ref=credential_ref,
    )

    assert failed.action_call.status == ActionCallStatus.FAILED
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

    failed = _run_action_to_terminal(
        session,
        ActionRepository(session),
        project_id=project_id,
        action_ref="utils.ftp.file.upload",
        input_json={
            "items": [{"local_path": str(source), "remote_path": "/safe"}],
            "conflict_policy": "overwrite",
            "error_policy": "stop",
        },
        credential_ref=credential_ref,
    )
    assert failed.action_call.status == ActionCallStatus.FAILED
    assert "cannot contain NUL, CR, or LF" in json.dumps(failed.output_json["provider_error"])
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

    failed = _run_action_to_terminal(
        session,
        ActionRepository(session),
        project_id=project_id,
        action_ref="utils.ftp.file.upload",
        input_json={
            "items": [{"local_path": str(source), "remote_path": "/payload.bin"}],
            "conflict_policy": "overwrite",
            "error_policy": "stop",
        },
        credential_ref=credential_ref,
    )

    assert failed.action_call.status == ActionCallStatus.FAILED
    failure = failed.output_json["provider_error"]["partial_result"]["failed"][0]
    assert failure["outcome_unknown"] is True
    assert failure["remote_partial_possible"] is True
    assert failure["retry_safe"] is False
    assert failure["attempted_bytes"] == 3
    assert "Inspect the selected remote path" in failure["reconciliation_guidance"]
    assert "ftp-secret" not in json.dumps(failed.model_dump(mode="json"))


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

    failed = _run_action_to_terminal(
        session,
        ActionRepository(session),
        project_id=project_id,
        action_ref="utils.ftp.file.download",
        input_json={
            "items": [{"remote_path": "/remote.txt", "local_path": str(tmp_path / "remote.txt")}],
            "conflict_policy": "overwrite",
            "error_policy": "stop",
        },
        credential_ref=credential_ref,
    )

    assert failed.action_call.status == ActionCallStatus.FAILED
    assert "server PWD cannot contain NUL, CR, or LF" in json.dumps(failed.output_json)
    assert all(
        not any(isinstance(value, str) and ("\r" in value or "\n" in value) for value in call)
        for call in _UnsafePwdFTPTLS.instances[0].calls
        if call[0] == "cwd"
    )
