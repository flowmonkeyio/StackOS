"""Load bundled and repo-local StackOS skill presets."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

import stackos
from stackos.plugins.manifest import plugin_sort_key
from stackos.repositories.base import ConflictError, NotFoundError
from stackos.skill_presets.schema import SkillPresetSpec, parse_skill_preset_bundle_yaml

PLUGIN_SKILL_PRESET_PRECEDENCE = 10
REPO_SKILL_PRESET_PRECEDENCE = 30
MAX_SKILL_PRESET_FILE_BYTES = 256_000


def _package_parent() -> Path:
    return Path(stackos.__file__).resolve().parent.parent


def _clone_plugins_root() -> Path | None:
    root = _package_parent() / "plugins"
    return root if root.is_dir() else None


def _bundled_plugins_root() -> Traversable | None:
    root = resources.files("stackos").joinpath("_assets").joinpath("plugins")
    return root if root.is_dir() else None


def _iter_yaml_paths(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        [
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in {".yaml", ".yml"}
        ]
    )


def _iter_traversable_yaml(root: Traversable) -> list[Traversable]:
    out: list[Traversable] = []

    def walk(node: Traversable) -> None:
        for child in node.iterdir():
            if child.is_dir():
                walk(child)
            elif child.name.lower().endswith((".yaml", ".yml")):
                out.append(child)

    walk(root)
    return sorted(out, key=lambda item: str(item))


class SkillPresetSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    name: str
    version: str
    description: str = ""
    domain: str | None = None
    skill_type: str
    source: str
    precedence: int
    plugin_slug: str | None = None
    origin_path: str | None = None
    applies_to_workflows: list[str]
    generic_preset: bool
    adaptation_required: bool
    shadowed_by: str | None = None


class LoadedSkillPreset(BaseModel):
    summary: SkillPresetSummaryOut
    preset: SkillPresetSpec


class SkillPresetListOut(BaseModel):
    presets: list[SkillPresetSummaryOut]
    include_shadowed: bool = False


@dataclass(frozen=True)
class _Candidate:
    preset: LoadedSkillPreset
    order: int


class SkillPresetLoader:
    """Load skill preset contracts from plugin assets and repo overrides."""

    def list_presets(
        self,
        *,
        repo_root: str | None = None,
        plugin_slug: str | None = None,
        workflow_key: str | None = None,
        include_shadowed: bool = False,
    ) -> SkillPresetListOut:
        candidates = self._load_candidates(repo_root=repo_root, plugin_slug=plugin_slug)
        resolved = self._resolve_candidates(candidates, include_shadowed=include_shadowed)
        if workflow_key is not None:
            resolved = [
                item
                for item in resolved
                if workflow_key in item.summary.applies_to_workflows
                or not item.summary.applies_to_workflows
            ]
        return SkillPresetListOut(
            presets=[item.summary for item in resolved],
            include_shadowed=include_shadowed,
        )

    def describe_preset(
        self,
        *,
        key: str,
        repo_root: str | None = None,
        plugin_slug: str | None = None,
        source: str | None = None,
    ) -> LoadedSkillPreset:
        candidates = self._load_candidates(repo_root=repo_root, plugin_slug=plugin_slug)
        resolved = self._resolve_candidates(candidates, include_shadowed=True)
        matches = [
            item
            for item in resolved
            if item.summary.key == key and (source is None or item.summary.source == source)
        ]
        if not matches:
            raise NotFoundError(
                f"skill preset {key!r} not found",
                data={"key": key, "plugin_slug": plugin_slug},
            )
        matches.sort(key=lambda item: item.summary.precedence, reverse=True)
        return matches[0]

    def _load_candidates(
        self,
        *,
        repo_root: str | None,
        plugin_slug: str | None,
    ) -> list[_Candidate]:
        candidates: list[_Candidate] = []
        order = 0
        for loaded in self._load_plugin_presets(plugin_slug=plugin_slug):
            candidates.append(_Candidate(loaded, order))
            order += 1
        if repo_root is not None:
            for loaded in self._load_repo_presets(repo_root=repo_root):
                candidates.append(_Candidate(loaded, order))
                order += 1
        return candidates

    def _resolve_candidates(
        self,
        candidates: list[_Candidate],
        *,
        include_shadowed: bool,
    ) -> list[LoadedSkillPreset]:
        by_key: dict[str, _Candidate] = {}
        for candidate in candidates:
            current = by_key.get(candidate.preset.summary.key)
            if current is None or self._beats(candidate, current):
                by_key[candidate.preset.summary.key] = candidate
        resolved: list[LoadedSkillPreset] = []
        for candidate in candidates:
            winner = by_key[candidate.preset.summary.key]
            preset = candidate.preset.model_copy(deep=True)
            if candidate is not winner:
                preset.summary.shadowed_by = winner.preset.summary.source
                if not include_shadowed:
                    continue
            resolved.append(preset)
        resolved.sort(
            key=lambda item: (
                *plugin_sort_key(item.summary.plugin_slug, None),
                item.summary.key,
                -item.summary.precedence,
                item.summary.source,
            )
        )
        return resolved

    def _beats(self, left: _Candidate, right: _Candidate) -> bool:
        if left.preset.summary.precedence != right.preset.summary.precedence:
            return left.preset.summary.precedence > right.preset.summary.precedence
        return left.order > right.order

    def _load_plugin_presets(self, *, plugin_slug: str | None) -> list[LoadedSkillPreset]:
        loaded: list[LoadedSkillPreset] = []
        seen_paths: set[str] = set()
        clone_root = _clone_plugins_root()
        if clone_root is not None:
            for path in _iter_yaml_paths(clone_root):
                if "/skill-presets/" not in path.as_posix():
                    continue
                if plugin_slug is not None and path.relative_to(clone_root).parts[0] != plugin_slug:
                    continue
                seen_paths.add(path.resolve().as_posix())
                loaded.extend(self._loaded_from_file_path(path, clone_root=clone_root))
        bundled_root = _bundled_plugins_root()
        if bundled_root is not None:
            for node in _iter_traversable_yaml(bundled_root):
                node_key = str(node)
                if node_key in seen_paths:
                    continue
                parts = Path(node_key).parts
                if "skill-presets" not in parts:
                    continue
                plugin = parts[parts.index("skill-presets") - 1]
                if plugin_slug is not None and plugin != plugin_slug:
                    continue
                loaded.extend(self._loaded_from_traversable(node, plugin_slug=plugin))
        return loaded

    def _load_repo_presets(self, *, repo_root: str) -> list[LoadedSkillPreset]:
        root = Path(repo_root).expanduser().resolve()
        preset_root = root / ".stackos" / "skill-presets"
        loaded: list[LoadedSkillPreset] = []
        for path in _iter_yaml_paths(preset_root):
            loaded.extend(self._loaded_from_repo_path(path))
        return loaded

    def _loaded_from_file_path(
        self,
        path: Path,
        *,
        clone_root: Path,
    ) -> list[LoadedSkillPreset]:
        if path.stat().st_size > MAX_SKILL_PRESET_FILE_BYTES:
            raise ConflictError("skill preset file is too large", data={"path": str(path)})
        plugin_slug = path.relative_to(clone_root).parts[0]
        specs = parse_skill_preset_bundle_yaml(path.read_text(encoding="utf-8"))
        return [
            self._loaded(
                spec,
                source="plugin",
                precedence=PLUGIN_SKILL_PRESET_PRECEDENCE,
                plugin_slug=plugin_slug,
                origin_path=path.as_posix(),
            )
            for spec in specs
        ]

    def _loaded_from_traversable(
        self,
        node: Traversable,
        *,
        plugin_slug: str,
    ) -> list[LoadedSkillPreset]:
        specs = parse_skill_preset_bundle_yaml(node.read_text(encoding="utf-8"))
        return [
            self._loaded(
                spec,
                source="plugin",
                precedence=PLUGIN_SKILL_PRESET_PRECEDENCE,
                plugin_slug=plugin_slug,
                origin_path=str(node),
            )
            for spec in specs
        ]

    def _loaded_from_repo_path(self, path: Path) -> list[LoadedSkillPreset]:
        if path.stat().st_size > MAX_SKILL_PRESET_FILE_BYTES:
            raise ConflictError("skill preset file is too large", data={"path": str(path)})
        specs = parse_skill_preset_bundle_yaml(path.read_text(encoding="utf-8"))
        return [
            self._loaded(
                spec,
                source="repo",
                precedence=REPO_SKILL_PRESET_PRECEDENCE,
                origin_path=path.as_posix(),
            )
            for spec in specs
        ]

    def _loaded(
        self,
        preset: SkillPresetSpec,
        *,
        source: str,
        precedence: int,
        plugin_slug: str | None = None,
        origin_path: str | None = None,
    ) -> LoadedSkillPreset:
        return LoadedSkillPreset(
            summary=SkillPresetSummaryOut(
                key=preset.key,
                name=preset.name,
                version=preset.version,
                description=preset.description,
                domain=preset.domain,
                skill_type=preset.skill_type,
                source=source,
                precedence=precedence,
                plugin_slug=plugin_slug,
                origin_path=origin_path,
                applies_to_workflows=preset.applies_to_workflows,
                generic_preset=preset.generic_preset,
                adaptation_required=preset.project_adaptation.required,
            ),
            preset=preset,
        )


__all__ = [
    "PLUGIN_SKILL_PRESET_PRECEDENCE",
    "REPO_SKILL_PRESET_PRECEDENCE",
    "LoadedSkillPreset",
    "SkillPresetListOut",
    "SkillPresetLoader",
    "SkillPresetSummaryOut",
]
