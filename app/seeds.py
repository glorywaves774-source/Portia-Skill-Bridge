"""Default subscription catalogue. Edit here, then run `flask seed-plans` (safe to re-run).

Annual price = 10 x monthly (two months free). Prices are in pesewas (GHS x 100).
Organization prices are from the product brief. Individual prices are a
proposal for review.
"""
from __future__ import annotations

from sqlalchemy import select

from app.extensions import db
from app.models.subscription import PlanAudience, SubscriptionPlan


def _ghs(amount: float) -> int:
    return int(round(amount * 100))


PLANS = [
    # ---- Organizations -------------------------------------------------------
    dict(code="org-starter", name="Starter", audience=PlanAudience.ORGANIZATION, sort_order=10,
         tagline="For small training centres getting started.",
         monthly=150, trial_days=14, max_learners=100, max_staff=5, max_courses=10, max_programmes=3,
         max_vacancies=5, storage_mb=5_000,
         features=["Learner records & profiles", "Online courses & quizzes", "Attendance tracking",
                   "Digital certificates", "Standard reports (PDF & Excel)", "Email support"]),
    dict(code="org-professional", name="Professional", audience=PlanAudience.ORGANIZATION, sort_order=20,
         tagline="For growing institutions and NGO programmes.", is_featured=True,
         monthly=350, trial_days=14, max_learners=500, max_staff=20, max_courses=50, max_programmes=15,
         max_vacancies=30, storage_mb=25_000,
         features=["Everything in Starter", "Skills assessment & competency profiles", "Apprenticeship management",
                   "Job vacancies & applicant tracking", "M&E dashboards with gender analysis", "Priority support"]),
    dict(code="org-business", name="Business", audience=PlanAudience.ORGANIZATION, sort_order=30,
         tagline="For multi-site providers and employers.",
         monthly=750, trial_days=14, max_learners=2_000, max_staff=60, max_courses=None, max_programmes=None,
         max_vacancies=None, storage_mb=100_000,
         features=["Everything in Professional", "Unlimited courses & programmes", "Employer recruitment portal",
                   "Employment outcome tracking", "Donor-ready project reports", "API access"]),
    dict(code="org-enterprise", name="Enterprise", audience=PlanAudience.ORGANIZATION, sort_order=40,
         tagline="For national programmes and large organizations.", custom=True,
         monthly=0, trial_days=30, max_learners=None, max_staff=None, max_courses=None, max_programmes=None,
         max_vacancies=None, storage_mb=None,
         features=["Everything in Business", "Unlimited learners & staff", "Custom integrations",
                   "Dedicated success manager", "Service-level agreement", "On-site training"]),
    # ---- Individuals (proposed pricing) --------------------------------------
    dict(code="ind-free", name="Learner Free", audience=PlanAudience.INDIVIDUAL, sort_order=110,
         tagline="Build your skills profile and apply for jobs.",
         monthly=0, trial_days=0, max_learners=1, max_staff=0, max_courses=3, max_programmes=0,
         max_vacancies=0, storage_mb=200,
         features=["Personal skills profile", "CV & portfolio", "Apply for vacancies", "Up to 3 self-paced courses"]),
    dict(code="ind-plus", name="Learner Plus", audience=PlanAudience.INDIVIDUAL, sort_order=120,
         tagline="For learners and job seekers who want more.", is_featured=True,
         monthly=35, trial_days=7, max_learners=1, max_staff=0, max_courses=None, max_programmes=0,
         max_vacancies=0, storage_mb=2_000,
         features=["Everything in Free", "Unlimited courses", "Verified digital certificates",
                   "Skills gap analysis", "Featured profile for employers"]),
    dict(code="ind-pro", name="Independent Pro", audience=PlanAudience.INDIVIDUAL, sort_order=130,
         tagline="For freelance trainers, assessors and coaches.",
         monthly=90, trial_days=14, max_learners=50, max_staff=1, max_courses=15, max_programmes=3,
         max_vacancies=5, storage_mb=10_000,
         features=["Publish your own courses", "Run assessments for up to 50 learners", "Issue certificates",
                   "Progress reports", "Post vacancies"]),
]


def seed_plans(update: bool = False) -> tuple[int, int]:
    """Insert missing default plans; with update=True also overwrite existing ones.

    Plans edited in the Super Admin console are kept unless update=True.
    Returns (created, updated).
    """
    created = updated = 0
    for spec in PLANS:
        spec = dict(spec)
        monthly = spec.pop("monthly")
        custom = spec.pop("custom", False)
        values = dict(
            spec,
            monthly_price_minor=_ghs(monthly),
            annual_price_minor=_ghs(monthly * 10),
            is_custom_pricing=custom,
            currency="GHS",
        )
        plan = db.session.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == values["code"]))
        if plan is None:
            db.session.add(SubscriptionPlan(**values))
            created += 1
        elif update:
            for key, value in values.items():
                setattr(plan, key, value)
            updated += 1
    db.session.commit()
    return created, updated
