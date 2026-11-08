"""Thin Ollama client with stable defaults and metadata capture.

Defaults are constants; environment overrides are applied once in
``agent_core.config.Config.from_env`` so importing this module has no side
effects and no hidden global state.

``generate_verilog`` returns just the text (kept for simple callers and
tests). ``generate_verilog_with_metadata`` additionally returns token counts
and durations from the Ollama response so a run can be audited later.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Dict, Optional

DEFAULT_MODEL = "qwen2.5-coder:7b"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_SEED = 42
DEFAULT_NUM_CTX = 8192
DEFAULT_NUM_PREDICT = 4096

SYSTEM_PROMPT = (
    "You are a careful RTL engineer. Return valid synthesizable "
    "Verilog-2001, preserve the specified module interface, and "
    "prefer explicit clocked FSMs over delays or testbench constructs."
)

JSON_SYSTEM_PROMPT = (
    "You are a careful digital design engineer. Reply with exactly one JSON "
    "object that matches the supplied schema. Do not add prose, markdown, or "
    "code fences."
)

# Response fields worth keeping for reproducibility (Ollama reports these).
_METADATA_FIELDS = (
    "model",
    "created_at",
    "done",
    "done_reason",
    "total_duration",
    "load_duration",
    "prompt_eval_count",
    "prompt_eval_duration",
    "eval_count",
    "eval_duration",
)


def _require_ollama():
    try:
        import ollama
    except ImportError as exc:
        raise RuntimeError(
            "The Ollama Python package is missing. Install project dependencies "
            "with `python -m pip install -r requirements.txt`, then ensure the "
            "Ollama service is running and the selected model has been pulled."
        ) from exc
    return ollama


def generate_verilog_with_metadata(
    prompt: str,
    model: str = DEFAULT_MODEL,
    temperature: float | None = None,
    seed: int = DEFAULT_SEED,
) -> Dict[str, Any]:
    """Generate RTL and return ``{"content": str, ...Ollama metadata}``."""
    ollama = _require_ollama()
    options = {
        "temperature": DEFAULT_TEMPERATURE if temperature is None else temperature,
        "seed": seed,
        "num_ctx": DEFAULT_NUM_CTX,
        "num_predict": DEFAULT_NUM_PREDICT,
    }
    response = ollama.chat(
        model=model or DEFAULT_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        options=options,
    )
    content = response["message"]["content"]
    metadata = {field: response.get(field) for field in _METADATA_FIELDS if field in response}
    metadata["options"] = options
    return {"content": content, **metadata}


def generate_verilog(
    prompt: str,
    model: str = DEFAULT_MODEL,
    temperature: float | None = None,
    seed: int = DEFAULT_SEED,
    with_metadata: bool = False,
) -> Any:
    """Generate RTL text, or the full metadata dict when ``with_metadata``.

    The string form is kept as the default because most callers (and tests)
    only need the code. ``rtl_generator._invoke_model`` asks for metadata so an
    attempt record can carry token counts and timings.
    """
    result = generate_verilog_with_metadata(
        prompt, model=model, temperature=temperature, seed=seed
    )
    return result if with_metadata else result["content"]


def generate_json_with_metadata(
    prompt: str,
    model: str = DEFAULT_MODEL,
    temperature: float | None = None,
    seed: int = DEFAULT_SEED,
    json_schema: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Ask the model for JSON, optionally constrained by a JSON schema.

    Ollama's ``format`` argument accepts either the string ``"json"`` (any
    valid JSON) or a schema dict (grammar-constrained output, server >= 0.5).
    The caller still parses and validates the result: a constrained response
    can be syntactically valid and semantically wrong.
    """
    ollama = _require_ollama()
    options = {
        "temperature": DEFAULT_TEMPERATURE if temperature is None else temperature,
        "seed": seed,
        "num_ctx": DEFAULT_NUM_CTX,
        "num_predict": DEFAULT_NUM_PREDICT,
    }
    response = ollama.chat(
        model=model or DEFAULT_MODEL,
        messages=[
            {"role": "system", "content": JSON_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        format=json_schema if json_schema is not None else "json",
        options=options,
    )
    content = response["message"]["content"]
    metadata = {field: response.get(field) for field in _METADATA_FIELDS if field in response}
    metadata["options"] = options
    return {"content": content, **metadata}


def generate_json(
    prompt: str,
    model: str = DEFAULT_MODEL,
    temperature: float | None = None,
    seed: int = DEFAULT_SEED,
    with_metadata: bool = False,
    json_schema: Optional[Dict[str, Any]] = None,
) -> Any:
    """Generate JSON text, or the full metadata dict when ``with_metadata``."""
    result = generate_json_with_metadata(
        prompt,
        model=model,
        temperature=temperature,
        seed=seed,
        json_schema=json_schema,
    )
    return result if with_metadata else result["content"]


_MODEL_DIGEST_CACHE: Dict[str, Optional[str]] = {}


def model_digest(model: str = DEFAULT_MODEL, timeout: float = 1.5) -> Optional[str]:
    """Return the local Ollama digest for ``model``, or ``None`` if unknown.

    Uses the HTTP API directly with a short timeout so a missing or busy
    Ollama service cannot stall a run. Results are cached per process.
    """
    if model in _MODEL_DIGEST_CACHE:
        return _MODEL_DIGEST_CACHE[model]

    host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    if not host.startswith("http"):
        host = f"http://{host}"
    digest: Optional[str] = None
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        for entry in payload.get("models", []):
            name = entry.get("model") or entry.get("name")
            if name in {model, f"{model}:latest"}:
                digest = entry.get("digest")
                break
    except Exception:
        digest = None
    _MODEL_DIGEST_CACHE[model] = digest
    return digest

