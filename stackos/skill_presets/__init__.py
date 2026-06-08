"""Reusable main-agent skill preset contracts."""

from stackos.skill_presets.loader import (
    LoadedSkillPreset,
    SkillPresetListOut,
    SkillPresetLoader,
    SkillPresetSummaryOut,
)
from stackos.skill_presets.schema import (
    SKILL_PRESET_SCHEMA_VERSION,
    SkillOperatingContractSpec,
    SkillPresetBundleSpec,
    SkillPresetIssue,
    SkillPresetReferenceSpec,
    SkillPresetSpec,
    SkillPresetValidationOut,
    SkillProjectAdaptationSpec,
    parse_skill_preset_bundle_obj,
    parse_skill_preset_bundle_yaml,
    parse_skill_preset_obj,
    parse_skill_preset_yaml,
    validate_skill_preset_obj,
    validate_skill_preset_yaml,
)

__all__ = [
    "SKILL_PRESET_SCHEMA_VERSION",
    "LoadedSkillPreset",
    "SkillOperatingContractSpec",
    "SkillPresetBundleSpec",
    "SkillPresetIssue",
    "SkillPresetListOut",
    "SkillPresetLoader",
    "SkillPresetReferenceSpec",
    "SkillPresetSpec",
    "SkillPresetSummaryOut",
    "SkillPresetValidationOut",
    "SkillProjectAdaptationSpec",
    "parse_skill_preset_bundle_obj",
    "parse_skill_preset_bundle_yaml",
    "parse_skill_preset_obj",
    "parse_skill_preset_yaml",
    "validate_skill_preset_obj",
    "validate_skill_preset_yaml",
]
