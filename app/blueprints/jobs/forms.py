from __future__ import annotations

from decimal import Decimal

from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DateField,
    DateTimeLocalField,
    DecimalField,
    EmailField,
    IntegerField,
    SelectField,
    StringField,
    TextAreaField,
    URLField,
)
from wtforms.validators import DataRequired, Email, Length, NumberRange, Optional, URL, ValidationError

from app.auth.forms import MultiCheckboxField, _c, phone_number, strip
from app.models.employment import SECTORS, ApplicationStatus, VacancyStatus
from app.models.learner import EMPLOYMENT_TYPES, GHANA_REGIONS

REGION_CHOICES = _c([("", "—")] + [(r, r) for r in GHANA_REGIONS])


def to_minor(value: Decimal | None) -> int | None:
    return None if value is None else int((value * 100).quantize(Decimal("1")))


def from_minor(value: int | None) -> Decimal | None:
    return None if value is None else (Decimal(value) / 100)


class EmployerForm(FlaskForm):
    name = StringField("Employer name", filters=[strip], validators=[DataRequired(), Length(max=160)])
    sector = SelectField("Sector", choices=_c([("", "—")] + list(SECTORS.items())), validators=[Optional()])
    contact_name = StringField("Contact person", filters=[strip], validators=[Optional(), Length(max=160)])
    contact_role = StringField("Their role", filters=[strip], validators=[Optional(), Length(max=120)])
    phone = StringField("Phone", filters=[strip], validators=[Optional(), Length(max=40), phone_number])
    email = EmailField("Email", filters=[strip], validators=[Optional(), Email(), Length(max=255)])
    website = URLField("Website", filters=[strip], validators=[Optional(), URL(require_tld=True), Length(max=255)])
    region = SelectField("Region", choices=REGION_CHOICES, validators=[Optional()])
    city = StringField("Town or city", filters=[strip], validators=[Optional(), Length(max=80)])
    address = StringField("Address", filters=[strip], validators=[Optional(), Length(max=255)])
    notes = TextAreaField("Notes", filters=[strip], validators=[Optional(), Length(max=4000)])


class InviteContactForm(FlaskForm):
    email = EmailField("Email", filters=[strip], validators=[DataRequired(), Email(), Length(max=255)])
    full_name = StringField("Name", filters=[strip], validators=[Optional(), Length(max=160)])


class LinkMemberForm(FlaskForm):
    member = SelectField("Person", choices=[], validators=[DataRequired("Choose a person.")])


class VacancyForm(FlaskForm):
    employer = SelectField("Employer", choices=[], validators=[DataRequired("Choose the employer.")])
    title = StringField("Job title", filters=[strip], validators=[DataRequired(), Length(max=160)])
    trade = StringField("Trade or skill area", filters=[strip], validators=[Optional(), Length(max=120)])
    employment_type = SelectField("Type of work", choices=_c(EMPLOYMENT_TYPES.items()), default="full_time")
    positions = IntegerField("Positions", default=1, validators=[DataRequired(), NumberRange(min=1, max=1000)])
    region = SelectField("Region", choices=REGION_CHOICES, validators=[Optional()])
    location = StringField("Town or workplace", filters=[strip], validators=[Optional(), Length(max=160)])
    salary_min = DecimalField("Pay from (GHS a month)", places=2, validators=[Optional(), NumberRange(min=0, max=10_000_000)])
    salary_max = DecimalField("Pay up to (GHS a month)", places=2, validators=[Optional(), NumberRange(min=0, max=10_000_000)])
    closing_date = DateField("Closing date", validators=[Optional()])
    open_to_learners = BooleanField("Learners can see this job and apply", default=True)
    competencies = MultiCheckboxField("Required competencies", choices=[], validators=[Optional()])
    description = TextAreaField("About the job", filters=[strip], validators=[Optional(), Length(max=6000)])
    requirements = TextAreaField("Requirements", filters=[strip], validators=[Optional(), Length(max=4000)])

    def validate_salary_max(self, field):
        if field.data is not None and self.salary_min.data is not None and field.data < self.salary_min.data:
            raise ValidationError("The highest pay is below the lowest pay.")


class VacancyStatusForm(FlaskForm):
    status = SelectField("Status", choices=_c(VacancyStatus.LABELS.items()))


class ApplicationStatusForm(FlaskForm):
    status = SelectField("Stage", choices=[])

    def limit_to(self, statuses):
        self.status.choices = _c((s, ApplicationStatus.LABELS[s]) for s in statuses)
        return self


class InterviewForm(FlaskForm):
    when = DateTimeLocalField("Date and time (GMT)", format="%Y-%m-%dT%H:%M", validators=[DataRequired()])
    location = StringField("Where", filters=[strip], validators=[Optional(), Length(max=160)])


class NotesForm(FlaskForm):
    notes = TextAreaField("Notes (not shown to the learner)", filters=[strip], validators=[Optional(), Length(max=4000)])


class HireForm(FlaskForm):
    job_title = StringField("Job title", filters=[strip], validators=[DataRequired(), Length(max=160)])
    employment_type = SelectField("Type of work", choices=_c(EMPLOYMENT_TYPES.items()))
    start_date = DateField("Start date", validators=[DataRequired()])
    monthly_pay = DecimalField("Monthly pay (GHS, optional)", places=2, validators=[Optional(), NumberRange(min=0, max=10_000_000)])


class PlacementForm(FlaskForm):
    learner = SelectField("Learner", choices=[], validators=[DataRequired("Choose the learner.")])
    employer = SelectField("Employer on your list", choices=[], validators=[Optional()])
    employer_name = StringField("Or another employer / business name", filters=[strip], validators=[Optional(), Length(max=160)])
    job_title = StringField("Job title", filters=[strip], validators=[DataRequired(), Length(max=160)])
    employment_type = SelectField("Type of work", choices=_c(EMPLOYMENT_TYPES.items()))
    start_date = DateField("Start date", validators=[DataRequired()])
    monthly_pay = DecimalField("Monthly pay or earnings (GHS, optional)", places=2, validators=[Optional(), NumberRange(min=0, max=10_000_000)])
    notes = TextAreaField("Notes", filters=[strip], validators=[Optional(), Length(max=2000)])

    def validate(self, extra_validators=None):
        ok = super().validate(extra_validators)
        if not self.employer.data and not self.employer_name.data:
            self.employer_name.errors = [*self.employer_name.errors, "Choose an employer or type the business name."]
            return False
        return ok


class EndPlacementForm(FlaskForm):
    end_date = DateField("Last day", validators=[DataRequired()])
    reason = StringField("Reason", filters=[strip], validators=[Optional(), Length(max=255)])


class FollowUpForm(FlaskForm):
    done_on = DateField("Date of the check", validators=[DataRequired()])
    still_employed = SelectField("Still in this work?", choices=_c([("yes", "Yes"), ("no", "No")]))
    monthly_pay = DecimalField("Monthly pay now (GHS, optional)", places=2, validators=[Optional(), NumberRange(min=0, max=10_000_000)])
    notes = StringField("Notes", filters=[strip], validators=[Optional(), Length(max=500)])


class ApplyForm(FlaskForm):
    cover_note = TextAreaField("Why you're a good fit (optional)", filters=[strip], validators=[Optional(), Length(max=2000)])
