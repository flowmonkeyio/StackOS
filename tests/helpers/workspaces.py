"""Real, disposable same-remote Git worktrees for workspace boundary tests."""

import subprocess
from pathlib import Path


def same_remote_worktrees(tmp_path: Path) -> tuple[Path, Path, str]:
    source = tmp_path / "source"
    source.mkdir()

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(source), *args], check=True, capture_output=True, text=True
        )

    git("init")
    git(
        "-c",
        "user.name=Workspace Test",
        "-c",
        "user.email=workspace@example.test",
        "commit",
        "--allow-empty",
        "-m",
        "fixture",
    )
    remote = "https://example.test/approved/workspace.git"
    git("remote", "add", "origin", remote)
    first, second = tmp_path / "first", tmp_path / "second"
    for branch, root in (("first", first), ("second", second)):
        git("worktree", "add", "-b", branch, str(root))
    return first.resolve(), second.resolve(), remote
