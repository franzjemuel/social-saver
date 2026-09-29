from urllib.parse import urlparse
from providers.base import UnsupportedUrl

ALLOWED_KINDS = {"p", "reel", "tv"}

def normalize_instagram_url(url: str) -> str:
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower().split(":")[0]
    if host not in {"instagram.com", "www.instagram.com"}:
        raise UnsupportedUrl("Only instagram.com media links are supported")
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2 or parts[0] not in ALLOWED_KINDS:
        raise UnsupportedUrl("Send an Instagram post or Reel link, not a profile link")
    return f"https://www.instagram.com/{parts[0]}/{parts[1]}/"
