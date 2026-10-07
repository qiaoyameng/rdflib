"""Small-scope incremental RDFS reasoner on top of rdflib.

Supports only a fixed subset of RDFS rules (subClassOf / subPropertyOf
transitivity and inheritance, domain / range type inference). Explicit
and derived facts are managed separately, and every derived fact keeps
its justifications so facts can be retracted without rebuilding the
whole closure (DRed-style over-deletion + re-derivation).
"""

from .reasoner import IncrementalRDFSReasoner, Justification, RULES

__all__ = ["IncrementalRDFSReasoner", "Justification", "RULES"]
