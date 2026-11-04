"""Versioned catalogues of defect mutations for hardware RTL and software drivers."""

from __future__ import annotations

from typing import Dict
from mutations.schema import Mutation, MutationCatalogue

I2C_CATALOGUE = MutationCatalogue(
    domain="i2c",
    version="1.0.0",
    mutations=[
        Mutation(
            mutation_id="I2C-MUT-001",
            domain="i2c",
            title="Missing START Condition",
            description="Master never asserts START condition (SDA high-to-low transition during SCL high).",
            fault_type="missing_condition",
            target_pattern="sda_oe   <= 1; // drive SDA low: START condition",
            replacement="sda_oe   <= 0; // MUTANT: omitted START",
            expected_failed_stage="simulate",
            expected_failed_check="start",
        ),
        Mutation(
            mutation_id="I2C-MUT-002",
            domain="i2c",
            title="Missing STOP Condition",
            description="Master never asserts STOP condition (SDA low-to-high transition during SCL high).",
            fault_type="missing_condition",
            target_pattern="sda_oe <= 0; // release SDA while SCL high -> STOP condition",
            replacement="sda_oe <= 1; // MUTANT: keep SDA low, never STOP",
            expected_failed_stage="simulate",
            expected_failed_check="stop",
        ),
        Mutation(
            mutation_id="I2C-MUT-003",
            domain="i2c",
            title="Missing Completion tb_done",
            description="Master completes transaction but never asserts tb_done completion flag.",
            fault_type="missing_signal",
            target_pattern="tb_done <= 1;",
            replacement="tb_done <= 0;",
            expected_failed_stage="simulate",
            expected_failed_check="tb_done",
        ),
        Mutation(
            mutation_id="I2C-MUT-004",
            domain="i2c",
            title="Wrong Slave Address",
            description="Master transmits address byte 0xA2 instead of expected address 0xA0.",
            fault_type="wrong_constant",
            target_pattern="localparam ADDR_BYTE = 8'hA0;",
            replacement="localparam ADDR_BYTE = 8'hA2;",
            expected_failed_stage="simulate",
            expected_failed_check="address_ack",
        ),
        Mutation(
            mutation_id="I2C-MUT-005",
            domain="i2c",
            title="Wrong Data Payload",
            description="Master transmits 0x00 instead of expected payload 0x3C.",
            fault_type="wrong_constant",
            target_pattern="localparam DATA_BYTE = 8'h3C;",
            replacement="localparam DATA_BYTE = 8'h00;",
            expected_failed_stage="simulate",
            expected_failed_check="correct_data",
        ),
        Mutation(
            mutation_id="I2C-MUT-006",
            domain="i2c",
            title="Active-High Bus Driver",
            description="SDA driver directly drives 1'b1 instead of releasing to 1'bz (violating open-drain).",
            fault_type="electrical_violation",
            target_pattern="assign sda = sda_oe ? 1'b0 : 1'bz;",
            replacement="assign sda = sda_oe ? 1'b1 : 1'b0;",
            expected_failed_stage="structural",
            expected_failed_check="legal_open_drain",
        ),
    ],
)

