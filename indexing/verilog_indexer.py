"""Verilog and SystemVerilog AST/syntax indexer."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class VerilogPortInfo(BaseModel):
    name: str
    direction: str  # input, output, inout
    width: str = ""
    is_clock: bool = False
    is_reset: bool = False


class VerilogInstanceInfo(BaseModel):
    module_type: str
    instance_name: str
    connections: Dict[str, str] = Field(default_factory=dict)


class VerilogModuleInfo(BaseModel):
    name: str
    file_path: str
    line_start: int
    line_end: int
    ports: List[VerilogPortInfo] = Field(default_factory=list)
    parameters: Dict[str, str] = Field(default_factory=dict)
    instances: List[VerilogInstanceInfo] = Field(default_factory=list)
    clock_nets: List[str] = Field(default_factory=list)
    reset_nets: List[str] = Field(default_factory=list)
    open_drain_drivers: List[Dict[str, str]] = Field(default_factory=list)
    state_machine_states: List[str] = Field(default_factory=list)


class VerilogIndexer:
    """Extracts ports, parameters, instances, clock/reset nets, open-drain and FSM encodings."""

    MODULE_RE = re.compile(r"^\s*module\s+(\w+)\s*(?:#\s*\(([\s\S]*?)\))?\s*\(([\s\S]*?)\);", re.MULTILINE)
    PORT_DECL_RE = re.compile(r"(input|output|inout)\s+(?:wire\s+|reg\s+)?(?:\[([^\]]+)\]\s+)?([a-zA-Z_][a-zA-Z0-9_]*)")
    PARAM_RE = re.compile(r"\b(?:parameter|localparam)\s+(?:\[[^\]]+\]\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*([^;,\)]+)")
    OPEN_DRAIN_RE = re.compile(r"assign\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*([^\?]+)\?\s*([^:]+)\s*:\s*([^;]+);")
    INSTANCE_RE = re.compile(r"([a-zA-Z_][a-zA-Z0-9_]*)\s+(?:#\s*\(([\s\S]*?)\)\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s*\(([\s\S]*?)\);")

    def index_file(self, file_path: str, content: Optional[str] = None) -> List[VerilogModuleInfo]:
        """Index all Verilog modules in a given source file."""
        if content is None:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

        modules: List[VerilogModuleInfo] = []
        lines = content.splitlines(keepends=True)

        mod_starts = list(re.finditer(r"^\s*module\s+(\w+)", content, re.MULTILINE))
        for i, m_start in enumerate(mod_starts):
            mod_name = m_start.group(1)
            start_pos = m_start.start()
            line_start = content.count("\n", 0, start_pos) + 1

            # Find matching endmodule
            end_match = re.search(r"^\s*endmodule\b", content[start_pos:], re.MULTILINE)
            if not end_match:
                continue
            end_pos = start_pos + end_match.end()
            line_end = content.count("\n", 0, end_pos) + 1
            mod_content = content[start_pos:end_pos]

            # 1. Ports
            ports: List[VerilogPortInfo] = []
            clocks = []
            resets = []

            for p_match in self.PORT_DECL_RE.finditer(mod_content):
                direction = p_match.group(1)
                width = p_match.group(2) or ""
                p_name = p_match.group(3)
                is_clk = any(c in p_name.lower() for c in ("clk", "clock"))
                is_rst = any(r in p_name.lower() for r in ("rst", "reset"))
                if is_clk:
                    clocks.append(p_name)
                if is_rst:
                    resets.append(p_name)
                ports.append(
                    VerilogPortInfo(
                        name=p_name,
                        direction=direction,
                        width=width,
                        is_clock=is_clk,
                        is_reset=is_rst,
                    )
                )

            # 2. Parameters / Localparams / FSM states
            params: Dict[str, str] = {}
            fsm_states: List[str] = []
            fsm_keywords = {"state", "idle", "start", "addr", "data", "ack", "stop", "done", "wait"}
            for param_match in self.PARAM_RE.finditer(mod_content):
                param_name = param_match.group(1)
                param_val = param_match.group(2).strip()
                params[param_name] = param_val
                if any(kw in param_name.lower() for kw in fsm_keywords):
                    fsm_states.append(param_name)

            # 3. Sub-module instantiations
            instances: List[VerilogInstanceInfo] = []
            ignored_types = {"module", "input", "output", "inout", "wire", "reg", "assign", "always", "initial"}
            for inst_match in self.INSTANCE_RE.finditer(mod_content):
                inst_type = inst_match.group(1)
                inst_name = inst_match.group(3)
                conns_raw = inst_match.group(4)
                if inst_type in ignored_types or inst_type == mod_name:
                    continue

                connections: Dict[str, str] = {}
                conn_matches = re.findall(r"\.([a-zA-Z_][a-zA-Z0-9_]*)\s*\(\s*([^)]*)\s*\)", conns_raw)
                for port_k, net_v in conn_matches:
                    connections[port_k] = net_v.strip()

                instances.append(
                    VerilogInstanceInfo(
                        module_type=inst_type,
                        instance_name=inst_name,
                        connections=connections,
                    )
                )

            # 4. Open-drain driver detection
            open_drains: List[Dict[str, str]] = []
            for od in self.OPEN_DRAIN_RE.finditer(mod_content):
                target_wire = od.group(1)
                cond = od.group(2).strip()
                val_true = od.group(3).strip()
                val_false = od.group(4).strip()
                if "1'bz" in val_true or "1'bz" in val_false:
                    open_drains.append({
                        "wire": target_wire,
                        "condition": cond,
                        "true_expr": val_true,
                        "false_expr": val_false,
                    })

            modules.append(
                VerilogModuleInfo(
                    name=mod_name,
                    file_path=file_path,
                    line_start=line_start,
                    line_end=line_end,
                    ports=ports,
                    parameters=params,
                    instances=instances,
                    clock_nets=sorted(list(set(clocks))),
                    reset_nets=sorted(list(set(resets))),
                    open_drain_drivers=open_drains,
                    state_machine_states=fsm_states,
                )
            )

        return modules
