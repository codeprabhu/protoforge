"""Durable experiment scheduling around ProtoForge's existing run pipeline."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from agent_core.agent import run_once
from agent_core.config import Config
from tools.rtl_memory import retrieval_corpus_hash
from ui.model_registry import ModelRegistry

ROOT = Path(__file__).resolve().parents[1]
SPECIFICATIONS = {"i2c-default": ROOT / "protocols/i2c/spec.md"}
TERMINAL_CANDIDATES = {"completed", "failed", "cancelled"}


class ExperimentError(ValueError):
    pass


class ExperimentStore:
    def __init__(self, root: str | Path = "results/experiments"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def path(self, experiment_id: str) -> Path:
        if not experiment_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in experiment_id):
            raise KeyError("Experiment not found.")
        return self.root / experiment_id / "experiment.json"

    def create(self, record: dict[str, Any]) -> None:
        with self._lock:
            path = self.path(record["experiment_id"])
            path.parent.mkdir(parents=True, exist_ok=False)
            self.save(record)

    def save(self, record: dict[str, Any]) -> None:
        with self._lock:
            path = self.path(record["experiment_id"])
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(temporary, path)

    def get(self, experiment_id: str) -> dict[str, Any] | None:
        try:
            with self._lock:
                return json.loads(self.path(experiment_id).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def list(self) -> list[dict[str, Any]]:
        records = []
        for item in self.root.glob("*/experiment.json"):
            try:
                records.append(json.loads(item.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(records, key=lambda record: record.get("created_at", 0), reverse=True)


class ExperimentManager:
    def __init__(
        self,
        *,
        registry: ModelRegistry,
        store: ExperimentStore | None = None,
        runner=run_once,
    ):
        self.registry = registry
        self.store = store or ExperimentStore()
        self.runner = runner
        self._lock = threading.RLock()
        self._pool: ThreadPoolExecutor | None = None
        self._futures: dict[str, Future] = {}
        self._cancelled: set[str] = set()

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = _text(payload.get("name"), "Experiment name")
        domain = payload.get("domain", "i2c")
        if domain != "i2c":
            raise ExperimentError("Only the I2C domain is currently supported.")
        spec_id = payload.get("specification_id", "i2c-default")
        spec = SPECIFICATIONS.get(spec_id)
        if spec is None or not spec.is_file():
            raise ExperimentError("Unknown I2C specification.")
        model_ids = payload.get("model_ids")
        if not isinstance(model_ids, list) or not model_ids or len(set(model_ids)) != len(model_ids):
            raise ExperimentError("Select one or more distinct configured models.")
        selected = {model["model_id"]: model for model in self.registry.list_models()}
        if any(model_id not in selected for model_id in model_ids):
            raise ExperimentError("A selected model is not in the Model Registry.")
        selected_models = [selected[model_id] for model_id in model_ids]
        repair_model_id = payload.get("repair_model_id")
        repair_model = selected.get(repair_model_id) if repair_model_id else None
        if repair_model_id and (repair_model is None or not repair_model["enabled"]):
            raise ExperimentError("Selected repair model is unavailable or disabled.")
        analyst_model_id = payload.get("analyst_model_id")
        analyst_model = selected.get(analyst_model_id) if analyst_model_id else None
        if analyst_model_id and (analyst_model is None or not analyst_model["enabled"]):
            raise ExperimentError("Selected analyst model is unavailable or disabled.")
        providers = {provider["provider_id"]: provider for provider in self.registry.list_providers()}
        if any(model["provider_id"] not in providers for model in selected_models):
            raise ExperimentError("A selected model has no configured provider.")
        if any(not model["enabled"] for model in selected_models):
            raise ExperimentError("Disabled models cannot be selected.")
        samples = _integer(payload.get("samples_per_model", 1), "Samples per model", 1, 100)
        repairs = _integer(payload.get("max_repairs", 2), "Maximum repairs", 0, 10)
        global_limit = _integer(payload.get("global_concurrency", 1), "Global concurrency", 1, 16)
        local_limit = _integer(payload.get("local_concurrency", 1), "Local concurrency", 1, 4)
        temperature = _number(payload.get("temperature", 0), "Temperature", 0, 2)
        seed = _integer(payload.get("seed", 42), "Seed", 0, 2**31 - 1)
        generation_timeout = _number(payload.get("generation_timeout", 180), "Generation timeout", 1, 3600)
        execution_mode = payload.get("execution_mode", "sequential")
        if execution_mode not in {"sequential", "concurrent"}:
            raise ExperimentError("Execution mode must be sequential or concurrent.")
        retrieval = payload.get("retrieval", True)
        if not isinstance(retrieval, bool):
            raise ExperimentError("Retrieval setting must be true or false.")
        if execution_mode == "sequential":
            global_limit = 1
        provider_limits = _limits(payload.get("provider_concurrency", {}), {p["provider_id"] for p in providers.values()})
        model_limits = _limits(payload.get("model_concurrency", {}), set(selected))
        experiment_id = str(uuid.uuid4())
        started = time.time()
        candidates = []
        for model in selected_models:
            provider = providers[model["provider_id"]]
            for sample in range(samples):
                candidate_id = str(uuid.uuid4())
                candidates.append({
                    "candidate_id": candidate_id,
                    "sample": sample,
                    "provider_id": provider["provider_id"],
                    "provider_type": provider["provider_type"],
                    "model_id": model["model_id"],
                    "model_identifier": model["model_identifier"],
                    "local": model["local"],
                    "repair_model_id": repair_model["model_id"] if repair_model else model["model_id"],
                    "repair_local": repair_model["local"] if repair_model else model["local"],
                    "repair_provider_id": repair_model["provider_id"] if repair_model else model["provider_id"],
                    "repair_provider_type": providers[repair_model["provider_id"]]["provider_type"] if repair_model else provider["provider_type"],
                    "analyst_model_id": analyst_model["model_id"] if analyst_model else (repair_model["model_id"] if repair_model else model["model_id"]),
                    "analyst_local": analyst_model["local"] if analyst_model else (repair_model["local"] if repair_model else model["local"]),
                    "analyst_provider_id": analyst_model["provider_id"] if analyst_model else (repair_model["provider_id"] if repair_model else provider["provider_id"]),
                    "analyst_provider_type": providers[analyst_model["provider_id"]]["provider_type"] if analyst_model else (providers[repair_model["provider_id"]]["provider_type"] if repair_model else provider["provider_type"]),
                    "status": "queued",
                    "current_stage": "queued",
                    "generation_latency_sec": None,
                    "total_latency_sec": None,
                    "token_usage": None,
                    "candidate_hash": None,
                    "functional_success": None,
                    "first_pass_success": None,
                    "verification": {},
                    "repair_count": 0,
                    "error": None,
                    "workspace": f"candidates/{candidate_id}",
                    "started_at": None,
                    "finished_at": None,
                })
        record = {
            "experiment_id": experiment_id,
            "name": name,
            "domain": domain,
            "specification_id": spec_id,
            "specification_sha256": hashlib.sha256(spec.read_bytes()).hexdigest(),
            "retrieval": {"enabled": retrieval, "corpus_sha256": retrieval_corpus_hash()},
            "selected_models": [{
                "model_id": model["model_id"], "provider_id": model["provider_id"],
                "provider_type": providers[model["provider_id"]]["provider_type"],
                "model_identifier": model["model_identifier"],
                "local": model["local"], "context_window": model["context_window"],
                "capabilities": model["capabilities"],
            } for model in selected_models],
            "generation": {
                "temperature": temperature, "seed": seed, "samples_per_model": samples,
                "generation_timeout": generation_timeout, "max_repairs": repairs,
                "seed_behavior": "The seed is passed to Ollama; provider-specific seed behavior may differ.",
            },
            "execution": {
                "mode": execution_mode, "global_concurrency": global_limit,
                "local_concurrency": local_limit, "provider_concurrency": provider_limits,
                "model_concurrency": model_limits,
            },
            "repair": {
                "mode": "cross_model" if repair_model else "same_model",
                "repair_model_id": repair_model["model_id"] if repair_model else None,
                "analyst_model_id": analyst_model["model_id"] if analyst_model else None,
                "max_repairs": repairs,
            },
            "verification": {"pipeline": ["structural", "compile", "hardware", "simulation", "protocol_judge"]},
            "reproducibility": {
                "prompt_hashes": "Recorded per candidate attempt after prompt construction.",
                "provider_nondeterminism": "Temperature and seed do not guarantee identical output across provider/model versions.",
            },
            "status": "created", "created_at": started, "started_at": None, "finished_at": None,
            "candidates": candidates,
        }
        self.store.create(record)
        (self.store.root / experiment_id / "candidates").mkdir(parents=True, exist_ok=True)
        return record

    def start(self, experiment_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._required(experiment_id)
            if record["status"] != "created":
                raise ExperimentError("Only a newly created experiment can be started.")
            record["status"] = "queued"
            record["started_at"] = time.time()
            self.store.save(record)
            limit = record["execution"]["global_concurrency"]
            self._pool = ThreadPoolExecutor(max_workers=limit, thread_name_prefix="protoforge-candidate")
            providers = self.registry.list_providers()
            provider_locks = {
                provider["provider_id"]: threading.Semaphore(
                    min(record["execution"]["provider_concurrency"].get(provider["provider_id"], limit), limit)
                ) for provider in providers
            }
            local_lock = threading.Semaphore(record["execution"]["local_concurrency"])
            model_locks = {model["model_id"]: threading.Semaphore(
                min(record["execution"]["model_concurrency"].get(model["model_id"], limit), limit)
            ) for model in self.registry.list_models()}
            for candidate in record["candidates"]:
                future = self._pool.submit(self._execute_candidate, experiment_id, candidate["candidate_id"], provider_locks, model_locks, local_lock)
                self._futures[candidate["candidate_id"]] = future
            self._refresh(experiment_id)
            return self._required(experiment_id)

    def cancel(self, experiment_id: str, candidate_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            record = self._required(experiment_id)
            targets = [c for c in record["candidates"] if candidate_id is None or c["candidate_id"] == candidate_id]
            if candidate_id and not targets:
                raise KeyError("Candidate not found.")
            for candidate in targets:
                self._cancelled.add(candidate["candidate_id"])
                future = self._futures.get(candidate["candidate_id"])
                if future and future.cancel():
                    candidate["status"] = "cancelled"
                    candidate["finished_at"] = time.time()
                elif candidate["status"] == "queued":
                    candidate["status"] = "cancelled"
                    candidate["finished_at"] = time.time()
                elif candidate["status"] == "running":
                    candidate["status"] = "cancel_requested"
            if candidate_id is None:
                record["status"] = "cancelled"
                record["finished_at"] = time.time()
            else:
                self._update_experiment_status(record)
            self.store.save(record)
            return record

    def get(self, experiment_id: str) -> dict[str, Any] | None:
        return self.store.get(experiment_id)

    def list(self) -> list[dict[str, Any]]:
        return self.store.list()

    def _execute_candidate(self, experiment_id: str, candidate_id: str, provider_locks, model_locks, local_lock) -> None:
        with provider_locks_for_candidate(candidate_id, self._required(experiment_id), provider_locks, model_locks, local_lock):
            with self._lock:
                record = self._required(experiment_id)
                candidate = _candidate(record, candidate_id)
                if candidate_id in self._cancelled or record["status"] == "cancelled":
                    candidate["status"] = "cancelled"
                    candidate["finished_at"] = time.time()
                    self.store.save(record)
                    return
                candidate.update({"status": "running", "current_stage": "generating", "started_at": time.time()})
                self.store.save(record)
                self._refresh(experiment_id)
        candidate_dir = self.store.root / experiment_id / candidate["workspace"]
        candidate_dir.mkdir(parents=True, exist_ok=True)
        model = next(m for m in record["selected_models"] if m["model_id"] == candidate["model_id"])
        provider = self.registry.get_provider(candidate["provider_id"])
        repair_model = next(m for m in self.registry.list_models() if m["model_id"] == candidate["repair_model_id"])
        repair_provider = self.registry.get_provider(candidate["repair_provider_id"])
        analyst_model = next(m for m in self.registry.list_models() if m["model_id"] == candidate["analyst_model_id"])
        analyst_provider = self.registry.get_provider(candidate["analyst_provider_id"])
        generation = record["generation"]
        config = Config.from_env().with_overrides(
            provider=model["provider_type"], provider_account=provider.get("credential_account"),
            provider_endpoint=provider.get("endpoint_url"), model=model["model_identifier"],
            repair_model=repair_model["model_identifier"],
            repair_provider=candidate["repair_provider_type"],
            repair_provider_account=repair_provider.get("credential_account"),
            repair_provider_endpoint=repair_provider.get("endpoint_url"),
            analyst_model=analyst_model["model_identifier"],
            analyst_provider=candidate["analyst_provider_type"],
            analyst_provider_account=analyst_provider.get("credential_account"),
            analyst_provider_endpoint=analyst_provider.get("endpoint_url"),
            generation_timeout=generation["generation_timeout"],
            spec_path=str(SPECIFICATIONS[record["specification_id"]]),
            temperature=generation["temperature"], seed=generation["seed"] + candidate["sample"],
            max_repairs=generation["max_repairs"], retrieval=record["retrieval"]["enabled"],
            output_root=str(candidate_dir), results_log=str(candidate_dir / "runs.jsonl"),
            trace_dir=str(candidate_dir / "traces"),
        )

        def progress(event: str, data: dict[str, Any]):
            with self._lock:
                latest = self._required(experiment_id)
                target = _candidate(latest, candidate_id)
                if event == "stage_finished":
                    target["current_stage"] = data.get("stage", "verifying")
                elif event == "model_invoked":
                    target["current_stage"] = "verifying"
                self.store.save(latest)
                self._refresh(experiment_id)

        start_time = time.time()
        try:
            current = self._required(experiment_id)
            locks = provider_locks_for_candidate(candidate_id, current, provider_locks, model_locks, local_lock)
            with locks:
                with self._lock:
                    latest = self._required(experiment_id)
                    target = _candidate(latest, candidate_id)
                    if candidate_id in self._cancelled or latest["status"] == "cancelled":
                        target.update({"status": "cancelled", "current_stage": "cancelled", "finished_at": time.time()})
                        self.store.save(latest)
                        self._refresh(experiment_id)
                        return
                result = self.runner(
                    config=config, run_id=candidate_id, progress_fn=progress,
                    on_success=lambda *_args, **_kwargs: None,
                    on_failure=lambda *_args, **_kwargs: None,
                )
            run_dir = Path(result.get("run_dir", ""))
            summary = _summarize_run(result, run_dir)
            with self._lock:
                latest = self._required(experiment_id)
                target = _candidate(latest, candidate_id)
                target.update(summary)
                target["total_latency_sec"] = round(time.time() - start_time, 4)
                target["finished_at"] = time.time()
                target["status"] = "cancelled" if candidate_id in self._cancelled else (
                    "completed" if result.get("final") == "pass" else "failed"
                )
                if result.get("generation_error") or result.get("tool_error"):
                    target["error"] = _safe_failure(result.get("generation_error") or result.get("tool_error"))
                self.store.save(latest)
                self._refresh(experiment_id)
        except Exception as error:
            with self._lock:
                latest = self._required(experiment_id)
                target = _candidate(latest, candidate_id)
                target.update({"status": "failed", "current_stage": "failed", "error": _safe_failure(error), "finished_at": time.time(), "total_latency_sec": round(time.time() - start_time, 4)})
                self.store.save(latest)
                self._refresh(experiment_id)

    def _refresh(self, experiment_id: str) -> None:
        record = self._required(experiment_id)
        self._update_experiment_status(record)
        self.store.save(record)
        if self._pool is not None and all(candidate["status"] in TERMINAL_CANDIDATES for candidate in record["candidates"]):
            self._pool.shutdown(wait=False)
            self._pool = None

    @staticmethod
    def _update_experiment_status(record: dict[str, Any]) -> None:
        statuses = [candidate["status"] for candidate in record["candidates"]]
        if record["status"] == "cancelled":
            return
        if any(status in {"queued", "running", "cancel_requested"} for status in statuses):
            record["status"] = "running" if any(status == "running" for status in statuses) else "queued"
        elif all(status == "completed" for status in statuses):
            record["status"] = "completed"
            record["finished_at"] = time.time()
        elif any(status == "completed" for status in statuses):
            record["status"] = "partially_completed"
            record["finished_at"] = time.time()
        elif all(status == "cancelled" for status in statuses):
            record["status"] = "cancelled"
            record["finished_at"] = time.time()
        else:
            record["status"] = "failed"
            record["finished_at"] = time.time()

    def _required(self, experiment_id: str) -> dict[str, Any]:
        record = self.store.get(experiment_id)
        if record is None:
            raise KeyError("Experiment not found.")
        return record


class _HeldLocks:
    def __init__(self, locks):
        self.locks = locks

    def __enter__(self):
        for lock in self.locks:
            lock.acquire()

    def __exit__(self, exc_type, exc, tb):
        for lock in reversed(self.locks):
            lock.release()


def provider_locks_for_candidate(candidate_id, record, provider_locks, model_locks, local_lock):
    candidate = _candidate(record, candidate_id)
    roles = (
        (candidate["provider_id"], candidate["model_id"], candidate["local"]),
        (candidate["repair_provider_id"], candidate["repair_model_id"], candidate["repair_local"]),
        (candidate["analyst_provider_id"], candidate["analyst_model_id"], candidate["analyst_local"]),
    )
    lock_keys = set()
    for provider_id, model_id, is_local in roles:
        lock_keys.add(("provider", provider_id))
        lock_keys.add(("model", model_id))
        if is_local:
            lock_keys.add(("local", "ollama"))
    locks = {("provider", key): lock for key, lock in provider_locks.items()}
    locks.update({("model", key): lock for key, lock in model_locks.items()})
    locks[("local", "ollama")] = local_lock
    return _HeldLocks([locks[key] for key in sorted(lock_keys)])


def _candidate(record: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    for candidate in record["candidates"]:
        if candidate["candidate_id"] == candidate_id:
            return candidate
    raise KeyError("Candidate not found.")


def _summarize_run(result: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    attempt_files = sorted(run_dir.glob("attempt_*.json"), key=lambda path: int(path.stem.split("_")[-1]))
    attempts = []
    for path in attempt_files:
        try:
            attempts.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    stages = attempts[-1].get("stages", []) if attempts else []
    verification = {stage.get("stage"): stage.get("passed") for stage in stages}
    first_pass = bool(attempts and attempts[0].get("outcome") == "pass")
    code_path = run_dir / "attempt_0.v"
    code = code_path.read_text(encoding="utf-8") if code_path.is_file() else ""
    metadata = attempts[0].get("model_metadata", {}) if attempts else {}
    generation_ns = metadata.get("total_duration")
    generation_latency = generation_ns / 1_000_000_000 if isinstance(generation_ns, (int, float)) else None
    token_sources = []
    for attempt in attempts:
        token_sources.append(attempt.get("model_metadata", {}) or {})
        token_sources.append((attempt.get("failure_analysis", {}) or {}).get("provider_metadata", {}) or {})
    tokens = {
        "prompt": sum(value for source in token_sources if isinstance((value := source.get("prompt_eval_count")), (int, float))),
        "completion": sum(value for source in token_sources if isinstance((value := source.get("eval_count")), (int, float))),
    }
    if not any(source.get("prompt_eval_count") is not None or source.get("eval_count") is not None for source in token_sources):
        tokens = None
    return {
        "run_id": result.get("run_id"),
        "candidate_hash": attempts[0].get("code_sha256") if attempts else None,
        "prompt_sha256": hashlib.sha256(attempts[0].get("prompt", "").encode("utf-8")).hexdigest() if attempts else None,
        "retrieved_context_sha256": hashlib.sha256(
            json.dumps(attempts[0].get("retrieval", {}), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest() if attempts else None,
        "rtl_size_bytes": len(code.encode("utf-8")) if code else None,
        "first_pass_success": first_pass,
        "functional_success": result.get("final") == "pass",
        "verification": verification,
        "repair_count": max(0, len(attempts) - 1),
        "generation_latency_sec": generation_latency,
        "token_usage": tokens,
        "candidate_rtl": "attempt_0.v" if code else None,
        "attempts": len(attempts),
    }


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 160:
        raise ExperimentError(f"{label} must be non-empty text under 160 characters.")
    return value.strip()


def _safe_failure(error: Any) -> str:
    return f"{type(error).__name__}: provider or verification operation failed; details are available in the candidate's sanitized stage records."


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ExperimentError(f"{label} must be between {minimum} and {maximum}.")
    return value


def _number(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum:
        raise ExperimentError(f"{label} must be between {minimum} and {maximum}.")
    return float(value)


def _limits(value: Any, allowed: set[str]) -> dict[str, int]:
    if not isinstance(value, dict) or any(key not in allowed for key in value):
        raise ExperimentError("Concurrency limits contain an unknown provider or model.")
    return {key: _integer(limit, "Concurrency limit", 1, 16) for key, limit in value.items()}
