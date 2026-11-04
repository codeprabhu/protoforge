"""Phase 6: the regex-patcher ablation must be a real switch.

With the patcher on, model output is cleaned and coerced into the fixed
wrapper. With it off, the harness still supplies the ports, device instances
and open-drain drivers, but the response is embedded verbatim. These tests
prove the switch changes behaviour rather than being a no-op.
"""

from __future__ import annotations

from pathlib import Path

import agent.rtl_generator as generator
from agent_core.agent import run_once
from agent_core.config import Config

ROOT = Path(__file__).resolve().parents[1]
DEVICES = ["devices/device_sensor.v", "devices/device_controller.v"]


def test_config_reads_no_patcher_env(monkeypatch):
    monkeypatch.setenv("PROTOFORGE_NO_PATCHER", "1")
    assert Config.from_env().patcher is False
    monkeypatch.setenv("PROTOFORGE_NO_PATCHER", "0")
    assert Config.from_env().patcher is True
    monkeypatch.delenv("PROTOFORGE_NO_PATCHER")
    assert Config.from_env().patcher is True


def test_wrap_raw_fragment_keeps_response_verbatim(monkeypatch):
    monkeypatch.chdir(ROOT)
    raw = "```verilog\nreg marker_not_cleaned;\n```\n"
    rtl = generator.wrap_raw_fragment(DEVICES, raw)
    assert rtl.count("module top_glue") == 1
    assert "device_sensor u_device_a" in rtl
    # The harness wrapper is present, but the model text was not patched.
    assert "```verilog" in rtl and "reg marker_not_cleaned;" in rtl


def test_generation_patcher_off_embeds_the_raw_response(monkeypatch):
    monkeypatch.chdir(ROOT)
    raw = "```verilog\nreg marker_not_cleaned;\n```\n"
    monkeypatch.setattr(generator, "generate_verilog", lambda *a, **k: raw)

    patched = generator.generate_multi_device_glue_rtl(
        DEVICES, "protocols/i2c/spec.md", model="stub", patcher=True
    )
    unpatched = generator.generate_multi_device_glue_rtl(
        DEVICES, "protocols/i2c/spec.md", model="stub", patcher=False
    )

    assert "```verilog" not in patched
    assert "```verilog" in unpatched
    assert "reg marker_not_cleaned;" in unpatched


def test_generation_patcher_off_does_not_coerce_a_full_module(monkeypatch):
    monkeypatch.chdir(ROOT)
    full = """module top_glue(input wire clk, input wire rst_n, inout wire sda,
      inout wire scl, input wire tb_start, output reg tb_done);
      reg sda_oe;
      assign sda = sda_oe ? 1'b0 : 1'bz;
      always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin sda_oe <= 0; tb_done <= 0; end
        else if (tb_start) tb_done <= 1;
      end
    endmodule"""
    monkeypatch.setattr(generator, "generate_verilog", lambda *a, **k: full)

    patched = generator.generate_multi_device_glue_rtl(
        DEVICES, "protocols/i2c/spec.md", model="stub", patcher=True
    )
    unpatched = generator.generate_multi_device_glue_rtl(
        DEVICES, "protocols/i2c/spec.md", model="stub", patcher=False
    )

    assert patched.count("module top_glue") == 1
    # Without coercion the model's own module is nested inside the wrapper,
    # which is exactly the ablation: the patcher is what fixes this.
    assert unpatched.count("module top_glue") == 2


def test_repair_patcher_off_keeps_the_raw_response(monkeypatch):
    monkeypatch.chdir(ROOT)
    raw = "```verilog\nreg marker_not_cleaned;\n```\n"
    monkeypatch.setattr(generator, "generate_verilog", lambda *a, **k: raw)

    fixed = generator.repair_rtl(
        "module top_glue; endmodule",
        "failed simulate checks",
        "no STOP",
        device_paths=DEVICES,
        patcher=False,
    )
    assert "```verilog" in fixed
    assert "reg marker_not_cleaned;" in fixed


def test_run_once_propagates_the_patcher_switch(tmp_path):
    seen: dict = {}

    def fake_generate(devices, spec_path, **kwargs):
        seen.update(kwargs)
        return "reg state;\nalways @(posedge clk) state <= tb_start;\n"

    config = Config(
        output_root=str(tmp_path / "runs"),
        results_log=str(tmp_path / "runs.jsonl"),
        trace_dir=str(tmp_path / "traces"),
        max_repairs=0,
        patcher=False,
    )
    run_once(
        model="stub",
        config=config,
        generate_fn=fake_generate,
        on_success=lambda *a, **k: None,
        on_failure=lambda *a, **k: None,
    )
    assert seen["patcher"] is False
