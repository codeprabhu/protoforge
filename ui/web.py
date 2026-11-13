"""Starlette web UI and management API for model configuration and run history."""

from __future__ import annotations

import json
import difflib
import re
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from agent.credentials import CredentialManager, CredentialStorageUnavailable
from agent.ollama_client import _require_ollama
from core.event_store import EventStore
from core.experiments import ExperimentError, ExperimentManager, ExperimentStore
from core.trace_serializer import export_timeline
from ui.dashboard import TraceDashboard
from ui.model_registry import ModelRegistry, RegistryError

STATIC = Path(__file__).parent / "static"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def create_app(
    *,
    registry: ModelRegistry | None = None,
    credentials: CredentialManager | None = None,
    event_store: EventStore | None = None,
    experiment_store: ExperimentStore | None = None,
    experiment_manager: ExperimentManager | None = None,
) -> Starlette:
    app = Starlette(debug=False, routes=[])
    app.state.registry = registry or ModelRegistry()
    app.state.credentials = credentials or CredentialManager()
    app.state.event_store = event_store or EventStore()
    app.state.experiments = experiment_manager or ExperimentManager(registry=app.state.registry, store=experiment_store)

    async def index(request: Request):
        return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))

    async def get_providers(request: Request):
        return JSONResponse(request.app.state.registry.list_providers())

    async def add_provider(request: Request):
        try:
            payload = await _body(request)
            provider_type = payload.get("provider_type") or payload.get("provider_id", "")
            account = payload.get("credential_account")
            if account and not _credential_profile_exists(request.app.state.credentials, provider_type, account):
                raise RegistryError("Credential profile is not configured for this provider type.")
            return JSONResponse(request.app.state.registry.register_provider(payload), status_code=201)
        except (RegistryError, ValueError) as error:
            raise HTTPException(400, str(error)) from None

    async def edit_provider(request: Request):
        try:
            provider_id = request.path_params["provider_id"]
            payload = await _body(request)
            current = request.app.state.registry.get_provider(provider_id)
            if current is None:
                raise KeyError
            provider_type = payload.get("provider_type", current["provider_type"])
            account = payload.get("credential_account", current.get("credential_account"))
            if account and not _credential_profile_exists(request.app.state.credentials, provider_type, account):
                raise RegistryError("Credential profile is not configured for this provider type.")
            return JSONResponse(request.app.state.registry.update_provider(provider_id, payload))
        except KeyError:
            raise HTTPException(404, "Provider not found.") from None
        except RegistryError as error:
            raise HTTPException(400, str(error)) from None

    async def remove_provider(request: Request):
        try:
            removed = request.app.state.registry.remove_provider(request.path_params["provider_id"])
        except RegistryError as error:
            raise HTTPException(409, str(error)) from None
        if not removed:
            raise HTTPException(404, "Provider not found.")
        return JSONResponse({"deleted": True})

    async def get_models(request: Request):
        provider = request.query_params.get("provider")
        query = request.query_params.get("q", "").casefold()
        models = request.app.state.registry.list_models(provider)
        if query:
            models = [m for m in models if query in (m["display_name"] + " " + m["model_identifier"] + " " + m["provider_id"]).casefold()]
        return JSONResponse({"models": models, "default_model": request.app.state.registry.default_model()})

    async def add_model(request: Request):
        try:
            return JSONResponse(request.app.state.registry.register_model(await _body(request)), status_code=201)
        except (RegistryError, ValueError) as error:
            raise HTTPException(400, str(error)) from None

    async def edit_model(request: Request):
        try:
            return JSONResponse(request.app.state.registry.update_model(request.path_params["model_id"], await _body(request)))
        except KeyError:
            raise HTTPException(404, "Model not found.") from None
        except RegistryError as error:
            raise HTTPException(400, str(error)) from None

    async def remove_model(request: Request):
        if not request.app.state.registry.remove_model(request.path_params["model_id"]):
            raise HTTPException(404, "Model not found.")
        return JSONResponse({"deleted": True})

    async def set_default_model(request: Request):
        try:
            result = request.app.state.registry.set_default_model(request.path_params["model_id"])
            return JSONResponse({"model_id": result["model_id"], "default_model": result["model_id"]})
        except KeyError:
            raise HTTPException(404, "Model not found.") from None
        except RegistryError as error:
            raise HTTPException(400, str(error)) from None

    async def test_provider(request: Request):
        provider_id = request.path_params["provider_id"]
        provider = request.app.state.registry.get_provider(provider_id)
        if provider is None:
            raise HTTPException(404, "Provider not found.")
        if provider["provider_type"] != "ollama":
            result = {"status": "unsupported_operation", "message": "Connectivity testing is not implemented for this provider yet."}
            request.app.state.registry.record_provider_status(provider_id, "unsupported_operation", error=result["message"])
            return JSONResponse(result)
        try:
            client = _require_ollama()
            endpoint = provider.get("endpoint_url")
            response = client.Client(host=endpoint).list() if endpoint else client.list()
            models = _ollama_model_names(response)
            request.app.state.registry.record_provider_status(provider_id, "available", available_models=models)
            return JSONResponse({"status": "available", "available_models": models, "message": "Ollama is reachable."})
        except Exception as error:
            status, message = _connection_status(error)
            request.app.state.registry.record_provider_status(provider_id, status, error=message, available_models=[])
            return JSONResponse({"status": status, "message": message})

    async def get_credentials(request: Request):
        profiles = request.app.state.credentials.list_profiles(request.query_params.get("provider"))
        return JSONResponse([profile.__dict__ for profile in profiles])

    async def add_credential(request: Request):
        payload = await _body(request)
        try:
            profile = request.app.state.credentials.set_credential(
                payload.get("provider", ""), payload.get("account", ""), payload.get("secret", ""),
                display_label=payload.get("display_label"),
            )
        except CredentialStorageUnavailable as error:
            raise HTTPException(503, str(error)) from None
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        return JSONResponse(profile.__dict__, status_code=201)

    async def validate_credential(request: Request):
        result = request.app.state.credentials.validate_credential(
            request.path_params["provider"], request.path_params["account"]
        )
        status = 503 if result["status"] == "secure_storage_unavailable" else 200
        return JSONResponse(result, status_code=status)

    async def remove_credential(request: Request):
        provider_id = request.path_params["provider"]
        account = request.path_params["account"]
        try:
            removed = request.app.state.credentials.delete_credential(
                provider_id, account
            )
        except CredentialStorageUnavailable as error:
            raise HTTPException(503, str(error)) from None
        if not removed:
            raise HTTPException(404, "Credential profile not found.")
        for provider in request.app.state.registry.list_providers():
            if provider["provider_type"] == provider_id and provider.get("credential_account") == account:
                request.app.state.registry.update_provider(provider["provider_id"], {"credential_account": None})
        return JSONResponse({"deleted": True})

    async def overview(request: Request):
        registry_state = request.app.state.registry
        providers = registry_state.list_providers()
        models = registry_state.list_models()
        profiles = request.app.state.credentials.list_profiles()
        return JSONResponse({
            "provider_count": len(providers),
            "model_count": len(models),
            "available_model_count": sum(m["availability"] == "available" for m in models),
            "providers": providers,
            "recent_validation": [p.__dict__ for p in sorted(
                (p for p in profiles if p.last_validated_at is not None),
                key=lambda p: p.last_validated_at or 0, reverse=True,
            )[:5]],
        })

    async def list_runs(request: Request):
        runs = request.app.state.event_store.list_traces()
        safe_runs = [{key: item.get(key) for key in (
            "run_id", "task_id", "domain", "status", "start_time", "events_count", "decision_records_count"
        )} for item in runs]
        return JSONResponse(safe_runs)

    async def get_run(request: Request):
        run_id = request.path_params["run_id"]
        if not all(character.isalnum() or character in "-_" for character in run_id):
            raise HTTPException(400, "Invalid run identifier.")
        trace = TraceDashboard.load_run(run_id)
        if trace is None:
            trace = request.app.state.event_store.get_trace(run_id)
        if trace is None:
            raise HTTPException(404, "Run not found.")
        return JSONResponse({
            "run_id": trace.run_id,
            "task_id": trace.task_id,
            "domain": trace.domain,
            "status": trace.status,
            "timeline": export_timeline(trace),
            "verification": TraceDashboard.render_verification_summary(trace),
            "decisions": [item.model_dump() for item in trace.decision_records],
        })

    async def list_experiments(request: Request):
        return JSONResponse([_rank_experiment(item) for item in app.state.experiments.list()])

    async def create_experiment(request: Request):
        try:
            return JSONResponse(app.state.experiments.create(await _body(request)), status_code=201)
        except (ExperimentError, ValueError) as error:
            raise HTTPException(400, str(error)) from None

    async def start_experiment(request: Request):
        try:
            return JSONResponse(app.state.experiments.start(request.path_params["experiment_id"]))
        except KeyError:
            raise HTTPException(404, "Experiment not found.") from None
        except ExperimentError as error:
            raise HTTPException(409, str(error)) from None

    async def get_experiment(request: Request):
        record = app.state.experiments.get(request.path_params["experiment_id"])
        if record is None:
            raise HTTPException(404, "Experiment not found.")
        return JSONResponse(_rank_experiment(record))

    async def cancel_experiment(request: Request):
        try:
            return JSONResponse(app.state.experiments.cancel(
                request.path_params["experiment_id"], request.path_params.get("candidate_id")
            ))
        except KeyError:
            raise HTTPException(404, "Experiment or candidate not found.") from None

    async def candidate_detail(request: Request):
        record, candidate, run_dir = _candidate_artifacts(app.state.experiments, request.path_params["experiment_id"], request.path_params["candidate_id"])
        attempts = _read_attempts(run_dir)
        versions = [{
            "attempt": attempt.get("attempt"), "repair": attempt.get("repair", False),
            "model": attempt.get("model"), "outcome": attempt.get("outcome"),
            "code_sha256": attempt.get("code_sha256"), "prompt_sha256": attempt.get("prompt_sha256"),
            "retrieval_sha256": attempt.get("retrieval_sha256"), "rtl": attempt.get("final_code", ""),
            "prompt": attempt.get("prompt", ""), "retrieval": attempt.get("retrieval", {}),
            "explanation": attempt.get("explanation"), "failure_analysis": attempt.get("failure_analysis"),
            "stages": _sanitize_payload(attempt.get("stages", []), [PROJECT_ROOT, (app.state.experiments.store.root / record["experiment_id"]).resolve()]),
        } for attempt in attempts]
        return JSONResponse({"experiment_id": record["experiment_id"], "candidate": candidate, "versions": versions})

    async def candidate_download(request: Request):
        _, candidate, run_dir = _candidate_artifacts(app.state.experiments, request.path_params["experiment_id"], request.path_params["candidate_id"])
        version = request.query_params.get("attempt", "0")
        if not version.isdecimal() or int(version) > 100:
            raise HTTPException(400, "Invalid candidate revision.")
        path = run_dir / f"attempt_{int(version)}.v"
        if not path.is_file():
            raise HTTPException(404, "Candidate RTL revision not found.")
        return Response(path.read_bytes(), media_type="text/plain", headers={"Content-Disposition": f"attachment; filename=protoforge-{candidate['candidate_id']}-attempt-{version}.v"})

    async def candidate_diff(request: Request):
        experiment_id = request.path_params["experiment_id"]
        left_id = request.query_params.get("left", "")
        right_id = request.query_params.get("right", "")
        _, left_candidate, left_dir = _candidate_artifacts(app.state.experiments, experiment_id, left_id)
        _, right_candidate, right_dir = _candidate_artifacts(app.state.experiments, experiment_id, right_id)
        left_code = _read_revision(left_dir, request.query_params.get("left_attempt", "0"))
        right_code = _read_revision(right_dir, request.query_params.get("right_attempt", "0"))
        diff = "".join(difflib.unified_diff(left_code.splitlines(True), right_code.splitlines(True), fromfile=left_id, tofile=right_id))
        return JSONResponse({"left_candidate": left_candidate["candidate_id"], "right_candidate": right_candidate["candidate_id"], "diff": diff})

    async def experiment_report(request: Request):
        record = app.state.experiments.get(request.path_params["experiment_id"])
        if record is None:
            raise HTTPException(404, "Experiment not found.")
        record = _report_experiment(app.state.experiments, _rank_experiment(record))
        if request.query_params.get("format", "json") == "md":
            lines = [f"# {record['name']}", "", f"Experiment: `{record['experiment_id']}`", "", f"Status: {record['status']}", "", "## Configuration", "", f"- Domain: {record['domain']}", f"- Specification SHA-256: `{record['specification_sha256']}`", f"- Retrieval corpus SHA-256: `{record['retrieval']['corpus_sha256']}`", f"- Parameters: `{json.dumps(record['generation'], sort_keys=True)}`", f"- Repair roles: `{json.dumps(record['repair'], sort_keys=True)}`", "", "## Candidates", ""]
            for candidate in record["candidates"]:
                lines.extend([f"### {candidate['provider_type']} / {candidate['model_identifier']}", "", f"- Status: {candidate['status']}", f"- Functional success: {candidate.get('functional_success')}", f"- First pass: {candidate.get('first_pass_success')}", f"- Candidate SHA-256: `{candidate.get('candidate_hash')}`", f"- Verification: `{json.dumps(candidate.get('verification', {}), sort_keys=True)}`", f"- Ranking: {candidate.get('rank')}", ""])
                for revision in candidate.get("revisions", []):
                    lines.extend([f"#### Revision {revision['attempt']} — {revision.get('model') or 'model unavailable'}", "", f"Outcome: {revision.get('outcome')}; SHA-256: `{revision.get('code_sha256')}`", "", "Generation or repair explanation (model-generated):", "", "```json", json.dumps(revision.get("explanation") or {}, indent=2), "```", "", "Failure diagnosis hypothesis (not independently verified):", "", "```json", json.dumps(revision.get("failure_analysis") or {}, indent=2), "```", "", "Verification evidence:", "", "```json", json.dumps(revision.get("stages") or [], indent=2), "```", "", "Prompt:", "", "```text", revision.get("prompt", ""), "```", "", "RTL:", "", "```verilog", revision.get("rtl", ""), "```", ""])
                    if revision.get("diff_from_previous") is not None:
                        lines.extend(["Diff from previous revision:", "", "```diff", revision["diff_from_previous"], "```", ""])
            body = "\n".join(lines)
            return Response(body, media_type="text/markdown", headers={"Content-Disposition": f"attachment; filename=experiment-{record['experiment_id']}.md"})
        return Response(json.dumps(record, indent=2) + "\n", media_type="application/json", headers={"Content-Disposition": f"attachment; filename=experiment-{record['experiment_id']}.json"})

    routes = [
        Route("/", index),
        Route("/api/dashboard", overview),
        Route("/api/providers", get_providers, methods=["GET"]),
        Route("/api/providers", add_provider, methods=["POST"]),
        Route("/api/providers/{provider_id:str}", edit_provider, methods=["PATCH"]),
        Route("/api/providers/{provider_id:str}", remove_provider, methods=["DELETE"]),
        Route("/api/providers/{provider_id:str}/test", test_provider, methods=["POST"]),
        Route("/api/models", get_models, methods=["GET"]),
        Route("/api/models", add_model, methods=["POST"]),
        Route("/api/models/{model_id:str}", edit_model, methods=["PATCH"]),
        Route("/api/models/{model_id:str}", remove_model, methods=["DELETE"]),
        Route("/api/models/{model_id:str}/default", set_default_model, methods=["POST"]),
        Route("/api/credentials", get_credentials, methods=["GET"]),
        Route("/api/credentials", add_credential, methods=["POST"]),
        Route("/api/credentials/{provider:str}/{account:str}/validate", validate_credential, methods=["POST"]),
        Route("/api/credentials/{provider:str}/{account:str}", remove_credential, methods=["DELETE"]),
        Route("/api/runs", list_runs, methods=["GET"]),
        Route("/api/runs/{run_id:str}", get_run, methods=["GET"]),
        Route("/api/experiments", list_experiments, methods=["GET"]),
        Route("/api/experiments", create_experiment, methods=["POST"]),
        Route("/api/experiments/{experiment_id:str}", get_experiment, methods=["GET"]),
        Route("/api/experiments/{experiment_id:str}/start", start_experiment, methods=["POST"]),
        Route("/api/experiments/{experiment_id:str}/cancel", cancel_experiment, methods=["POST"]),
        Route("/api/experiments/{experiment_id:str}/candidates/{candidate_id:str}", candidate_detail, methods=["GET"]),
        Route("/api/experiments/{experiment_id:str}/candidates/{candidate_id:str}/cancel", cancel_experiment, methods=["POST"]),
        Route("/api/experiments/{experiment_id:str}/candidates/{candidate_id:str}/rtl", candidate_download, methods=["GET"]),
        Route("/api/experiments/{experiment_id:str}/diff", candidate_diff, methods=["GET"]),
        Route("/api/experiments/{experiment_id:str}/report", experiment_report, methods=["GET"]),
        Route("/assets/{path:path}", _asset, methods=["GET"]),
    ]
    app.router.routes.extend(routes)
    return app


