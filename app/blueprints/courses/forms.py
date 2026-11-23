from __future__ import annotations

from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired
from wtforms import BooleanField, DateField, IntegerField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from app.auth.forms import _c, strip
from app.models.lms import COURSE_LEVELS, CourseStatus, LessonKind


class CourseForm(FlaskForm):
    title = StringField("Course title", filters=[strip], validators=[DataRequired(), Length(max=160)])
    summary = TextAreaField("Summary", filters=[strip], validators=[Optional(), Length(max=2000)])
    category = StringField("Category", filters=[strip], validators=[Optional(), Length(max=80)])
    level = SelectField("Level", choices=_c(COURSE_LEVELS.items()), default="beginner")
    estimated_hours = IntegerField("Estimated hours", validators=[Optional(), NumberRange(min=1, max=2000)])
    programme = SelectField("Linked programme (optional)", choices=[], validators=[Optional()])


class CourseStatusForm(FlaskForm):
    status = SelectField("Status", choices=_c(CourseStatus.LABELS.items()))


class ModuleForm(FlaskForm):
    title = StringField("Module title", filters=[strip], validators=[DataRequired(), Length(max=160)])


class NewLessonForm(FlaskForm):
    title = StringField("Lesson title", filters=[strip], validators=[DataRequired(), Length(max=160)])
    kind = SelectField("Type", choices=_c(LessonKind.LABELS.items()), default=LessonKind.TEXT)


class LessonForm(FlaskForm):
    title = StringField("Title", filters=[strip], validators=[DataRequired(), Length(max=160)])
    body = TextAreaField("Content / instructions", filters=[strip], validators=[Optional(), Length(max=50000)])
    video_url = StringField("YouTube or Vimeo link", filters=[strip], validators=[Optional(), Length(max=500)])
    duration_minutes = IntegerField("Duration (minutes)", validators=[Optional(), NumberRange(min=1, max=600)])
    pass_mark = IntegerField("Pass mark (%)", default=50, validators=[Optional(), NumberRange(min=0, max=100)])
    max_attempts = IntegerField("Attempts allowed (0 = unlimited)", default=0, validators=[Optional(), NumberRange(min=0, max=20)])
    time_limit_minutes = IntegerField("Time limit (minutes)", validators=[Optional(), NumberRange(min=1, max=600)])
    show_answers = BooleanField("Show correct answers after submitting", default=True)
    due_date = DateField("Due date", validators=[Optional()])
    max_score = IntegerField("Marked out of", default=100, validators=[Optional(), NumberRange(min=1, max=1000)])


class LessonFileForm(FlaskForm):
    title = StringField("Title", filters=[strip], validators=[Optional(), Length(max=160)])
    file = FileField("File (PDF, Word, PowerPoint, Excel, JPG or PNG)", validators=[FileRequired("Choose a file.")])


class GradeForm(FlaskForm):
    score = IntegerField("Mark", validators=[DataRequired(), NumberRange(min=0, max=1000)])
    feedback = TextAreaField("Feedback for the learner", filters=[strip], validators=[Optional(), Length(max=5000)])
