"""Gunicorn settings for production (Render runs `gunicorn -c gunicorn.conf.py wsgi:app`)."""
import os
import re

from gunicorn.glogging import Logger

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
worker_class = "gthread"
timeout = 60
graceful_timeout = 30
keepalive = 5
max_requests = 1000          # recycle workers now and then, so memory stays steady
max_requests_jitter = 100
forwarded_allow_ips = "*"    # Render's proxy; the app trusts one hop (ProxyFix)
accesslog = "-"
errorlog = "-"
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(M)sms'

# Secret links (password reset, invitations) carry a token in the path: keep it out of the logs.
_SECRET_PATH = re.compile(r"/(reset-password|invite)/[^/?\s\"]+")


class RedactingLogger(Logger):
    def atoms(self, resp, req, environ, request_time):
        atoms = super().atoms(resp, req, environ, request_time)
        for key in ("r", "U", "q"):
            if isinstance(atoms.get(key), str):
                atoms[key] = _SECRET_PATH.sub(r"/\1/[hidden]", atoms[key])
        return atoms


logger_class = RedactingLogger
