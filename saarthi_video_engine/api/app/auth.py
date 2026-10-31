"""Password login with server-side sessions.

Accounts are created by the owner with `python -m app.cli create-user`; there is no
self-registration. Passwords are hashed with scrypt. The browser holds a random
session token in an HttpOnly cookie; only its SHA-256 is stored. Every state-changing
request must also send the session's CSRF token in X-CSRF-Token.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import uuid
from datetime import timedelta

from sqlalchemy import and_, delete, func, insert, select, update

from app.db import as_utc, audit_events, auth_sessions, login_attempts, users, utcnow
from app.errors import StudioError
from app.logs import current

COOKIE = "sarthi_session"
CSRF_HEADER = "x-csrf-token"
MIN_PASSWORD = 12
SCRYPT = {"n": 2**15, "r": 8, "p": 1}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
LOCKOUT_MINUTES = 15


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, maxmem=128 * 1024 * 1024, dklen=32, **SCRYPT)
    return "scrypt${n}${r}${p}${salt}${digest}".format(
        **SCRYPT, salt=base64.b64encode(salt).decode(), digest=base64.b64encode(digest).decode()
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), maxmem=128 * 1024 * 1024, dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def normalize_email(email: str) -> str:
    value = (email or "").strip().lower()
    if not _EMAIL.match(value) or len(value) > 320:
        raise StudioError(422, "invalid_email", "Enter a valid email address.")
    return value


def check_password_strength(password: str) -> None:
    if len(password or "") < MIN_PASSWORD:
        raise StudioError(422, "weak_password", f"Use a password of at least {MIN_PASSWORD} characters.")


def create_user(db, email: str, password: str, display_name: str = "") -> str:
    email = normalize_email(email)
    check_password_strength(password)
    user_id = str(uuid.uuid4())
    with db.begin() as conn:
        if conn.execute(select(users.c.id).where(users.c.email == email)).first():
            raise StudioError(409, "user_exists", "A user with that email already exists.")
        conn.execute(insert(users).values(id=user_id, email=email, display_name=display_name[:200], password_hash=hash_password(password), is_active=True, created_at=utcnow()))
    audit(db, "user_created", user_id=user_id, detail={"email": email})
    return user_id


def set_password(db, email: str, password: str) -> None:
    check_password_strength(password)
    with db.begin() as conn:
        changed = conn.execute(update(users).where(users.c.email == normalize_email(email)).values(password_hash=hash_password(password))).rowcount
        if changed:
            user = conn.execute(select(users.c.id).where(users.c.email == normalize_email(email))).first()
            conn.execute(delete(auth_sessions).where(auth_sessions.c.user_id == user.id))
    if not changed:
        raise StudioError(404, "not_found", "No user with that email.")


def set_active(db, email: str, active: bool) -> None:
    with db.begin() as conn:
        user = conn.execute(select(users.c.id).where(users.c.email == normalize_email(email))).first()
        if user is None:
            raise StudioError(404, "not_found", "No user with that email.")
        conn.execute(update(users).where(users.c.id == user.id).values(is_active=active))
        if not active:
            conn.execute(delete(auth_sessions).where(auth_sessions.c.user_id == user.id))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _recent_failures(db, email: str) -> int:
    cutoff = utcnow() - timedelta(minutes=LOCKOUT_MINUTES)
    with db.connect() as conn:
        return int(
            conn.execute(
                select(func.count()).select_from(login_attempts).where(
                    and_(login_attempts.c.email == email, login_attempts.c.success.is_(False), login_attempts.c.created_at >= cutoff)
                )
            ).scalar()
            or 0
        )


def login(db, email: str, password: str, ip: str, user_agent: str, ttl_hours: int, max_failures: int) -> tuple[dict, str]:
    """Returns (user, session token). Failures do not reveal whether the email exists."""
    try:
        email = normalize_email(email)
    except StudioError as exc:
        raise StudioError(401, "invalid_credentials", "The email or password is incorrect.") from exc
    if _recent_failures(db, email) >= max_failures:
        raise StudioError(429, "too_many_attempts", f"Too many failed sign-ins. Try again in {LOCKOUT_MINUTES} minutes.")
    with db.connect() as conn:
        user = conn.execute(select(users).where(users.c.email == email)).first()
    ok = bool(user and user.is_active and verify_password(password or "", user.password_hash))
    if not user:
        verify_password(password or "", hash_password("timing-equaliser-not-a-password"))
    with db.begin() as conn:
        conn.execute(insert(login_attempts).values(email=email, ip=ip[:64], success=ok, created_at=utcnow()))
    if not ok:
        audit(db, "login_failed", detail={"email": email, "ip": ip[:64]})
        raise StudioError(401, "invalid_credentials", "The email or password is incorrect.")
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    now = utcnow()
    with db.begin() as conn:
        conn.execute(
            insert(auth_sessions).values(
                token_hash=_token_hash(token), user_id=user.id, csrf_token=csrf, created_at=now,
                expires_at=now + timedelta(hours=ttl_hours), last_seen_at=now, user_agent=(user_agent or "")[:300],
            )
        )
        conn.execute(update(users).where(users.c.id == user.id).values(last_login_at=now))
    audit(db, "login", user_id=user.id, detail={"ip": ip[:64]})
    return {"id": user.id, "email": user.email, "display_name": user.display_name, "csrf_token": csrf}, token


def session_user(db, token: str | None) -> dict | None:
    if not token:
        return None
    now = utcnow()
    with db.connect() as conn:
        row = conn.execute(
            select(auth_sessions, users.c.email, users.c.display_name, users.c.is_active)
            .join(users, users.c.id == auth_sessions.c.user_id)
            .where(auth_sessions.c.token_hash == _token_hash(token))
        ).first()
    if row is None or not row.is_active or as_utc(row.expires_at) <= now:
        return None
    if (now - as_utc(row.last_seen_at)).total_seconds() > 300:
        with db.begin() as conn:
            conn.execute(update(auth_sessions).where(auth_sessions.c.token_hash == row.token_hash).values(last_seen_at=now))
    return {"id": row.user_id, "email": row.email, "display_name": row.display_name, "csrf_token": row.csrf_token}


def logout(db, token: str | None) -> None:
    if not token:
        return
    with db.begin() as conn:
        conn.execute(delete(auth_sessions).where(auth_sessions.c.token_hash == _token_hash(token)))


def audit(db, action: str, *, user_id: str | None = None, session_id: str | None = None, detail: dict | None = None) -> None:
    context = current()
    with db.begin() as conn:
        conn.execute(
            insert(audit_events).values(
                user_id=user_id or context.get("user_id"), session_id=session_id or context.get("session_id"), action=action,
                detail=detail or {}, request_id=context.get("request_id", ""), created_at=utcnow(),
            )
        )
