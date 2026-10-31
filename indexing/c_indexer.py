"""C source and Linux driver symbol indexer with AST and pattern extraction."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field


class CSymbolInfo(BaseModel):
    """Extracted C symbol metadata."""
    name: str
    symbol_type: str  # function, struct, macro, typedef, driver_table
    return_type: Optional[str] = None
    parameters: List[str] = Field(default_factory=list)
    fields: List[str] = Field(default_factory=list)
    file_path: str
    line_start: int
    line_end: int
    calls: List[str] = Field(default_factory=list)
    called_by: List[str] = Field(default_factory=list)
    is_driver_lifecycle: bool = False
    is_dma_op: bool = False
    is_bar_op: bool = False
    is_irq_op: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CIndexer:
    """Indexes C source files, driver lifecycle hooks, and hardware resource interactions."""

    LIFECYCLE_KEYWORDS = {"probe", "remove", "shutdown", "suspend", "resume"}
    DMA_KEYWORDS = {"dma_alloc", "dma_free", "dma_map", "dma_unmap", "dma_sync", "pci_alloc_consistent", "pci_free_consistent"}
    BAR_KEYWORDS = {"pci_iomap", "pci_iounmap", "ioremap", "iounmap", "pci_resource_start", "pci_resource_len", "pci_request_region"}
    IRQ_KEYWORDS = {"request_irq", "free_irq", "pci_alloc_irq_vectors", "pci_free_irq_vectors", "request_threaded_irq"}

    def __init__(self, kernel_version: Optional[str] = None, include_paths: Optional[List[str]] = None):
        self.kernel_version = kernel_version
        self.include_paths = include_paths or []

    def index_file(self, file_path: str, content: Optional[str] = None) -> List[CSymbolInfo]:
        """Index symbols, structures, functions, and hardware calls in a C file."""
        if content is None:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

        symbols: List[CSymbolInfo] = []
        lines = content.splitlines(keepends=True)

        # 1. Struct indexing
        struct_re = re.compile(r"struct\s+(\w+)\s*\{([^}]*)\}\s*;", re.MULTILINE)
        for m in struct_re.finditer(content):
            s_name = m.group(1)
            body = m.group(2)
            start_pos = m.start()
            line_start = content.count("\n", 0, start_pos) + 1
            line_end = content.count("\n", 0, m.end()) + 1

            fields = []
            for f_line in body.split(";"):
                f_strip = f_line.strip()
                if f_strip:
                    parts = f_strip.split()
                    if parts:
                        fields.append(parts[-1].strip("*"))

            symbols.append(
                CSymbolInfo(
                    name=s_name,
                    symbol_type="struct",
                    file_path=file_path,
                    line_start=line_start,
                    line_end=line_end,
                    fields=fields,
                )
            )

        # 2. Function indexing
        func_re = re.compile(
            r"^(?:static\s+|inline\s+|__init\s+|__exit\s+)*"
            r"((?:(?:unsigned\s+|signed\s+|struct\s+|enum\s+)?\w+(?:\s*\*+)?))\s+"
            r"(\w+)\s*\(([^;]*?)\)\s*\{",
            re.MULTILINE,
        )

        for m in func_re.finditer(content):
            ret_type = m.group(1).strip()
            f_name = m.group(2).strip()
            params_raw = m.group(3).strip()

            if f_name in {"if", "while", "for", "switch"}:
                continue

            start_pos = m.start()
            line_start = content.count("\n", 0, start_pos) + 1
            brace_idx = content.find("{", start_pos)
            line_end = line_start
            body = ""
            if brace_idx != -1:
                end_pos = self._find_matching_brace(content, brace_idx)
                line_end = content.count("\n", 0, end_pos) + 1
                body = content[brace_idx:end_pos]

            params = [p.strip() for p in params_raw.split(",") if p.strip()]
            calls = self._extract_calls(body, f_name)

            is_lifecycle = any(kw in f_name.lower() for kw in self.LIFECYCLE_KEYWORDS)
            is_dma = any(kw in body for kw in self.DMA_KEYWORDS)
            is_bar = any(kw in body for kw in self.BAR_KEYWORDS)
            is_irq = any(kw in body for kw in self.IRQ_KEYWORDS)

            symbols.append(
                CSymbolInfo(
                    name=f_name,
                    symbol_type="function",
                    return_type=ret_type,
                    parameters=params,
                    file_path=file_path,
                    line_start=line_start,
                    line_end=line_end,
                    calls=calls,
                    is_driver_lifecycle=is_lifecycle,
                    is_dma_op=is_dma,
                    is_bar_op=is_bar,
                    is_irq_op=is_irq,
                )
            )

        # 3. Macro indexing
        macro_re = re.compile(r"^#define\s+(\w+)(?:\([^)]*\))?\s+(.*)$", re.MULTILINE)
        for m in macro_re.finditer(content):
            m_name = m.group(1)
            line_start = content.count("\n", 0, m.start()) + 1
            symbols.append(
                CSymbolInfo(
                    name=m_name,
                    symbol_type="macro",
                    file_path=file_path,
                    line_start=line_start,
                    line_end=line_start,
                )
            )

        return symbols

    def _extract_calls(self, body: str, self_name: str) -> List[str]:
        """Extract called function names within a function body."""
        call_re = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")
        keywords = {
            "if", "while", "for", "switch", "return", "sizeof",
            "likely", "unlikely", "typeof", "__builtin_expect"
        }
        calls = set()
        for m in call_re.finditer(body):
            name = m.group(1)
            if name != self_name and name not in keywords:
                calls.add(name)
        return sorted(list(calls))

    def _find_matching_brace(self, text: str, open_idx: int) -> int:
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
