from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import BooleanField, DateField, IntegerField, SelectField, StringField, TextAreaField, TimeField
from wtforms.validators import DataRequired, Length, NumberRange, Optional, ValidationError

from app.auth.forms import _c, strip
from app.models.training import DELIVERY_MODES, EnrolmentStatus, ProgrammeStatus


class ProgrammeForm(FlaskForm):
    name = StringField("Programme name", filters=[strip], validators=[DataRequired(), Length(max=160)])
    trade = StringField("Trade or skill area", filters=[strip], validators=[Optional(), Length(max=120)])
    delivery_mode = SelectField("Delivery", choices=_c(DELIVERY_MODES.items()), default="in_person")
    location = StringField("Location", filters=[strip], validators=[Optional(), Length(max=160)])
    start_date = DateField("Start date", validators=[Optional()])
    end_date = DateField("End date", validators=[Optional()])
    capacity = IntegerField("Capacity (blank = no limit)", validators=[Optional(), NumberRange(min=1, max=100000)])
    funder = StringField("Funder or project (optional)", filters=[strip], validators=[Optional(), Length(max=160)])
    description = TextAreaField("Description", filters=[strip], validators=[Optional(), Length(max=4000)])

    def validate_end_date(self, field):
        if field.data and self.start_date.data and field.data < self.start_date.data:
            raise ValidationError("The end date is before the start date.")


class ProgrammeStatusForm(FlaskForm):
    status = SelectField("Status", choices=_c(ProgrammeStatus.LABELS.items()))


class TrainerForm(FlaskForm):
    user = SelectField("Trainer", choices=[], validators=[DataRequired("Choose a trainer.")])
    lead = BooleanField("Lead trainer")


class SessionForm(FlaskForm):
    title = StringField("Session title", filters=[strip], validators=[DataRequired(), Length(max=160)])
    date = DateField("Date", validators=[DataRequired()])
    start_time = TimeField("Starts", validators=[Optional()])
    end_time = TimeField("Ends", validators=[Optional()])
    venue = StringField("Venue", filters=[strip], validators=[Optional(), Length(max=160)])
    trainer = SelectField("Trainer", choices=[], validators=[Optional()])

    def validate_end_time(self, field):
        if field.data and self.start_time.data and field.data <= self.start_time.data:
            raise ValidationError("The session must end after it starts.")


class EnrolmentStatusForm(FlaskForm):
    status = SelectField("Status", choices=_c(EnrolmentStatus.LABELS.items()))
