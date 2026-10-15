from datetime import timedelta, timezone
import hashlib
import hmac
import secrets

from fastapi import HTTPException, Request, Response
from sqlalchemy import delete

from .models import LoginSession, User, utcnow

COOKIE_NAME = "cloud_session"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=64)
    return f"scrypt${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, salt, expected = stored.split("$")
        if algorithm != "scrypt":
            return False
        derived = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=64)
        return hmac.compare_digest(derived.hex(), expected)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def current_user(request: Request, session, *, required=True) -> User | None:
    token = request.cookies.get(COOKIE_NAME)
    login = session.get(LoginSession, token_hash(token)) if token else None
    # SQLite returns naive UTC datetimes; normalize consistently for both engines.
    if login:
        expires = login.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires > utcnow():
            return session.get(User, login.user_id)
    if required:
        raise HTTPException(401, "Zaloguj się, aby kontynuować.")
    return None


def start_session(session, user: User, response: Response, settings):
    session.execute(delete(LoginSession).where(LoginSession.expires_at <= utcnow()))
    token = secrets.token_urlsafe(32)
    session.add(LoginSession(token_hash=token_hash(token), user_id=user.id,
                             expires_at=utcnow() + timedelta(days=settings.session_days)))
    session.commit()
    response.set_cookie(COOKIE_NAME, token, max_age=settings.session_days * 86400,
                        httponly=True, secure=settings.secure_cookies, samesite="strict", path="/")


def user_json(user: User) -> dict:
    return {"id": str(user.id), "email": user.email}
