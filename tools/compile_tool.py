import subprocess
from typing import List, Union


def compile_verilog(filepaths: Union[str, List[str]], out: str = "generated/sim.out") -> dict:
    """
    Compile one or more Verilog files together with Icarus Verilog.
    Needed once a top-level module instantiates other modules (devices +
    generated glue + testbench all have to be compiled together).
    """
    if isinstance(filepaths, str):
        filepaths = [filepaths]

    result = subprocess.run(
        ["iverilog", "-o", out, *filepaths],
        capture_output=True,
        text=True,
    )

    return {
        "success": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
