import ollama

DEFAULT_MODEL = "qwen2.5-coder:7b"  # swap for hermes4/deepseek-coder/etc as needed


def generate_verilog(prompt: str, model: str = DEFAULT_MODEL) -> str:
    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    return response["message"]["content"]
