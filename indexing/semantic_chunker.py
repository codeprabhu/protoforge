"""Semantic code chunker based on syntax units rather than fixed token slices."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class SemanticChunk(BaseModel):
    """A semantically bounded code unit (function, struct, module, macro)."""
    chunk_id: str
    file_path: str
    symbol_name: str
    chunk_type: str  # function, struct, module, macro, enum, typedef, block
    line_start: int
    line_end: int
    content: str
    dependencies: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SemanticChunker:
    """Extracts semantic chunks from C and Verilog source code."""

    # C regex patterns for top-level constructs
    C_FUNC_RE = re.compile(
        r"^(?:static\s+|inline\s+|__init\s+|__exit\s+)*"
        r"(?:(?:unsigned\s+|signed\s+|struct\s+|enum\s+)?\w+(?:\s*\*+)?)\s+"
        r"(\w+)\s*\([^;]*?\)\s*\{",
        re.MULTILINE,
    )
    C_STRUCT_RE = re.compile(r"^(?:typedef\s+)?struct\s+(\w+)\s*\{", re.MULTILINE)
    C_ENUM_RE = re.compile(r"^(?:typedef\s+)?enum\s+(\w+)\s*\{", re.MULTILINE)
    C_MACRO_RE = re.compile(r"^#define\s+(\w+)(?:\([^)]*\))?\s+(.*)$", re.MULTILINE)

    # Verilog regex patterns
    VERILOG_MODULE_RE = re.compile(r"^\s*module\s+(\w+)\s*(?:#\s*\([^)]*\))?\s*\(([\s\S]*?)\);", re.MULTILINE)

    def chunk_file(self, file_path: str, content: Optional[str] = None) -> List[SemanticChunk]:
        """Chunk a file into semantic units based on its language extension."""
        if content is None:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

        if file_path.endswith((".c", ".h")):
            return self.chunk_c(file_path, content)
        elif file_path.endswith((".v", ".sv", ".vh", ".svh")):
            return self.chunk_verilog(file_path, content)
        else:
            # Fallback block chunking
            return self._chunk_lines(file_path, content)

    def chunk_c(self, file_path: str, content: str) -> List[SemanticChunk]:
        """Extract functions, structs, and macros from C source."""
        chunks: List[SemanticChunk] = []
        lines = content.splitlines(keepends=True)

        # 1. Macros
        for m in self.C_MACRO_RE.finditer(content):
            macro_name = m.group(1)
            start_pos = m.start()
            line_start = content.count("\n", 0, start_pos) + 1
            line_end = line_start  # Macro definition line (or multiline with \)
            while line_end <= len(lines) and lines[line_end - 1].rstrip().endswith("\\"):
                line_end += 1
            macro_content = "".join(lines[line_start - 1 : line_end])
            chunks.append(
                SemanticChunk(
                    chunk_id=f"{file_path}::{macro_name}",
                    file_path=file_path,
                    symbol_name=macro_name,
                    chunk_type="macro",
                    line_start=line_start,
                    line_end=line_end,
                    content=macro_content.strip(),
                )
            )

        # 2. Structs
        for m in self.C_STRUCT_RE.finditer(content):
            struct_name = m.group(1)
            start_pos = m.start()
            line_start = content.count("\n", 0, start_pos) + 1
            # Find matching closing brace
            brace_idx = content.find("{", start_pos)
            if brace_idx != -1:
                end_pos = self._find_matching_brace(content, brace_idx)
                semicolon_pos = content.find(";", end_pos)
                if semicolon_pos != -1:
                    end_pos = semicolon_pos + 1
                line_end = content.count("\n", 0, end_pos) + 1
                struct_content = "".join(lines[line_start - 1 : line_end])
                chunks.append(
                    SemanticChunk(
                        chunk_id=f"{file_path}::struct_{struct_name}",
                        file_path=file_path,
                        symbol_name=struct_name,
                        chunk_type="struct",
                        line_start=line_start,
                        line_end=line_end,
                        content=struct_content.strip(),
                    )
                )

        # 3. Functions
        for m in self.C_FUNC_RE.finditer(content):
            func_name = m.group(1)
            if func_name in {"if", "while", "for", "switch"}:
                continue
            start_pos = m.start()
            line_start = content.count("\n", 0, start_pos) + 1
            brace_idx = content.find("{", start_pos)
            if brace_idx != -1:
                end_pos = self._find_matching_brace(content, brace_idx)
                line_end = content.count("\n", 0, end_pos) + 1
                func_content = "".join(lines[line_start - 1 : line_end])
                calls = self._extract_c_calls(func_content, func_name)
                chunks.append(
                    SemanticChunk(
                        chunk_id=f"{file_path}::{func_name}",
                        file_path=file_path,
                        symbol_name=func_name,
                        chunk_type="function",
                        line_start=line_start,
                        line_end=line_end,
                        content=func_content.strip(),
                        dependencies=calls,
                    )
                )

        return chunks

    def chunk_verilog(self, file_path: str, content: str) -> List[SemanticChunk]:
        """Extract Verilog modules and instance blocks."""
        chunks: List[SemanticChunk] = []
        lines = content.splitlines(keepends=True)

        # Match module ... endmodule
        mod_pattern = re.compile(r"^\s*module\s+(\w+)", re.MULTILINE)
        for m in mod_pattern.finditer(content):
            mod_name = m.group(1)
            start_pos = m.start()
            line_start = content.count("\n", 0, start_pos) + 1
            end_match = re.search(r"^\s*endmodule\b", content[start_pos:], re.MULTILINE)
            if end_match:
                end_pos = start_pos + end_match.end()
                line_end = content.count("\n", 0, end_pos) + 1
                mod_content = "".join(lines[line_start - 1 : line_end])

                # Extract sub-module instantiations inside this module
                insts = re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s+(?:#\s*\([^)]*\)\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", mod_content)
                ignored_keywords = {"module", "input", "output", "inout", "wire", "reg", "assign", "always", "initial"}
                sub_modules = [inst[0] for inst in insts if inst[0] not in ignored_keywords]

                chunks.append(
                    SemanticChunk(
                        chunk_id=f"{file_path}::{mod_name}",
                        file_path=file_path,
                        symbol_name=mod_name,
                        chunk_type="module",
                        line_start=line_start,
                        line_end=line_end,
                        content=mod_content.strip(),
                        dependencies=sub_modules,
                    )
                )

        return chunks

    def _find_matching_brace(self, text: str, open_idx: int) -> int:
        """Find index of closing brace matching the open brace at open_idx."""
        depth = 0
        in_string = False
        in_char = False
        escape = False
        for i in range(open_idx, len(text)):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == "\\":
                escape = True
                continue
            if c == '"' and not in_char:
                in_string = not in_string
                continue
            if c == "'" and not in_string:
                in_char = not in_char
                continue
            if not in_string and not in_char:
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        return i
        return len(text) - 1

    def _extract_c_calls(self, func_content: str, self_name: str) -> List[str]:
        """Find called function identifiers within C function body."""
        call_re = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")
        keywords = {
            "if", "while", "for", "switch", "return", "sizeof",
            "likely", "unlikely", "typeof", "__builtin_expect",
            "ERR_PTR", "PTR_ERR", "IS_ERR", "spin_lock", "spin_unlock"
        }
        calls = set()
        for m in call_re.finditer(func_content):
            name = m.group(1)
            if name != self_name and name not in keywords:
                calls.add(name)
        return sorted(list(calls))

    def _chunk_lines(self, file_path: str, content: str, chunk_size: int = 60) -> List[SemanticChunk]:
        """Fallback chunker for arbitrary files."""
        lines = content.splitlines()
        chunks: List[SemanticChunk] = []
        for i in range(0, len(lines), chunk_size):
            chunk_lines = lines[i : i + chunk_size]
            chunks.append(
                SemanticChunk(
                    chunk_id=f"{file_path}::lines_{i+1}_{i+len(chunk_lines)}",
                    file_path=file_path,
                    symbol_name=f"lines_{i+1}",
                    chunk_type="block",
                    line_start=i + 1,
                    line_end=i + len(chunk_lines),
                    content="\n".join(chunk_lines),
                )
            )
        return chunks
