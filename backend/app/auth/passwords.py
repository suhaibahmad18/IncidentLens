"""Password hashing with Argon2id (argon2-cffi), using OWASP's recommended minimum parameters
(m=19 MiB, t=2, p=1) so a login costs little memory on a small instance."""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher(time_cost=2, memory_cost=19 * 1024, parallelism=1)

# Verified against when the email does not exist, so a login takes the same time either way
# (prevents discovering registered emails by timing).
_DUMMY_HASH = _hasher.hash("incidentlens-timing-equalizer")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
