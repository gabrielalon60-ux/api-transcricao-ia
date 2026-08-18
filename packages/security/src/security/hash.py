import hashlib
import hmac


def hash_secret(secret: str, salt: str) -> str:
    """
    Computes an HMAC-SHA256 hash of a secret using a salt key.
    """
    return hmac.new(
        salt.encode("utf-8"), secret.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def verify_secret(secret: str, stored_hash: str, salt: str) -> bool:
    """
    Timing-safe comparison of a raw secret against a stored hash.
    Uses hmac.compare_digest to prevent timing side-channel attacks.
    """
    computed = hash_secret(secret, salt)
    if not stored_hash:
        return False
    return hmac.compare_digest(computed, stored_hash)


def hash_pii(value: str, key: str) -> str:
    """
    Computes an HMAC-SHA256 of a PII value (like phone number) for safe log correlation.
    """
    return hmac.new(
        key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256
    ).hexdigest()
