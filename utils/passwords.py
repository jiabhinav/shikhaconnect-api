import hmac

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError


password_hasher = PasswordHasher(type=Type.ID)


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, stored_password: str) -> bool:
    if stored_password.startswith("$argon2"):
        try:
            return password_hasher.verify(stored_password, password)
        except (InvalidHashError, VerificationError):
            return False
    # Compatibility for existing plaintext accounts; upgrade after login.
    return hmac.compare_digest(password.encode("utf-8"), stored_password.encode("utf-8"))


def needs_rehash(stored_password: str) -> bool:
    return not stored_password.startswith("$argon2") or password_hasher.check_needs_rehash(stored_password)
