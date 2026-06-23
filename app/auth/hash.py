import hashlib
import hmac

from app.core.config import get_settings


def hash_api_key(api_key: str) -> str:
    """
    Computes an HMAC-SHA256 of the API key using the server-side secret.
    This is resistant to rainbow table attacks and offline brute-force
    even in the event of a database breach.
    """
    secret = get_settings().api_key_hash_secret.encode("utf-8")
    return hmac.new(secret, api_key.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_api_key(api_key: str, stored_hash: str) -> bool:
    """
    Timing-safe comparison of a raw API key against a stored HMAC-SHA256 hash.
    Uses hmac.compare_digest to prevent timing side-channel attacks.
    """
    return hmac.compare_digest(hash_api_key(api_key), stored_hash)

