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
    health = client.get("/api/health").json()
    assert {k: health[k] for k in ("ok", "comfy", "device", "queue")} == {
        "ok": True, "comfy": False, "device": None, "queue": "sqlite"}
    assert [i["name"] for i in health["instances"]] == ["comfy-1"]


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


def _add_job(client, status, images=()):
    from app.db import Job

    db = client.app.state.pool.db
    return db.add(Job(template_id="flux_schnell_t2i", params={"prompt": "x"}, status=status, images=list(images)))


def test_delete_job_removes_record_and_image(client, tmp_path):
    from app.db import JobStatus

    (tmp_path / "outputs" / "a_0.png").write_bytes(b"PNG")
    job = _add_job(client, JobStatus.succeeded, ["a_0.png"])
    r = client.delete(f"/api/jobs/{job.id}")
    assert r.status_code == 200 and r.json() == {"deleted": [job.id]}
    assert client.get(f"/api/jobs/{job.id}").status_code == 404
    assert not (tmp_path / "outputs" / "a_0.png").exists()


def test_running_job_cannot_be_deleted(client):
    from app.db import JobStatus

    job = _add_job(client, JobStatus.running)
    assert client.delete(f"/api/jobs/{job.id}").status_code == 409
    assert client.get(f"/api/jobs/{job.id}").status_code == 200


def test_clear_only_removes_requested_statuses(client):
    from app.db import JobStatus

    failed = _add_job(client, JobStatus.failed)
    cancelled = _add_job(client, JobStatus.cancelled)
    kept = _add_job(client, JobStatus.succeeded)
    running = _add_job(client, JobStatus.running)
    r = client.post("/api/jobs/clear", json={"statuses": ["failed", "cancelled", "running"]})
    assert sorted(r.json()["deleted"]) == sorted([failed.id, cancelled.id])
    remaining = {j["id"] for j in client.get("/api/jobs").json()}
    assert remaining == {kept.id, running.id}


def test_finished_image_can_become_an_input(client, tmp_path):
    from app.db import JobStatus

    (tmp_path / "outputs" / "b_0.png").write_bytes(b"PNG")
    job = _add_job(client, JobStatus.succeeded, ["b_0.png"])
    uploaded = {}

    async def fake_upload(filename, data, content_type):
        uploaded["data"] = data
        return filename

    client.app.state.comfy.upload_image = fake_upload
    r = client.post(f"/api/jobs/{job.id}/as-input")
    assert r.status_code == 201 and uploaded["data"] == b"PNG"
    assert client.post(f"/api/jobs/{_add_job(client, JobStatus.failed).id}/as-input").status_code == 404


def test_prioritize_moves_job_to_front(client):
    from app.db import JobStatus

    first, second, third = (_add_job(client, JobStatus.queued) for _ in range(3))
    r = client.post(f"/api/jobs/{third.id}/prioritize")
    assert r.status_code == 200 and r.json()["queue_position"] == 0
    positions = {j["id"]: j["queue_position"] for j in client.get("/api/jobs").json()}
    assert [positions[first.id], positions[second.id], positions[third.id]] == [1, 2, 0]
    assert client.app.state.pool.db.next_queued().id == third.id


def test_only_queued_jobs_can_be_prioritized(client):
    from app.db import JobStatus

    done = _add_job(client, JobStatus.succeeded)
    assert client.post(f"/api/jobs/{done.id}/prioritize").status_code == 409
