"""Repository scanner to discover source files, build scripts, tests, and configuration."""

from __future__ import annotations

import os
from typing import Dict, List, Set
from pydantic import BaseModel, Field


class RepoScanResult(BaseModel):
    """Structured report of repository contents and classification."""
    root_dir: str
    languages: Dict[str, List[str]] = Field(default_factory=dict)
    source_files: List[str] = Field(default_factory=list)
    header_files: List[str] = Field(default_factory=list)
    build_files: List[str] = Field(default_factory=list)
    test_files: List[str] = Field(default_factory=list)
    spec_files: List[str] = Field(default_factory=list)
    generated_files: List[str] = Field(default_factory=list)
    vendored_files: List[str] = Field(default_factory=list)
    ignored_patterns: List[str] = Field(default_factory=list)


class RepositoryScanner:
    """Scans and indexes files according to domain language patterns and ignore rules."""

    DEFAULT_IGNORE_DIRS = {
        ".git",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        "node_modules",
        ".ruff_cache",
        ".mypy_cache",
    }

    BUILD_FILE_NAMES = {
        "Makefile",
        "Kbuild",
        "CMakeLists.txt",
        "meson.build",
        "setup.py",
        "pyproject.toml",
    }

    def __init__(self, root_dir: str = ".", custom_ignore_dirs: Set[str] | None = None):
        self.root_dir = os.path.abspath(root_dir)
        self.ignore_dirs = set(self.DEFAULT_IGNORE_DIRS)
        if custom_ignore_dirs:
            self.ignore_dirs.update(custom_ignore_dirs)

    def scan(self) -> RepoScanResult:
        """Scan the repository and categorize files."""
        result = RepoScanResult(root_dir=self.root_dir)
        langs: Dict[str, List[str]] = {
            "c": [],
            "verilog": [],
            "python": [],
            "markdown": [],
            "build": [],
        }

        for root, dirs, files in os.walk(self.root_dir):
            # Prune ignored directories
            dirs[:] = [d for d in dirs if d not in self.ignore_dirs and not d.startswith(".")]

            rel_root = os.path.relpath(root, self.root_dir)
            is_test_dir = "test" in rel_root.lower()
            is_generated = "generated" in rel_root.lower() or "build" in rel_root.lower()
            is_vendored = "vendor" in rel_root.lower() or "third_party" in rel_root.lower()

            for f in files:
                rel_path = os.path.normpath(os.path.join(rel_root, f)) if rel_root != "." else f
                os.path.join(root, f)

                if is_generated:
                    result.generated_files.append(rel_path)
                if is_vendored:
                    result.vendored_files.append(rel_path)

                # Build files
                if f in self.BUILD_FILE_NAMES or f.endswith(".mk"):
                    result.build_files.append(rel_path)
                    langs["build"].append(rel_path)

                # C / C++
                elif f.endswith((".c", ".cc", ".cpp")):
                    result.source_files.append(rel_path)
                    langs["c"].append(rel_path)
                    if is_test_dir or "test" in f.lower():
                        result.test_files.append(rel_path)

                elif f.endswith((".h", ".hpp", ".hh")):
                    result.header_files.append(rel_path)
                    langs["c"].append(rel_path)

                # Verilog / SystemVerilog
                elif f.endswith((".v", ".sv", ".vh", ".svh")):
                    result.source_files.append(rel_path)
                    langs["verilog"].append(rel_path)
                    if is_test_dir or f.startswith("tb_") or "testbench" in f.lower():
                        result.test_files.append(rel_path)

                # Python
                elif f.endswith(".py"):
                    langs["python"].append(rel_path)
                    if is_test_dir or f.startswith("test_"):
                        result.test_files.append(rel_path)

                # Specs / Documentation
                elif f.endswith((".md", ".txt", ".rst")) and ("spec" in f.lower() or "doc" in rel_root.lower()):
                    result.spec_files.append(rel_path)
                    langs["markdown"].append(rel_path)

        result.languages = {k: v for k, v in langs.items() if v}
        result.ignored_patterns = sorted(list(self.ignore_dirs))
        return result
