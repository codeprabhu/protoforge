"""Tests for Phase 3 mutation, fault-injection and oracle evaluation framework."""

import json
from pathlib import Path
import pytest
from mutations import (
    Mutation,
    MutationRunner,
    apply_mutation,
    create_mutant,
    generate_applicable_mutants,
    get_catalogue,
    I2C_CATALOGUE,
    PCIE_CATALOGUE,
)


def test_mutation_catalogues():
    i2c_cat = get_catalogue("i2c")
    assert i2c_cat.domain == "i2c"
    assert len(i2c_cat.mutations) >= 5

    pci_cat = get_catalogue("pcie_linux")
    assert pci_cat.domain == "pcie_linux"
    assert len(pci_cat.mutations) >= 8

    pci_ids = [m.mutation_id for m in pci_cat.mutations]
    assert "PCI-001" in pci_ids
    assert "PCI-002" in pci_ids
    assert "PCI-003" in pci_ids
    assert "PCI-007" in pci_ids


def test_apply_mutation_and_immutable_reference():
    original_code = "localparam ADDR_BYTE = 8'hA0;\nassign sda = 1'bz;"
    mutation = Mutation(
        mutation_id="TEST-001",
        domain="test",
        title="Bad Address",
        description="Corrupt address",
        fault_type="wrong_constant",
        target_pattern="8'hA0",
        replacement="8'hA2",
        expected_failed_stage="simulate",
        expected_failed_check="address_ack",
    )

    mutated = apply_mutation(original_code, mutation)
    assert "8'hA2" in mutated
    assert "8'hA0" not in mutated
    # Ensure original string is completely unchanged
    assert "8'hA0" in original_code


def test_create_mutant_and_hashes():
    original_code = "int probe(struct pci_dev *pdev) { pci_iomap(pdev, 0, 0); return 0; }"
    mutation = Mutation(
        mutation_id="PCI-001",
        domain="pcie_linux",
        title="Wrong BAR",
        description="Map BAR 1 instead of 0",
        fault_type="wrong_offset",
        target_pattern="pci_iomap(pdev, 0, 0)",
        replacement="pci_iomap(pdev, 1, 0)",
        expected_failed_stage="kunit",
        expected_failed_check="bar_mapping",
    )

    mutant = create_mutant("drivers/pcie.c", mutation, source_text=original_code)
    assert mutant.mutant_id.startswith("MUTANT-PCI-001-")
    assert mutant.original_hash != mutant.mutated_hash
    assert "pci_iomap(pdev, 1, 0)" in mutant.mutated_content


def test_mutation_runner_and_kill_rate(tmp_path):
    golden_rtl = open("protocols/i2c/reference/good_master_reset_safe.v").read()

    mutants = generate_applicable_mutants(
        "protocols/i2c/reference/good_master_reset_safe.v",
        I2C_CATALOGUE,
        source_text=golden_rtl,
    )
    assert len(mutants) >= 4

    # Build simulated oracle that checks for known mutants
    def mock_oracle(code: str) -> dict:
        failed = []
        if "sda_oe   <= 0; // MUTANT: omitted START" in code:
            failed.append("start")
        if "sda_oe <= 1; // MUTANT: keep SDA low, never STOP" in code:
            failed.append("stop")
        if "tb_done <= 0;" in code:
            failed.append("tb_done")
        if "localparam ADDR_BYTE = 8'hA2;" in code:
            failed.append("address_ack")
        if "localparam DATA_BYTE = 8'h00;" in code:
            failed.append("correct_data")
        if "assign sda = sda_oe ? 1'b1 : 1'b0;" in code:
            failed.append("legal_open_drain")

        return {
            "pass": len(failed) == 0,
            "failed_checks": failed,
            "stage": "simulate",
        }

    runner = MutationRunner()
    summary = runner.run_suite(mutants, mock_oracle)

    assert summary["total_mutants"] == len(mutants)
    assert summary["killed_mutants"] == len(mutants)
    assert summary["survived_mutants"] == 0
    assert summary["mutation_score_pct"] == 100.0

    out_file = tmp_path / "mutation_results.json"
    runner.save_results(summary, str(out_file))
    assert out_file.exists()

    saved_data = json.loads(out_file.read_text())
    assert saved_data["mutation_score_pct"] == 100.0
