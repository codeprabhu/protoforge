"""Tests for Phase 2 repository ingestion, semantic chunking, and dependency graph."""

import os
from pathlib import Path
from indexing.repository_scanner import RepositoryScanner
from indexing.semantic_chunker import SemanticChunker
from indexing.c_indexer import CIndexer
from indexing.verilog_indexer import VerilogIndexer
from indexing.dependency_graph import DependencyGraph, SymbolNode
from indexing.manifest import save_manifest, load_manifest, build_repo_graph


def test_repository_scanner(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "driver.c").write_text("void probe() {}")
    (tmp_path / "src" / "driver.h").write_text("struct dev {};")
    (tmp_path / "src" / "rtl.v").write_text("module top; endmodule")
    (tmp_path / "tests" / "test_driver.c").write_text("void test_probe() {}")
    (tmp_path / "Makefile").write_text("all: build")

    scanner = RepositoryScanner(root_dir=str(tmp_path))
    scan = scanner.scan()

    assert "c" in scan.languages
    assert "verilog" in scan.languages
    assert any("driver.c" in f for f in scan.source_files)
    assert any("driver.h" in f for f in scan.header_files)
    assert any("Makefile" in f for f in scan.build_files)
    assert any("test_driver.c" in f for f in scan.test_files)


def test_c_indexing_and_calls(tmp_path):
    sample_c = """
#define DEVICE_MAGIC 0xbeef

struct pci_dev_priv {
    void __iomem *bar0;
    dma_addr_t dma_handle;
};

static int my_dma_alloc(struct pci_dev_priv *priv) {
    pci_alloc_consistent(priv);
    return 0;
}

int my_driver_probe(struct pci_dev *pdev) {
    struct pci_dev_priv *priv;
    pci_iomap(pdev);
    my_dma_alloc(priv);
    return 0;
}
"""
    f = tmp_path / "sample.c"
    f.write_text(sample_c)

    indexer = CIndexer()
    symbols = indexer.index_file(str(f), sample_c)

    sym_dict = {s.name: s for s in symbols}
    assert "DEVICE_MAGIC" in sym_dict
    assert "pci_dev_priv" in sym_dict
    assert "bar0" in sym_dict["pci_dev_priv"].fields
    assert "dma_handle" in sym_dict["pci_dev_priv"].fields

    assert "my_driver_probe" in sym_dict
    probe = sym_dict["my_driver_probe"]
    assert probe.is_driver_lifecycle is True
    assert probe.is_bar_op is True
    assert "pci_iomap" in probe.calls
    assert "my_dma_alloc" in probe.calls


def test_verilog_indexing_ports_clocks_and_opendrain(tmp_path):
    sample_v = """
module i2c_master (
    input  wire        clk,
    input  wire        rst_n,
    inout  wire        sda,
    inout  wire        scl,
    input  wire        tb_start,
    output wire        tb_done
);
    localparam IDLE = 3'b000;
    localparam START = 3'b001;

    wire sda_oe;
    assign sda = sda_oe ? 1'b0 : 1'bz;

    sub_engine u_sub (
        .clk(clk),
        .rst_n(rst_n)
    );
endmodule
"""
    f = tmp_path / "sample.v"
    f.write_text(sample_v)

    indexer = VerilogIndexer()
    modules = indexer.index_file(str(f), sample_v)

    assert len(modules) == 1
    m = modules[0]
    assert m.name == "i2c_master"
    assert "clk" in m.clock_nets
    assert "rst_n" in m.reset_nets
    assert len(m.open_drain_drivers) == 1
    assert m.open_drain_drivers[0]["wire"] == "sda"
    assert any("IDLE" in s for s in m.state_machine_states)
    assert any(inst.module_type == "sub_engine" for inst in m.instances)


def test_dependency_graph_query_symbol():
    graph = DependencyGraph()

    # Define function, callee, caller, struct, and test
    graph.add_node(SymbolNode(name="dma_submit", node_type="function", file_path="drivers/dma.c", line_start=10, line_end=30))
    graph.add_node(SymbolNode(name="dma_map_single", node_type="function", file_path="kernel/dma.c", line_start=1, line_end=15))
    graph.add_node(SymbolNode(name="pcie_start_xfer", node_type="function", file_path="drivers/pcie.c", line_start=50, line_end=70))
    graph.add_node(SymbolNode(name="struct_dma_ring", node_type="struct", file_path="drivers/dma.h", line_start=5, line_end=12))
    graph.add_node(SymbolNode(name="test_dma_submit_success", node_type="test", file_path="tests/test_dma.c", line_start=1, line_end=20))

    graph.add_edge("dma_submit", "dma_map_single", "calls")
    graph.add_edge("pcie_start_xfer", "dma_submit", "calls")
    graph.add_edge("dma_submit", "struct_dma_ring", "uses_struct")
    graph.add_edge("test_dma_submit_success", "dma_submit", "tests")

    query = graph.query_symbol("dma_submit")
    assert query["symbol"]["name"] == "dma_submit"
    assert len(query["called_functions"]) == 1
    assert query["called_functions"][0]["name"] == "dma_map_single"
    assert len(query["caller_functions"]) == 1
    assert query["caller_functions"][0]["name"] == "pcie_start_xfer"
    assert len(query["data_structures"]) == 1
    assert query["data_structures"][0]["name"] == "struct_dma_ring"
    assert len(query["associated_tests"]) == 1
    assert query["associated_tests"][0]["name"] == "test_dma_submit_success"


def test_manifest_roundtrip_and_repo_build(tmp_path):
    graph = build_repo_graph(".")
    assert len(graph.nodes) > 0

    manifest_file = tmp_path / "manifest.json"
    save_manifest(graph, str(manifest_file))
    assert manifest_file.exists()

    loaded_graph = load_manifest(str(manifest_file))
    assert len(loaded_graph.nodes) == len(graph.nodes)
    assert len(loaded_graph.edges) == len(graph.edges)
