"""Authentication helpers.

* Coordinators sign in with email + password (bcrypt-hashed in the database).
* Faculty use a personal, unguessable link (?invite=<token>). The coordinator
  can rotate a link at any time, which immediately disables the old one.
* Candidates use a read-only link (?itinerary=<token>) that only ever shows
  their latest PUBLISHED itinerary.
* Optional: university SSO via Streamlit's built-in OpenID Connect support
  (st.login), configured in .streamlit/secrets.toml. Signed-in emails are
  matched to coordinator accounts.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from collections import defaultdict

import bcrypt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import models as m

_FAILED: dict[str, list[float]] = defaultdict(list)
MAX_ATTEMPTS = 5
WINDOW_SECONDS = 15 * 60


def hash_password(pw: str) -> str:
    if len(pw) < 8:
        raise ValueError("Passwords must be at least 8 characters.")
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except ValueError:
        return False


def _throttled(email: str) -> bool:
    now = time.time()
    _FAILED[email] = [t for t in _FAILED[email] if now - t < WINDOW_SECONDS]
    return len(_FAILED[email]) >= MAX_ATTEMPTS


def authenticate(s: Session, email: str, password: str) -> m.User | None:
    """Return the user on success. Raises PermissionError when throttled."""
    key = email.strip().lower()
    if _throttled(key):
        raise PermissionError("Too many failed attempts. Try again in 15 minutes.")
    user = s.scalar(select(m.User).where(func.lower(m.User.email) == key, m.User.active.is_(True)))
    # Accept accidental leading/trailing spaces from copy-paste.
    if user and (verify_password(password, user.password_hash)
                 or (password != password.strip() and verify_password(password.strip(), user.password_hash))):
        _FAILED.pop(key, None)
        return user
    _FAILED[key].append(time.time())
    return None


def user_by_email(s: Session, email: str) -> m.User | None:
    return s.scalar(select(m.User).where(func.lower(m.User.email) == email.strip().lower(), m.User.active.is_(True)))


def participant_by_token(s: Session, token: str) -> m.Participant | None:
    if not token or len(token) < 16:
        return None
    p = s.scalar(select(m.Participant).where(m.Participant.access_token == token))
    if p and p.active and hmac.compare_digest(p.access_token, token):
        return p
    return None


def candidate_by_token(s: Session, token: str) -> m.Candidate | None:
    if not token or len(token) < 16:
        return None
    c = s.scalar(select(m.Candidate).where(m.Candidate.share_token == token))
    if c and hmac.compare_digest(c.share_token, token):
        return c
    return None


def create_coordinator(s: Session, email: str, name: str, password: str) -> m.User:
    u = m.User(email=email.strip().lower(), name=name, role=m.ROLE_COORDINATOR, password_hash=hash_password(password))
    s.add(u)
    s.flush()
    return u


# ---------------------------------------------------------------- remembered sign-in

def make_session_token(user_id: int, secret: str, hours: int = 12, now: float | None = None) -> str:
    """Signed 'user_id.expiry.signature' token stored in a browser cookie so a page
    reload doesn't sign the coordinator out."""
    exp = int((now or time.time()) + hours * 3600)
    msg = f"{user_id}.{exp}"
    sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()
    return f"{msg}.{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"


def read_session_token(token: str | None, secret: str, now: float | None = None) -> int | None:
    try:
        uid, exp, sig = (token or "").split(".")
        good = base64.urlsafe_b64encode(
            hmac.new(secret.encode(), f"{uid}.{exp}".encode(), hashlib.sha256).digest()).decode().rstrip("=")
        if not hmac.compare_digest(good, sig) or int(exp) < (now or time.time()):
            return None
        return int(uid)
    except (ValueError, TypeError):
        return None
