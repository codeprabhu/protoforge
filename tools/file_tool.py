from pathlib import Path


def save_file(filepath: str, content: str) -> str:
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w") as f:
        f.write(content)
    return filepath
