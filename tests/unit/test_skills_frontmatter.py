"""Smoke checks for ``skills/**/SKILL.md`` frontmatter.

Three invariants:

1. Frontmatter parses as valid YAML and carries the seven contract
   keys named in PLAN.md L957-L968: ``name``, ``description``,
   ``version``, ``runtime_support``, ``derived_from``, ``license``,
   ``allowed_tools``.
2. ``allowed_tools`` matches the corresponding entry in
   ``stackos.mcp.permissions.SKILL_TOOL_GRANTS`` — the
   frontmatter is human-readable docs; the registry is the canonical
   enforcement (audit B-10), and the two MUST agree.
3. When repo-level skills exist, every skill links the shared operating
   contract so operator-agent flow does not drift between prompts.

Skip the tests when ``skills/`` is empty; StackOS domain behavior now lives in
plugin manifests and workflow templates.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from stackos.mcp.permissions import SKILL_TOOL_GRANTS

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPO_ROOT / "skills"
SHARED_REFERENCE_PATHS: tuple[Path, ...] = (
    SKILLS_ROOT / "references" / "skill-operating-contract.md",
)
SHARED_REFERENCE_MARKERS: tuple[str, ...] = (
    "## Shared operating contract",
    "../../references/skill-operating-contract.md",
)

REQUIRED_FRONTMATTER_KEYS: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "version",
        "runtime_support",
        "derived_from",
        "license",
        "allowed_tools",
    }
)


def _iter_skill_files() -> list[Path]:
    if not SKILLS_ROOT.exists():
        return []
    return sorted(SKILLS_ROOT.rglob("SKILL.md"))


def _parse_frontmatter(path: Path) -> dict[str, object]:
    """Extract YAML frontmatter from a ``SKILL.md`` file."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: missing leading '---' delimiter")
    rest = text[4:]
    if "\n---\n" not in rest:
        raise ValueError(f"{path}: missing closing '---' delimiter")
    fm_text = rest.split("\n---\n", 1)[0]
    parsed = yaml.safe_load(fm_text)
    if not isinstance(parsed, dict):
        raise ValueError(f"{path}: frontmatter is not a mapping")
    return parsed


def _skill_key_from_path(path: Path) -> str:
    """Return the ``<phase>/<skill>`` key used in ``SKILL_TOOL_GRANTS``."""
    rel = path.relative_to(SKILLS_ROOT)
    # SKILL.md sits at the leaf; the skill key is the parent directory
    # path expressed as ``phase/skill``.
    parent = rel.parent
    return parent.as_posix()


def test_frontmatter_required_keys_present() -> None:
    files = _iter_skill_files()
    if not files:
        pytest.skip("skills/ tree empty")

    failures: list[str] = []
    for path in files:
        try:
            fm = _parse_frontmatter(path)
        except (ValueError, yaml.YAMLError) as exc:
            failures.append(str(exc))
            continue
        missing = REQUIRED_FRONTMATTER_KEYS - set(fm.keys())
        if missing:
            failures.append(f"{path.relative_to(REPO_ROOT)}: missing keys {sorted(missing)}")

    assert not failures, "\n".join(failures)


def test_runtime_support_lists_both_runtimes() -> None:
    """Per PLAN.md the same SKILL.md ships to both ~/.codex and ~/.claude."""
    files = _iter_skill_files()
    if not files:
        pytest.skip("skills/ tree empty")

    for path in files:
        fm = _parse_frontmatter(path)
        runtimes = fm.get("runtime_support", [])
        assert isinstance(runtimes, list), f"{path}: runtime_support must be a list"
        assert "codex" in runtimes, f"{path}: runtime_support must include 'codex'"
        assert "claude-code" in runtimes, f"{path}: runtime_support must include 'claude-code'"


def test_allowed_tools_matches_permissions_matrix() -> None:
    """Frontmatter ``allowed_tools`` must match ``SKILL_TOOL_GRANTS``."""
    files = _iter_skill_files()
    if not files:
        pytest.skip("skills/ tree empty")

    failures: list[str] = []
    for path in files:
        fm = _parse_frontmatter(path)
        skill_key = _skill_key_from_path(path)
        allowed = fm.get("allowed_tools", [])
        assert isinstance(allowed, list), f"{path}: allowed_tools must be a list"
        declared = frozenset(allowed)
        registered = SKILL_TOOL_GRANTS.get(skill_key)
        if registered is None:
            failures.append(f"{skill_key}: no entry in SKILL_TOOL_GRANTS")
            continue
        if declared != registered:
            only_in_frontmatter = declared - registered
            only_in_registry = registered - declared
            failures.append(
                f"{skill_key}: frontmatter ↔ registry mismatch — "
                f"only in frontmatter: {sorted(only_in_frontmatter)}; "
                f"only in registry: {sorted(only_in_registry)}"
            )

    assert not failures, "\n".join(failures)


def test_skills_link_shared_operating_references() -> None:
    """Every skill must load the shared agent-flow and SEO-quality rules."""
    files = _iter_skill_files()
    if not files:
        pytest.skip("skills/ tree empty")

    missing_reference_files = [
        str(path.relative_to(REPO_ROOT)) for path in SHARED_REFERENCE_PATHS if not path.exists()
    ]
    assert not missing_reference_files, f"missing shared references: {missing_reference_files}"

    failures: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        missing = [marker for marker in SHARED_REFERENCE_MARKERS if marker not in text]
        if missing:
            failures.append(
                f"{path.relative_to(REPO_ROOT)}: missing shared-reference markers {missing}"
            )

    assert not failures, "\n".join(failures)
