import re

MODULE_RE = re.compile(r"module\b[\s\S]*?endmodule", re.IGNORECASE)
CODE_BLOCK_RE = re.compile(r"```(?:\w+)?\s*([\s\S]*?)```", re.IGNORECASE)


def clean_rtl(text: str) -> str:
    """
    Extracts Verilog from an LLM response: prefers the largest fenced code
    block, falls back to grabbing module...endmodule spans if the model
    didn't fence its output.
    """
    text = text.strip()
    blocks = CODE_BLOCK_RE.findall(text)

    if blocks:
        text = max(blocks, key=len)
    else:
        modules = MODULE_RE.findall(text)
        if modules:
            text = "\n\n".join(modules)

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("`module", "module").replace("endmodule`", "endmodule")
    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
    return text.strip()
