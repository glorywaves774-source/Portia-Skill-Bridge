"""PORTIA Assistant: answers from the guide, role-aware links, redaction and protections."""
import pytest

from app.help import load_topics, redact, search
from app.models import Role
from tests.conftest import sign_in


def test_guide_parses_and_every_link_exists(app):
    topics = load_topics()
    assert len(topics) >= 20
    for t in topics:
        assert t.body, t.title
        for endpoint in t.links:
            assert endpoint in app.view_functions, (t.title, endpoint)


@pytest.mark.parametrize("question, title", [
    ("how do i add a new trainer", "How do I invite someone to my workspace?"),
    ("I forgot my password!", "I forgot my password"),
    ("when does the trial end", "When does my free trial end?"),
    ("how can I pay with momo", "How do I pay or renew my subscription?"),
    ("passer en mode sombre dark mode", "How do I use dark mode?"),
    ("remove someone who left", "How do I deactivate or remove someone?"),
])
def test_search_finds_the_right_topic(app, question, title):
    assert search(question)[0][1].title == title


@pytest.mark.parametrize("raw, expected", [
    ("call me on +233 24 123 4567 please", "call me on [phone] please"),
    ("my email is ama.owusu@gmail.com", "my email is [email]"),
    ("ghana card GHA-123456789-0", "ghana card [ID number]"),
    ("passport G1234567", "passport [ID number]"),
    ("0244123456", "[phone]"),
    ("how do I invite 3 trainers", "how do I invite 3 trainers"),
])
def test_redaction(raw, expected):
    assert redact(raw) == expected


def _ask(client, q):
    return client.post("/help/ask", json={"question": q})


def test_admin_gets_answer_with_links(client, org, make_person):
    make_person("esi@attc.example", [Role.ORG_ADMIN], org, owner=True)
    sign_in(client, "esi@attc.example")
    data = _ask(client, "how do I invite someone").get_json()
    assert data["found"] and data["note"] is None
    assert {"label": "People", "url": "/app/people"} in data["links"]


def test_learner_is_told_whose_job_it_is_and_gets_no_link(client, org, make_person):
    make_person("abena@attc.example", [Role.LEARNER], org)
    sign_in(client, "abena@attc.example")
    data = _ask(client, "how do I invite someone").get_json()
    assert data["links"] == []
    assert "Organization Administrator" in data["note"]


def test_platform_topics_hidden_from_workspace_people(client, org, make_person):
    make_person("esi@attc.example", [Role.ORG_ADMIN], org, owner=True)
    sign_in(client, "esi@attc.example")
    data = _ask(client, "how do I record a payment received outside the app").get_json()
    assert data["links"] == [] and "Super Administrator" in data["note"]
    assert b"How do I create a workspace for a customer?" not in client.get("/help/").data


def test_unknown_question_offers_topics(client, org, make_person, caplog):
    make_person("esi@attc.example", [Role.ORG_ADMIN], org)
    sign_in(client, "esi@attc.example")
    with caplog.at_level("INFO"):
        data = _ask(client, "zebra pancakes 0244123456 kofi@x.com").get_json()
    assert not data["found"] and data["related"]
    assert "0244123456" not in caplog.text and "kofi@x.com" not in caplog.text and "[phone]" in caplog.text


def test_help_requires_sign_in_csrf_and_rate_limit(app, client, org, make_person):
    assert _ask(client, "password").status_code in (302, 401)
    make_person("esi@attc.example", [Role.ORG_ADMIN], org)
    sign_in(client, "esi@attc.example")
    assert _ask(client, "").status_code == 400
    codes = [_ask(client, "password").status_code for _ in range(31)]
    assert codes[-1] == 429
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.post("/help/ask", json={"question": "password"}).status_code == 400


def test_panel_and_suggestions_render_for_role(client, org, make_person):
    make_person("esi@attc.example", [Role.ORG_ADMIN], org, owner=True)
    sign_in(client, "esi@attc.example")
    html = client.get("/app/").get_data(as_text=True)
    assert 'data-help-panel' in html and "How do I invite someone to my workspace?" in html
    assert "helpbot.js" in html and 'name="csrf-token"' in html
