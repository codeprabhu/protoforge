import ollama

DEFAULT_MODEL = "qwen2.5-coder:7b"  # swap for hermes4/deepseek-coder/etc as needed


def generate_verilog(prompt: str, model: str = DEFAULT_MODEL, temperature: float = None) -> str:
    options = {}
    if temperature is not None:
        options["temperature"] = temperature
    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        options=options,
    )
    return response["message"]["content"]
