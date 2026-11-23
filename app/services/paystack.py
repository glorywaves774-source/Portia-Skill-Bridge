"""Talking to Paystack (https://paystack.com/docs/api/).

PAYSTACK_BACKEND chooses how:
* live — the real API with PAYSTACK_SECRET_KEY (a test key `sk_test_…` or live key `sk_live_…`).
* fake — an in-memory stand-in with a local practice checkout page, for development and the tests. Refused in
  production.
When it isn't set, `live` is used if a secret key is configured, otherwise `fake` outside production and
"not configured" in production (online payment is then switched off with a clear message).

Only the billing email, the amount, our random reference and two non-personal ids (workspace and invoice
numbers) are sent to Paystack. Card and Mobile Money details are entered on Paystack's own page, never here.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import urllib.error
import urllib.request

from flask import current_app, url_for

API = "https://api.paystack.co"
CHECKOUT_HOST = "https://checkout.paystack.com"


class PaystackError(RuntimeError):
    """Paystack couldn't be reached or refused the request. The message is safe to show."""


def backend() -> str | None:
    cfg = current_app.config
    chosen = (cfg.get("PAYSTACK_BACKEND") or "").strip().lower()
    if chosen in ("live", "fake"):
        if chosen == "fake" and cfg.get("ENV_NAME") == "production":
            return None
        if chosen == "live" and not cfg.get("PAYSTACK_SECRET_KEY"):
            return None
        return chosen
    if cfg.get("PAYSTACK_SECRET_KEY"):
        return "live"
    return None if cfg.get("ENV_NAME") == "production" else "fake"


def enabled() -> bool:
    return backend() is not None


def is_test_mode() -> bool:
    b = backend()
    return b == "fake" or (b == "live" and str(current_app.config.get("PAYSTACK_SECRET_KEY", "")).startswith("sk_test"))


def client():
    b = backend()
    if b == "live":
        return LiveClient(current_app.config["PAYSTACK_SECRET_KEY"], current_app.config.get("PAYSTACK_API", API))
    if b == "fake":
        return fake()
    raise PaystackError("Online payment isn't set up for this site yet.")


def signature_ok(raw_body: bytes, header: str | None) -> bool:
    """Webhooks are signed with HMAC-SHA512 of the raw body using the secret key."""
    secret = current_app.config.get("PAYSTACK_SECRET_KEY") or ""
    if backend() == "fake" and not secret:
        secret = FakePaystack.SECRET
    if not secret or not header:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, header.strip())


# ---- Live API ------------------------------------------------------------------------------------

class LiveClient:
    def __init__(self, secret: str, base: str = API):
        if not base.startswith("https://"):
            raise PaystackError("The Paystack address must start with https://.")
        self.secret = secret
        self.base = base.rstrip("/")

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.secret}", "Content-Type": "application/json",
            "User-Agent": "PORTIA-SkillBridge"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:  # nosec B310 — https only, checked in __init__
                body = json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode() or "{}")
            except ValueError:
                body = {}
            current_app.logger.warning("Paystack %s %s failed: %s %s", method, path, exc.code, body.get("message"))
            raise PaystackError(body.get("message") or "Paystack refused the request.") from None
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            current_app.logger.warning("Paystack %s %s unreachable: %s", method, path, exc)
            raise PaystackError("Paystack couldn't be reached. Please try again in a few minutes.") from None
        if not body.get("status"):
            raise PaystackError(body.get("message") or "Paystack refused the request.")
        return body.get("data") or {}

    def initialize(self, *, email, amount, currency, reference, callback_url, metadata) -> dict:
        return self._call("POST", "/transaction/initialize", {
            "email": email, "amount": int(amount), "currency": currency, "reference": reference,
            "callback_url": callback_url, "metadata": metadata})

    def verify(self, reference: str) -> dict:
        return self._call("GET", f"/transaction/verify/{urllib.request.quote(reference, safe='')}")

    def charge_authorization(self, *, email, amount, currency, authorization_code, reference, metadata) -> dict:
        return self._call("POST", "/transaction/charge_authorization", {
            "email": email, "amount": int(amount), "currency": currency, "authorization_code": authorization_code,
            "reference": reference, "metadata": metadata})


# ---- Fake (development and tests) ----------------------------------------------------------------

class FakePaystack:
    """Behaves like the parts of Paystack we use. Payments are completed on /dev/paystack/<reference>."""
    SECRET = "sk_fake_portia_development_only"

    def __init__(self):
        self.transactions: dict[str, dict] = {}
        self.fail_next_charge: str | None = None   # set to a reason to make the next saved-card charge fail
        self.calls: list[tuple[str, dict]] = []

    def initialize(self, *, email, amount, currency, reference, callback_url, metadata) -> dict:
        self.calls.append(("initialize", {"email": email, "amount": amount, "metadata": metadata}))
        self.transactions[reference] = {"reference": reference, "amount": int(amount), "currency": currency,
                                        "status": "abandoned", "customer": {"email": email},
                                        "metadata": metadata, "callback_url": callback_url}
        return {"authorization_url": url_for("devpay.practice", reference=reference, _external=True),
                "access_code": secrets.token_hex(8), "reference": reference}

    def complete(self, reference: str, *, outcome: str = "success", channel: str = "card",
                 amount: int | None = None) -> dict:
        """What a person paying on the checkout page does."""
        t = self.transactions[reference]
        t["status"] = "success" if outcome == "success" else "failed"
        t["id"] = secrets.randbelow(10 ** 9)
        t["channel"] = channel
        t["gateway_response"] = "Approved" if outcome == "success" else "Declined by issuer"
        if amount is not None:
            t["amount"] = amount
        t["paid_at"] = "2026-01-01T00:00:00.000Z"
        if channel == "card":
            t["authorization"] = {"authorization_code": "AUTH_" + secrets.token_hex(5), "reusable": True,
                                  "channel": "card", "card_type": "visa", "brand": "visa", "last4": "4081",
                                  "exp_month": "12", "exp_year": "2030", "bank": "Test Bank",
                                  "signature": "SIG_TEST4081"}
        else:
            t["authorization"] = {"authorization_code": "AUTH_" + secrets.token_hex(5), "reusable": False,
                                  "channel": "mobile_money", "brand": "MTN", "bank": "MTN", "last4": None}
        return t

    def verify(self, reference: str) -> dict:
        if reference not in self.transactions:
            raise PaystackError("Transaction reference not found")
        return dict(self.transactions[reference])

    def charge_authorization(self, *, email, amount, currency, authorization_code, reference, metadata) -> dict:
        self.calls.append(("charge", {"email": email, "amount": amount, "authorization_code": authorization_code}))
        failed = self.fail_next_charge
        self.fail_next_charge = None
        t = {"reference": reference, "amount": int(amount), "currency": currency, "id": secrets.randbelow(10 ** 9),
             "status": "failed" if failed else "success", "channel": "card",
             "gateway_response": failed or "Approved", "customer": {"email": email}, "metadata": metadata,
             "authorization": {"authorization_code": authorization_code, "reusable": True, "channel": "card",
                               "brand": "visa", "last4": "4081", "signature": "SIG_TEST4081"}}
        self.transactions[reference] = t
        return dict(t)

    def sign(self, body: bytes) -> str:
        return hmac.new(self.SECRET.encode(), body, hashlib.sha512).hexdigest()


def fake() -> FakePaystack:
    ext = current_app.extensions
    if "paystack_fake" not in ext:
        ext["paystack_fake"] = FakePaystack()
    return ext["paystack_fake"]
