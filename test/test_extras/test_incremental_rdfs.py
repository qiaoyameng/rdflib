import random

import pytest

from rdflib import Namespace
from rdflib.namespace import RDF, RDFS

from rdflib.extras.incremental_rdfs import IncrementalRDFSReasoner, rdfs_closure

EX = Namespace("http://example.org/")
SC = RDFS.subClassOf
SP = RDFS.subPropertyOf
DOM = RDFS.domain
RNG = RDFS.range
TYPE = RDF.type


def make_diamond():
    reasoner = IncrementalRDFSReasoner()
    for triple in [
        (EX.x, TYPE, EX.A),
        (EX.A, SC, EX.B),
        (EX.A, SC, EX.C),
        (EX.B, SC, EX.D),
        (EX.C, SC, EX.D),
    ]:
        reasoner.add(triple)
    return reasoner


def test_diamond_conclusion_has_two_independent_justifications():
    reasoner = make_diamond()
    explanations = reasoner.explain((EX.x, TYPE, EX.D))
    assert len(explanations) == 2
    via_b = frozenset({(EX.x, TYPE, EX.A), (EX.A, SC, EX.B), (EX.B, SC, EX.D)})
    via_c = frozenset({(EX.x, TYPE, EX.A), (EX.A, SC, EX.C), (EX.C, SC, EX.D)})
    assert via_b in explanations
    assert via_c in explanations


def test_retracting_one_support_keeps_conclusion_alive():
    reasoner = make_diamond()
    reasoner.retract((EX.B, SC, EX.D))
    assert reasoner.is_entailed((EX.x, TYPE, EX.D))
    assert not reasoner.is_entailed((EX.B, SC, EX.D))
    explanations = reasoner.explain((EX.x, TYPE, EX.D))
    assert len(explanations) == 1
    assert (EX.B, SC, EX.D) not in next(iter(explanations))


def test_retracting_all_supports_drops_conclusion():
    reasoner = make_diamond()
    reasoner.retract((EX.B, SC, EX.D))
    reasoner.retract((EX.C, SC, EX.D))
    assert not reasoner.is_entailed((EX.x, TYPE, EX.D))
    assert reasoner.explain((EX.x, TYPE, EX.D)) == ()


def test_cycle_does_not_become_self_sustaining():
    reasoner = IncrementalRDFSReasoner()
    reasoner.add((EX.A, SC, EX.B))
    reasoner.add((EX.B, SC, EX.A))
    reasoner.add((EX.y, TYPE, EX.A))
    assert reasoner.is_entailed((EX.y, TYPE, EX.B))
    assert reasoner.is_entailed((EX.A, SC, EX.A))
    # Break the cycle: nothing derived from it may survive.
    reasoner.retract((EX.A, SC, EX.B))
    assert not reasoner.is_entailed((EX.y, TYPE, EX.B))
    assert not reasoner.is_entailed((EX.A, SC, EX.A))
    assert not reasoner.is_entailed((EX.B, SC, EX.B))
    # Explicit facts are untouched.
    assert reasoner.is_entailed((EX.B, SC, EX.A))
    assert reasoner.is_entailed((EX.y, TYPE, EX.A))


def test_user_asserted_conclusion_survives_cascade():
    reasoner = IncrementalRDFSReasoner()
    reasoner.add((EX.A, SC, EX.B))
    reasoner.add((EX.y, TYPE, EX.A))
    assert reasoner.is_explicit((EX.y, TYPE, EX.B)) is False
    # The user directly asserts a conclusion that is also derived.
    reasoner.add((EX.y, TYPE, EX.B))
    reasoner.retract((EX.A, SC, EX.B))
    assert reasoner.is_entailed((EX.y, TYPE, EX.B))
    assert reasoner.is_explicit((EX.y, TYPE, EX.B))
    assert reasoner.explain((EX.y, TYPE, EX.B)) == (frozenset({(EX.y, TYPE, EX.B)}),)


def test_retracting_explicit_triple_keeps_derivable_fact():
    reasoner = IncrementalRDFSReasoner()
    reasoner.add((EX.A, SC, EX.B))
    reasoner.add((EX.B, SC, EX.C))
    reasoner.add((EX.A, SC, EX.C))  # explicit, but also derivable
    reasoner.retract((EX.A, SC, EX.C))
    # Still entailed, now as a purely derived fact.
    assert reasoner.is_entailed((EX.A, SC, EX.C))
    assert not reasoner.is_explicit((EX.A, SC, EX.C))


