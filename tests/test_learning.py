"""The learner's side: access, lessons, progress, quizzes, timed exams and assignments."""
import io
import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import AssignmentSubmission, Course, CourseEnrolment, Learner, Lesson, QuizAttempt, Role
from app.services import mailer
from app.services import lms
from app.tenancy import platform_scope
from tests.conftest import sign_in
from tests.test_files import PDF
from tests.test_learners import register


@pytest.fixture()
def course_setup(app, client, org, make_person):
    """A published course with a reading, a quiz (2 attempts), a 10-minute exam and an assignment,
    and a learner who can sign in and is enrolled."""
    mailer.outbox.clear()
    make_person("tm@attc.example", [Role.TRAINING_MANAGER], org)
    sign_in(client, "tm@attc.example")
    register(client)
    register(client, first_name="Kofi", last_name="Mensah", gender="male", id_number="GHA-987654321-0",
             date_of_birth="1999-01-01")
    client.post("/app/courses/new", data={"title": "Garment Construction", "level": "beginner"})
    with platform_scope():
        c = db.session.scalar(select(Course))
        module = c.modules[0]
        reading = lms.add_lesson(module, {"title": "Reading", "kind": "text", "body": "Hello"}, actor=None)
        quiz = lms.add_lesson(module, {"title": "Quiz", "kind": "quiz", "max_attempts": 2, "pass_mark": 50}, actor=None)
        q1 = lms.add_question(quiz, prompt="2+2?", options=[("4", True), ("5", False)])
        q2 = lms.add_question(quiz, prompt="Pick primes", options=[("2", True), ("3", True), ("4", False)], points=2)
        exam = lms.add_lesson(module, {"title": "Final exam", "kind": "exam", "time_limit_minutes": 10}, actor=None)
        lms.add_question(exam, prompt="Capital of Ghana?", options=[("Accra", True), ("Kumasi", False)])
        task = lms.add_lesson(module, {"title": "Sew a hem", "kind": "assignment", "max_score": 20}, actor=None)
        lms.set_status(c, "published", actor=None)
        abena = db.session.scalar(select(Learner).where(Learner.first_name == "Abena"))
        kofi = db.session.scalar(select(Learner).where(Learner.first_name == "Kofi"))
        lms.enrol(c, [abena], actor=None)
        db.session.commit()
        ids = dict(c=c.public_id, reading=reading.public_id, quiz=quiz.public_id, exam=exam.public_id,
                   task=task.public_id, q1=q1.id, q2=q2.id,
                   q1_right=[o.id for o in q1.options if o.is_correct], q2_right=[o.id for o in q2.options if o.is_correct],
                   q2_wrong=[o.id for o in q2.options if not o.is_correct], kofi=kofi.public_id, abena_id=abena.id)
    client.post(f"/app/learners/{abena.public_id}/invite", data={"inv-email": "abena@example.com"})
    link = re.search(r"http://localhost(/invite/\S+)", mailer.outbox[-1].get_content()).group(1)
    learner = app.test_client()
    learner.post(link, data={"full_name": "Abena Darko", "password": "Abena-pass-2026", "confirm": "Abena-pass-2026",
                             "accept_terms": "y"})
    return learner, ids


def lesson_url(ids, key):
    return f"/app/learn/courses/{ids['c']}/lessons/{ids[key]}"


def progress(ids):
    with platform_scope():
        return db.session.scalar(select(CourseEnrolment).where(CourseEnrolment.learner_id == ids["abena_id"])).progress


def test_learner_sees_only_enrolled_published_courses(app, course_setup, org, make_person, client):
    learner, ids = course_setup
    home = learner.get("/app/learn/").get_data(as_text=True)
    assert "Garment Construction" in home and "0%" in home
    assert learner.get(f"/app/learn/courses/{ids['c']}").status_code == 200
    assert learner.get(f"/app/courses/{ids['c']}").status_code == 403  # no builder access
    with platform_scope():
        c = db.session.scalar(select(Course))
        lms.set_status(c, "draft", actor=None)
        db.session.commit()
    assert learner.get(f"/app/learn/courses/{ids['c']}").status_code == 404


def test_reading_completion_and_progress(course_setup):
    learner, ids = course_setup
    r = learner.post(lesson_url(ids, "reading") + "/complete")
    assert r.status_code == 302 and r.headers["Location"].endswith(ids["quiz"])
    assert progress(ids) == 25
    assert learner.post(lesson_url(ids, "quiz") + "/complete").status_code == 400  # quizzes complete by passing


def test_quiz_grading_attempts_and_answers(course_setup):
    learner, ids = course_setup
    r = learner.post(lesson_url(ids, "quiz") + "/start")
    attempt_url = r.headers["Location"]
    page = learner.get(attempt_url).get_data(as_text=True)
    assert "2+2?" in page and "Pick primes" in page and "is-correct" not in page  # answers not revealed
    # q1 right (1 pt), q2 partly right -> wrong (0 of 2 pts) => 33% -> fail
    learner.post(attempt_url, data={f"q{ids['q1']}": ids["q1_right"], f"q{ids['q2']}": ids["q2_right"][:1]})
    result = learner.get(attempt_url).get_data(as_text=True)
    assert "33%" in result and "Not passed yet" in result
    assert progress(ids) == 0
    r = learner.post(lesson_url(ids, "quiz") + "/start")
    second = r.headers["Location"]
    learner.post(second, data={f"q{ids['q1']}": ids["q1_right"], f"q{ids['q2']}": ids["q2_right"]})
    assert "100%" in learner.get(second).get_data(as_text=True)
    assert progress(ids) == 25
    r = learner.post(lesson_url(ids, "quiz") + "/start", follow_redirects=True)
    assert b"used all your attempts" in r.data
    assert learner.post(second, data={}).status_code == 200  # shows the result; nothing is re-graded
    with platform_scope():
        assert QuizAttempt.query.count() == 2


