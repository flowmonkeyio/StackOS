"""Independent, read-only final-app binding and offline contract check."""

import argparse
import datetime
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

EXPECTED_HOST_COMMIT = "de7c87c92d085ec221a2de4f32dbf481f7269bcd"
EXPECTED_PACKAGE_COMMIT = "b001096c90e661e14b9cc40c861ce5bdc79f4e5b"
EXPECTED_MANIFESTS = {
    "host-candidate-v2.json": "acda1c40a91128ce26617440d9d9f34f1d21b799677bbe27316532e8b823cf62",
    "package-candidate.json": "bb7f50dd2c4aa057fb9516b8fe66c51e8a1a569a1071c0f525f54167c9c10dd1",
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    evidence = Path(__file__).resolve().parent
    manifests = {}
    for filename, expected in EXPECTED_MANIFESTS.items():
        path = evidence / filename
        assert digest(path) == expected, f"Changed accepted manifest: {filename}"
        manifests[filename] = json.loads(path.read_text())
    app = args.app.resolve()
    payload = app / "Contents/Resources/stackos"
    sites = list((payload / ".venv/lib").glob("python*/site-packages"))
    assert len(sites) == 1, f"Expected one packaged site-packages, found {len(sites)}"
    site = sites[0]
    selected_host = {
        "stackos/auth_providers/repository/credentials.py",
        "stackos/auth_providers/repository/resolution.py",
    }
    checks = []
    for item in manifests["host-candidate-v2.json"]["files"]:
        if item["path"] in selected_host:
            target = site / item["path"]
            actual = digest(target)
            assert actual == item["sha256"], f"Packaged host mismatch: {item['path']}"
            checks.append({"path": item["path"], "sha256": actual, "matches_accepted": True})
    for item in manifests["package-candidate.json"]["files"]:
        if item["path"].startswith("src/"):
            rel = item["path"][4:]
            actual = digest(site / rel)
            assert actual == item["sha256"], f"Packaged connector mismatch: {rel}"
            checks.append({"path": rel, "sha256": actual, "matches_accepted": True})
    assert len(checks) == 6
    code = """
import importlib.metadata
import json
from pathlib import Path
import stackos
import stackos_connectors
from stackos_connectors.auth import get_auth_contract, scope_satisfies

READ = "https://www.googleapis.com/auth/webmasters.readonly"
WRITE = "https://www.googleapis.com/auth/webmasters"
methods = (
    "oauth2_authorization_code",
    "oauth2_access_token",
    "oauth2_refresh_token",
    "service-account",
)
contracts = []
for method in methods:
    default = get_auth_contract("google-search-console", method=method)
    explicit_read = get_auth_contract(
        "google-search-console", method=method, config={"access_mode": "readonly"}
    )
    write = get_auth_contract(
        "google-search-console", method=method, config={"access_mode": "sitemap_write"}
    )
    assert default.scopes == explicit_read.scopes == (READ,)
    assert write.scopes == (WRITE,)
    contracts.append(
        {
            "method": method,
            "default": list(default.scopes),
            "readonly": list(explicit_read.scopes),
            "sitemap_write": list(write.scopes),
        }
    )
assert scope_satisfies("google-search-console", READ, {WRITE})
assert not scope_satisfies("google-search-console", WRITE, {READ})
assert not scope_satisfies("google-analytics", READ, {WRITE})
versions = {
    "stackos": importlib.metadata.version("stackos"),
    "stackos-connectors": importlib.metadata.version("stackos-connectors"),
}
assert versions == {"stackos": "2.1.34", "stackos-connectors": "0.2.1"}, versions
print(
    json.dumps(
        {
            "versions": versions,
            "contracts": contracts,
            "scope_compatibility": "pass",
            "loaded_sources": {
                "stackos": str(Path(stackos.__file__).resolve()),
                "stackos_connectors": str(Path(stackos_connectors.__file__).resolve()),
            },
        }
    )
)
"""
    with tempfile.TemporaryDirectory(prefix="stackos-artifact-review-") as isolated:
        result = subprocess.run(
            [str(payload / ".venv/bin/python"), "-I", "-B", "-c", code],
            cwd=isolated,
            capture_output=True,
            text=True,
            timeout=45,
            check=True,
        )
    offline = json.loads(result.stdout)
    for loaded in offline["loaded_sources"].values():
        assert Path(loaded).is_relative_to(site), f"Source shadow: {loaded}"
    build_info_path = payload / "build-info.json"
    build_info = json.loads(build_info_path.read_text())
    assert build_info["version"] == "2.1.34"
    report = {
        "status": "pass",
        "reviewer": "day1_deploy_review",
        "independent": True,
        "run_plan_id": 572,
        "execution_id": 567,
        "attempt": 1,
        "contract_revision": 1,
        "source_host_commit": EXPECTED_HOST_COMMIT,
        "source_package_commit": EXPECTED_PACKAGE_COMMIT,
        "checked_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "app": str(app),
        "accepted_manifest_hashes": EXPECTED_MANIFESTS,
        "packaged_files": checks,
        "offline_bundled_python": offline,
        "build_info": build_info,
        "limitations": [
            "Read-only artifact binding and pure auth-contract evaluation only.",
            "Signing/notarization trust verification remains with main.",
            "No installation, restart, real credential access or provider action.",
            "Live activation and sitemap retry follow operator installation.",
        ],
    }
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": "pass",
                "report": str(args.report),
                "sha256": digest(args.report),
                "files_verified": len(checks),
                "methods_verified": len(offline["contracts"]),
            }
        )
    )


if __name__ == "__main__":
    main()
