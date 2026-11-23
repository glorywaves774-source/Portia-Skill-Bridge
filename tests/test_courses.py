"""Course builder, publishing, enrolment and marking."""
import io

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import Course, CourseEnrolment, Learner, Lesson, Programme, Role
from app.models.lms import video_embed_url
from app.tenancy import platform_scope
from tests.conftest import sign_in
from tests.test_files import PDF
from tests.test_learners import register


@pytest.fixture()
def builder(client, org, make_person):
    make_person("tm@attc.example", [Role.TRAINING_MANAGER], org)
    sign_in(client, "tm@attc.example")
    r = client.post("/app/courses/new", data={"title": "Garment Construction", "level": "beginner",
                                              "summary": "Sewing basics", "estimated_hours": "12"})
    assert r.status_code == 302
    with platform_scope():
        return db.session.scalar(select(Course))


def _add_lesson(client, c, title, kind):
    with platform_scope():
        module_id = db.session.get(Course, c.id).modules[0].id
    r = client.post(f"/app/courses/{c.public_id}/modules/{module_id}/lessons", data={"l-title": title, "l-kind": kind})
    with platform_scope():
        return db.session.scalar(select(Lesson).where(Lesson.title == title))


def test_video_links():
    assert video_embed_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10") == \
        "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?rel=0"
    assert video_embed_url("https://youtu.be/dQw4w9WgXcQ").startswith("https://www.youtube-nocookie.com/embed/")
    assert video_embed_url("https://vimeo.com/123456789") == "https://player.vimeo.com/video/123456789?dnt=1"
    assert video_embed_url("https://evil.example/video.mp4") is None
    assert video_embed_url("javascript:alert(1)") is None


def test_build_and_publish(client, builder):
    c = builder
    r = client.post(f"/app/courses/{c.public_id}/status", data={"st-status": "published"}, follow_redirects=True)
    assert b"Add at least one lesson" in r.data
    text = _add_lesson(client, c, "Tools of the trade", "text")
    client.post(f"/app/courses/{c.public_id}/lessons/{text.public_id}",
                data={"title": "Tools of the trade", "body": "## Scissors\nUse **sharp** ones.\n\n- Tape\n- Pins"})
    video = _add_lesson(client, c, "Threading a machine", "video")
    r = client.post(f"/app/courses/{c.public_id}/lessons/{video.public_id}",
                    data={"title": "Threading a machine", "video_url": "https://evil.example/x.mp4"}, follow_redirects=True)
    assert b"YouTube or Vimeo" in r.data
    client.post(f"/app/courses/{c.public_id}/lessons/{video.public_id}",
                data={"title": "Threading a machine", "video_url": "https://youtu.be/dQw4w9WgXcQ"})
    quiz = _add_lesson(client, c, "Check your knowledge", "quiz")
    r = client.post(f"/app/courses/{c.public_id}/status", data={"st-status": "published"}, follow_redirects=True)
    assert b"has no questions" in r.data
    r = client.post(f"/app/courses/{c.public_id}/lessons/{quiz.public_id}/questions",
                    data={"prompt": "Which tool cuts fabric?", "opt1": "Scissors", "opt2": "Ruler"}, follow_redirects=True)
    assert b"Mark at least one answer as correct" in r.data
    client.post(f"/app/courses/{c.public_id}/lessons/{quiz.public_id}/questions",
                data={"prompt": "Which tool cuts fabric?", "opt1": "Scissors", "ok1": "y", "opt2": "Ruler"})
    r = client.post(f"/app/courses/{c.public_id}/status", data={"st-status": "published"}, follow_redirects=True)
    assert b"now published" in r.data
    page = client.get(f"/app/courses/{c.public_id}/lessons/{text.public_id}").get_data(as_text=True)
    assert "<h3>Scissors</h3>" in page and "<strong>sharp</strong>" in page
    vpage = client.get(f"/app/courses/{c.public_id}/lessons/{video.public_id}")
    assert b"youtube-nocookie.com/embed/dQw4w9WgXcQ" in vpage.data
    assert "frame-src https://www.youtube-nocookie.com" in vpage.headers["Content-Security-Policy"]


