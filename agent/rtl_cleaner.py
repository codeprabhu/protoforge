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

    # --- markdown-escape cleanup ---
    # Some models over-apply markdown escaping (learned from markdown-heavy
    # training data where `word_word_word` would render as italics) even
    # inside fenced code blocks. This produces illegal Verilog like
    # `top\_glue`, `rst\_n`, or a stray `*` colliding with `//` comments
    # (`*// comment`). Strip these mechanically before saving.
    text = text.replace("\\_", "_")
    text = text.replace("\\*", "*")
    # a leading "*" directly touching a "//" comment marker is markdown bold
    # bleeding into a Verilog line comment -- drop the stray asterisk(s).
    text = re.sub(r"\*+(//)", r"\1", text)
    # trailing stray asterisks at end of a comment line, same cause
    text = re.sub(r"(//[^\n]*?)\*+\s*$", r"\1", text, flags=re.MULTILINE)

    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
    return text.strip()
