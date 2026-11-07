"""Bounded proof against this assignment's pre-edit source snapshot."""

import hashlib
import json
import pathlib
import re

root = pathlib.Path(__file__).resolve().parents[4]
report = root / "reports/design/2026-10-05-template-review/implementation"
packet = json.loads((report / "packet.json").read_text())
baseline = report / "baseline"
results = []
for slug in (
    "how-to-do-keyword-research-for-ai-search",
    "how-ai-orchestrators-triage-feedback",
    "how-to-refine-ai-agent-workflow",
):
    path = pathlib.Path("website/content/articles") / (slug + ".md")
    old, new = (baseline / path).read_text(), (root / path).read_text()
    table = re.search(r"(?m)^\|[^\n]+\n\| *---[^\n]+\n(?:\|[^\n]+\n)+", old)
    cells = [
        [cell.strip() for cell in line.strip("|").split("|")]
        for line in table[0].rstrip().splitlines()
    ]
    rows = list(
        re.finditer(
            r"::article-evidence-row\{([^\n]+)\}\n#evidence\n([\s\S]*?)\n\n#decision\n([\s\S]*?)\n::",
            new,
        )
    )
    assert len(rows) == len(cells) - 2, slug
    for row, expected in zip(rows, cells[2:], strict=False):
        attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', row[1]))
        assert [attrs["label"], row[2], row[3]] == expected, (slug, expected)
        assert [attrs["record-heading"], attrs["evidence-label"], attrs["decision-label"]] == cells[
            0
        ], slug
    restored = new[: rows[0].start()] + table[0].rstrip() + new[rows[-1].end() :]
    assert restored == old, f"Copy/frontmatter/links outside first table changed: {slug}"
    results.append(
        {
            "article": slug,
            "exact_rows_preserved": len(rows),
            "all_other_source_bytes_preserved": True,
        }
    )

for path in packet["write_ownership"]:
    if "/pages/" not in path:
        continue
    old, new = (baseline / path).read_text(), (root / path).read_text()
    for pattern, label in [
        (r"<script setup[\s\S]*?</script>", "script/metadata"),
        (
            r'<(?:header|section) class="(?:article|detail|integration-detail)-hero"'
            r"[\s\S]*?</(?:header|section)>",
            "hero",
        ),
    ]:
        assert re.search(pattern, old)[0] == re.search(pattern, new)[0], (path, label)

preserved = [
    "website/app/assets/css/main.css",
    "website/app/components/LibraryFrame.vue",
    "website/app/components/SiteHeader.vue",
    "website/app/pages/getting-started.vue",
]
for path in preserved:
    assert (baseline / path).read_bytes() == (root / path).read_bytes(), path

before_build = json.loads((report / "before-build-hashes.json").read_text())
changed_by_build = [
    path
    for path, digest in before_build.items()
    if hashlib.sha256((root / path).read_bytes()).hexdigest() != digest
]
assert not changed_by_build, changed_by_build
candidate = []
for path in packet["write_ownership"]:
    file = root / path
    changed = not (baseline / path).exists() or file.read_bytes() != (baseline / path).read_bytes()
    if changed:
        candidate.append({"path": path, "sha256": hashlib.sha256(file.read_bytes()).hexdigest()})
identity = hashlib.sha256(json.dumps(candidate, sort_keys=True).encode()).hexdigest()
result = {
    "status": "passed",
    "ticket": packet["ticket_key"],
    "attempt_id": packet["attempt_id"],
    "contract_revision": packet["constraint_revision"],
    "candidate_sha256": identity,
    "changed_files": candidate,
    "articles": results,
    "shell_files_preserved": preserved,
    "source_files_unchanged_by_build": len(before_build),
}
(report / "source-preservation.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
