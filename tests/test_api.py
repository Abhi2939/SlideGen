import json
from pathlib import Path
import threading
import time

from fastapi.testclient import TestClient
import pytest

from app.core.deck_schema import Deck
from app.jobs.runner import JobRunner
from app.jobs.store import JobStore
from app.main import create_app


FIXTURE = Path(__file__).parent / "fixtures" / "decks" / "all_layouts.json"


def fixture_deck() -> Deck:
    return Deck.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))


def make_app(tmp_path, *, generate=None, **kwargs):
    store = JobStore(tmp_path)
    runner = JobRunner(store, generate=generate or (lambda prompt: fixture_deck()))
    return create_app(runner=runner, **kwargs), runner, store


def wait_for_status(client: TestClient, job_id: str, status: str):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        if response.status_code == 200 and response.json()["status"] == status:
            return response
        time.sleep(0.01)
    pytest.fail(f"job {job_id} did not reach status {status!r}")


def test_api_full_submit_status_and_download_flow(tmp_path):
    app, _, _ = make_app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"ok": True}
        created = client.post("/api/jobs", json={"prompt": "Create a Blush investor presentation"})
        assert created.status_code == 202
        job_id = created.json()["id"]
        assert created.json()["status"] == "queued"

        completed = wait_for_status(client, job_id, "done")
        assert completed.json() == {
            "id": job_id,
            "status": "done",
            "error": None,
            "stage": None,
            "queue_position": None,
        }

        download = client.get(f"/api/jobs/{job_id}/download")
        assert download.status_code == 200
        assert download.headers["content-type"] == (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )
        assert f'filename="{job_id}.pptx"' in download.headers["content-disposition"]
        assert download.content.startswith(b"PK")


def test_root_serves_presentation_studio_page(tmp_path):
    app, _, _ = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "Good ideas deserve a great deck." in response.text
    assert 'id="access-code"' in response.text
    assert "slidegenAccessCode" in response.text
    assert "X-Access-Code" in response.text


def test_access_code_protects_job_endpoints_and_leaves_health_public(tmp_path):
    app, _, _ = make_app(tmp_path, access_code="client-secret")
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.post(
            "/api/jobs", json={"prompt": "Create a protected deck"}
        ).status_code == 401
        assert client.get("/api/jobs/unknown").status_code == 401
        assert client.get("/api/jobs/unknown/download").status_code == 401

        bad = client.post(
            "/api/jobs",
            json={"prompt": "Create a protected deck"},
            headers={"X-Access-Code": "wrong"},
        )
        assert bad.status_code == 401
        assert "valid access code" in bad.json()["detail"]

        headers = {"X-Access-Code": "client-secret"}
        created = client.post(
            "/api/jobs",
            json={"prompt": "Create a protected deck"},
            headers=headers,
        )
        assert created.status_code == 202
        job_id = created.json()["id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status_response = client.get(f"/api/jobs/{job_id}", headers=headers)
            if status_response.json()["status"] == "done":
                break
            time.sleep(0.01)
        assert status_response.json()["status"] == "done"
        assert client.get(f"/api/jobs/{job_id}/download", headers=headers).status_code == 200


def test_required_access_code_must_be_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIDEGEN_REQUIRE_ACCESS_CODE", "true")
    monkeypatch.delenv("SLIDEGEN_ACCESS_CODE", raising=False)
    with pytest.raises(ValueError, match="SLIDEGEN_ACCESS_CODE is required"):
        create_app(data_dir=tmp_path)


@pytest.mark.parametrize(
    "prompt",
    ["tiny", "    ", "x" * 4001],
)
def test_api_rejects_bad_prompt_lengths(tmp_path, prompt):
    app, _, store = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/jobs", json={"prompt": prompt})
    assert response.status_code == 422
    assert store.count_active() == 0


def test_api_returns_404_for_unknown_job(tmp_path):
    app, _, _ = make_app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/jobs/unknown").status_code == 404
        assert client.get("/api/jobs/unknown/download").status_code == 404


def test_completed_download_survives_app_restart(tmp_path):
    original_store = JobStore(tmp_path)
    runner = JobRunner(original_store, generate=lambda prompt: fixture_deck())
    submitted = runner.submit("Create a persistent presentation")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        completed = original_store.get(submitted.id)
        if completed and completed.status == "done":
            break
        time.sleep(0.01)
    assert completed is not None and completed.status == "done"
    runner.close()

    restarted_store = JobStore(tmp_path)
    restarted_app = create_app(store=restarted_store)
    with TestClient(restarted_app) as client:
        status_response = client.get(f"/api/jobs/{submitted.id}")
        download = client.get(f"/api/jobs/{submitted.id}/download")
    assert status_response.json()["status"] == "done"
    assert download.status_code == 200
    assert download.content.startswith(b"PK")


def test_api_rejects_download_before_job_is_ready(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def slow_generate(prompt: str) -> Deck:
        started.set()
        assert release.wait(timeout=5)
        return fixture_deck()

    app, _, _ = make_app(tmp_path, generate=slow_generate)
    try:
        with TestClient(app) as client:
            created = client.post("/api/jobs", json={"prompt": "Create a slow deck"})
            job_id = created.json()["id"]
            assert started.wait(timeout=5)
            response = client.get(f"/api/jobs/{job_id}/download")
            assert response.status_code == 409
            assert response.json()["detail"] == "The presentation is not ready for download."
            release.set()
            wait_for_status(client, job_id, "done")
    finally:
        release.set()


def test_api_enforces_per_ip_submission_rate_limit(tmp_path):
    app, _, store = make_app(tmp_path, submissions_per_window=1)
    with TestClient(app) as client:
        first = client.post("/api/jobs", json={"prompt": "Create the first deck"})
        second = client.post("/api/jobs", json={"prompt": "Create the second deck"})
    assert first.status_code == 202
    assert second.status_code == 429
    assert "Too many submissions" in second.json()["detail"]
    assert store.count_active() <= 1


def test_api_rejects_submissions_when_active_job_limit_is_reached(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def slow_generate(prompt: str) -> Deck:
        started.set()
        assert release.wait(timeout=5)
        return fixture_deck()

    app, _, store = make_app(tmp_path, generate=slow_generate, max_active_jobs=1)
    try:
        with TestClient(app) as client:
            first = client.post("/api/jobs", json={"prompt": "Create the first deck"})
            assert first.status_code == 202
            assert started.wait(timeout=5)
            second = client.post("/api/jobs", json={"prompt": "Create the second deck"})
            assert second.status_code == 429
            assert "queue is full" in second.json()["detail"]
            assert store.count_active() == 1
            release.set()
    finally:
        release.set()


def test_api_reports_queue_position_and_generation_stage(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def slow_generate(prompt: str) -> Deck:
        started.set()
        assert release.wait(timeout=5)
        return fixture_deck()

    app, _, _ = make_app(tmp_path, generate=slow_generate)
    try:
        with TestClient(app) as client:
            first = client.post("/api/jobs", json={"prompt": "Create the first deck"})
            assert first.status_code == 202
            assert started.wait(timeout=5)
            second = client.post("/api/jobs", json={"prompt": "Create the second deck"})
            second_id = second.json()["id"]
            queued = client.get(f"/api/jobs/{second_id}").json()
            assert queued["status"] == "queued"
            assert queued["stage"] == "Waiting in queue"
            assert queued["queue_position"] == 1
            release.set()
            active = wait_for_status(client, second_id, "running")
            assert active.json()["stage"] in {
                "Generating presentation",
                "Rendering PowerPoint",
            }
            assert active.json()["queue_position"] is None
    finally:
        release.set()
