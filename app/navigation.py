"""Sidebar, phone menu and bottom tab bar contents, built from the person's permissions."""
from __future__ import annotations

from dataclasses import dataclass

from flask import g, request, url_for
from flask_login import current_user

from app.access import can


@dataclass(frozen=True)
class NavItem:
    key: str
    endpoint: str
    icon: str
    label: str
    short: str
    permission: str | None = None
    tab: bool = True
    also: tuple = ()  # other pages that belong under this menu item

    @property
    def url(self) -> str:
        return url_for(self.endpoint)

    @property
    def active(self) -> bool:
        ep = request.endpoint or ""
        return ep == self.endpoint or ep.startswith(self.endpoint + "_") or ep in self.also


ADMIN = [
    ("Platform", [
        NavItem("overview", "admin.overview", "layout-dashboard", "Overview", "Overview"),
        NavItem("tenants", "admin.tenants", "building-2", "Workspaces", "Workspaces",
                also=("admin.tenant_detail", "admin.tenant_new")),
        NavItem("users", "admin.users", "users", "People", "People", also=("admin.user_detail",)),
        NavItem("plans", "admin.plans", "credit-card", "Plans", "Plans", also=("admin.plan_edit",)),
        NavItem("billing", "admin.billing_overview", "credit-card", "Payments", "Payments", tab=False),
        NavItem("audit", "admin.audit_log", "scroll-text", "Audit log", "Audit", tab=False),
        NavItem("system", "admin.system_status", "server", "System status", "System", tab=False),
        NavItem("backups", "admin.backups_page", "database-backup", "Backups", "Backups", tab=False),
    ]),
]

WORKSPACE = [
    ("Workspace", [
        NavItem("dashboard", "workspace.dashboard", "layout-dashboard", "Dashboard", "Home", "workspace.view"),
        NavItem("learners", "learners.index", "graduation-cap", "Learners", "Learners", "learners.view",
                also=("learners.profile", "learners.new", "learners.edit")),
        NavItem("programmes", "programmes.index", "layers", "Programmes", "Training", "programmes.view",
                also=("programmes.detail", "programmes.new", "programmes.edit", "programmes.enrol",
                      "programmes.attendance")),
        NavItem("courses", "courses.index", "book-open", "Courses", "Courses", "courses.view",
                also=("courses.detail", "courses.new", "courses.lesson", "courses.enrol", "courses.submission")),
        NavItem("assessments", "assessments.index", "clipboard-check", "Assessments", "Assess", "assessments.view",
                also=("assessments.detail", "assessments.new", "assessments.gaps")),
        NavItem("frameworks", "frameworks.index", "layers", "Competency frameworks", "Skills", "assessments.view",
                tab=False, also=("frameworks.detail", "frameworks.new", "frameworks.edit_competency")),
        NavItem("certificates", "certificates.index", "award", "Certificates", "Certs", "certificates.issue",
                tab=False, also=("certificates.detail", "certificates.issue")),
        NavItem("jobs", "jobs.index", "briefcase", "Jobs", "Jobs", "vacancies.view",
                also=("jobs.detail", "jobs.new", "jobs.edit", "jobs.application")),
        NavItem("employers", "employers.index", "building-2", "Employers", "Employers", "vacancies.view", tab=False,
                also=("employers.detail", "employers.new", "employers.edit")),
        NavItem("placements", "placements.index", "handshake", "Placements", "In work", "vacancies.view", tab=False,
                also=("placements.detail", "placements.new")),
        NavItem("reports", "reports.index", "chart-column", "Reports", "Reports", "reports.view"),
        NavItem("learn", "learn.home", "book-open", "My learning", "Learn", "self.learning",
                also=("learn.course", "learn.lesson", "learn.attempt")),
        NavItem("me", "me.my_profile", "user", "My profile", "Profile", "self.learning"),
        NavItem("myjobs", "myjobs.index", "briefcase", "Jobs for me", "Jobs", "self.jobs", also=("myjobs.job",)),
        NavItem("people", "workspace.people", "users", "People", "People", "members.view",
                also=("workspace.person",)),
        NavItem("subscription", "workspace.subscription", "credit-card", "Subscription", "Plan", "billing.view",
                also=("billing.checkout", "billing.invoices", "billing.invoice")),
        NavItem("activity", "workspace.activity", "scroll-text", "Activity", "Activity", "audit.view"),
        NavItem("settings", "workspace.settings", "settings", "Settings", "Settings", "workspace.settings", tab=False),
    ]),
]

ACCOUNT = NavItem("account", "account.profile", "user", "My account", "Account")
HELP = NavItem("help", "help.guide", "circle-help", "Help guide", "Help", tab=False)


def build() -> dict:
    """Navigation for the current page: sections for the sidebar and up to 4 tabs (+ Menu)."""
    if request.blueprint == "admin" or (request.blueprint == "account" and g.get("workspace") is None
                                         and current_user.is_authenticated and current_user.is_platform_admin):
        sections = [(title, list(items)) for title, items in ADMIN]
        context = "admin"
    elif request.blueprint == "help" and g.get("workspace") is None and current_user.is_platform_admin:
        sections = [(title, list(items)) for title, items in ADMIN]
        context = "admin"
    elif g.get("workspace") is not None:
        sections = [(title, [i for i in items if i.permission is None or can(i.permission)])
                    for title, items in WORKSPACE]
        context = "workspace"
    else:
        sections = []
        context = "account"

    sections = [(t, items) for t, items in sections if items]
    sections.append(("You", [ACCOUNT, HELP]))
    tabs = [i for _, items in sections for i in items if i.tab]
    if context == "workspace" and can("reports.view") and not can("learners.manage"):
        # People whose job is reporting (M&E, finance) get Reports right after Home.
        tabs.sort(key=lambda i: {"dashboard": 0, "reports": 1}.get(i.key, 2))
    tabs = tabs[:4]
    return {"sections": sections, "tabs": tabs, "context": context}
