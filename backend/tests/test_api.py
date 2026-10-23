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
    assert client.get("/api/health").json() == {
        "ok": True, "comfy": False, "device": None, "vram_total": None, "vram_free": None,
    }


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


def test_count_is_limited(client):
    r = client.post("/api/jobs", json={"template_id": "flux_schnell_t2i", "params": {"prompt": "a cat"}, "count": 9})
    assert r.status_code == 422


def test_batch_creates_jobs_with_consecutive_seeds(client):
    async def no_problems(workflow):
        return []

    client.app.state.comfy.validate = no_problems
    r = client.post("/api/jobs", json={"template_id": "flux_schnell_t2i",
                                       "params": {"prompt": "a cat", "seed": 100}, "count": 3})
    assert r.status_code == 201
    jobs = r.json()
    assert [j["params"]["seed"] for j in jobs] == [100, 101, 102]
    assert [j["queue_position"] for j in jobs] == [0, 1, 2]


def test_upload_rejects_non_images(client):
    r = client.post("/api/uploads", files={"file": ("a.txt", b"hello", "text/plain")})
    assert r.status_code == 415


def test_upload_forwards_to_comfy(client):
    async def fake_upload(filename, data, content_type):
        assert filename.startswith("easycomfy_") and filename.endswith(".png") and data == b"PNG"
        return filename

    client.app.state.comfy.upload_image = fake_upload
    r = client.post("/api/uploads", files={"file": ("cat.png", b"PNG", "image/png")})
    assert r.status_code == 201
    assert r.json()["url"] == f"/api/inputs/{r.json()['name']}"


def test_input_image_rejects_paths(client):
    assert client.get("/api/inputs/..%2F..%2Fsecret.png").status_code == 404


def test_image_param_must_be_an_uploaded_name(client):
    r = client.post("/api/jobs", json={"template_id": "flux_schnell_i2i",
                                       "params": {"image": "../x.png", "prompt": "a cat"}})
    assert r.status_code == 422
    assert r.json()["detail"]["field"] == "image"
