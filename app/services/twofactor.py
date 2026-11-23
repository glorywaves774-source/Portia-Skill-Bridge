"""Two-step sign-in with an authenticator app (TOTP, RFC 6238) and one-time recovery codes.

Works with Google Authenticator, Microsoft Authenticator, Authy, 1Password and similar apps. The QR code is drawn on
this server, so the secret never goes to another service. Each code is accepted once (codes from the previous or next
30 seconds are allowed for clock drift). Recovery codes are stored hashed and each works once.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from app.models import User
from app.models.base import utcnow
from app.services import audit

STEP = 30
DIGITS = 6
RECOVERY_CODES = 8
ISSUER = "PORTIA SkillBridge"


class TwoFactorError(ValueError):
    pass


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    return base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)


def code_at(secret: str, step: int) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10 ** DIGITS).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((now if now is not None else time.time()) // STEP)


def _clean(code: str | None) -> str:
    return "".join(ch for ch in (code or "") if ch.isalnum()).upper()


def check_code(secret: str, code: str | None, *, last_step: int | None = None, now: float | None = None) -> int | None:
    """Return the matching step if the code is right (and not already used), else None."""
    code = _clean(code)
    if len(code) != DIGITS or not code.isdigit():
        return None
    step = current_step(now)
    for candidate in (step - 1, step, step + 1):
        if last_step is not None and candidate <= last_step:
            continue
        if hmac.compare_digest(code_at(secret, candidate), code):
            return candidate
    return None


def provisioning_uri(user: User, secret: str) -> str:
    label = quote(f"{ISSUER}:{user.email}")
    return (f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}&algorithm=SHA1&digits={DIGITS}"
            f"&period={STEP}")


def qr_data_uri(uri: str) -> str:
    import segno
    return segno.make(uri, error="m").png_data_uri(scale=6, border=2)


def _hash(code: str) -> str:
    return hashlib.sha256(("portia-recovery:" + _clean(code)).encode()).hexdigest()


def _new_codes() -> list[str]:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I mix-ups
    return ["".join(secrets.choice(alphabet) for _ in range(4)) + "-" + "".join(secrets.choice(alphabet) for _ in range(4))
            for _ in range(RECOVERY_CODES)]


def enable(user: User, secret: str, code: str | None) -> list[str]:
    """Switch on two-step sign-in once the person proves their app works. Returns the recovery codes to show once."""
    if user.two_factor_enabled:
        raise TwoFactorError("Two-step sign-in is already on.")
    step = check_code(secret, code)
    if step is None:
        raise TwoFactorError("That code didn't match. Check the time on your phone is set automatically, then try "
                             "the newest code.")
    codes = _new_codes()
    user.totp_secret = secret
    user.totp_enabled_at = utcnow()
    user.totp_last_step = step
    user.recovery_codes = [_hash(c) for c in codes]
    audit.record("auth.2fa_enabled", actor=user, entity_type="user", entity_id=user.id,
                 summary="Two-step sign-in switched on")
    return codes


def disable(user: User, password: str, *, actor=None) -> None:
    if actor is None and not user.check_password(password or ""):
        raise TwoFactorError("Your password wasn't right.")
    user.totp_secret = user.totp_enabled_at = user.totp_last_step = None
    user.recovery_codes = None
    user.rotate_session_token()  # sign out other devices
    audit.record("auth.2fa_disabled", actor=actor or user, entity_type="user", entity_id=user.id,
                 summary="Two-step sign-in switched off" + (" by an administrator" if actor else ""))


def regenerate_codes(user: User, password: str) -> list[str]:
    if not user.two_factor_enabled:
        raise TwoFactorError("Switch on two-step sign-in first.")
    if not user.check_password(password or ""):
        raise TwoFactorError("Your password wasn't right.")
    codes = _new_codes()
    user.recovery_codes = [_hash(c) for c in codes]
    audit.record("auth.2fa_codes", actor=user, entity_type="user", entity_id=user.id,
                 summary="New recovery codes made (old ones no longer work)")
    return codes


def verify(user: User, code: str | None, *, now: float | None = None) -> str | None:
    """Check a sign-in code or recovery code. Returns "app" or "recovery" when accepted, else None."""
    if not user.two_factor_enabled:
        return None
    step = check_code(user.totp_secret, code, last_step=user.totp_last_step, now=now)
    if step is not None:
        user.totp_last_step = step
        return "app"
    hashed = _hash(code or "")
    remaining = list(user.recovery_codes or [])
    if len(_clean(code)) == 8 and hashed in remaining:
        remaining.remove(hashed)
        user.recovery_codes = remaining
        return "recovery"
    return None


def recovery_left(user: User) -> int:
    return len(user.recovery_codes or [])