def test_lesson_text_cannot_inject_html(client, builder):
    lesson = _add_lesson(client, builder, "Unsafe", "text")
    client.post(f"/app/courses/{builder.public_id}/lessons/{lesson.public_id}",
                data={"title": "Unsafe", "body": "<script>alert(1)</script><img src=x onerror=alert(1)>"})
    html = client.get(f"/app/courses/{builder.public_id}/lessons/{lesson.public_id}").get_data(as_text=True)
    assert "<script>alert(1)" not in html and "<img src=x" not in html and "&lt;script&gt;" in html


def test_reorder_and_delete(client, builder):
    a = _add_lesson(client, builder, "First", "text")
    b = _add_lesson(client, builder, "Second", "text")
    client.post(f"/app/courses/{builder.public_id}/lessons/{b.public_id}/move/up")
    with platform_scope():
        assert [l.title for l in db.session.get(Course, builder.id).lessons] == ["Second", "First"]
    client.post(f"/app/courses/{builder.public_id}/lessons/{a.public_id}/delete")
    with platform_scope():
        assert [l.title for l in db.session.get(Course, builder.id).lessons] == ["Second"]


def test_lesson_files(client, builder):
    lesson = _add_lesson(client, builder, "Slides", "files")
    r = client.post(f"/app/courses/{builder.public_id}/lessons/{lesson.public_id}/files",
                    data={"f-title": "Week 1 slides", "f-file": (io.BytesIO(PDF), "slides.pdf")},
                    content_type="multipart/form-data", follow_redirects=True)
    assert b"File added" in r.data
    with platform_scope():
        lf = db.session.get(Lesson, lesson.id).files[0]
        assert lf.file.key.startswith("tenants/") and "/course-files/" in lf.file.key
    r = client.get(f"/app/courses/{builder.public_id}/lessons/{lesson.public_id}/files/{lf.public_id}")
    assert r.data == PDF


def test_programme_learners_get_published_course(client, builder, org):
    register(client)
    client.post("/app/programmes/new", data={"name": "Cohort", "delivery_mode": "blended"})
    with platform_scope():
        p = db.session.scalar(select(Programme))
        learner = db.session.scalar(select(Learner))
    client.post(f"/app/programmes/{p.public_id}/enrol", data={"learner": [learner.public_id]})
    client.post(f"/app/courses/{builder.public_id}/settings",
                data={"c-title": "Garment Construction", "c-level": "beginner", "c-programme": p.public_id})
    _add_lesson(client, builder, "Intro", "text")
    with platform_scope():
        assert CourseEnrolment.query.count() == 0  # not published yet
    client.post(f"/app/courses/{builder.public_id}/status", data={"st-status": "published"})
    with platform_scope():
        assert CourseEnrolment.query.one().learner_id == learner.id


def test_course_limit_and_permissions(app, client, builder, org, make_person):
    make_person("me@attc.example", [Role.ME_OFFICER], org)
    c = app.test_client()
    sign_in(c, "me@attc.example")
    assert c.get("/app/courses/").status_code == 403  # M&E officers don't use the course builder
    make_person("t@attc.example", [Role.TRAINER], org)
    t = app.test_client()
    sign_in(t, "t@attc.example")
    assert t.get(f"/app/courses/{builder.public_id}").status_code == 200  # trainers build courses
    assert t.post("/app/courses/new", data={"title": "Trainer course", "level": "beginner"}).status_code == 302


def test_courses_isolated(app, builder, make_tenant, make_person):
    other = make_tenant("Beta NGO", organization_type="ngo")
    make_person("b@beta.example", [Role.ORG_ADMIN], other)
    c = app.test_client()
    sign_in(c, "b@beta.example")
    assert c.get(f"/app/courses/{builder.public_id}").status_code == 404
    assert c.post(f"/app/courses/{builder.public_id}/status", data={"st-status": "archived"}).status_code == 404
