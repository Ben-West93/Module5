# exercises/security-basics/app/security.py
# L10 — Password hashing and login tokens

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import JWT_ALGORITHM, JWT_EXPIRE_MINUTES, JWT_SECRET_KEY

# bcrypt's work factor. Each extra round doubles the time a hash takes, for
# the server and for anyone trying passwords against a stolen hash. 12 takes
# about a quarter of a second here, which is unnoticeable at login and slow
# for an attacker.
BCRYPT_ROUNDS = 12

# bcrypt only reads the first 72 bytes of a password, and bcrypt 5 raises an
# error on longer ones. The schemas refuse them before they get here.
MAX_PASSWORD_BYTES = 72


def hash_password(password: str) -> str:
    """A salted bcrypt hash. The salt is stored inside the hash itself."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(BCRYPT_ROUNDS)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """True if `password` matches. bcrypt compares in constant time."""
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))


# Checked when a username does not exist, so a login for an unknown user
# takes as long as one for a real user with the wrong password. Without it,
# the response time would tell an attacker which usernames are registered.
DUMMY_HASH = hash_password("not-a-real-password")


def create_access_token(user_id: int) -> str:
    """A signed token saying "this is user `user_id`", valid for a while."""
    now = datetime.now(timezone.utc)
    claims = {"sub": str(user_id), "iat": now, "exp": now + timedelta(minutes=JWT_EXPIRE_MINUTES)}
    return jwt.encode(claims, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> int | None:
    """The user id in a valid token, or None for any token that is not.

    `algorithms` is a fixed list, never read from the token. A token whose
    header says "alg": "none" (unsigned) or names another algorithm is
    refused, which is the classic way JWT checks are bypassed.
    """
    try:
        claims = jwt.decode(
            token,
            JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "iat", "sub"]},
        )
        user_id = int(claims["sub"])
    except (jwt.InvalidTokenError, ValueError, TypeError):
        return None
    return user_id if user_id > 0 else None
