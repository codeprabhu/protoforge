"""Mutation and fault injection framework."""

from mutations.schema import Mutation, MutantCandidate, MutationResult, MutationCatalogue
from mutations.catalogue import get_catalogue, register_catalogue, I2C_CATALOGUE, PCIE_CATALOGUE
from mutations.mutator import apply_mutation, create_mutant, generate_applicable_mutants
from mutations.runner import MutationRunner

__all__ = [
    "Mutation",
    "MutantCandidate",
    "MutationResult",
    "MutationCatalogue",
    "get_catalogue",
    "register_catalogue",
    "I2C_CATALOGUE",
    "PCIE_CATALOGUE",
    "apply_mutation",
    "create_mutant",
    "generate_applicable_mutants",
    "MutationRunner",
]
