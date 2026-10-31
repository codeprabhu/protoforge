"""I2C Protocol Domain Adapter for ProtoForge."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.schemas import CandidatePatch, Requirement, SourceReference, Task, ValidationResult
from domains.base import BaseDomainAdapter
from domains.registry import register_domain

# Reuse existing verified tools and generators
from agent.rtl_generator import (
    generate_multi_device_glue_rtl,
    repair_rtl,
)
from tools.verilog_parser import parse_verilog_file
from tools.i2c_spec import parse_i2c_spec, I2CSpec
from tools.compile_tool import compile_verilog
from tools.error_context import extract_error_context
from tools.hardware_checks import run_hardware_checks
from tools.simulation_tool import run_simulation, parse_result_line
from tools.judge import judge, explain_failures
from tools.structural_checks import structural_checks, explain_structural_failures


class I2CDomainAdapter(BaseDomainAdapter):
    """Domain adapter for I2C protocol multi-device glue RTL generation and verification."""

    DEFAULT_DEVICES = ["devices/device_sensor.v", "devices/device_controller.v"]
    DEFAULT_TB = "protocols/i2c/testbench_i2c.v"
    DEFAULT_SPEC = "protocols/i2c/spec.md"
    DEFAULT_REQUIRED_INSTANCES = ["device_sensor", "device_controller"]

    def __init__(
        self,
        device_paths: Optional[List[str]] = None,
        tb_path: Optional[str] = None,
        spec_path: Optional[str] = None,
        required_instances: Optional[List[str]] = None,
    ):
        self.device_paths = device_paths or list(self.DEFAULT_DEVICES)
        self.tb_path = tb_path or self.DEFAULT_TB
        self.spec_path = spec_path or self.DEFAULT_SPEC
        self.required_instances = required_instances or list(self.DEFAULT_REQUIRED_INSTANCES)

    @property
    def name(self) -> str:
        return "i2c"

    @property
    def description(self) -> str:
        return "I2C protocol multi-device glue RTL generation and verification harness"

    def parse_requirements(self, spec_input: Any) -> List[Requirement]:
        """Parse I2C specification text or file into formal requirements."""
        if isinstance(spec_input, str):
            if os.path.exists(spec_input):
                spec = parse_i2c_spec(spec_input)
            else:
                # Treat as in-memory spec string
                import tempfile
                with tempfile.NamedTemporaryFile("w", delete=False) as f:
                    f.write(spec_input)
                    temp_name = f.name
                try:
                    spec = parse_i2c_spec(temp_name)
                finally:
                    if os.path.exists(temp_name):
                        os.unlink(temp_name)
        elif isinstance(spec_input, I2CSpec):
            spec = spec_input
        else:
            spec = parse_i2c_spec(self.spec_path)

        reqs = [
            Requirement(
                req_id="I2C-ADDR",
                domain="i2c",
                title="I2C Slave Address Matching",
                description=f"Transaction must target 7-bit slave address 0x{spec.slave_address:02x} (write address byte 0x{spec.address_hex})",
                severity="must",
            ),
            Requirement(
                req_id="I2C-DATA",
                domain="i2c",
                title="I2C Data Payload",
                description=f"Transaction must transmit data byte 0x{spec.data_hex}",
                severity="must",
            ),
            Requirement(
                req_id="I2C-START",
                domain="i2c",
                title="I2C START Condition",
                description="Must generate a valid START condition (SDA falls while SCL is high) before address transmission",
                severity="must",
            ),
            Requirement(
                req_id="I2C-STOP",
                domain="i2c",
                title="I2C STOP Condition",
                description="Must generate a valid STOP condition (SDA rises while SCL is high) after data transmission",
                severity="must",
            ),
            Requirement(
                req_id="I2C-ACK",
                domain="i2c",
                title="I2C Acknowledge Phase",
                description="Master must sample slave ACK bit on 9th clock pulse of address and data phases",
                severity="must",
            ),
            Requirement(
                req_id="I2C-OPEN-DRAIN",
                domain="i2c",
                title="I2C Open-Drain Bus Driving",
                description="SDA and SCL must be driven as open-drain (drive 0 or release to high-Z, never drive active 1)",
                severity="must",
            ),
        ]
        return reqs

    def index_sources(self, source_paths: List[str]) -> List[SourceReference]:
        """Index device modules, interfaces, and testbench."""
        refs: List[SourceReference] = []
        paths_to_index = source_paths or [*self.device_paths, self.tb_path]
        for p in paths_to_index:
            if not os.path.exists(p):
                continue
            with open(p, encoding="utf-8") as f:
                content = f.read()
            try:
                mod_info = parse_verilog_file(p)
                symbol_name = mod_info.name
                inputs = [port.name for port in mod_info.inputs]
                outputs = [port.name for port in mod_info.outputs]
                inouts = [port.name for port in mod_info.inouts]
            except Exception:
                symbol_name = Path(p).stem
                inputs, outputs, inouts = [], [], []

            refs.append(
                SourceReference(
                    file_path=p,
                    symbol=symbol_name,
                    metadata={
                        "inputs": inputs,
                        "outputs": outputs,
                        "inouts": inouts,
                    },
                )
            )
        return refs

    def build_context(self, task: Task) -> Dict[str, Any]:
        """Assemble domain-specific context bundle for generation or repair."""
        spec_path = task.metadata.get("spec_path", self.spec_path)
        parsed_spec = parse_i2c_spec(spec_path) if os.path.exists(spec_path) else None
        return {
            "domain": "i2c",
            "device_paths": self.device_paths,
            "tb_path": self.tb_path,
            "spec_path": spec_path,
            "address_hex": parsed_spec.address_hex if parsed_spec else "a0",
            "data_hex": parsed_spec.data_hex if parsed_spec else "3c",
            "required_instances": self.required_instances,
        }

    def generate_candidate(self, task: Task, context: Dict[str, Any], **kwargs) -> CandidatePatch:
        """Generate candidate RTL using the multi-device glue generator."""
        out_path = kwargs.get("out_path", task.metadata.get("out_path", "protocols/i2c/generated/top_glue.v"))
        generate_fn = kwargs.get("generate_fn", generate_multi_device_glue_rtl)
        model = kwargs.get("model", task.metadata.get("model"))
        temperature = kwargs.get("temperature", 0.0)
        seed = kwargs.get("seed")

        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        generate_fn(
            self.device_paths,
            context.get("spec_path", self.spec_path),
            out_path=out_path,
            model=model,
            temperature=temperature,
            seed=seed,
        )

        content = ""
        if os.path.exists(out_path):
            with open(out_path, encoding="utf-8") as f:
                content = f.read()

        return CandidatePatch(
            target_file=out_path,
            full_content=content,
            author_role="implementer",
        )

    def validate_structure(self, candidate: CandidatePatch) -> ValidationResult:
        """Run structural checks for module contract, ports, open-drain and required instances."""
        code = candidate.full_content or ""
        struct = structural_checks(code, self.required_instances)
        failed_checks = struct.get("failed_checks", [])
        return ValidationResult(
            stage="structural",
            passed=struct.get("pass", False),
            failed_checks=failed_checks,
            diagnostics=explain_structural_failures(failed_checks, self.required_instances) if failed_checks else "",
            metadata=struct,
        )

    def build_and_test(self, candidate: CandidatePatch, **kwargs) -> List[ValidationResult]:
        """Compile RTL, run hardware checks, and run simulation."""
        results: List[ValidationResult] = []
        out_path = candidate.target_file
        code = candidate.full_content or ""
        sim_out = kwargs.get("sim_out", "protocols/i2c/generated/top_sim.vvp")

        # Compile step
        compile_fn = kwargs.get("compile_fn", compile_verilog)
        compile_res = compile_fn([*self.device_paths, out_path, self.tb_path], out=sim_out)
        compile_val = ValidationResult(
            stage="compile",
            passed=compile_res.get("success", False),
            tool_missing=compile_res.get("tool_missing", False),
            stdout=compile_res.get("stdout", ""),
            stderr=compile_res.get("stderr", ""),
            diagnostics=compile_res.get("stderr", ""),
        )
        results.append(compile_val)
        if not compile_val.passed:
            return results

        # Hardware check step (Verilator / Yosys)
        hw_fn = kwargs.get("hardware_check_fn", run_hardware_checks)
        hw_res = hw_fn([*self.device_paths, out_path])
        hw_passed = hw_res.get("success", False)
        hw_tool_missing = hw_res.get("stage", "").endswith("tool_missing")
        hw_val = ValidationResult(
            stage="hardware",
            passed=hw_passed,
            tool_missing=hw_tool_missing,
            failed_checks=[hw_res.get("stage", "hardware")] if not hw_passed else [],
            diagnostics=str(hw_res),
            metadata=hw_res,
        )
        results.append(hw_val)
        if not hw_val.passed and hw_tool_missing:
            return results

        # Simulation step (Icarus vvp)
        sim_fn = kwargs.get("sim_fn", run_simulation)
        spec = parse_i2c_spec(self.spec_path)
        plusargs = [f"+I2C_ADDR={spec.address_hex}", f"+I2C_DATA={spec.data_hex}"]
        sim_res = sim_fn(sim_out, plusargs=plusargs)
        sim_passed = sim_res.get("success", False)
        sim_tool_missing = sim_res.get("tool_missing", False)
        sim_val = ValidationResult(
            stage="simulation",
            passed=sim_passed,
            tool_missing=sim_tool_missing,
            stdout=sim_res.get("stdout", ""),
            stderr=sim_res.get("stderr", ""),
            metadata={"raw": parse_result_line(sim_res.get("stdout", ""))},
        )
        results.append(sim_val)
        return results

    def judge(self, validation_results: List[ValidationResult], **kwargs) -> ValidationResult:
        """Evaluate simulation protocol output against I2C testbench oracles."""
        sim_val = next((r for r in validation_results if r.stage == "simulation"), None)
        if not sim_val or not sim_val.passed:
            failed = []
            if sim_val and sim_val.tool_missing:
                return ValidationResult(stage="judge", passed=False, tool_missing=True, diagnostics="Simulation tool missing")
            return ValidationResult(stage="judge", passed=False, failed_checks=["simulation_failed"], diagnostics="Simulation failed or was not run")

        raw_result = sim_val.metadata.get("raw", {})
        spec = parse_i2c_spec(self.spec_path)
        judge_fn = kwargs.get("judge_fn", judge)
        verdict = judge_fn(raw_result, expected_addr=spec.address_hex, expected_data=spec.data_hex)
        failed_checks = list(verdict.get("failed_checks") or [])
        return ValidationResult(
            stage="judge",
            passed=verdict.get("pass", False),
            failed_checks=failed_checks,
            diagnostics=explain_failures(failed_checks) if failed_checks else "",
            metadata=verdict,
        )

    def repair_candidate(
        self,
        candidate: CandidatePatch,
        failed_results: List[ValidationResult],
        **kwargs
    ) -> CandidatePatch:
        """Execute repair on candidate RTL with error-specific feedback."""
        out_path = candidate.target_file
        code = candidate.full_content or ""
        repair_fn = kwargs.get("repair_fn", repair_rtl)
        model = kwargs.get("model")
        temperature = kwargs.get("temperature", 0.3)
        seed = kwargs.get("seed")

        spec = parse_i2c_spec(self.spec_path)
        latest_failure = failed_results[-1]
        summary = f"failed {latest_failure.stage} checks"
        detail = latest_failure.diagnostics or latest_failure.stderr

        repair_fn(
            code,
            summary,
            detail,
            out_path=out_path,
            model=model,
            temperature=temperature,
            seed=seed,
            expected_addr=spec.address_hex,
            expected_data=spec.data_hex,
            device_paths=self.device_paths,
        )

        repaired_content = ""
        if os.path.exists(out_path):
            with open(out_path, encoding="utf-8") as f:
                repaired_content = f.read()

        return CandidatePatch(
            target_file=out_path,
            full_content=repaired_content,
            author_role="repair_agent",
        )

    def get_mutations(self) -> List[Dict[str, Any]]:
        """Known defect mutations for I2C bus testing."""
        return [
            {
                "id": "I2C-MUT-001",
                "name": "no_start",
                "fault": "Missing START condition on bus",
                "expected_failed_check": "start",
            },
            {
                "id": "I2C-MUT-002",
                "name": "no_stop",
                "fault": "Missing STOP condition on bus",
                "expected_failed_check": "stop",
            },
            {
                "id": "I2C-MUT-003",
                "name": "no_tb_done",
                "fault": "FSM fails to assert tb_done completion signal",
                "expected_failed_check": "tb_done",
            },
            {
                "id": "I2C-MUT-004",
                "name": "wrong_address",
                "fault": "Master drives wrong slave address byte",
                "expected_failed_check": "address_ack",
            },
            {
                "id": "I2C-MUT-005",
                "name": "wrong_data",
                "fault": "Master drives wrong data payload byte",
                "expected_failed_check": "correct_data",
            },
            {
                "id": "I2C-MUT-006",
                "name": "repeated_start",
                "fault": "Master issues spurious repeated START",
                "expected_failed_check": "single_start",
            },
        ]


# Automatically register I2C adapter
register_domain(I2CDomainAdapter())
