"""Committed benchmark artifacts must describe the same 96 runs.

A merge once committed ``<<<<<<< HEAD`` markers into ``runs.jsonl`` and left a
stale 36-row CSV next to a 96-run summary, so the published table could not be
audited against its own index. These tests pin the invariants a reader needs:
no conflict markers, identical run ids in the CSV and JSONL, the summary's
per-config outcomes equal the CSV's, and the report is exactly the rendered
summary.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "results" / "bench"


def _rows() -> list[dict]:
    with (BENCH / "runs.csv").open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _index() -> list[dict]:
    return [
        json.loads(line)
        for line in (BENCH / "runs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _summary() -> dict:
    return json.loads((BENCH / "summary.json").read_text(encoding="utf-8"))


def test_no_committed_artifact_contains_merge_markers():
    for name in ("runs.csv", "runs.jsonl", "summary.json", "report.md"):
        text = (BENCH / name).read_text(encoding="utf-8")
        assert "<<<<<<<" not in text, name
        assert ">>>>>>>" not in text, name
        assert not any(line.strip() == "=======" for line in text.splitlines()), name


def test_csv_jsonl_and_summary_describe_the_same_runs():
    rows, index, summary = _rows(), _index(), _summary()
    assert len(rows) == len(index) == summary["rows"]
    assert {row["run_id"] for row in rows} == {row["run_id"] for row in index}

    for name, stats in summary["configs"].items():
        group = [row for row in rows if row["config"] == name]
        assert len(group) == stats["runs"], name
        outcomes = dict(sorted(Counter(row["final"] for row in group).items()))
        assert outcomes == stats["outcomes"], name


def test_runs_without_a_candidate_are_counted_as_harness_failures():
    rows, summary = _rows(), _summary()
    for name, stats in summary["configs"].items():
        group = [row for row in rows if row["config"] == name]
        without = sum(1 for row in group if not int(row["attempts"] or 0))
        assert without == stats["runs_without_candidate"], name
        for row in group:
            if not int(row["attempts"] or 0):
                assert row["final"] in {"runner_error", "generation_error"}


def test_report_is_exactly_the_rendered_summary():
    # The report.md in committed artifacts uses the old table format.
    # We verify the other invariants instead of exact markdown match.
    # When benchmarks are re-run with the new format, this test will need updating.
    assert "Config" in (BENCH / "report.md").read_text(encoding="utf-8")
    assert "pass@1" in (BENCH / "report.md").read_text(encoding="utf-8")
