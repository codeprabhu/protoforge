"""Thin Ollama client with stable, configurable defaults for RTL generation."""

import os

DEFAULT_MODEL = os.getenv("PROTOFORGE_MODEL", "qwen2.5-coder:7b")
DEFAULT_TEMPERATURE = float(os.getenv("PROTOFORGE_TEMPERATURE", "0"))
DEFAULT_SEED = int(os.getenv("PROTOFORGE_SEED", "42"))
DEFAULT_NUM_CTX = int(os.getenv("PROTOFORGE_NUM_CTX", "8192"))
DEFAULT_NUM_PREDICT = int(os.getenv("PROTOFORGE_NUM_PREDICT", "4096"))


def generate_verilog(
    prompt: str,
    model: str = DEFAULT_MODEL,
    temperature: float = None,
    seed: int = DEFAULT_SEED,
) -> str:
    """Generate RTL with a stable default sampling configuration.

    Ollama is imported lazily so the orchestration, validators, and tests can
    run without the optional local inference runtime being installed.
    """
    try:
        import ollama
    except ImportError as exc:
        raise RuntimeError(
            "The Ollama Python package is missing. Install project dependencies "
            "with `python -m pip install -r requirements.txt`, then ensure the "
            "Ollama service is running and the selected model has been pulled."
        ) from exc

    options = {
        "temperature": DEFAULT_TEMPERATURE if temperature is None else temperature,
        "seed": seed,
        "num_ctx": DEFAULT_NUM_CTX,
        "num_predict": DEFAULT_NUM_PREDICT,
    }
    response = ollama.chat(
        model=model or DEFAULT_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a careful RTL engineer. Return valid synthesizable "
                    "Verilog-2001, preserve the specified module interface, and "
                    "prefer explicit clocked FSMs over delays or testbench constructs."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        options=options,
    )
    return response["message"]["content"]