async def _body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(400, "Request body must be valid JSON.") from None
    if not isinstance(payload, dict):
        raise HTTPException(400, "Request body must be a JSON object.")
    return payload


async def _asset(request: Request):
    name = request.path_params["path"]
    path = (STATIC / name).resolve()
    if STATIC.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "Asset not found.")
    media_type = "text/css" if path.suffix == ".css" else "text/javascript"
    return Response(path.read_bytes(), media_type=media_type)


def _ollama_model_names(response: Any) -> list[str]:
    entries = response.get("models", []) if isinstance(response, dict) else getattr(response, "models", [])
    names = []
    for entry in entries:
        if isinstance(entry, dict):
            name = entry.get("model") or entry.get("name")
        else:
            name = getattr(entry, "model", None) or getattr(entry, "name", None)
        if isinstance(name, str):
            names.append(name)
    return sorted(set(names))


def _connection_status(error: Exception) -> tuple[str, str]:
    code = getattr(error, "status_code", None) or getattr(error, "status", None)
    if code in {401, 403}:
        return "authentication_failure", "The provider rejected authentication."
    if code == 429:
        return "rate_limited", "The provider rate limited the connection check."
    if code in {500, 502, 503, 504}:
        return "unavailable", "The provider is temporarily unavailable."
    if isinstance(error, (ConnectionError, TimeoutError, OSError)):
        return "unavailable", "Could not connect to the provider."
    return "unavailable", "Provider connectivity check failed."


