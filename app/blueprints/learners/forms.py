"""Learner forms (all validated on the server)."""
from __future__ import annotations

from datetime import date

from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired
from wtforms import BooleanField, DateField, EmailField, HiddenField, IntegerField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, NumberRange, Optional, ValidationError

from app.auth.forms import MultiCheckboxField, _c, phone_number, strip
from app.models.learner import (
    CATEGORIES,
    DOCUMENT_TYPES,
    EDUCATION_LEVELS,
    EMPLOYMENT_STATUSES,
    EMPLOYMENT_TYPES,
    GHANA_REGIONS,
    IDENTITY_TYPES,
    SKILL_LEVELS,
    Gender,
    LearnerStatus,
)

BLANK = [("", "—")]
THIS_YEAR = date.today().year


def _not_future(form, field):
    if field.data and field.data > date.today():
        raise ValidationError("This date can't be in the future.")


def _plausible_birth(form, field):
    if field.data:
        age = (date.today() - field.data).days // 365
        if age < 10 or age > 100:
            raise ValidationError("Check the date of birth: the learner should be 10 to 100 years old.")


class ContactMixin:
    phone = StringField("Phone", filters=[strip], validators=[Optional(), Length(max=40), phone_number])
    alt_phone = StringField("Other phone", filters=[strip], validators=[Optional(), Length(max=40), phone_number])
    email = EmailField("Email", filters=[strip], validators=[Optional(), Email(), Length(max=255)])
    address = StringField("Address / landmark", filters=[strip], validators=[Optional(), Length(max=255)])
    city = StringField("Town or city", filters=[strip], validators=[Optional(), Length(max=80)])
    district = StringField("District", filters=[strip], validators=[Optional(), Length(max=80)])
    region = SelectField("Region", choices=_c(BLANK + [(r, r) for r in GHANA_REGIONS]), default="")
    emergency_name = StringField("Emergency contact name", filters=[strip], validators=[Optional(), Length(max=160)])
    emergency_phone = StringField("Emergency contact phone", filters=[strip],
                                  validators=[Optional(), Length(max=40), phone_number])
    emergency_relation = StringField("Relationship", filters=[strip], validators=[Optional(), Length(max=60)])


class LearnerForm(ContactMixin, FlaskForm):
    first_name = StringField("First name", filters=[strip], validators=[DataRequired(), Length(max=80)])
    middle_name = StringField("Middle name(s)", filters=[strip], validators=[Optional(), Length(max=80)])
    last_name = StringField("Surname", filters=[strip], validators=[DataRequired(), Length(max=80)])
    gender = SelectField("Gender", choices=_c(BLANK + list(Gender.LABELS.items())), default="",
                         validators=[DataRequired("Choose a gender option.")])
    date_of_birth = DateField("Date of birth", validators=[Optional(), _not_future, _plausible_birth])
    nationality = StringField("Nationality", filters=[strip], default="Ghanaian", validators=[Optional(), Length(max=60)])
    id_type = SelectField("ID document", choices=_c(BLANK + list(IDENTITY_TYPES.items())), default="")
    id_number = StringField("ID number", filters=[strip], validators=[Optional(), Length(max=40)])
    has_disability = BooleanField("Has a disability")
    disability_details = StringField("Disability details (optional)", filters=[strip],
                                     validators=[Optional(), Length(max=255)])
    education_level = SelectField("Highest education", choices=_c(BLANK + list(EDUCATION_LEVELS.items())), default="")
    employment_status = SelectField("Employment status at registration",
                                    choices=_c(BLANK + list(EMPLOYMENT_STATUSES.items())), default="")
    categories = MultiCheckboxField("Beneficiary categories", choices=_c(CATEGORIES.items()))
    registered_on = DateField("Registration date", default=date.today, validators=[Optional(), _not_future])
    notes = TextAreaField("Notes", filters=[strip], validators=[Optional(), Length(max=2000)])
    confirm_duplicate = HiddenField()

    def validate_id_number(self, field):
        if field.data and not self.id_type.data:
            raise ValidationError("Choose the type of ID document.")
        if self.id_type.data == "ghana_card" and field.data:
            import re
            if not re.fullmatch(r"GHA-?\d{9}-?\d", field.data.upper().replace(" ", "")):
                raise ValidationError("A Ghana Card number looks like GHA-123456789-0.")


class SelfProfileForm(ContactMixin, FlaskForm):
    pass


class EducationForm(FlaskForm):
    institution = StringField("School or institution", filters=[strip], validators=[DataRequired(), Length(max=160)])
    qualification = StringField("Qualification", filters=[strip], validators=[DataRequired(), Length(max=160)])
    field_of_study = StringField("Field of study", filters=[strip], validators=[Optional(), Length(max=120)])
    level = SelectField("Level", choices=_c(BLANK + list(EDUCATION_LEVELS.items())), default="")
    start_year = IntegerField("Start year", validators=[Optional(), NumberRange(min=1950, max=THIS_YEAR + 1)])
    end_year = IntegerField("End year (blank if ongoing)", validators=[Optional(), NumberRange(min=1950, max=THIS_YEAR + 6)])

    def validate_end_year(self, field):
        if field.data and self.start_year.data and field.data < self.start_year.data:
            raise ValidationError("The end year is before the start year.")


class SkillForm(FlaskForm):
    name = StringField("Skill", filters=[strip], validators=[DataRequired(), Length(max=120)])
    level = SelectField("Level", choices=_c(SKILL_LEVELS.items()), default="basic")
    certified = BooleanField("Certified")
    certificate_body = StringField("Certified by", filters=[strip], validators=[Optional(), Length(max=160)])
    year = IntegerField("Year", validators=[Optional(), NumberRange(min=1950, max=THIS_YEAR)])


class EmploymentForm(FlaskForm):
    employer = StringField("Employer or business", filters=[strip], validators=[DataRequired(), Length(max=160)])
    position = StringField("Position", filters=[strip], validators=[DataRequired(), Length(max=120)])
    employment_type = SelectField("Type", choices=_c(EMPLOYMENT_TYPES.items()), default="full_time")
    sector = StringField("Sector", filters=[strip], validators=[Optional(), Length(max=80)])
    location = StringField("Location", filters=[strip], validators=[Optional(), Length(max=120)])
    start_date = DateField("Start date", validators=[DataRequired(), _not_future])
    end_date = DateField("End date (blank if current)", validators=[Optional()])
    monthly_income = IntegerField("Monthly income, GHS (optional)", validators=[Optional(), NumberRange(min=0, max=1_000_000)])

    def validate_end_date(self, field):
        if field.data and self.start_date.data and field.data < self.start_date.data:
            raise ValidationError("The end date is before the start date.")


class DocumentForm(FlaskForm):
    doc_type = SelectField("Type", choices=_c(DOCUMENT_TYPES.items()), default="other")
    title = StringField("Title", filters=[strip], validators=[Optional(), Length(max=160)])
    file = FileField("File (PDF, Word, JPG or PNG)", validators=[FileRequired("Choose a file to upload.")])


class PhotoForm(FlaskForm):
    photo = FileField("Passport photo (JPG or PNG)", validators=[FileRequired("Choose a photo to upload.")])


class StatusForm(FlaskForm):
    status = SelectField("Status", choices=_c(LearnerStatus.LABELS.items()))


class DeleteLearnerForm(FlaskForm):
    confirm = StringField("Type the learner number to confirm", filters=[strip], validators=[DataRequired()])


class InviteLearnerForm(FlaskForm):
    email = EmailField("Email for sign-in", filters=[strip], validators=[DataRequired(), Email(), Length(max=255)])
