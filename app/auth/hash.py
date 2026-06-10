import hashlib

def hash_api_key(api_key: str) -> str:
    """
    Computes the SHA-256 hash of a given API key.
    """
    return hashlib.sha256(api_key.encode('utf-8')).hexdigest()

def verify_api_key(api_key: str, stored_hash: str) -> bool:
    """
    Verifies if the given API key matches the stored SHA-256 hash.
    """
    return hash_api_key(api_key) == stored_hash