def _credential_profile_exists(manager: CredentialManager, provider: str, account: str) -> bool:
    return any(profile.account == account for profile in manager.list_profiles(provider))


def _rank_experiment(record: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(record))
    def score(candidate):
        verification = candidate.get("verification", {})
        token_usage = candidate.get("token_usage") or {}
        tokens = sum(value for value in token_usage.values() if isinstance(value, (int, float)))
        return (
            bool(candidate.get("functional_success")),
            bool(candidate.get("first_pass_success")),
            bool(verification.get("structural")),
            bool(verification.get("compile")),
            -candidate.get("repair_count", 0),
            -(candidate.get("total_latency_sec") if candidate.get("total_latency_sec") is not None else float("inf")),
            -(tokens if token_usage else float("inf")),
        )
    ranked = sorted(result["candidates"], key=score, reverse=True)
    rank_by_id = {candidate["candidate_id"]: index + 1 for index, candidate in enumerate(ranked)}
    for candidate in result["candidates"]:
        candidate["rank"] = rank_by_id[candidate["candidate_id"]]
    return result


def _candidate_artifacts(manager, experiment_id: str, candidate_id: str):
    record = manager.get(experiment_id)
    if record is None:
        raise HTTPException(404, "Experiment not found.")
    candidate = next((item for item in record["candidates"] if item["candidate_id"] == candidate_id), None)
    if candidate is None:
        raise HTTPException(404, "Candidate not found.")
    base = (manager.store.root / experiment_id).resolve()
    run_dir = (base / candidate["workspace"] / candidate.get("run_id", "")).resolve()
    if base not in run_dir.parents:
        raise HTTPException(400, "Invalid candidate workspace.")
    return record, candidate, run_dir


