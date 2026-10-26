"""
test_health.py
Checks the "are you alive?" addresses that Render and the uptime monitor call.

Monitoring robots often ask with HEAD (GET without the body). On 25 Oct the page and /health
answered HEAD with 405 "Method Not Allowed", and UptimeRobot reported the demo as DOWN while it
worked fine. No AI here. Run all tests from the project folder with:  python -m pytest
"""

import re

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


def test_big_files_are_compressed():
    """three.js (~740 KB) goes over the wire compressed, which matters on a phone (26 Oct redesign)."""
    response = client.get("/static/vendor/three/three.core.min.js", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"


def test_the_page_loads_its_new_files():
    """Every file the redesigned page asks for exists: a typo would quietly break the look or the 3D picture."""
    page = client.get("/").text
    for path in ["/static/style.css", "/static/app.js", "/static/js/hero3d.js", "/static/js/motion.js",
                 "/static/vendor/three/three.module.min.js", "/static/vendor/three/three.core.min.js",
                 "/static/vendor/three/addons/renderers/CSS2DRenderer.js", "/static/vendor/gsap/gsap.min.js",
                 "/static/vendor/gsap/ScrollTrigger.min.js", "/static/vendor/lenis/lenis.min.js",
                 "/static/vendor/lenis/lenis.css", "/static/brand/mark.svg",
                 "/static/fonts/inter/inter-latin-wght-normal.woff2"]:
        assert client.get(path).status_code == 200, path
    # Outages happen at any hour (Pavan's feedback, 26 Oct): not on the page. Comments (notes for
    # developers, never shown to visitors) may still explain the change.
    shown = re.sub(r"<!--.*?-->", "", page, flags=re.DOTALL)
    assert "3 a.m." not in shown
