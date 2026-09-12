import re

# Matches iverilog's "path:LINE: message" or "path:LINE: error: message" format
ERROR_LOC_RE = re.compile(r"([^\s:]+\.v):(\d+):\s*(.*)")


def extract_error_context(source_path: str, source_text: str, stderr: str, context_lines: int = 2) -> str:
    """
    iverilog error messages like "generated/top_glue.v:43: syntax error"
    are nearly useless to a small local model on their own -- it has to
    re-derive which lines that refers to. This pulls the actual source
    lines (+/- context_lines) for every error that points at source_path,
    labeled with line numbers, so the repair prompt shows the model
    exactly what it wrote instead of an abstract line number.

    Errors pointing at OTHER files (e.g. the testbench) are omitted here
    since the model can't fix a file it doesn't control.
    """
    lines = source_text.split("\n")
    seen_line_numbers = set()
    blocks = []

    for match in ERROR_LOC_RE.finditer(stderr):
        file_ref, line_no_str, message = match.groups()
        if not source_path.endswith(file_ref) and not file_ref.endswith(source_path):
            continue  # error is in a different file (e.g. the testbench) -- skip
        line_no = int(line_no_str)
        if line_no in seen_line_numbers:
            continue
        seen_line_numbers.add(line_no)

        start = max(0, line_no - 1 - context_lines)
        end = min(len(lines), line_no + context_lines)
        snippet_lines = [f"{i + 1}: {lines[i]}" for i in range(start, end)]
        blocks.append(f"Error at line {line_no} ({message.strip()}):\n" + "\n".join(snippet_lines))

    if not blocks:
        return "(no line-specific errors in your file were found -- see the full compiler output above)"
    return "\n\n".join(blocks)