def test_property_rules_trigger_each_other():
    reasoner = IncrementalRDFSReasoner()
    for triple in [
        (EX.p, SP, EX.q),
        (EX.q, SP, EX.r),
        (EX.p, DOM, EX.C1),
        (EX.q, RNG, EX.C2),
        (EX.C2, SC, EX.C3),
        (EX.x, EX.p, EX.y),
    ]:
        reasoner.add(triple)
    assert reasoner.is_entailed((EX.p, SP, EX.r))
    assert reasoner.is_entailed((EX.x, EX.q, EX.y))
    assert reasoner.is_entailed((EX.x, EX.r, EX.y))
    assert reasoner.is_entailed((EX.x, TYPE, EX.C1))
    assert reasoner.is_entailed((EX.y, TYPE, EX.C2))
    assert reasoner.is_entailed((EX.y, TYPE, EX.C3))

    reasoner.retract((EX.p, SP, EX.q))
    # The whole trigger chain falls back ...
    assert not reasoner.is_entailed((EX.p, SP, EX.r))
    assert not reasoner.is_entailed((EX.x, EX.q, EX.y))
    assert not reasoner.is_entailed((EX.x, EX.r, EX.y))
    assert not reasoner.is_entailed((EX.y, TYPE, EX.C2))
    assert not reasoner.is_entailed((EX.y, TYPE, EX.C3))
    # ... except what does not depend on the retracted link.
    assert reasoner.is_entailed((EX.x, TYPE, EX.C1))


def test_explicit_and_derived_facts_are_managed_separately():
    reasoner = make_diamond()
    explicit = set(reasoner.graph)
    derived = set(reasoner.derived())
    assert explicit.isdisjoint(derived)
    assert set(reasoner.conclusions()) == explicit | derived
    assert (EX.x, TYPE, EX.D) in derived
    assert (EX.x, TYPE, EX.A) in explicit


def test_add_is_idempotent_and_retract_of_unknown_is_noop():
    reasoner = IncrementalRDFSReasoner()
    reasoner.add((EX.A, SC, EX.B))
    reasoner.add((EX.A, SC, EX.B))
    assert len(reasoner.explain((EX.A, SC, EX.B))) == 1
    reasoner.retract((EX.C, SC, EX.D))  # never asserted
    assert reasoner.is_entailed((EX.A, SC, EX.B))


def test_reasoner_can_wrap_a_prepopulated_graph():
    from rdflib import Graph

    graph = Graph()
    graph.add((EX.A, SC, EX.B))
    graph.add((EX.x, TYPE, EX.A))
    reasoner = IncrementalRDFSReasoner(graph)
    assert reasoner.is_entailed((EX.x, TYPE, EX.B))


def test_matches_full_closure_after_every_operation():
    """Random add/retract sequences must always match a full recomputation."""
    rng = random.Random(20261005)
    classes = [EX[f"C{i}"] for i in range(5)]
    props = [EX[f"p{i}"] for i in range(3)]
    individuals = [EX[f"x{i}"] for i in range(3)]
    pool = []
    for a in classes:
        for b in classes:
            if a != b and rng.random() < 0.5:
                pool.append((a, SC, b))
    for p in props:
        for q in props:
            if p != q and rng.random() < 0.5:
                pool.append((p, SP, q))
    for p in props:
        for c in classes:
            if rng.random() < 0.4:
                pool.append((p, DOM, c))
            if rng.random() < 0.4:
                pool.append((p, RNG, c))
    for x in individuals:
        for c in classes:
            if rng.random() < 0.4:
                pool.append((x, TYPE, c))
        for p in props:
            for y in individuals:
                if rng.random() < 0.3:
                    pool.append((x, p, y))

    reasoner = IncrementalRDFSReasoner()
    for step in range(400):
        triple = rng.choice(pool)
        if triple in reasoner.graph and rng.random() < 0.45:
            reasoner.retract(triple)
        else:
            reasoner.add(triple)
        expected = rdfs_closure(set(reasoner.graph))
        actual = set(reasoner.conclusions())
        assert actual == expected, f"step {step}: mismatch for {triple}"
        # Every justification must be grounded in currently explicit triples.
        explicit = set(reasoner.graph)
        for fact in actual:
            for support in reasoner.explain(fact):
                assert support <= explicit
        # Justifications must be sufficient: closing a support derives the fact.
        for fact in rng.sample(sorted(actual), min(5, len(actual))):
            for support in reasoner.explain(fact):
                assert fact in rdfs_closure(support)


@pytest.mark.parametrize(
    "triple",
    [
        (EX.A, SC, EX.B),
        (EX.p, SP, EX.q),
        (EX.p, DOM, EX.C),
        (EX.p, RNG, EX.C),
        (EX.x, TYPE, EX.A),
        (EX.x, EX.p, EX.y),
    ],
)
def test_single_fact_closure_matches_naive(triple):
    reasoner = IncrementalRDFSReasoner()
    reasoner.add(triple)
    assert set(reasoner.conclusions()) == rdfs_closure({triple})