PCIE_CATALOGUE = MutationCatalogue(
    domain="pcie_linux",
    version="1.0.0",
    mutations=[
        Mutation(
            mutation_id="PCI-001",
            domain="pcie_linux",
            title="Incorrect BAR selection or offset calculation",
            description="Driver maps BAR1 instead of BAR0 or applies wrong register offset.",
            fault_type="wrong_offset",
            target_pattern="pci_iomap(pdev, 0, 0)",
            replacement="pci_iomap(pdev, 1, 0)",
            expected_failed_stage="kunit",
            expected_failed_check="bar_mapping",
        ),
        Mutation(
            mutation_id="PCI-002",
            domain="pcie_linux",
            title="Probe error path resource leak",
            description="Probe error unwind path fails to unmap BAR or release claimed regions.",
            fault_type="missing_cleanup",
            target_pattern="pci_iounmap(pdev, priv->bar0);",
            replacement="/* MUTANT: omitted pci_iounmap on error */",
            expected_failed_stage="kunit",
            expected_failed_check="probe_cleanup_leak",
        ),
        Mutation(
            mutation_id="PCI-003",
            domain="pcie_linux",
            title="Streaming DMA unmap omission",
            description="DMA buffer is mapped with dma_map_single but unmap is omitted upon completion.",
            fault_type="missing_cleanup",
            target_pattern="dma_unmap_single(&pdev->dev, ring->dma_addr, ring->len, DMA_FROM_DEVICE);",
            replacement="/* MUTANT: omitted dma_unmap_single */",
            expected_failed_stage="kunit",
            expected_failed_check="dma_unmap_leak",
        ),
        Mutation(
            mutation_id="PCI-004",
            domain="pcie_linux",
            title="Coherent DMA buffer leak in remove",
            description="Driver remove routine fails to free coherent DMA buffer via dma_free_coherent.",
            fault_type="missing_cleanup",
            target_pattern="dma_free_coherent(&pdev->dev, priv->ring_size, priv->ring_vaddr, priv->ring_dma);",
            replacement="/* MUTANT: omitted dma_free_coherent in remove */",
            expected_failed_stage="kunit",
            expected_failed_check="coherent_dma_leak",
        ),
        Mutation(
            mutation_id="PCI-005",
            domain="pcie_linux",
            title="Inverted DMA transfer direction",
            description="RX DMA mapping uses DMA_TO_DEVICE instead of DMA_FROM_DEVICE.",
            fault_type="direction_bug",
            target_pattern="dma_map_single(&pdev->dev, skb->data, len, DMA_FROM_DEVICE)",
            replacement="dma_map_single(&pdev->dev, skb->data, len, DMA_TO_DEVICE)",
            expected_failed_stage="kunit",
            expected_failed_check="dma_direction",
        ),
        Mutation(
            mutation_id="PCI-006",
            domain="pcie_linux",
            title="Interrupt enabled before handler registration",
            description="Hardware interrupt enable register written prior to request_irq registration.",
            fault_type="ordering_bug",
            target_pattern="pci_enable_msi(pdev);\n    request_irq(pdev->irq, my_handler, 0, \"my_driver\", priv);",
            replacement="writel(ENABLE_ALL_IRQS, priv->bar0 + INTR_ENABLE);\n    request_irq(pdev->irq, my_handler, 0, \"my_driver\", priv);",
            expected_failed_stage="kunit",
            expected_failed_check="irq_ordering",
        ),
        Mutation(
            mutation_id="PCI-007",
            domain="pcie_linux",
            title="Remove omits DMA quiesce",
            description="Driver frees DMA structures while bus mastering and hardware DMA remain active.",
            fault_type="ordering_bug",
            target_pattern="pci_clear_master(pdev);\n    dma_free_coherent(&pdev->dev, priv->ring_size, priv->ring_vaddr, priv->ring_dma);",
            replacement="dma_free_coherent(&pdev->dev, priv->ring_size, priv->ring_vaddr, priv->ring_dma);",
            expected_failed_stage="kunit",
            expected_failed_check="active_dma_removal",
        ),
        Mutation(
            mutation_id="PCI-008",
            domain="pcie_linux",
            title="MMIO access width mismatch",
            description="Accesses 32-bit control register using 8-bit readb instead of readl.",
            fault_type="width_mismatch",
            target_pattern="readl(priv->bar0 + REG_CTRL)",
            replacement="readb(priv->bar0 + REG_CTRL)",
            expected_failed_stage="kunit",
            expected_failed_check="access_width",
        ),
    ],
)

_CATALOGUES: Dict[str, MutationCatalogue] = {
    "i2c": I2C_CATALOGUE,
    "pcie_linux": PCIE_CATALOGUE,
}


def get_catalogue(domain: str) -> MutationCatalogue:
    """Retrieve the mutation catalogue for a given domain."""
    if domain not in _CATALOGUES:
        raise KeyError(f"No mutation catalogue registered for domain '{domain}'. Available: {list(_CATALOGUES.keys())}")
    return _CATALOGUES[domain]


def register_catalogue(catalogue: MutationCatalogue) -> None:
    """Register or replace a mutation catalogue."""
    _CATALOGUES[catalogue.domain] = catalogue
