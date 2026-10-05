"""Validate a requested social profile without importing posts or media."""

from providers.tiktok.validation import TikTokProfileValidationFailure, TikTokProfileValidator


async def process_validate_profile_import(job, validator=None):
    """Return controlled internal state for later confirmation/import.

    The worker result deliberately retains the expected stable account identity,
    but API projections expose only ``preview``.
    """
    payload = job["input"] or {}
    if payload.get("platform") != "tiktok" or not isinstance(payload.get("target"), str):
        raise TikTokProfileValidationFailure("invalid_target")
    validator = validator or TikTokProfileValidator()
    validation = await validator.validate(payload["target"])
    return {
        "platform": "tiktok",
        "target": validation.canonical_target,
        "platform_account_id": validation.platform_account_id,
        "preview": validation.safe_preview(),
    }
