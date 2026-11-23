"""HTTP security headers, including a strict Content-Security-Policy (no inline scripts)."""
from __future__ import annotations

from flask import Flask

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self' https://checkout.paystack.com",  # the payment page we send people to
    "frame-ancestors 'none'",
    "frame-src https://www.youtube-nocookie.com https://player.vimeo.com",
    "manifest-src 'self'",
])


def init_security_headers(app: Flask) -> None:
    @app.after_request
    def _headers(response):
        h = response.headers
        h.setdefault("Content-Security-Policy", CSP)
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        h.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        h.setdefault("X-Permitted-Cross-Domain-Policies", "none")
        if app.config.get("SECURITY_HSTS"):
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if response.mimetype == "text/html":
            h.setdefault("Cache-Control", "no-store")
        return response
