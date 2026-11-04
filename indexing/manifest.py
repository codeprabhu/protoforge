"""Serialization, deserialization, and construction of repository dependency manifests."""

from __future__ import annotations

import json
import os

from indexing.repository_scanner import RepositoryScanner
from indexing.semantic_chunker import SemanticChunker
from indexing.c_indexer import CIndexer
from indexing.verilog_indexer import VerilogIndexer
from indexing.dependency_graph import DependencyGraph, SymbolNode


def save_manifest(graph: DependencyGraph, path: str) -> None:
    """Save repository dependency graph to a JSON manifest file."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(graph.model_dump_json(indent=2))


def load_manifest(path: str) -> DependencyGraph:
    """Load repository dependency graph from a JSON manifest file."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return DependencyGraph.model_validate(data)


def build_repo_graph(root_dir: str = ".") -> DependencyGraph:
    """Scan and index an entire repository into a unified DependencyGraph."""
    scanner = RepositoryScanner(root_dir=root_dir)
    scan = scanner.scan()
    SemanticChunker()
    c_indexer = CIndexer()
    v_indexer = VerilogIndexer()
    graph = DependencyGraph()

    # Index C source and headers
    c_files = scan.languages.get("c", [])
    for rel_p in c_files:
        full_p = os.path.join(root_dir, rel_p)
        if not os.path.exists(full_p):
            continue
        with open(full_p, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        c_symbols = c_indexer.index_file(rel_p, content)
        for sym in c_symbols:
            node = SymbolNode(
                name=sym.name,
                node_type=sym.symbol_type,
                file_path=sym.file_path,
                line_start=sym.line_start,
                line_end=sym.line_end,
                metadata={
                    "is_lifecycle": sym.is_driver_lifecycle,
                    "is_dma": sym.is_dma_op,
                    "is_bar": sym.is_bar_op,
                    "is_irq": sym.is_irq_op,
                    "fields": sym.fields,
                    "parameters": sym.parameters,
                },
            )
            graph.add_node(node)
            for call in sym.calls:
                graph.add_edge(sym.name, call, "calls")

    # Index Verilog files
    v_files = scan.languages.get("verilog", [])
    for rel_p in v_files:
        full_p = os.path.join(root_dir, rel_p)
        if not os.path.exists(full_p):
            continue
        with open(full_p, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        v_mods = v_indexer.index_file(rel_p, content)
        for mod in v_mods:
            is_tb = "tb" in mod.name.lower() or "testbench" in mod.name.lower()
            node = SymbolNode(
                name=mod.name,
                node_type="test" if is_tb else "module",
                file_path=mod.file_path,
                line_start=mod.line_start,
                line_end=mod.line_end,
                metadata={
                    "ports": [p.model_dump() for p in mod.ports],
                    "clock_nets": mod.clock_nets,
                    "reset_nets": mod.reset_nets,
                    "open_drain_drivers": mod.open_drain_drivers,
                    "state_machine_states": mod.state_machine_states,
                },
            )
            graph.add_node(node)
            for inst in mod.instances:
                relation = "tests" if is_tb else "instantiates"
                graph.add_edge(mod.name, inst.module_type, relation, metadata={"instance_name": inst.instance_name})

    return graph