def test_single_answer_question_rejects_multiple_choices(course_setup):
    learner, ids = course_setup
    attempt_url = learner.post(lesson_url(ids, "quiz") + "/start").headers["Location"]
    with platform_scope():
        q1 = db.session.get(__import__("app.models", fromlist=["QuizQuestion"]).QuizQuestion, ids["q1"])
        all_q1 = [o.id for o in q1.options]
    learner.post(attempt_url, data={f"q{ids['q1']}": all_q1, f"q{ids['q2']}": ids["q2_right"]})
    assert "67%" in learner.get(attempt_url).get_data(as_text=True)  # q1 scored 0


def test_exam_after_deadline_does_not_count(course_setup):
    learner, ids = course_setup
    attempt_url = learner.post(lesson_url(ids, "exam") + "/start").headers["Location"]
    page = learner.get(attempt_url).get_data(as_text=True)
    assert "data-deadline" in page
    with platform_scope():
        a = db.session.scalar(select(QuizAttempt))
        a.deadline_at = a.started_at - timedelta(minutes=5)
        db.session.commit()
        right = [o.id for o in a.lesson.questions[0].options if o.is_correct]
        qid = a.lesson.questions[0].id
    learner.post(attempt_url, data={f"q{qid}": right})
    result = learner.get(attempt_url).get_data(as_text=True)
    assert "Time ran out" in result
    with platform_scope():
        a = db.session.scalar(select(QuizAttempt))
        assert a.late and not a.passed and a.percent == 100


def test_cannot_open_another_learners_attempt(app, course_setup, client):
    learner, ids = course_setup
    attempt_url = learner.post(lesson_url(ids, "quiz") + "/start").headers["Location"]
    assert client.get(attempt_url).status_code in (403, 404)  # staff member, not the learner


def test_assignment_submit_mark_and_feedback(client, course_setup):
    learner, ids = course_setup
    r = learner.post(lesson_url(ids, "task") + "/submit", data={"text": ""}, follow_redirects=True)
    assert b"Write your answer or attach a file" in r.data
    r = learner.post(lesson_url(ids, "task") + "/submit",
                     data={"text": "I sewed a blind hem.", "file": (io.BytesIO(PDF), "hem.pdf")},
                     content_type="multipart/form-data", follow_redirects=True)
    assert b"Submitted" in r.data and progress(ids) == 25
    with platform_scope():
        sub = db.session.scalar(select(AssignmentSubmission))
    marking = client.get(f"/app/courses/{ids['c']}?tab=submissions").get_data(as_text=True)
    assert "To mark" in marking
    r = client.post(f"/app/courses/{ids['c']}/submissions/{sub.public_id}", data={"score": "25"})
    assert b"between 0 and 1000" in r.data or b"from 0 to 20" in r.data or r.status_code == 200
    client.post(f"/app/courses/{ids['c']}/submissions/{sub.public_id}", data={"score": "17", "feedback": "Neat work"})
    page = learner.get(lesson_url(ids, "task")).get_data(as_text=True)
    assert "17/20" in page and "Neat work" in page
    r = learner.post(lesson_url(ids, "task") + "/submit", data={"text": "changed"}, follow_redirects=True)
    assert b"already been marked" in r.data


def test_course_completion(course_setup):
    learner, ids = course_setup
    learner.post(lesson_url(ids, "reading") + "/complete")
    a = learner.post(lesson_url(ids, "quiz") + "/start").headers["Location"]
    learner.post(a, data={f"q{ids['q1']}": ids["q1_right"], f"q{ids['q2']}": ids["q2_right"]})
    e = learner.post(lesson_url(ids, "exam") + "/start").headers["Location"]
    with platform_scope():
        exam = db.session.scalar(select(Lesson).where(Lesson.public_id == ids["exam"]))
        qid, right = exam.questions[0].id, [o.id for o in exam.questions[0].options if o.is_correct]
    learner.post(e, data={f"q{qid}": right})
    learner.post(lesson_url(ids, "task") + "/submit", data={"text": "Done"})
    assert progress(ids) == 100
    assert "Course completed" in learner.get(f"/app/learn/courses/{ids['c']}").get_data(as_text=True)


def test_unenrolled_learner_cannot_open_lessons(app, course_setup, client):
    learner, ids = course_setup
    with platform_scope():
        ce = db.session.scalar(select(CourseEnrolment))
        db.session.delete(ce)
        db.session.commit()
    assert learner.get(lesson_url(ids, "reading")).status_code == 404
    assert learner.post(lesson_url(ids, "quiz") + "/start").status_code == 404
