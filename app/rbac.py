"""Role-based access control.

Permissions are fine-grained strings ("learners.manage"). Roles are bundles of
permissions. A person's permissions in a workspace are the union of the roles
they hold there, plus owner permissions if they own the workspace. Platform
(Super Administrator) access is separate and held on the user account.

Pages and API endpoints check *permissions*, never role names, so roles can be
reshaped later without touching views.
"""
from __future__ import annotations

from app.models.user import Role

# ---- Permission catalogue ---------------------------------------------------
# Modules arrive in later stages; their permissions are defined now so roles
# are complete and stable from the start.
PERMISSIONS: dict[str, str] = {
    # Workspace
    "workspace.view": "Open the workspace dashboard",
    "workspace.settings": "Change workspace details and settings",
    "members.view": "See the people in the workspace",
    "members.manage": "Invite people, change roles and deactivate accounts",
    "audit.view": "See the workspace activity log",
    # Subscription & finance
    "billing.view": "See the plan, usage and billing status",
    "billing.manage": "Change plan and pay for the subscription",
    "payments.view": "See payment records and financial reports",
    # Learners & training
    "learners.view": "See learner and beneficiary records",
    "learners.manage": "Register and edit learners and beneficiaries",
    "programmes.view": "See training programmes",
    "programmes.manage": "Create and run training programmes",
    "attendance.record": "Take attendance",
    "courses.view": "See courses and learning content",
    "courses.manage": "Create and edit courses and content",
    "apprenticeships.manage": "Manage apprenticeship placements",
    # Assessment & certification
    "assessments.view": "See assessment records",
    "assessments.conduct": "Carry out and record skills assessments",
    "competencies.manage": "Define competency frameworks",
    "certificates.issue": "Issue and revoke certificates",
    # Employment
    "vacancies.view": "See job vacancies",
    "vacancies.manage": "Post and edit job vacancies",
    "applicants.manage": "Shortlist applicants and schedule interviews",
    "placements.manage": "Record internships and employment placements",
    # Monitoring & evaluation
    "reports.view": "See monitoring and evaluation reports",
    "reports.export": "Export reports to PDF and Excel",
    # Self-service (every learner)
    "self.profile": "Manage your own profile, CV and documents",
    "self.learning": "Take courses and see your own progress",
    "self.jobs": "Browse and apply for vacancies",
}

_ALL = frozenset(PERMISSIONS)
_SELF = frozenset({"self.profile", "self.learning", "self.jobs", "workspace.view"})

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    Role.ORG_ADMIN: _ALL - {"self.learning", "self.jobs"},
    Role.TRAINING_MANAGER: frozenset({
        "workspace.view", "members.view", "learners.view", "learners.manage", "programmes.view",
        "programmes.manage", "attendance.record", "courses.view", "courses.manage", "apprenticeships.manage",
        "assessments.view", "certificates.issue", "vacancies.view", "applicants.manage", "placements.manage",
        "reports.view", "reports.export", "self.profile",
    }),
    Role.TRAINER: frozenset({
        "workspace.view", "learners.view", "programmes.view", "attendance.record", "courses.view",
        "courses.manage", "assessments.view", "self.profile",
    }),
    Role.ASSESSOR: frozenset({
        "workspace.view", "learners.view", "assessments.view", "assessments.conduct", "competencies.manage",
        "certificates.issue", "self.profile",
    }),
    Role.EMPLOYER: frozenset({
        "workspace.view", "vacancies.view", "vacancies.manage", "applicants.manage", "placements.manage",
        "self.profile",
    }),
    Role.ME_OFFICER: frozenset({
        "workspace.view", "learners.view", "programmes.view", "assessments.view", "vacancies.view",
        "reports.view", "reports.export", "audit.view", "self.profile",
    }),
    Role.FINANCE_OFFICER: frozenset({
        "workspace.view", "billing.view", "billing.manage", "payments.view", "reports.view", "self.profile",
    }),
    Role.LEARNER: _SELF,
}

# Whoever owns a workspace (the person who signed it up) can always manage it.
OWNER_PERMISSIONS = frozenset({
    "workspace.view", "workspace.settings", "members.view", "billing.view", "billing.manage",
    "payments.view", "audit.view",
})


def _validate() -> None:
    for role, perms in ROLE_PERMISSIONS.items():
        unknown = perms - _ALL
        if unknown:
            raise RuntimeError(f"Role {role} has unknown permissions: {sorted(unknown)}")
    missing = set(Role.TENANT_ROLES) - set(ROLE_PERMISSIONS)
    if missing:
        raise RuntimeError(f"Roles without permissions: {sorted(missing)}")


_validate()


def permissions_for(roles, *, is_owner: bool = False) -> frozenset[str]:
    perms: set[str] = set()
    for role in roles:
        perms |= ROLE_PERMISSIONS.get(role, frozenset())
    if is_owner:
        perms |= OWNER_PERMISSIONS
    return frozenset(perms)
