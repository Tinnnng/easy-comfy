"""在沒有 ComfyUI 的情況下測試 API 行為（worker 不啟動）。"""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    settings = Settings(data_dir=tmp_path, comfy_url="http://127.0.0.1:9", start_worker=False)
    with TestClient(create_app(settings)) as c:
        yield c


def test_health_reports_comfy_offline(client):
    assert client.get("/api/health").json() == {"ok": True, "comfy": False, "device": None}


def test_templates_hide_workflow(client):
    templates = client.get("/api/templates").json()
    flux = next(t for t in templates if t["id"] == "flux_schnell_t2i")
    assert "workflow" not in flux
    assert all("set" not in o for f in flux["fields"] for o in f["options"])


def test_create_job_validates_params_before_contacting_comfy(client):
    r = client.post("/api/jobs", json={"template_id": "flux_schnell_t2i", "params": {}})
    assert r.status_code == 422
    assert r.json()["detail"]["field"] == "prompt"


def test_create_job_without_comfy_returns_503(client):
    r = client.post("/api/jobs", json={"template_id": "flux_schnell_t2i", "params": {"prompt": "a cat"}})
    assert r.status_code == 503
    assert client.get("/api/jobs").json() == []


def test_unknown_template_and_job(client):
    assert client.post("/api/jobs", json={"template_id": "nope"}).status_code == 404
    assert client.get("/api/jobs/nope").status_code == 404
