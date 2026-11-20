"""Freeze the accepted existing export and stage website deltas. No build/network."""

import hashlib
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
BUILD = ROOT / "website/.output/public"
PRIOR = ROOT / "reports/seo/2026-10-05-workflow-pages/implementation"
ACCEPTED = HERE.parent / "implementation/source-preservation-97dc66f0.json"
REVIEW = HERE.parent / "implementation/independent-review.json"
PHASES = ("assets", "documents", "feeds")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def phase(name):
    return (
        "feeds"
        if name in ("sitemap.xml", "feed.xml")
        else "documents"
        if name.endswith(".html")
        else "assets"
    )


def inventory(folder):
    assert not any(p.is_symlink() for p in folder.rglob("*")), "Symlinks are not allowed."
    return [
        {"path": str(p.relative_to(folder)), "sha256": sha(p), "bytes": p.stat().st_size}
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    ]


def identity(rows):
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def main():
    accepted, review = read(ACCEPTED), read(REVIEW)
    assert (
        accepted["candidate_sha256"]
        == review["candidate_sha256"]
        == "97dc66f058dcac100801455a8e78071c8f2b5a15e19467d905e5f4ce7ed3b21b"
    )
    assert review["verdict"] == "approve"
    for row in accepted["changed_files"]:
        assert sha(ROOT / row["path"]) == row["sha256"], row["path"]
    rows = inventory(BUILD)
    assert identity(rows) == "ebf26e1d0ff132febae6797d2d304201144cf32f630e84db190c1dc440d4a8bd", (
        "Accepted output drift."
    )
    old_manifest = PRIOR / "build-stage-v2/output-manifest.json"
    old_upload = PRIOR / "build-stage-v2/upload-manifest.json"
    old_public = PRIOR / "public-after-retry/result.json"
    assert read(old_public)["status"] == "pass"
    assert read(old_public)["upload_manifest_sha256"] == sha(old_upload)
    assert read(old_upload)["build_manifest_sha256"] == sha(old_manifest)
    old = {r["path"]: r for r in read(old_manifest)}
    excluded = [
        r
        for r in rows
        if Path(r["path"]).name == ".DS_Store" or Path(r["path"]).name.startswith("._")
    ]
    files = [r for r in rows if r not in excluded]
    assert not any(
        r["path"].split("/")[0] == "StackOS"
        or (r["path"].startswith("google") and r["path"].endswith(".html"))
        for r in files
    )
    assert {
        r["path"] for r in files if r["path"].endswith("/index.html") or r["path"] == "index.html"
    } == {n for n in old if n.endswith("/index.html") or n == "index.html"}, (
        "Unexpected route-set change."
    )
    for name in (
        ".htaccess",
        "robots.txt",
        "feed.xml",
        "getting-started.md",
        "1a9b7d83c6b1250e4023904947fa6660.txt",
    ):
        assert name in old and sha(BUILD / name) == old[name]["sha256"], (
            "Unexpected static-control change: " + name
        )
    changed = [
        dict(r) for r in files if r["path"] not in old or r["sha256"] != old[r["path"]]["sha256"]
    ]
    for row in changed:
        row["phase"] = phase(row["path"])
        row["before_sha256"] = old.get(row["path"], {}).get("sha256")
    if (HERE / "frozen").exists():
        assert inventory(HERE / "frozen") == files, "Existing freeze differs."
    if (HERE / "staging").exists():
        expected_stage = sorted(
            [
                {"path": r["phase"] + "/" + r["path"], "sha256": r["sha256"], "bytes": r["bytes"]}
                for r in changed
            ],
            key=lambda r: r["path"],
        )
        assert {r["path"]: r for r in inventory(HERE / "staging")} == {
            r["path"]: r for r in expected_stage
        }, "Existing staging differs."
    for row in files:
        dest = HERE / "frozen" / row["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(BUILD / row["path"], dest)
        assert sha(dest) == row["sha256"]
    for row in changed:
        dest = HERE / "staging" / row["phase"] / row["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HERE / "frozen" / row["path"], dest)
    assert inventory(BUILD) == rows, "Output changed while freezing."
    write(HERE / "output-manifest.json", rows)
    write(HERE / "previous-output-manifest.json", list(old.values()))
    payload = {
        "items": [
            {"local_path": str(HERE / "staging" / p), "remote_path": "/"}
            for p in PHASES
            if (HERE / "staging" / p).exists()
        ],
        "conflict_policy": "overwrite",
        "error_policy": "stop",
        "follow_symlinks": False,
    }
    write(HERE / "upload-input.json", payload)
    write(
        HERE / "upload-manifest.json",
        {
            "run_plan_id": 556,
            "execution_run_id": 551,
            "source_candidate_sha256": accepted["candidate_sha256"],
            "source_manifest_sha256": sha(ACCEPTED),
            "review_sha256": sha(REVIEW),
            "output_identity_sha256": identity(rows),
            "output_manifest_sha256": sha(HERE / "output-manifest.json"),
            "previous_output_manifest_sha256": sha(HERE / "previous-output-manifest.json"),
            "prior_publication": "run-plan:551",
            "prior_public_pass_sha256": sha(old_public),
            "file_count": len(changed),
            "bytes": sum(r["bytes"] for r in changed),
            "files": sorted(changed, key=lambda r: (PHASES.index(r["phase"]), r["path"])),
            "files_identity_sha256": identity(sorted(changed, key=lambda r: r["path"])),
            "upload_input_sha256": sha(HERE / "upload-input.json"),
            "phase_order": list(PHASES),
            "excluded": excluded,
            "remote_deletions": [],
            "unchanged_static_controls_preserved": [
                ".htaccess",
                "robots.txt",
                "feed.xml",
                "getting-started.md",
                "1a9b7d83c6b1250e4023904947fa6660.txt",
            ],
            "protected_remote_only": ["/StackOS/", "/google1e7f2e5eaf4f6048.html"],
            "limits": [
                (
                    "Historical manifest is bound to successful publication551 evidence; "
                    "live preflight must verify overwritten prior bytes before upload."
                ),
                (
                    "Desktop binaries and remote-only ownership are excluded. Obsolete "
                    "remote assets are retained."
                ),
            ],
        },
    )
    print(
        json.dumps(
            {
                "status": "staged-locally",
                "files": len(changed),
                "bytes": sum(r["bytes"] for r in changed),
                "phases": {p: sum(r["phase"] == p for r in changed) for p in PHASES},
                "network_requests": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
