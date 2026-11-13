from __future__ import annotations

import asyncio
import httpx
import hashlib
import json
import time

from agent.credentials import CredentialManager
from core.event_store import EventStore
from core.experiments import ExperimentManager, ExperimentStore
from core.trace_schema import EventType, RunTrace
from ui.model_registry import ModelRegistry
from ui.web import create_app


class MemoryKeyring:
    def __init__(self):
        self.values = {}

    def set_password(self, service, account, secret):
        self.values[(service, account)] = secret

    def get_password(self, service, account):
        return self.values.get((service, account))

    def delete_password(self, service, account):
        del self.values[(service, account)]


def client_for(tmp_path):
    registry = ModelRegistry(tmp_path / "registry.json")
    credentials = CredentialManager(backend=MemoryKeyring(), profile_path=tmp_path / "profiles.json")
    store = EventStore(str(tmp_path / "traces"))
    return LocalClient(create_app(registry=registry, credentials=credentials, event_store=store)), registry, credentials, store


class LocalClient:
    def __init__(self, app):
        self.app = app

    def request(self, method, path, **kwargs):
        async def send():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app), base_url="http://test"
            ) as client:
                return await client.request(method, path, **kwargs)
        return asyncio.run(send())

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)

    def delete(self, path, **kwargs):
        return self.request("DELETE", path, **kwargs)


def test_provider_and_multiple_model_registry_persistence(tmp_path):
    client, registry, _, _ = client_for(tmp_path)
    response = client.post("/api/providers", json={"provider_id": "ollama", "display_name": "Ollama"})
    assert response.status_code == 201
    assert response.json()["status"] == "configured"
    for identifier in ("model-a", "model-b"):
        added = client.post("/api/models", json={
            "provider_id": "ollama", "model_identifier": identifier, "display_name": identifier,
        })
        assert added.status_code == 201
    assert len(client.get("/api/models?provider=ollama").json()["models"]) == 2
    model_id = client.get("/api/models").json()["models"][0]["model_id"]
    assert client.post(f"/api/models/{model_id}/default").json()["default_model"] == model_id
    assert ModelRegistry(registry.path).default_model() == model_id


def test_multiple_provider_accounts_share_provider_type(tmp_path):
    client, _, _, _ = client_for(tmp_path)
    for instance, account in (("vendor-lab", "lab"), ("vendor-personal", "personal")):
        client.post("/api/credentials", json={
            "provider": "openai-compatible", "account": account, "secret": f"fake-{account}-key",
        })
        result = client.post("/api/providers", json={
            "provider_id": instance,
            "provider_type": "openai-compatible",
            "display_name": instance,
            "credential_account": account,
            "endpoint_url": "https://api.example.invalid/v1",
        })
        assert result.status_code == 201
        assert result.json()["provider_type"] == "openai-compatible"
        assert result.json()["credential_account"] == account


def test_provider_and_model_input_validation(tmp_path):
    client, _, _, _ = client_for(tmp_path)
    invalid = client.post("/api/providers", json={
        "provider_id": "bad id", "display_name": "Bad", "endpoint_url": "https://u:p@example.test",
    })
    assert invalid.status_code == 400
    client.post("/api/providers", json={"provider_id": "custom", "display_name": "Custom"})
    invalid_model = client.post("/api/models", json={
        "provider_id": "custom", "model_identifier": "m", "display_name": "M", "context_window": -2,
    })
    assert invalid_model.status_code == 400


def test_multiple_credential_accounts_and_no_secret_in_api_or_registry(tmp_path):
    client, registry, credentials, _ = client_for(tmp_path)
    secret = "recognizable-test-key-5521"
    for account in ("personal", "lab"):
        response = client.post("/api/credentials", json={
            "provider": "openai", "account": account, "secret": secret, "display_label": account,
        })
        assert response.status_code == 201
        assert secret not in response.text
    listed = client.get("/api/credentials")
    assert len(listed.json()) == 2
    assert secret not in listed.text
    if registry.path.exists():
        assert secret not in registry.path.read_text(encoding="utf-8")
    assert secret not in credentials._profile_path.read_text(encoding="utf-8")
    status = client.post("/api/credentials/openai/personal/validate")
    assert status.json()["status"] == "validation_unsupported"
    assert secret not in status.text
    assert client.delete("/api/credentials/openai/personal").json() == {"deleted": True}


def test_ollama_discovery_updates_availability(monkeypatch, tmp_path):
    from ui import web

    class FakeClient:
        def list(self):
            return {"models": [{"model": "local-model:latest"}]}

    class OllamaModule:
        @staticmethod
        def list():
            return {"models": [{"name": "local-model:latest"}]}

        Client = lambda **kwargs: FakeClient()

    monkeypatch.setattr(web, "_require_ollama", lambda: OllamaModule)
    client, registry, _, _ = client_for(tmp_path)
    client.post("/api/providers", json={"provider_id": "ollama", "display_name": "Ollama"})
    client.post("/api/models", json={
        "provider_id": "ollama", "model_identifier": "local-model:latest", "display_name": "Local",
    }).json()
    result = client.post("/api/providers/ollama/test").json()
    assert result["status"] == "available"
    assert registry.list_models()[0]["availability"] == "available"


def test_remote_connectivity_is_explicitly_unsupported(tmp_path):
    client, _, _, _ = client_for(tmp_path)
    client.post("/api/providers", json={"provider_id": "remote", "display_name": "Remote"})
    result = client.post("/api/providers/remote/test").json()
    assert result["status"] == "unsupported_operation"


