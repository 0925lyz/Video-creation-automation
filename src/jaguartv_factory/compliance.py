from __future__ import annotations

from typing import Any, Mapping


ALLOWED_RIGHTS_STATUSES = {"OWNED", "LICENSED", "PUBLIC_DOMAIN", "CC_BY", "VERIFIED"}
HIGH_RISK_TYPES = {"football", "sports_highlight", "soap_opera", "cartoon_kids"}


def rights_record(metadata: Mapping[str, Any], overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    stored = metadata.get("rights") or {}
    if not isinstance(stored, Mapping):
        stored = {}
    overrides = overrides or {}
    return {
        "status": str(overrides.get("rights_status") or stored.get("status") or "UNKNOWN").strip().upper(),
        "license_type": str(stored.get("license_type") or ""),
        "licensor": str(stored.get("licensor") or ""),
        "proof_url": str(stored.get("proof_url") or ""),
        "territories": list(stored.get("territories") or []),
        "commercial_use": bool(stored.get("commercial_use", False)),
        "expires_at": str(stored.get("expires_at") or ""),
    }


def assess_compliance(content_type: str, rights: Mapping[str, Any]) -> dict[str, Any]:
    status = str(rights.get("status") or "UNKNOWN").upper()
    verified = status in ALLOWED_RIGHTS_STATUSES
    risk = "high" if content_type in HIGH_RISK_TYPES else "medium"
    if verified:
        risk = "medium" if risk == "high" else "low"
    return {
        "risk_level": risk,
        "rights_verified": verified,
        "rights_status": status,
        "decision": "ALLOW" if verified else "BLOCKED_RIGHTS",
        "reason": "verified_rights_record" if verified else "missing_or_unverified_rights_record",
    }


def assert_render_allowed(
    config: Mapping[str, Any], content_type: str, metadata: Mapping[str, Any], overrides: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    rights = rights_record(metadata, overrides)
    assessment = assess_compliance(content_type, rights)
    enforce = bool((config.get("compliance", {}) or {}).get("require_verified_rights", True))
    if enforce and not assessment["rights_verified"]:
        raise PermissionError(
            "BLOCKED_RIGHTS: rendering requires OWNED, LICENSED, PUBLIC_DOMAIN, CC_BY or VERIFIED rights status"
        )
    return {"rights": rights, **assessment, "enforced": enforce}
