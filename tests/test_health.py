"""
test_health.py
Checks the "are you alive?" addresses that Render and the uptime monitor call.

Monitoring robots often ask with HEAD (GET without the body). On 25 Oct the page and /health
answered HEAD with 405 "Method Not Allowed", and UptimeRobot reported the demo as DOWN while it
worked fine. No AI here. Run all tests from the project folder with:  python -m pytest
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_answers_get():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_answers_head():
    """HEAD gets the same status as GET, without a body."""
    response = client.head("/health")
    assert response.status_code == 200
    assert response.content == b""


def test_page_answers_get_and_head():
    assert client.get("/").status_code == 200
    assert client.head("/").status_code == 200
