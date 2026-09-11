from mcp.server.fastmcp import FastMCP

from tools.compile_tool import compile_verilog
from tools.verilator_tool import lint_verilog
from tools.simulation_tool import run_simulation, parse_result_line
from tools.judge import judge, explain_failures
from agent_core.agent import run_once

mcp = FastMCP("ProtoForge")


@mcp.tool()
def compile_rtl(filepaths):
    """Compile one or more Verilog files together using Icarus Verilog."""
    return compile_verilog(filepaths)


@mcp.tool()
def lint_rtl(filepath: str):
    """Lint a Verilog file using Verilator."""
    return lint_verilog(filepath)


@mcp.tool()
def simulate_and_judge(executable: str):
    """Run a compiled simulation and judge it against the I2C protocol checks."""
    sim = run_simulation(executable)
    result = parse_result_line(sim["stdout"])
    verdict = judge(result)
    if not verdict["pass"]:
        verdict["explanation"] = explain_failures(verdict["failed_checks"])
    return verdict


@mcp.tool()
def run_pipeline(model: str = None):
    """Run the full generate -> compile -> repair -> simulate -> judge pipeline once."""
    return run_once(model=model)


if __name__ == "__main__":
    print("Starting ProtoForge MCP Server...")
    mcp.run()
