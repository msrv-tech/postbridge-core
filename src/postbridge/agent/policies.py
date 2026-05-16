from __future__ import annotations

from dataclasses import asdict, dataclass, field

from postbridge.domain.errors import ValidationError

ALLOWED_AUTONOMY_MODES = {
    "full_manual",
    "draft_approval",
    "plan_approval",
    "guarded_auto_publish",
}


def _normalize_risk_flags(items: list[str] | None) -> list[str]:
    flags: list[str] = []
    seen: set[str] = set()
    for item in items or []:
        normalized = str(item).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        flags.append(normalized)
    return flags


@dataclass(slots=True)
class AutonomyPolicy:
    mode: str
    requires_review: bool
    materialize_on_approval: bool
    materialization_level: str
    auto_dispatch: bool
    min_source_quality: float = 0.0
    min_source_corroboration: float = 0.0
    max_source_conflict: float = 1.0
    max_angle_pressure: float = 1.0
    auto_resolve_review_actions: tuple[str, ...] = ()
    auto_resolve_review_actions_by_preset: dict[str, tuple[str, ...]] = field(default_factory=dict)
    blocked_risk_flags: tuple[str, ...] = ()


def policy_to_dict(policy: AutonomyPolicy) -> dict:
    payload = asdict(policy)
    payload["auto_resolve_review_actions"] = list(policy.auto_resolve_review_actions)
    payload["auto_resolve_review_actions_by_preset"] = {
        key: list(value) for key, value in policy.auto_resolve_review_actions_by_preset.items()
    }
    payload["blocked_risk_flags"] = list(policy.blocked_risk_flags)
    return payload


def apply_policy_overrides(policy: AutonomyPolicy, overrides: dict | None) -> AutonomyPolicy:
    if not isinstance(overrides, dict):
        return policy
    data = policy_to_dict(policy)
    for key in ("requires_review", "materialize_on_approval", "auto_dispatch"):
        if key in overrides:
            data[key] = bool(overrides[key])
    if isinstance(overrides.get("materialization_level"), str) and overrides["materialization_level"].strip():
        data["materialization_level"] = overrides["materialization_level"].strip()
    if "min_source_quality" in overrides:
        data["min_source_quality"] = float(overrides["min_source_quality"])
    if "min_source_corroboration" in overrides:
        data["min_source_corroboration"] = float(overrides["min_source_corroboration"])
    if "max_source_conflict" in overrides:
        data["max_source_conflict"] = float(overrides["max_source_conflict"])
    if "max_angle_pressure" in overrides:
        data["max_angle_pressure"] = float(overrides["max_angle_pressure"])
    if isinstance(overrides.get("auto_resolve_review_actions"), list):
        data["auto_resolve_review_actions"] = [
            str(item) for item in overrides["auto_resolve_review_actions"] if str(item).strip()
        ]
    if isinstance(overrides.get("auto_resolve_review_actions_by_preset"), dict):
        data["auto_resolve_review_actions_by_preset"] = {
            str(key): [str(item) for item in value if str(item).strip()]
            for key, value in overrides["auto_resolve_review_actions_by_preset"].items()
            if isinstance(value, list)
        }
    if isinstance(overrides.get("blocked_risk_flags"), list):
        data["blocked_risk_flags"] = [str(item) for item in overrides["blocked_risk_flags"] if str(item).strip()]
    return AutonomyPolicy(
        mode=str(data["mode"]),
        requires_review=bool(data["requires_review"]),
        materialize_on_approval=bool(data["materialize_on_approval"]),
        materialization_level=str(data["materialization_level"]),
        auto_dispatch=bool(data["auto_dispatch"]),
        min_source_quality=float(data["min_source_quality"]),
        min_source_corroboration=float(data["min_source_corroboration"]),
        max_source_conflict=float(data["max_source_conflict"]),
        max_angle_pressure=float(data["max_angle_pressure"]),
        auto_resolve_review_actions=tuple(str(item) for item in data["auto_resolve_review_actions"]),
        auto_resolve_review_actions_by_preset={
            str(key): tuple(str(item) for item in value)
            for key, value in data["auto_resolve_review_actions_by_preset"].items()
        },
        blocked_risk_flags=tuple(str(item) for item in data["blocked_risk_flags"]),
    )


def evaluate_policy_guardrails(policy: AutonomyPolicy, *, scores: dict | None, risk_flags: list[str] | None) -> dict:
    score_map = scores if isinstance(scores, dict) else {}
    flags = set(_normalize_risk_flags(risk_flags))
    reasons: list[str] = []
    source_quality = float(score_map.get("source_quality") or 0.0)
    source_conflict = float(score_map.get("source_conflict") or 0.0)
    source_corroboration = float(score_map.get("source_corroboration") or 0.0)
    angle_pressure = float(score_map.get("angle_pressure") or 0.0)
    source_count = int(score_map.get("source_count") or 0)
    if source_count > 0 and source_quality < policy.min_source_quality:
        reasons.append(f"source_quality_below_threshold:{source_quality:.2f}")
    if source_conflict > policy.max_source_conflict:
        reasons.append(f"source_conflict_above_threshold:{source_conflict:.2f}")
    if source_count > 0 and source_corroboration < policy.min_source_corroboration:
        reasons.append("insufficient_source_corroboration")
    if angle_pressure > policy.max_angle_pressure:
        reasons.append(f"angle_pressure_above_threshold:{angle_pressure:.2f}")
    matched_flags = sorted(flag for flag in flags if flag in policy.blocked_risk_flags)
    reasons.extend(f"blocked_risk_flag:{flag}" for flag in matched_flags)
    return {
        "blocked": bool(reasons),
        "reasons": reasons,
        "checked_scores": {
            "source_quality": source_quality,
            "source_conflict": source_conflict,
            "source_corroboration": source_corroboration,
            "angle_pressure": angle_pressure,
            "source_count": source_count,
        },
    }


def get_autonomy_policy(mode: str | None) -> AutonomyPolicy:
    resolved = (mode or "draft_approval").strip() or "draft_approval"
    if resolved not in ALLOWED_AUTONOMY_MODES:
        raise ValidationError(
            code="VALIDATION_AUTONOMY_MODE_INVALID",
            message="unsupported autonomy mode",
            details={"autonomy_mode": resolved, "allowed": sorted(ALLOWED_AUTONOMY_MODES)},
        )
    if resolved == "full_manual":
        return AutonomyPolicy(
            mode=resolved,
            requires_review=True,
            materialize_on_approval=False,
            materialization_level="none",
            auto_dispatch=False,
        )
    if resolved == "draft_approval":
        return AutonomyPolicy(
            mode=resolved,
            requires_review=True,
            materialize_on_approval=True,
            materialization_level="draft_only",
            auto_dispatch=False,
        )
    if resolved == "plan_approval":
        return AutonomyPolicy(
            mode=resolved,
            requires_review=True,
            materialize_on_approval=True,
            materialization_level="plan",
            auto_dispatch=False,
        )
    return AutonomyPolicy(
        mode=resolved,
        requires_review=False,
        materialize_on_approval=False,
        materialization_level="plan",
        auto_dispatch=True,
        min_source_quality=0.35,
        min_source_corroboration=0.35,
        max_source_conflict=0.6,
        max_angle_pressure=0.34,
        blocked_risk_flags=(
            "possible_duplicate",
            "embedding_duplicate",
            "source_disagreement",
            "source_conflict",
            "source_concentration",
            "single_source",
            "no_sources",
            "missing_image_candidates",
            "repeated_angle",
        ),
    )
