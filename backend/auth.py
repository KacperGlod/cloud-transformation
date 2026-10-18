from datetime import timedelta, timezone
import hashlib
import hmac
import secrets

from fastapi import HTTPException, Request, Response
from sqlalchemy import delete, update

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


def change_password(session, user: User, current_password: str, new_password: str) -> None:
    """Change the hash and revoke sessions; the caller commits with a replacement session."""
    previous_hash = user.password_hash
    if not verify_password(current_password, previous_hash):
        raise HTTPException(400, "Obecne hasło jest nieprawidłowe.")
    if current_password == new_password:
        raise HTTPException(400, "Nowe hasło musi różnić się od obecnego.")
    new_hash = hash_password(new_password)
    # Reject a concurrent change rather than overwrite a password verified against an old hash.
    changed = session.execute(update(User).where(
        User.id == user.id, User.password_hash == previous_hash,
    ).values(password_hash=new_hash).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        raise HTTPException(409, "Hasło zostało już zmienione. Zaloguj się ponownie.")
    session.execute(delete(LoginSession).where(LoginSession.user_id == user.id))


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
