"""Server-side validated forms (Flask-WTF)."""
from __future__ import annotations

import re

from flask_wtf import FlaskForm
from wtforms.fields.choices import SelectChoice
from wtforms import BooleanField, EmailField, HiddenField, PasswordField, RadioField, SelectField, StringField
from wtforms.validators import DataRequired, Email, EqualTo, Length, Optional, ValidationError

from app.models import OrganizationType

PHONE_RE = re.compile(r"^\+?[0-9 ()-]{7,20}$")


def _c(pairs):
    """WTForms 3.3+ choice objects."""
    return [SelectChoice(value, label) for value, label in pairs]


def strip(value):
    return value.strip() if isinstance(value, str) else value


def strong_password(form, field):
    value = field.data or ""
    if len(value) < 10:
        raise ValidationError("Use at least 10 characters.")
    if not (re.search(r"[A-Za-z]", value) and re.search(r"[0-9\W_]", value)):
        raise ValidationError("Mix letters with numbers or symbols.")
    email = (getattr(form, "email", None) and form.email.data) or ""
    if email and email.split("@")[0].lower() in value.lower() and len(email.split("@")[0]) >= 4:
        raise ValidationError("Don't use your email address in your password.")


def phone_number(form, field):
    if field.data and not PHONE_RE.match(field.data.strip()):
        raise ValidationError("Enter a valid phone number, e.g. +233 24 123 4567.")


class SignInForm(FlaskForm):
    email = EmailField("Email", filters=[strip], validators=[DataRequired(), Email(), Length(max=255)])
    password = PasswordField("Password", validators=[DataRequired(), Length(max=200)])
    remember = BooleanField("Keep me signed in on this device")
    next = HiddenField()


class SignUpForm(FlaskForm):
    kind = RadioField("Account type", choices=_c([("organization", "Organization"), ("individual", "Individual")]),
                      default="organization", validators=[DataRequired()])
    plan = SelectField("Plan", validators=[DataRequired()], choices=[])
    billing_cycle = RadioField("Billing", choices=_c([("monthly", "Monthly"), ("annual", "Yearly (2 months free)")]),
                               default="monthly", validators=[DataRequired()])
    organization_name = StringField("Organization name", filters=[strip], validators=[Length(max=160)])
    organization_type = SelectField("Organization type",
                                    choices=_c([("", "Choose…")] + list(OrganizationType.LABELS.items())))
    full_name = StringField("Your full name", filters=[strip], validators=[DataRequired(), Length(min=2, max=160)])
    email = EmailField("Email", filters=[strip], validators=[DataRequired(), Email(), Length(max=255)])
    phone = StringField("Phone (optional)", validators=[Optional(), Length(max=40), phone_number])
    city = StringField("Town or city (optional)", validators=[Optional(), Length(max=80)])
    password = PasswordField("Password", validators=[DataRequired(), strong_password])
    confirm = PasswordField("Confirm password", validators=[DataRequired(), EqualTo("password", "Passwords don't match.")])
    accept_terms = BooleanField("I agree to the terms of service and privacy policy",
                                validators=[DataRequired("Please accept the terms to continue.")])

    def validate_organization_name(self, field):
        if self.kind.data == "organization" and len(field.data or "") < 2:
            raise ValidationError("Enter your organization's name.")

    def validate_organization_type(self, field):
        if self.kind.data == "organization" and not field.data:
            raise ValidationError("Choose the type of organization.")


class ForgotPasswordForm(FlaskForm):
    email = EmailField("Email", filters=[strip], validators=[DataRequired(), Email(), Length(max=255)])


class ResetPasswordForm(FlaskForm):
    password = PasswordField("New password", validators=[DataRequired(), strong_password])
    confirm = PasswordField("Confirm new password",
                            validators=[DataRequired(), EqualTo("password", "Passwords don't match.")])


class ProfileForm(FlaskForm):
    full_name = StringField("Full name", filters=[strip], validators=[DataRequired(), Length(min=2, max=160)])
    phone = StringField("Phone", validators=[Optional(), Length(max=40), phone_number])


class CodeForm(FlaskForm):
    code = StringField("Code", filters=[lambda v: (v or "").strip()], validators=[DataRequired(), Length(max=20)])


class ConfirmPasswordForm(FlaskForm):
    password = PasswordField("Your password", validators=[DataRequired()])


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField("Current password", validators=[DataRequired()])
    password = PasswordField("New password", validators=[DataRequired(), strong_password])
    confirm = PasswordField("Confirm new password",
                            validators=[DataRequired(), EqualTo("password", "Passwords don't match.")])


class WorkspaceSettingsForm(FlaskForm):
    name = StringField("Workspace name", validators=[DataRequired(), Length(min=2, max=160)])
    organization_type = SelectField("Organization type", validators=[Optional()],
                                    choices=_c(OrganizationType.LABELS.items()))
    email = EmailField("Contact email", filters=[strip], validators=[Optional(), Email(), Length(max=255)])
    phone = StringField("Phone", validators=[Optional(), Length(max=40), phone_number])
    website = StringField("Website", validators=[Optional(), Length(max=255)])
    registration_number = StringField("Registration number", validators=[Optional(), Length(max=80)])
    address = StringField("Address", validators=[Optional(), Length(max=255)])
    city = StringField("Town or city", validators=[Optional(), Length(max=80)])
    region = StringField("Region", validators=[Optional(), Length(max=80)])

    def validate_website(self, field):
        if field.data and not re.match(r"^https?://[^\s/$.?#].[^\s]*$", field.data.strip(), re.I):
            raise ValidationError("Enter a full web address starting with https://")


# ---- Stage 3: people ---------------------------------------------------------------

from wtforms import SelectMultipleField, TextAreaField  # noqa: E402
from wtforms.widgets import CheckboxInput, ListWidget  # noqa: E402

from app.models import Role  # noqa: E402


class MultiCheckboxField(SelectMultipleField):
    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()


def role_choices(kind: str):
    roles = [r for r in Role.TENANT_ROLES if not (kind == "individual" and r == Role.ORG_ADMIN)]
    return _c((r, Role.LABELS[r]) for r in roles)


class InviteForm(FlaskForm):
    email = EmailField("Email", filters=[strip], validators=[DataRequired(), Email(), Length(max=255)])
    full_name = StringField("Name (optional)", filters=[strip], validators=[Optional(), Length(max=160)])
    roles = MultiCheckboxField("Roles", validators=[DataRequired("Choose at least one role.")])
    message = TextAreaField("Personal message (optional)", filters=[strip], validators=[Optional(), Length(max=500)])


class RolesForm(FlaskForm):
    roles = MultiCheckboxField("Roles", validators=[DataRequired("Choose at least one role.")])


class AcceptInvitationForm(FlaskForm):
    full_name = StringField("Your full name", filters=[strip], validators=[DataRequired(), Length(min=2, max=160)])
    password = PasswordField("Choose a password", validators=[DataRequired(), strong_password])
    confirm = PasswordField("Confirm password", validators=[DataRequired(), EqualTo("password", "Passwords don't match.")])
    accept_terms = BooleanField("I agree to the terms of service and privacy policy",
                                validators=[DataRequired("Please accept the terms to continue.")])
