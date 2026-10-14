"""Small, dependency-free retrieval memory for verified RTL examples.

Only RTL that has actually passed the protocol testbench is promoted into the
learned-example bank. The initial few-shot seed is a reset-safe copy of the
checked-in I2C oracle. Lexical retrieval is intentionally deterministic and
lightweight so it works offline without an embedding model or vector database.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REFERENCE = PROJECT_ROOT / "protocols" / "i2c" / "reference" / "good_master_reset_safe.v"
DEFAULT_MEMORY_DIR = PROJECT_ROOT / "results" / "rtl_memory"

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*|0x[0-9a-fA-F]+")
_STOPWORDS = {
    "module", "endmodule", "input", "output", "wire", "reg", "assign",
    "begin", "end", "always", "posedge", "negedge", "if", "else", "case",
    "endcase", "parameter", "localparam", "logic", "the", "and", "for",
    "with", "from", "that", "this", "which", "must", "should", "only",
    "into", "your", "code", "rtl", "verilog", "module", "top_glue",
}


def _tokens(text: str) -> Counter:
    # Remove comments before matching so comment prose cannot dominate ranking.
    text = re.sub(r"/\*[\s\S]*?\*/", " ", text)
    text = re.sub(r"//[^\n]*", " ", text)
    tokens = [t.lower() for t in _TOKEN_RE.findall(text)]
    tokens = [t for t in tokens if len(t) > 1 and t not in _STOPWORDS]
    return Counter(tokens)


def _similarity(query_tokens: Counter, document: str) -> float:
    doc_tokens = _tokens(document)
    if not query_tokens or not doc_tokens:
        return 0.0
    # Query coverage is more useful here than raw document length: RTL samples
    # are long, and the query contains the design requirements we care about.
    shared = set(query_tokens) & set(doc_tokens)
    if not shared:
        return 0.0
    weighted = sum(min(query_tokens[t], 2) * (1.0 + min(doc_tokens[t], 3) * 0.1) for t in shared)
    total = sum(min(count, 2) for count in query_tokens.values())
    return weighted / max(total, 1)


def record_passing_example(
    code: str,
    model: str | None = None,
    metadata: dict | None = None,
    memory_dir: str | Path | None = None,
) -> Path:
    """Persist a passing generation, deduplicated by SHA-256 content hash."""
    root = Path(memory_dir) if memory_dir else DEFAULT_MEMORY_DIR
    examples_dir = root / "passing"
    examples_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    rtl_path = examples_dir / f"{digest[:16]}.v"
    if not rtl_path.exists():
        rtl_path.write_text(code.rstrip() + "\n", encoding="utf-8")
        sidecar = {
            "sha256": digest,
            "verified_pass": True,
            "model": model,
            "created_at": time.time(),
            "metadata": metadata or {},
        }
        rtl_path.with_suffix(".json").write_text(
            json.dumps(sidecar, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return rtl_path


def _load_successful_examples(memory_dir: Path) -> list[tuple[str, str, str]]:
    examples: list[tuple[str, str, str]] = []

    # This reset-safe few-shot fixture is derived from the project's existing
    # known-good oracle. It preserves the bus sequencing while keeping LLM
    # context free from initial blocks and declaration-time initialization.
    if DEFAULT_REFERENCE.is_file():
        examples.append(("validated I2C reference", DEFAULT_REFERENCE.read_text(encoding="utf-8"), "seed"))

    passing_dir = memory_dir / "passing"
    if passing_dir.is_dir():
        for rtl_path in sorted(passing_dir.glob("*.v")):
            meta_path = rtl_path.with_suffix(".json")
            if not meta_path.is_file():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if meta.get("verified_pass") is True:
                code = rtl_path.read_text(encoding="utf-8")
                actual_digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
                # Memory is an input to code generation: reject stale/tampered
                # sidecars rather than treating a modified artifact as proven.
                if meta.get("sha256") == actual_digest and rtl_path.stem == actual_digest[:16]:
                    examples.append((rtl_path.stem, code, "learned"))
    return examples


def retrieve_rtl_context(
    query: str,
    max_examples: int = 1,
    max_chars: int = 10000,
    memory_dir: str | Path | None = None,
) -> str:
    """Retrieve the most relevant verified examples and return prompt-ready text.

    Set PROTOFORGE_NO_RETRIEVAL=1 to disable retrieval (ablation). The seed
    example is derived from the known-good oracle, so with retrieval on a pass
    partly measures copying it; with retrieval off it measures generation.
    """
    if os.getenv("PROTOFORGE_NO_RETRIEVAL", "").strip().lower() in {"1", "true", "yes", "on"}:
        return "Retrieval is disabled for this run; no reference examples are provided. Work only from the spec above."
    root = Path(memory_dir) if memory_dir else DEFAULT_MEMORY_DIR
    examples = _load_successful_examples(root)
    if not examples or max_examples <= 0:
        return "No previously verified RTL examples are available."

    query_tokens = _tokens(query)
    ranked = []
    for label, code, origin in examples:
        score = _similarity(query_tokens, code)
        # Passing artifacts discovered through this project are more useful
        # than the generic seed once enough examples have accumulated.
        if origin == "learned":
            score += 0.08
        ranked.append((score, origin, label, code))
    ranked.sort(key=lambda item: (-item[0], item[1] != "learned", item[2]))

    sections = [
        "RETRIEVED VERIFIED EXAMPLE(S) — use as behavioral guidance, not as text to copy blindly.",
        "Preserve the requested wrapper and generated device instances. Check every example against the current spec; never copy testbench-only constructs, `initial`, `wait`, or delay controls.",
    ]
    used = 0
    for score, origin, label, code in ranked:
        if used >= max_examples:
            break
        header = f"\n--- {origin} example: {label} (retrieval score {score:.3f}) ---\n"
        remaining = max_chars - sum(len(s) for s in sections) - len(header)
        if remaining < 500:
            break
        snippet = code.strip()
        if len(snippet) > remaining:
            snippet = snippet[:remaining].rsplit("\n", 1)[0]
            if "endmodule" not in snippet:
                snippet += "\n// [retrieved example truncated to fit the context budget]"
        sections.extend([header, snippet])
        used += 1
    return "\n".join(sections)


def memory_summary(memory_dir: str | Path | None = None) -> dict:
    """Return a simple count useful for logs and CLI diagnostics."""
    root = Path(memory_dir) if memory_dir else DEFAULT_MEMORY_DIR
    passing_dir = root / "passing"
    learned = len(_load_successful_examples(root)) - int(DEFAULT_REFERENCE.is_file())
    learned = max(learned, 0)
    return {"seed_examples": int(DEFAULT_REFERENCE.is_file()), "learned_passing_examples": learned}


def record_failed_example(
    code: str,
    model: str | None,
    stage: str,
    failed_checks: list[str] | None = None,
    detail: str = "",
    memory_dir: str | Path | None = None,
) -> Path:
    """Archive a failed artifact for diagnosis without ever retrieving it as a positive example."""
    root = Path(memory_dir) if memory_dir else DEFAULT_MEMORY_DIR
    failures_dir = root / "failed"
    failures_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    rtl_path = failures_dir / f"{digest[:16]}.v"
    if not rtl_path.exists():
        rtl_path.write_text(code.rstrip() + "\n", encoding="utf-8")
        metadata = {
            "sha256": digest,
            "verified_pass": False,
            "model": model,
            "created_at": time.time(),
            "stage": stage,
            "failed_checks": failed_checks or [],
            "detail": detail[:4000],
        }
        rtl_path.with_suffix(".json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return rtl_path
