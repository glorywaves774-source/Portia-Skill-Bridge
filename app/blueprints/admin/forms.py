"""Super Administrator forms."""
from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import BooleanField, DecimalField, EmailField, IntegerField, RadioField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, NumberRange, Optional, ValidationError

from app.auth.forms import _c, strip
from app.models import BillingCycle, OrganizationType, TenantKind


class NewTenantForm(FlaskForm):
    kind = RadioField("Account type", choices=_c([(TenantKind.ORGANIZATION, "Organization"),
                                                  (TenantKind.INDIVIDUAL, "Individual")]),
                      default=TenantKind.ORGANIZATION, validators=[DataRequired()])
    name = StringField("Workspace name", filters=[strip], validators=[DataRequired(), Length(min=2, max=160)])
    organization_type = SelectField("Organization type", default="",
                                    choices=_c([("", "—")] + list(OrganizationType.LABELS.items())))
    plan = SelectField("Plan", validators=[DataRequired()], choices=[])
    billing_cycle = SelectField("Billing", choices=_c([(BillingCycle.MONTHLY, "Monthly"), (BillingCycle.ANNUAL, "Yearly")]))
    start_trial = BooleanField("Start with the plan's free trial", default=True)
    city = StringField("Town or city", filters=[strip], validators=[Optional(), Length(max=80)])
    owner_name = StringField("Owner's name", filters=[strip], validators=[DataRequired(), Length(min=2, max=160)])
    owner_email = EmailField("Owner's email", filters=[strip], validators=[DataRequired(), Email(), Length(max=255)])
    message = TextAreaField("Message in the invitation (optional)", filters=[strip], validators=[Optional(), Length(max=500)])

    def validate_organization_type(self, field):
        if self.kind.data == TenantKind.ORGANIZATION and not field.data:
            raise ValidationError("Choose the type of organization.")


class SubscriptionActionForm(FlaskForm):
    action = SelectField("Action", choices=_c([
        ("activate", "Record payment and start a new paid period"),
        ("renew", "Record payment and extend by one period"),
        ("extend_trial", "Extend the trial"),
        ("change_plan", "Change plan"),
        ("cancel", "Cancel at end of period"),
        ("cancel_now", "Cancel now"),
        ("start", "Start a new subscription"),
    ]))
    plan = SelectField("Plan", choices=[], validators=[Optional()])
    billing_cycle = SelectField("Billing", choices=_c([("", "Keep current"), (BillingCycle.MONTHLY, "Monthly"),
                                                       (BillingCycle.ANNUAL, "Yearly")]), validators=[Optional()])
    days = IntegerField("Days", validators=[Optional(), NumberRange(min=1, max=90)])
    reference = StringField("Payment reference", filters=[strip], validators=[Optional(), Length(max=80)])
    note = StringField("Note", filters=[strip], validators=[Optional(), Length(max=200)])


PAYMENT_METHODS = ["Bank transfer", "Mobile Money", "Cheque", "Cash", "Other"]


class ManualPaymentForm(FlaskForm):
    plan = SelectField("Plan", choices=[])
    billing_cycle = SelectField("Billing", choices=_c([(BillingCycle.MONTHLY, "Monthly"), (BillingCycle.ANNUAL, "Yearly")]))
    amount = DecimalField("Amount received (GHS)", places=2, validators=[DataRequired(), NumberRange(min=0.01, max=10_000_000)])
    method = SelectField("Paid by", choices=_c([(m, m) for m in PAYMENT_METHODS]))
    reference = StringField("Reference", filters=[strip], validators=[DataRequired(), Length(max=60)])


class PlanForm(FlaskForm):
    name = StringField("Name", filters=[strip], validators=[DataRequired(), Length(max=80)])
    tagline = StringField("Tagline", filters=[strip], validators=[Optional(), Length(max=160)])
    monthly_price = DecimalField("Monthly price (GHS)", places=2, validators=[DataRequired(), NumberRange(min=0)])
    annual_price = DecimalField("Yearly price (GHS)", places=2, validators=[DataRequired(), NumberRange(min=0)])
    trial_days = IntegerField("Trial days", validators=[DataRequired(), NumberRange(min=0, max=90)])
    max_learners = IntegerField("Learners (blank = unlimited)", validators=[Optional(), NumberRange(min=0)])
    max_staff = IntegerField("Staff users (blank = unlimited)", validators=[Optional(), NumberRange(min=0)])
    max_courses = IntegerField("Courses (blank = unlimited)", validators=[Optional(), NumberRange(min=0)])
    max_programmes = IntegerField("Programmes (blank = unlimited)", validators=[Optional(), NumberRange(min=0)])
    max_vacancies = IntegerField("Vacancies (blank = unlimited)", validators=[Optional(), NumberRange(min=0)])
    storage_mb = IntegerField("Storage in MB (blank = unlimited)", validators=[Optional(), NumberRange(min=0)])
    features = TextAreaField("Features (one per line)", validators=[Optional(), Length(max=2000)])
    is_public = BooleanField("Show on the pricing page")
    is_active = BooleanField("Available for new subscriptions")
    is_featured = BooleanField("Highlight as most popular")

    def validate_monthly_price(self, field):
        if field.data is not None and field.data > 1_000_000:
            raise ValidationError("That price looks too high.")