def _read_attempts(run_dir: Path) -> list[dict[str, Any]]:
    results = []
    for path in sorted(run_dir.glob("attempt_*.json")):
        try:
            results.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return results


def _read_revision(run_dir: Path, value: str) -> str:
    if not value.isdecimal() or int(value) > 100:
        raise HTTPException(400, "Invalid candidate revision.")
    path = run_dir / f"attempt_{int(value)}.v"
    if not path.is_file():
        raise HTTPException(404, "Candidate RTL revision not found.")
    return path.read_text(encoding="utf-8")


def _report_experiment(manager, record: dict[str, Any]) -> dict[str, Any]:
    for candidate in record["candidates"]:
        try:
            _, _, run_dir = _candidate_artifacts(manager, record["experiment_id"], candidate["candidate_id"])
        except HTTPException:
            candidate["revisions"] = []
            continue
        attempts = _read_attempts(run_dir)
        revisions = []
        previous = ""
        for attempt in attempts:
            code = attempt.get("final_code", "")
            diff = "".join(difflib.unified_diff(previous.splitlines(True), code.splitlines(True), fromfile=f"attempt_{max(0, attempt.get('attempt', 1)-1)}", tofile=f"attempt_{attempt.get('attempt', 0)}")) if previous else None
            revisions.append({
                "attempt": attempt.get("attempt"), "repair": attempt.get("repair", False),
                "model": attempt.get("model"), "outcome": attempt.get("outcome"),
                "code_sha256": attempt.get("code_sha256"), "prompt_sha256": attempt.get("prompt_sha256"),
                "retrieval_sha256": attempt.get("retrieval_sha256"), "rtl": code,
                "prompt": attempt.get("prompt", ""), "retrieval": attempt.get("retrieval", {}),
                "explanation": attempt.get("explanation"), "failure_analysis": attempt.get("failure_analysis"),
                "stages": _sanitize_payload(attempt.get("stages", []), [PROJECT_ROOT, (manager.store.root / record["experiment_id"]).resolve()]), "diff_from_previous": diff,
            })
            previous = code
        candidate["revisions"] = revisions
    return record


def _sanitize_payload(value, paths):
    if isinstance(value, str):
        result = value
        for path in paths:
            result = result.replace(str(path), "<local-path>")
        return re.sub(r"(?<![A-Za-z0-9_])/(?:[^\s/:]+/)*[^\s/:]+(?=[:\s])", "<local-path>", result)
    if isinstance(value, list):
        return [_sanitize_payload(item, paths) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_payload(item, paths) for key, item in value.items()}
    return value


app = create_app()
