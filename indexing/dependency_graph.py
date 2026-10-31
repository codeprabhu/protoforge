"""Cross-file symbol and module dependency graph."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field


class SymbolNode(BaseModel):
    """A node in the dependency graph representing a code symbol or asset."""
    name: str
    node_type: str  # function, struct, module, macro, test, spec
    file_path: str
    line_start: int = 1
    line_end: int = 1
    content: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DependencyEdge(BaseModel):
    """A directed edge representing a relationship between two symbols."""
    source: str
    target: str
    relation: str  # calls, instantiates, uses_type, tests, includes
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DependencyGraph(BaseModel):
    """Graph structure capturing cross-symbol and cross-file dependencies."""
    nodes: Dict[str, SymbolNode] = Field(default_factory=dict)
    edges: List[DependencyEdge] = Field(default_factory=list)

    def add_node(self, node: SymbolNode) -> None:
        """Add or update a node in the graph."""
        self.nodes[node.name] = node

    def add_edge(self, source: str, target: str, relation: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Add a directed edge between two symbol names."""
        self.edges.append(
            DependencyEdge(
                source=source,
                target=target,
                relation=relation,
                metadata=metadata or {},
            )
        )

    def get_node(self, name: str) -> Optional[SymbolNode]:
        """Look up symbol node by name."""
        return self.nodes.get(name)

    def get_callees(self, symbol_name: str) -> List[str]:
        """Return names of all symbols directly called/instantiated by symbol_name."""
        return [
            edge.target
            for edge in self.edges
            if edge.source == symbol_name and edge.relation in ("calls", "instantiates")
        ]

    def get_callers(self, symbol_name: str) -> List[str]:
        """Return names of all symbols that call or instantiate symbol_name."""
        return [
            edge.source
            for edge in self.edges
            if edge.target == symbol_name and edge.relation in ("calls", "instantiates")
        ]

    def get_structures(self, symbol_name: str) -> List[str]:
        """Return names of structs or types used by symbol_name."""
        return [
            edge.target
            for edge in self.edges
            if edge.source == symbol_name and edge.relation in ("uses_type", "uses_struct")
        ]

    def get_associated_tests(self, symbol_name: str) -> List[str]:
        """Return names of tests that target or mention symbol_name."""
        return [
            edge.source
            for edge in self.edges
            if edge.target == symbol_name and edge.relation == "tests"
        ]

    def query_symbol(self, symbol_name: str) -> Dict[str, Any]:
        """Full contextual dependency query for a symbol."""
        node = self.get_node(symbol_name)
        if not node:
            return {"error": f"Symbol '{symbol_name}' not found in graph"}

        callee_names = self.get_callees(symbol_name)
        caller_names = self.get_callers(symbol_name)
        struct_names = self.get_structures(symbol_name)
        test_names = self.get_associated_tests(symbol_name)

        return {
            "symbol": node.model_dump(),
            "called_functions": [self.nodes[c].model_dump() for c in callee_names if c in self.nodes],
            "caller_functions": [self.nodes[c].model_dump() for c in caller_names if c in self.nodes],
            "data_structures": [self.nodes[s].model_dump() for s in struct_names if s in self.nodes],
            "associated_tests": [self.nodes[t].model_dump() for t in test_names if t in self.nodes],
        }

    def expand_context(self, symbol_name: str, max_depth: int = 1) -> List[SymbolNode]:
        """Collect all related nodes within graph distance max_depth."""
        visited: Set[str] = set()
        to_visit: List[str] = [symbol_name]
        depth = 0

        while to_visit and depth <= max_depth:
            next_level: List[str] = []
            for s in to_visit:
                if s not in visited:
                    visited.add(s)
                    for edge in self.edges:
                        if edge.source == s and edge.target not in visited:
                            next_level.append(edge.target)
                        elif edge.target == s and edge.source not in visited:
                            next_level.append(edge.source)
            to_visit = next_level
            depth += 1

        return [self.nodes[name] for name in visited if name in self.nodes]
