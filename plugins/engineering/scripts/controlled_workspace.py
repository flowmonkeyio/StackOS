"""Synchronous controller-owned file capabilities for isolated workers.

The host must deny worker writes to repo/view and disable native tools. This
module does not establish an OS sandbox. External writers must be quiescent:
POSIX rename has no compare-and-swap primitive against unrelated processes.
Detected stale content/identity fails closed; all broker dispatch is serialized.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import threading
import uuid
from contextlib import contextmanager, suppress
from pathlib import Path

from stackos.artifacts import redact_secret_text
from stackos.artifacts.checked_output import OutputPolicy, write_all

MAX_BYTES = 500_000
COMMAND_PROFILE = "stackos_worker_command"


def native_binary(binary, *, output_policy=None):
    if output_policy is not None:
        output_policy.require(str(binary))
    launcher = Path(shutil.which(str(binary)) or binary).resolve(strict=True)
    if launcher.name == "codex.js":
        system, machine = platform.system(), platform.machine()
        if system != "Darwin":
            raise ValueError("unsupported npm native launcher platform")
        command = ["/usr/sbin/sysctl", "-n", "hw.optional.arm64"]
        if output_policy is not None:
            output_policy.require(command)
        probe = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=5,
        )
        arm = machine == "arm64" or (probe.returncode == 0 and probe.stdout.strip() == "1")
        arch, package = ("aarch64", "arm64") if arm else ("x86_64", "x64")
        launcher = (
            launcher.parent.parent
            / "node_modules"
            / "@openai"
            / ("codex-darwin-" + package)
            / "vendor"
            / (arch + "-apple-darwin")
            / "bin"
            / "codex"
        )
    if not launcher.is_file() or not os.access(launcher, os.X_OK):
        raise ValueError("supported native Codex executable unavailable")
    with launcher.open("rb") as stream:
        magic = stream.read(4)
    if magic not in {b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe"}:
        raise ValueError("Codex launcher must resolve to a native executable")
    if output_policy is not None:
        output_policy.require(str(launcher))
    return str(launcher)


def test_path(path: str) -> bool:
    name = Path(path).name
    return (
        path.startswith("tests/")
        or "/__tests__/" in f"/{path}"
        or name.startswith("test_")
        or ".test." in name
        or ".spec." in name
    )


_OUTCOME_PRIVATE_PARTS = {
    ".git",
    ".aws",
    ".ssh",
    ".gnupg",
    ".azure",
    ".gcloud",
    ".config",
    ".claude",
    ".agents",
    ".serena",
    ".mcp",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".cache",
    "reports",
    "audits",
    "evidence",
    "logs",
    "sessions",
    "history",
    "transcripts",
    "rollouts",
    "credentials",
    "secrets",
    "runtime-state",
    "host-state",
}
_OUTCOME_SOURCE_ROOTS = {
    "src",
    "lib",
    "app",
    "apps",
    "packages",
    "stackos",
    "plugins",
    "scripts",
    "tests",
    "ui",
    "desktop",
    "website",
    "workers",
    "examples",
    "public",
    "assets",
    "static",
    "migrations",
    "include",
    "bin",
}
_OUTCOME_SOURCE_SUFFIXES = {
    ".py",
    ".pyi",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".vue",
    ".svelte",
    ".rs",
    ".go",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".java",
    ".kt",
    ".swift",
    ".rb",
    ".php",
    ".sh",
    ".bash",
    ".zsh",
    ".ps1",
    ".sql",
    ".html",
    ".css",
    ".scss",
    ".sass",
    ".svg",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".lock",
    ".txt",
    ".md",
    ".rst",
    ".xml",
    ".csv",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".woff",
    ".woff2",
    ".ttf",
}
_OUTCOME_CODE_SUFFIXES = {
    ".py",
    ".pyi",
    ".js",
    ".mjs",
    ".cjs",
    ".jsx",
    ".ts",
    ".tsx",
    ".vue",
    ".svelte",
    ".rs",
    ".go",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".java",
    ".kt",
    ".swift",
    ".rb",
    ".php",
    ".sh",
    ".sql",
}
_OUTCOME_ROOT_FILES = {
    "pyproject.toml",
    "uv.lock",
    "requirements.txt",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Cargo.toml",
    "Cargo.lock",
    "go.mod",
    "go.sum",
    "Makefile",
    "CMakeLists.txt",
    "alembic.ini",
    "setup.py",
    "setup.cfg",
    "tsconfig.json",
    "vite.config.ts",
    "README.md",
    "LICENSE",
}


def outcome_selection(relative: str, *, changed: bool, worker: bool = False) -> str | None:
    path = Path(relative)
    parts = set(path.parts)
    name = path.name.lower()
    if path.is_absolute() or ".." in parts or not path.parts:
        raise ValueError(f"invalid outcome path: {relative}")
    private = (
        bool(parts & _OUTCOME_PRIVATE_PARTS)
        or name
        in {
            "auth.json",
            "auth.token",
            "credentials.json",
            "credentials.db",
            "history.jsonl",
            "config.jsonl",
            ".netrc",
            ".npmrc",
            "report.md",
            "report.json",
            "receipt.json",
            "progress.md",
            "id_rsa",
            "id_ed25519",
            "stackos.db",
            "state.sqlite",
        }
        or name == ".env"
        or name.startswith(".env.")
        or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".sqlite", ".db", ".jsonl"}
        or name.endswith(("-receipt.json", "-report.json", "-report.md", "-progress.md"))
    )
    # Static role/configuration edits are reviewable data. The rest of .codex
    # remains a host state boundary even if tracked or newly changed.
    static_guidance = path.parts[0] == ".codex" and (
        (len(path.parts) >= 3 and path.parts[1] in {"agents", "orchestrator"})
        or relative == ".codex/config.toml"
    )
    if path.parts[0] == ".codex" and not static_guidance:
        private = True
    if private:
        if changed:
            raise ValueError(
                "review held: changed path has forbidden runtime/report/credential "
                f"provenance: {relative}"
            )
        return None
    # Ordinary workers may inspect unchanged project guidance, but private
    # provenance above always wins and the actual changed status stays intact.
    if (
        worker
        and not changed
        and (
            path.name == "AGENTS.md"
            or (path.parts[0] == "docs" and path.suffix in {".md", ".rst"})
            or relative.startswith((".codex/agents/", ".codex/orchestrator/"))
        )
    ):
        return "project-guidance-data"
    guidance = name in {"agents.md", "claude.md", "plan.md", ".mcp.json"} or static_guidance
    if guidance:
        return "changed-guidance-data" if changed else None
    if (
        path.suffix.lower() in _OUTCOME_CODE_SUFFIXES
        or (
            path.parts[0] in _OUTCOME_SOURCE_ROOTS
            and path.suffix.lower() in _OUTCOME_SOURCE_SUFFIXES
        )
        or (len(path.parts) == 1 and relative in _OUTCOME_ROOT_FILES)
    ):
        return "changed-deliverable" if changed else "source-runtime-dependency"
    if (
        changed
        and (path.parts[0] == "docs" or len(path.parts) == 1)
        and path.suffix.lower() in _OUTCOME_SOURCE_SUFFIXES
    ):
        return "changed-document-or-configuration-data"
    if changed:
        raise ValueError(
            "review held: ambiguous outcome provenance requires controller scope "
            f"reconciliation: {relative}"
        )
    return None


class Workspace:
    """Manifest-admitted UTF-8 reads and stage-scoped synchronous edits."""

    def __init__(self, repo, view, scratch, role, manifest_digest, *, known_secrets=(),
                 output_policy=None, emit=None):
        self.repo, self.view, self.scratch = (Path(p).absolute() for p in (repo, view, scratch))
        self.role = role
        self.known_secrets = tuple(value for value in known_secrets if value)
        self.policy = output_policy or OutputPolicy(self.known_secrets)
        self.emit = emit or (lambda record: None)
        self.lock = threading.RLock()
        self.closed = False
        self.broken = False
        self.manifest_path = self.view.parent / "outcome-manifest.json"
        with self.manifest_path.open("rb") as stream:
            data = stream.read(10_000_001)
        if len(data) > 10_000_000 or hashlib.sha256(data).hexdigest() != manifest_digest:
            raise ValueError("manifest admission mismatch")
        self.manifest = self.policy.decode_json(data)
        if self.manifest.get("selection_contract") != "controller-source-outcome-v1":
            raise ValueError("unsupported selection contract")
        changed = {self._path(path) for path in self.manifest["changed_paths"]}
        self.entries = {}
        for entry in self.manifest["files"]:
            path = self._path(entry["path"])
            expected_selection = outcome_selection(
                path,
                changed=path in changed,
                worker=role in {"planner", "executor", "designer", "test_writer"},
            )
            if path in self.entries:
                raise ValueError("duplicate manifest path")
            digest = entry.get("sha256")
            if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
                raise ValueError("invalid manifest digest")
            mode = entry.get("mode")
            selection = entry.get("selection")
            if (
                type(mode) is not int
                or mode & ~0o777
                or not isinstance(selection, str)
                or selection
                not in {
                    "changed-guidance-data",
                    "changed-deliverable",
                    "source-runtime-dependency",
                    "changed-document-or-configuration-data",
                    "project-guidance-data",
                }
                or selection != expected_selection
            ):
                raise ValueError("invalid manifest file metadata")
            self.entries[path] = {
                "path": path,
                "sha256": digest,
                "mode": mode,
                "selection": selection,
            }
        self.manifest_digest = manifest_digest
        self.repo_fd = os.open(self.repo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            self.view_fd = os.open(self.view, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except BaseException:
            os.close(self.repo_fd)
            raise

    def _safe(self, text):
        self.policy.text(text)
        clean = redact_secret_text(text)
        for value in self.known_secrets:
            for representation in (
                value,
                json.dumps(value)[1:-1],
                json.dumps(value, ensure_ascii=False)[1:-1],
            ):
                clean = clean.replace(representation, "[redacted]")
        return clean

    def _path(self, path):
        if (
            not isinstance(path, str)
            or not path
            or len(path.encode()) > 4096
            or path.startswith("/")
            or "\\" in path
            or any(ord(c) < 32 for c in path)
            or any(p in {"", ".", ".."} for p in path.split("/"))
            or self._safe(path) != path
        ):
            raise ValueError("invalid workspace path")
        return path

    @contextmanager
    def _parent(self, root, path, create=False):
        fd = os.dup(root)
        try:
            for part in path.split("/")[:-1]:
                if create:
                    with suppress(FileExistsError):
                        os.mkdir(part, 0o700, dir_fd=fd)
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            yield fd, path.split("/")[-1]
        finally:
            os.close(fd)

    def _read_at(self, parent, name):
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        except FileNotFoundError:
            return None, None
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_size > MAX_BYTES
            ):
                raise ValueError("workspace requires bounded ordinary unshared files")
            data = b""
            while len(data) <= MAX_BYTES:
                chunk = os.read(fd, min(65536, MAX_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data += chunk
            after = os.fstat(fd)
            if len(data) > MAX_BYTES or self._identity(before) != self._identity(after):
                raise ValueError("workspace file changed during read")
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                raise ValueError("unsupported binary file; UTF-8 required") from None
            if "\0" in text:
                raise ValueError("unsupported binary file; UTF-8 required")
            if self._safe(text) != text:
                raise ValueError("workspace content withheld by secret filter")
            return data, before
        finally:
            os.close(fd)

    @staticmethod
    def _identity(info):
        return (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
            info.st_nlink,
        )

    def specs(self):
        def spec(name, description, properties, required):
            return {
                "type": "function",
                "name": name,
                "description": description,
                "inputSchema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            }

        result = [
            spec(
                "workspace_list",
                "List admitted paths with SHA256, paginated.",
                {"offset": {"type": "integer", "minimum": 0}},
                [],
            ),
            spec(
                "workspace_read",
                "Read one admitted bounded UTF-8 file and its SHA256.",
                {"path": {"type": "string"}},
                ["path"],
            ),
        ]
        if self.role in {"executor", "test_writer"}:
            result.append(
                spec(
                    "workspace_edit",
                    "Synchronously write or delete one stage-authorized UTF-8 "
                    "file. expected_sha256=null creates; content=null deletes.",
                    {
                        "path": {"type": "string"},
                        "expected_sha256": {"type": ["string", "null"]},
                        "content": {"type": ["string", "null"]},
                    },
                    ["path", "expected_sha256", "content"],
                )
            )
        return result

    tools = specs

    def dispatch(self, name, args):
        self.policy.require([name, args])
        with self.lock:
            if self.closed or self.broken:
                raise ValueError("workspace broker is closed or requires reconciliation")
            if not isinstance(args, dict):
                raise ValueError("workspace arguments must be an object")
            try:
                if name == "workspace_list" and set(args) <= {"offset"}:
                    offset = args.get("offset", 0)
                    if type(offset) is not int or offset < 0:
                        raise ValueError("invalid list offset")
                    paths = sorted(self.entries)
                    return {
                        "files": [self.entries[p] for p in paths[offset : offset + 200]],
                        "next_offset": offset + 200 if offset + 200 < len(paths) else None,
                        "manifest_sha256": self.manifest_digest,
                    }
                if name == "workspace_read" and set(args) == {"path"}:
                    path = self._path(args["path"])
                    if path not in self.entries:
                        raise ValueError("path is not admitted")
                    with self._parent(self.view_fd, path) as (fd, leaf):
                        data, _ = self._read_at(fd, leaf)
                    if (
                        data is None
                        or hashlib.sha256(data).hexdigest() != self.entries[path]["sha256"]
                    ):
                        raise ValueError("filtered view differs from manifest")
                    return {
                        "path": path,
                        "content": data.decode(),
                        "sha256": self.entries[path]["sha256"],
                        "manifest_sha256": self.manifest_digest,
                    }
                if name == "workspace_edit" and set(args) == {"path", "expected_sha256", "content"}:
                    return self._edit(**args)
                raise ValueError("unsupported workspace call")
            except OSError:
                raise ValueError(
                    "workspace filesystem operation failed; reconciliation may be required"
                ) from None

    def _replace(self, fd, leaf, data, mode):
        # This final descriptor-safe sink owns admission too; callers cannot
        # bypass it by handing the replacement function unchecked bytes.
        self.policy.text(data.decode("utf-8"))
        temporary = ".broker-" + uuid.uuid4().hex
        out = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=fd
        )
        try:
            with os.fdopen(out, "wb") as stream:
                write_all(stream, data)
                stream.flush()
                os.fchmod(stream.fileno(), mode)
                os.fsync(stream.fileno())
            os.rename(temporary, leaf, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        except BaseException:
            # Preserve partial temporary bytes for controller reconciliation.
            raise

    def _edit(self, path, expected_sha256, content):
        path = self._path(path)
        if self.role not in {"executor", "test_writer"} or test_path(path) != (
            self.role == "test_writer"
        ):
            raise ValueError("path is outside this role stage")
        for protected in (self.view, self.scratch, self.manifest_path, Path(__file__)):
            target = self.repo / path
            if target == protected or protected in target.parents:
                raise ValueError("trusted controller runtime path is protected")
        reason = outcome_selection(path, changed=True)
        if expected_sha256 is not None and (
            not isinstance(expected_sha256, str)
            or not re.fullmatch("[0-9a-f]{64}", expected_sha256)
        ):
            raise ValueError("invalid expected SHA256")
        if content is not None and (
            not isinstance(content, str)
            or "\0" in content
            or len(content.encode()) > MAX_BYTES
            or self._safe(content) != content
        ):
            raise ValueError("bounded secret-free UTF-8 content required")
        data = self.policy.encoded_text(content) if content is not None else None
        after = hashlib.sha256(data).hexdigest() if data is not None else None
        record = {"path": path, "before_sha256": expected_sha256, "after_sha256": after}
        self.emit({"type": "workspace.edit_intent", **record})
        try:
            with self._parent(self.repo_fd, path, create=True) as (fd, leaf):
                current, info = self._read_at(fd, leaf)
                before = hashlib.sha256(current).hexdigest() if current is not None else None
                if before != expected_sha256 or (current is None and data is None):
                    raise ValueError("file changed or missing; reconcile before editing")
                if current is not None and path not in self.entries:
                    raise ValueError("existing file is not admitted for editing")
                if info is not None and self._identity(
                    os.stat(leaf, dir_fd=fd, follow_symlinks=False)
                ) != self._identity(info):
                    raise ValueError("file identity changed before edit")
                mode = stat.S_IMODE(info.st_mode) if info else 0o600
                entries = dict(self.entries)
                if data is None:
                    entries.pop(path)
                else:
                    entries[path] = {
                        "path": path, "sha256": after,
                        "mode": 0o600 | (mode & 0o111), "selection": reason,
                    }
                manifest = dict(self.manifest)
                manifest["files"] = [entries[p] for p in sorted(entries)]
                deleted = set(manifest.get("deleted", []))
                deleted.add(path) if data is None else deleted.discard(path)
                manifest["deleted"] = sorted(deleted)
                manifest["changed_paths"] = sorted(set(manifest.get("changed_paths", [])) | {path})
                encoded = self.policy.encoded_json(
                    manifest, sort_keys=True, separators=(",", ":"), suffix="\n"
                )
                if data is None:
                    os.unlink(leaf, dir_fd=fd)
                    os.fsync(fd)
                else:
                    self._replace(fd, leaf, data, mode)
            with self._parent(self.view_fd, path, create=True) as (fd, leaf):
                if data is None:
                    os.unlink(leaf, dir_fd=fd)
                else:
                    self._replace(fd, leaf, data, 0o600 | (mode & 0o111))
            fd = os.open(self.manifest_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                self._replace(fd, self.manifest_path.name, encoded, 0o600)
            finally:
                os.close(fd)
            self.entries, self.manifest = entries, manifest
            self.manifest_digest = hashlib.sha256(encoded).hexdigest()
            result = {**record, "manifest_sha256": self.manifest_digest}
            self.emit({"type": "workspace.edit_completed", **result})
            return result
        except BaseException:
            self.broken = True
            self.emit({"type": "workspace.edit_failed", **record, "requires_reconciliation": True})
            raise

    def close(self):
        with self.lock:
            if not self.closed:
                self.closed = True
                os.close(self.repo_fd)
                os.close(self.view_fd)
                self.emit(
                    {
                        "type": "workspace.broker_closed",
                        "manifest_sha256": self.manifest_digest,
                        "requires_reconciliation": self.broken,
                    }
                )
