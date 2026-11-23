from __future__ import annotations

from datetime import date

from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired
from wtforms import DateField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional, ValidationError

from app.auth.forms import _c, strip
from app.models.assessment import ASSESSMENT_KINDS, LEVELS, FrameworkStatus


class FrameworkForm(FlaskForm):
    name = StringField("Framework name", filters=[strip], validators=[DataRequired(), Length(max=160)])
    trade = StringField("Trade", filters=[strip], validators=[Optional(), Length(max=120)])
    level = StringField("Qualification level (optional)", filters=[strip], validators=[Optional(), Length(max=60)])
    description = TextAreaField("Description", filters=[strip], validators=[Optional(), Length(max=4000)])


class FrameworkStatusForm(FlaskForm):
    status = SelectField("Status", choices=_c(FrameworkStatus.LABELS.items()))


class CompetencyForm(FlaskForm):
    code = StringField("Code", filters=[strip], validators=[DataRequired(), Length(max=20)])
    title = StringField("Competency (unit) title", filters=[strip], validators=[DataRequired(), Length(max=200)])
    description = TextAreaField("Description", filters=[strip], validators=[Optional(), Length(max=2000)])
    criteria = TextAreaField("Performance criteria (one per line)", validators=[Optional(), Length(max=6000)])
    required_level = SelectField("Level needed to be competent", choices=_c((str(k), f"{k} — {v}") for k, v in LEVELS.items()),
                                 default="3")


class NewAssessmentForm(FlaskForm):
    learner = SelectField("Learner", choices=[], validators=[DataRequired("Choose the learner.")])
    framework = SelectField("Competency framework", choices=[], validators=[DataRequired("Choose a framework.")])
    kind = SelectField("Type of assessment", choices=_c(ASSESSMENT_KINDS.items()), default="practical")
    assessed_on = DateField("Date", default=date.today, validators=[DataRequired()])
    programme = SelectField("Programme (optional)", choices=[], validators=[Optional()])
    location = StringField("Location", filters=[strip], validators=[Optional(), Length(max=160)])

    def validate_assessed_on(self, field):
        if field.data and field.data > date.today():
            raise ValidationError("The assessment date can't be in the future.")


class EvidenceForm(FlaskForm):
    title = StringField("Title", filters=[strip], validators=[Optional(), Length(max=160)])
    file = FileField("Evidence (photo, PDF, Word, PowerPoint or Excel)", validators=[FileRequired("Choose a file.")])


class IssueForm(FlaskForm):
    basis = SelectField("What is the certificate for?", choices=[], validators=[DataRequired("Choose what was earned.")])
    issued_on = DateField("Issue date", default=date.today, validators=[DataRequired()])
    expires_on = DateField("Valid until (optional)", validators=[Optional()])
    signatory_name = StringField("Signed by (name)", filters=[strip], validators=[Optional(), Length(max=160)])
    signatory_title = StringField("Signatory's title", filters=[strip], validators=[Optional(), Length(max=160)])


class BulkIssueForm(FlaskForm):
    signatory_name = StringField("Signed by (name)", filters=[strip], validators=[Optional(), Length(max=160)])
    signatory_title = StringField("Signatory's title", filters=[strip], validators=[Optional(), Length(max=160)])


class RevokeForm(FlaskForm):
    reason = StringField("Reason", filters=[strip], validators=[DataRequired("Give a reason."), Length(max=255)])
