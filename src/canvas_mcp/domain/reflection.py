"""Pure bounded byte reflection checks; no I/O or document interpretation."""

from html import unescape
from urllib.parse import unquote


def reflects_secrets(data: bytes, secrets: tuple[str, ...]) -> bool:
    for encoding in ("utf-8", "utf-16-le", "utf-16-be"):
        if any(secret and secret.encode(encoding) in data for secret in secrets):
            return True
    text = data.decode("utf-8", errors="ignore").replace("\\/", "/")
    for _ in range(8):
        if any(secret and secret in text for secret in secrets):
            return True
        following = unquote(unescape(text))
        if following == text:
            return False
        text = following
    return any(secret and secret in text for secret in secrets)
