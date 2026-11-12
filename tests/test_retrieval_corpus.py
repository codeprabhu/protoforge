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


def test_multi_byte_query_prefers_the_two_byte_reference(tmp_path: Path) -> None:
    """The seeds tie lexically, so the payload length must break the tie.

    Before the hint, the 1-byte reference always won (label order) and every
    multi-byte prompt was shown a single-byte design.
    """
    from tools.rtl_memory import retrieve_rtl_examples

    multi = retrieve_rtl_examples(
        "I2C open-drain master FSM START address ACK 2 data bytes STOP Verilog-2001",
        max_examples=1,
        memory_dir=tmp_path,
        enabled=True,
        prefer_byte_count=2,
    )
    single = retrieve_rtl_examples(
        "I2C open-drain master FSM START address ACK data STOP Verilog-2001",
        max_examples=1,
        memory_dir=tmp_path,
        enabled=True,
        prefer_byte_count=1,
    )

    assert multi["examples"][0]["label"] == "validated I2C reference (2-byte)"
    assert single["examples"][0]["label"] == "validated I2C reference (1-byte)"


def test_three_byte_query_falls_back_to_the_two_byte_reference(tmp_path: Path) -> None:
    from tools.rtl_memory import retrieve_rtl_examples

    result = retrieve_rtl_examples(
        "I2C open-drain master FSM START address ACK 3 data bytes STOP Verilog-2001",
        max_examples=1,
        memory_dir=tmp_path,
        enabled=True,
        prefer_byte_count=3,
    )
    assert result["examples"][0]["label"] == "validated I2C reference (2-byte)"
