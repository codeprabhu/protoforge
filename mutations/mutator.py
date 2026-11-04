"""Applies mutations deterministically to source code to generate mutant candidates."""

from __future__ import annotations

import hashlib
import uuid
from typing import List, Optional

from mutations.schema import MutantCandidate, Mutation, MutationCatalogue


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def apply_mutation(source_text: str, mutation: Mutation) -> str:
    """Apply a mutation to source text, ensuring the target pattern exists uniquely."""
    if not mutation.target_pattern:
        raise ValueError(f"Mutation {mutation.mutation_id} has no target_pattern defined")

    count = source_text.count(mutation.target_pattern)
    if count == 0:
        raise ValueError(
            f"Target pattern for mutation {mutation.mutation_id} was not found in source text: {mutation.target_pattern!r}"
        )
    if count > 1:
        raise ValueError(
            f"Target pattern for mutation {mutation.mutation_id} is ambiguous (found {count} occurrences): {mutation.target_pattern!r}"
        )

    return source_text.replace(mutation.target_pattern, mutation.replacement, 1)


def create_mutant(
    original_file: str,
    mutation: Mutation,
    source_text: Optional[str] = None,
) -> MutantCandidate:
    """Create a MutantCandidate from an original file and a Mutation."""
    if source_text is None:
        with open(original_file, "r", encoding="utf-8") as f:
            source_text = f.read()

    mutated_text = apply_mutation(source_text, mutation)

    return MutantCandidate(
        mutant_id=f"MUTANT-{mutation.mutation_id}-{uuid.uuid4().hex[:6]}",
        mutation=mutation,
        original_file=original_file,
        mutated_content=mutated_text,
        original_hash=_hash_text(source_text),
        mutated_hash=_hash_text(mutated_text),
    )


def generate_applicable_mutants(
    original_file: str,
    catalogue: MutationCatalogue,
    source_text: Optional[str] = None,
) -> List[MutantCandidate]:
    """Generate all mutants from a catalogue whose target patterns match the source."""
    if source_text is None:
        with open(original_file, "r", encoding="utf-8") as f:
            source_text = f.read()

    mutants: List[MutantCandidate] = []
    for mutation in catalogue.mutations:
        if mutation.target_pattern and mutation.target_pattern in source_text:
            try:
                mutant = create_mutant(original_file, mutation, source_text=source_text)
                mutants.append(mutant)
            except ValueError:
                continue
    return mutants
