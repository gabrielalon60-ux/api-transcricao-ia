import secrets
import string
import sys
import os

# Add parent directory to path so we can import from app
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.auth.hash import hash_api_key

def generate_api_key(prefix: str = "sk_live_") -> str:
    alphabet = string.ascii_letters + string.digits
    random_part = ''.join(secrets.choice(alphabet) for i in range(64))
    return f"{prefix}{random_part}"

if __name__ == "__main__":
    prefix = "sk_live_"
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        prefix = "sk_test_"
        
    raw_key = generate_api_key(prefix)
    hashed_key = hash_api_key(raw_key)

    print("Raw API Key (deliver ONCE to customer, DO NOT save):")
    print(raw_key)
    print("\nSHA256 Hash (insert this into Supabase 'api_key_hash'):")
    print(hashed_key)