def test_ollama_connection_failure_is_sanitized(monkeypatch, tmp_path):
    from ui import web

    secret_like_detail = "private-token-must-not-escape"

    class BrokenOllama:
        @staticmethod
        def list():
            raise RuntimeError(secret_like_detail)

    monkeypatch.setattr(web, "_require_ollama", lambda: BrokenOllama)
    client, registry, _, _ = client_for(tmp_path)
    client.post("/api/providers", json={"provider_id": "ollama", "display_name": "Ollama"})
    response = client.post("/api/providers/ollama/test")
    assert response.json()["status"] == "unavailable"
    assert secret_like_detail not in response.text
    assert registry.get_provider("ollama")["last_error"] == response.json()["message"]


def test_dashboard_serves_ui_assets(tmp_path):
    client, _, _, _ = client_for(tmp_path)
    assert client.get("/").status_code == 200
    assert "Model Registry" in client.get("/").text
    assert client.get("/assets/dashboard.css").status_code == 200


def test_run_history_api_uses_existing_event_store_without_paths(tmp_path):
    client, _, _, store = client_for(tmp_path)
    trace = RunTrace(run_id="stable-run", task_id="i2c-stable", domain="i2c", status="completed")
    trace.add_event(EventType.RUN_STARTED, model="test-model", data={})
    trace.add_event(EventType.STAGE_FINISHED, model="test-model", data={"stage": "compile", "passed": True})
    store.save_trace(trace)
    listing = client.get("/api/runs")
    assert listing.status_code == 200
    assert "file_path" not in listing.text
    result = client.get("/api/runs/stable-run")
    assert result.status_code == 200
    assert result.json()["verification"]["total_verifications"] == 1


def test_experiment_api_persists_progress_and_serves_candidate_artifacts(tmp_path):
    registry = ModelRegistry(tmp_path / "registry.json")
    registry.register_provider({"provider_id": "ollama", "display_name": "Ollama"})
    models = [registry.register_model({
        "provider_id": "ollama", "model_identifier": model, "display_name": model,
    }) for model in ("model-a", "model-b")]

    def runner(*, config, run_id, progress_fn, on_success, on_failure):
        run_dir = __import__("pathlib").Path(config.output_root) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        code = f"module top; // {config.model}\nendmodule\n"
        (run_dir / "attempt_0.v").write_text(code, encoding="utf-8")
        (run_dir / "attempt_0.json").write_text(json.dumps({
            "attempt": 0, "outcome": "pass", "final_code": code,
            "code_sha256": hashlib.sha256(code.encode()).hexdigest(), "repair": False,
            "prompt": "same prompt", "retrieval": {"enabled": True, "text": "frozen"},
                "explanation": {"summary": "generated"}, "stages": [{"stage": "judge", "passed": True, "stderr": "/tmp/private-run/candidate.v:9: diagnostic"}],
            "model_metadata": {},
        }), encoding="utf-8")
        return {"run_id": run_id, "run_dir": str(run_dir), "final": "pass"}

    experiment_manager = ExperimentManager(
        registry=registry, store=ExperimentStore(tmp_path / "experiments"), runner=runner,
    )
    client = LocalClient(create_app(
        registry=registry, credentials=CredentialManager(backend=MemoryKeyring(), profile_path=tmp_path / "profiles.json"),
        event_store=EventStore(str(tmp_path / "traces")), experiment_manager=experiment_manager,
    ))
    created = client.post("/api/experiments", json={
        "name": "api test", "model_ids": [model["model_id"] for model in models],
    })
    assert created.status_code == 201
    experiment_id = created.json()["experiment_id"]
    client.post(f"/api/experiments/{experiment_id}/start")
    deadline = time.monotonic() + 3
    record = client.get(f"/api/experiments/{experiment_id}").json()
    while record["status"] not in {"completed", "failed", "partially_completed"} and time.monotonic() < deadline:
        time.sleep(0.02)
        record = client.get(f"/api/experiments/{experiment_id}").json()
    assert record["status"] == "completed"
    candidates = record["candidates"]
    assert candidates[0]["rank"] in {1, 2}
    details = client.get(f"/api/experiments/{experiment_id}/candidates/{candidates[0]['candidate_id']}")
    assert details.json()["versions"][0]["explanation"] == {"summary": "generated"}
    assert "/tmp/private-run" not in json.dumps(details.json()["versions"][0]["stages"])
    downloaded = client.get(f"/api/experiments/{experiment_id}/candidates/{candidates[0]['candidate_id']}/rtl")
    assert downloaded.status_code == 200 and "module top" in downloaded.text
    diff = client.get(f"/api/experiments/{experiment_id}/diff?left={candidates[0]['candidate_id']}&right={candidates[1]['candidate_id']}")
    assert diff.status_code == 200 and "model-a" in diff.json()["diff"]
    json_report = client.get(f"/api/experiments/{experiment_id}/report?format=json")
    assert json_report.status_code == 200
    assert json_report.json()["candidates"][0]["revisions"][0]["rtl"].startswith("module top")
    assert json_report.json()["candidates"][0]["revisions"][0]["explanation"] == {"summary": "generated"}
    assert client.get(f"/api/experiments/{experiment_id}/report?format=md").status_code == 200
    assert client.get("/api/experiments/../../etc/passwd").status_code in {400, 404}
