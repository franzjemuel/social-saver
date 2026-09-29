"""Provider-neutral save request normalization.

Frontends submit a URL only. Provider-specific URL validation stays behind this
boundary so Instagram can be replaced and Facebook added without changing API
contracts.
"""
from dataclasses import dataclass
from providers.base import UnsupportedUrl
from providers.instagram.urls import normalize_instagram_url

@dataclass(frozen=True)
class NormalizedSave:
    platform: str
    canonical_url: str


def normalize_save_url(url: str) -> NormalizedSave:
    raw=(url or "").strip()
    normalizers=(
        ("instagram", normalize_instagram_url),
        # ("facebook", normalize_facebook_url),  # future provider adapter
    )
    for platform, normalize in normalizers:
        try:
            return NormalizedSave(platform, normalize(raw))
        except UnsupportedUrl:
            continue
    raise UnsupportedUrl("This link is not from a supported provider")
