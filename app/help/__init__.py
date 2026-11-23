"""PORTIA Assistant: answers questions from the Markdown help guide.

* Answers come only from `guide.md`. Nothing is sent to an outside AI service.
* Links are offered only if the person's role can open them; topics that belong
  to another role say which role does it.
* Questions are redacted (emails, phone numbers, ID numbers) before they are
  logged, and records are never included.
* Requires sign-in, CSRF token and is rate-limited.
"""
from __future__ import annotations

import functools
import pathlib
import re
import unicodedata
from dataclasses import dataclass, field

from flask import Blueprint, current_app, g, jsonify, render_template, request, url_for
from flask_login import current_user, login_required

from app.access import load_workspace
from app.extensions import limiter
from app.models import Role
from app.rbac import ROLE_PERMISSIONS

bp = Blueprint("help", __name__)
GUIDE = pathlib.Path(__file__).with_name("guide.md")

STOPWORDS = set("""a an and are as at be by can could do does for from get got have how i i'm in is it its
me my of on or our please so that the their them there this to us want was we what when where which who why
will with would you your yourself about any some""".split())


# ---- Redaction --------------------------------------------------------------------

_REDACTIONS = [
    (re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"), "[email]"),
    (re.compile(r"\bGHA-?\d{9}-?\d\b", re.I), "[ID number]"),          # Ghana Card
    (re.compile(r"(?<!\w)\+?\d[\d\s().-]{6,}\d(?!\w)"), "[phone]"),      # phone numbers
    (re.compile(r"\b[A-Z]{1,3}\d{6,}\b"), "[ID number]"),               # passport / other IDs
]


def redact(text: str) -> str:
    """Remove personal details before a question is logged or sent anywhere."""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


# ---- Guide parsing -------------------------------------------------------------------

def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9&']+", _fold(text))
    return {_stem(w.strip("'")) for w in words if w not in STOPWORDS and len(w) > 1}


@dataclass
class Topic:
    title: str
    body: str
    keywords: list[str] = field(default_factory=list)
    permission: str | None = None
    links: list[str] = field(default_factory=list)
    suggest: bool = False

    @functools.cached_property
    def title_tokens(self):
        return tokens(self.title)

    @functools.cached_property
    def keyword_tokens(self):
        return tokens(" ".join(self.keywords))

    @functools.cached_property
    def keyword_phrases(self):
        return [_fold(k) for k in self.keywords if " " in k]

    @functools.cached_property
    def body_tokens(self):
        return tokens(self.body)

    def blocks(self) -> list[dict]:
        out, bullets = [], []
        for line in self.body.splitlines():
            line = line.strip()
            if line.startswith("- "):
                bullets.append(_plain(line[2:]))
                continue
            if bullets:
                out.append({"type": "ul", "items": bullets})
                bullets = []
            if line:
                out.append({"type": "p", "text": _plain(line)})
        if bullets:
            out.append({"type": "ul", "items": bullets})
        return out


def _plain(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text)


@functools.lru_cache(maxsize=1)
def load_topics(path: str = str(GUIDE)) -> tuple[Topic, ...]:
    text = pathlib.Path(path).read_text(encoding="utf-8")
    text = re.sub(r"<!--\s*How this file works.*?-->", "", text, flags=re.S)
    topics = []
    for chunk in re.split(r"^## ", text, flags=re.M)[1:]:
        title, _, rest = chunk.partition("\n")
        meta = dict(re.findall(r"<!--\s*(\w+)\s*:?\s*(.*?)\s*-->", rest))
        body = re.sub(r"<!--.*?-->", "", rest, flags=re.S).strip()
        topics.append(Topic(
            title=title.strip(),
            body=body,
            keywords=[k.strip() for k in meta.get("keywords", "").split(",") if k.strip()],
            permission=meta.get("permission") or None,
            links=[k.strip() for k in meta.get("links", "").split(",") if k.strip()],
            suggest="suggest" in meta,
        ))
    return tuple(topics)


# ---- Who can do what --------------------------------------------------------------------

def _has(permission: str | None) -> bool:
    if permission is None:
        return True
    if permission == "platform":
        return bool(current_user.is_authenticated and current_user.is_platform_admin)
    return permission in getattr(g, "permissions", frozenset())


def _roles_with(permission: str) -> list[str]:
    if permission == "platform":
        return ["the PORTIA SkillBridge team (Super Administrator)"]
    roles = [Role.LABELS[r] for r, perms in ROLE_PERMISSIONS.items() if permission in perms]
    return roles or ["your workspace owner"]


def _can_open(endpoint: str) -> bool:
    view = current_app.view_functions.get(endpoint)
    if view is None:
        return False
    if endpoint.startswith("admin."):
        return bool(current_user.is_platform_admin)
    if endpoint.startswith("workspace.") and g.get("workspace") is None:
        return False
    needed = getattr(view, "required_permissions", ())
    return all(_has(p) for p in needed)


_LINK_LABELS = {
    "auth.signin": "Sign in", "auth.forgot_password": "Reset password", "auth.choose_workspace": "My workspaces",
    "account.profile": "My account", "workspace.dashboard": "Dashboard", "workspace.people": "People",
    "workspace.subscription": "Subscription", "workspace.activity": "Activity", "workspace.settings": "Settings",
    "workspace.my_access": "Your access", "admin.tenant_new": "New workspace", "admin.tenants": "Workspaces",
    "admin.users": "People (all accounts)", "admin.plans": "Plans",
}


def _links(topic: Topic) -> list[dict]:
    out = []
    for endpoint in topic.links:
        if _can_open(endpoint):
            out.append({"label": _LINK_LABELS.get(endpoint, endpoint), "url": url_for(endpoint)})
    return out


# ---- Search ---------------------------------------------------------------------------------

def search(question: str, topics=None) -> list[tuple[float, Topic]]:
    topics = topics or load_topics()
    q = tokens(question)
    folded = _fold(question)
    scored = []
    for t in topics:
        score = 3 * len(q & t.title_tokens) + 2 * len(q & t.keyword_tokens) + 0.5 * len(q & t.body_tokens)
        score += sum(3 for phrase in t.keyword_phrases if phrase in folded)
        if score:
            scored.append((score, t))
    scored.sort(key=lambda x: (-x[0], x[1].title))
    return scored


def answer(question: str) -> dict:
    results = search(question)
    if not results or results[0][0] < 2:
        return {
            "found": False,
            "title": None,
            "blocks": [{"type": "p", "text": "I couldn't find that in the help guide. Try asking in a different "
                                             "way, or pick one of these topics."}],
            "links": [],
            "related": [t.title for t in suggestions(limit=4)],
            "note": None,
        }
    best = results[0][1]
    related = [t.title for s, t in results[1:6] if s >= 2 and _has(t.permission)][:3]
    note = None
    if not _has(best.permission):
        note = (f"This is usually done by: {', '.join(_roles_with(best.permission))}. "
                "Ask them if you need it done.")
    return {"found": True, "title": best.title, "blocks": best.blocks(), "links": _links(best),
            "related": related, "note": note}


def suggestions(limit: int = 5) -> list[Topic]:
    topics = [t for t in load_topics() if _has(t.permission)]
    picked = [t for t in topics if t.suggest]
    # Role-specific topics first, then general ones.
    picked.sort(key=lambda t: t.permission is None)
    return picked[:limit]


# ---- Routes ---------------------------------------------------------------------------------

@bp.before_request
@login_required
def _context():
    return load_workspace(required=False)


@bp.post("/ask")
@limiter.limit("30 per minute;300 per day")
def ask():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()[:300]
    if len(question) < 2:
        return jsonify(error="empty", message="Type a question first."), 400
    result = answer(question)
    if not result["found"]:
        current_app.logger.info("Help: unanswered question: %s", redact(question))
    return jsonify(result)


@bp.get("/")
def guide():
    topics = [t for t in load_topics() if _has(t.permission)]
    return render_template("help/guide.html", topics=topics, links={t.title: _links(t) for t in topics})


def suggested_questions() -> list[str]:
    if not current_user.is_authenticated:
        return []
    return [t.title for t in suggestions()]
