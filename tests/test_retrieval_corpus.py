"""Tests for retrieval corpus provenance and example counting."""

from __future__ import annotations

from pathlib import Path

from tools.rtl_memory import (
    memory_summary,
    record_passing_example,
    retrieval_corpus_hash,
)


def _seed_memory(root: Path) -> None:
    """Create a memory dir with one learned verified example."""
    record_passing_example(
        "module top_glue;\n  // verified mock design\nendmodule\n",
        model="mock-model",
        metadata={"case": "c-mock"},
        memory_dir=root,
    )


def test_corpus_hash_is_deterministic(tmp_path: Path) -> None:
    _seed_memory(tmp_path)
    first = retrieval_corpus_hash(tmp_path)
    second = retrieval_corpus_hash(tmp_path)

    assert first == second
    assert len(first) == 64
    int(first, 16)


def test_corpus_hash_changes_when_learned_example_changes(tmp_path: Path) -> None:
    _seed_memory(tmp_path)
    before = retrieval_corpus_hash(tmp_path)

    record_passing_example(
        "module top_glue;\n  // a different verified mock design\nendmodule\n",
        model="mock-model",
        memory_dir=tmp_path,
    )

    assert retrieval_corpus_hash(tmp_path) != before


def test_empty_memory_has_stable_hash(tmp_path: Path) -> None:
    assert retrieval_corpus_hash(tmp_path) == retrieval_corpus_hash(tmp_path)


def test_memory_summary_counts_both_seed_references(tmp_path: Path) -> None:
    """Both checked-in references are seed examples, not just the 1-byte one."""
    _seed_memory(tmp_path)
    summary = memory_summary(tmp_path)

    assert summary["seed_examples"] == 2
    assert summary["learned_passing_examples"] == 1
