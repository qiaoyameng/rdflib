import random

import pytest
from rdflib import Namespace

from ..reasoner import IncrementalRDFSReasoner, SC, SP, DOM, RNG, TYPE
from .closure import full_closure

EX = Namespace("http://example.org/")


def make_reasoner(facts=()):
    r = IncrementalRDFSReasoner()
    for f in facts:
        r.add(f)
    return r


def assert_matches_closure(reasoner, explicit):
    assert set(reasoner.conclusions()) == full_closure(explicit)
    assert set(reasoner.explicit) == set(explicit)


# ---------------------------------------------------------------------
# diamond inheritance
# ---------------------------------------------------------------------
def diamond_facts():
    return [
        (EX.x, TYPE, EX.A),
        (EX.A, SC, EX.B),
        (EX.A, SC, EX.C),
        (EX.B, SC, EX.D),
        (EX.C, SC, EX.D),
    ]


def test_diamond_inheritance_derives_all_supertypes():
    r = make_reasoner(diamond_facts())
    for cls in (EX.A, EX.B, EX.C, EX.D):
        assert (EX.x, TYPE, cls) in r
    assert_matches_closure(r, diamond_facts())


def test_diamond_conclusion_has_two_independent_supports():
    r = make_reasoner(diamond_facts())
    reasons = r.explain((EX.x, TYPE, EX.D))
    paths = {j.antecedents for j in reasons if j != "explicit" and j.rule == "type-inherit"}
    # one path via B, one via C
    assert any((EX.x, TYPE, EX.B) in ants for ants in paths)
    assert any((EX.x, TYPE, EX.C) in ants for ants in paths)


def test_diamond_retracting_one_support_keeps_conclusion():
    facts = diamond_facts()
    r = make_reasoner(facts)
    r.retract((EX.B, SC, EX.D))
    facts.remove((EX.B, SC, EX.D))
    # x is still a D via C, but no longer a B-derived D
    assert (EX.x, TYPE, EX.D) in r
    assert (EX.B, SC, EX.D) not in r
    assert (EX.x, TYPE, EX.B) in r  # A->B still holds
    reasons = r.explain((EX.x, TYPE, EX.D))
    assert all((EX.x, TYPE, EX.B) not in j.antecedents for j in reasons if j != "explicit")
    assert_matches_closure(r, facts)


# ---------------------------------------------------------------------
# mutual (cyclic) inheritance
# ---------------------------------------------------------------------
def test_cycle_derives_but_does_not_self_sustain():
    facts = [(EX.A, SC, EX.B), (EX.B, SC, EX.A)]
    r = make_reasoner(facts)
    # cycle yields the reflexive facts while both edges exist
    assert (EX.A, SC, EX.A) in r
    assert (EX.B, SC, EX.B) in r
    assert_matches_closure(r, facts)

    r.retract((EX.A, SC, EX.B))
    facts.remove((EX.A, SC, EX.B))
    # the cycle is broken: nothing derived may survive on its own
    assert (EX.A, SC, EX.A) not in r
    assert (EX.B, SC, EX.B) not in r
    assert (EX.B, SC, EX.A) in r  # the remaining explicit edge
    assert_matches_closure(r, facts)


def test_cycle_with_external_anchor_keeps_grounded_conclusions():
    facts = [
        (EX.x, TYPE, EX.A),
        (EX.A, SC, EX.B),
        (EX.B, SC, EX.A),
        (EX.B, SC, EX.C),
    ]
    r = make_reasoner(facts)
    assert (EX.x, TYPE, EX.C) in r
    r.retract((EX.B, SC, EX.A))
    facts.remove((EX.B, SC, EX.A))
    # still grounded: x type A, A sc B, B sc C
    assert (EX.x, TYPE, EX.C) in r
    assert (EX.A, SC, EX.A) not in r
    assert_matches_closure(r, facts)


# ---------------------------------------------------------------------
# property rules triggering each other
# ---------------------------------------------------------------------
def property_facts():
    return [
        (EX.alice, EX.worksWith, EX.bob),
        (EX.worksWith, SP, EX.knows),
        (EX.knows, SP, EX.acquaintedWith),
        (EX.knows, DOM, EX.Person),
        (EX.knows, RNG, EX.Person),
    ]


def test_property_rules_chain_through_derived_facts():
    r = make_reasoner(property_facts())
    # prop-inherit along the sp chain
    assert (EX.alice, EX.knows, EX.bob) in r
    assert (EX.alice, EX.acquaintedWith, EX.bob) in r
    # sp transitivity
    assert (EX.worksWith, SP, EX.acquaintedWith) in r
    # domain/range fire on the *derived* (alice, knows, bob) triple
    assert (EX.alice, TYPE, EX.Person) in r
    assert (EX.bob, TYPE, EX.Person) in r
    assert_matches_closure(r, property_facts())


def test_retracting_property_edge_cascades_through_derived_types():
    facts = property_facts()
    r = make_reasoner(facts)
    r.retract((EX.worksWith, SP, EX.knows))
    facts.remove((EX.worksWith, SP, EX.knows))
    assert (EX.alice, EX.knows, EX.bob) not in r
    assert (EX.alice, EX.acquaintedWith, EX.bob) not in r
    # the type facts only existed because of the derived knows-edge
    assert (EX.alice, TYPE, EX.Person) not in r
    assert (EX.bob, TYPE, EX.Person) not in r
    assert_matches_closure(r, facts)


def test_domain_on_superproperty_applies_to_subproperty_instances():
    facts = [
        (EX.alice, EX.worksWith, EX.bob),
        (EX.worksWith, SP, EX.knows),
        (EX.knows, DOM, EX.Person),
    ]
    r = make_reasoner(facts)
    assert (EX.alice, TYPE, EX.Person) in r
    assert_matches_closure(r, facts)


# ---------------------------------------------------------------------
# explicit facts are never collateral damage
# ---------------------------------------------------------------------
def test_explicitly_asserted_conclusion_survives_retraction_of_premises():
    facts = diamond_facts()
    r = make_reasoner(facts)
    assert (EX.x, TYPE, EX.D) in r  # derived twice
    # user also states the conclusion directly
    r.add((EX.x, TYPE, EX.D))
    facts.append((EX.x, TYPE, EX.D))
    r.retract((EX.B, SC, EX.D))
    facts.remove((EX.B, SC, EX.D))
    r.retract((EX.C, SC, EX.D))
    facts.remove((EX.C, SC, EX.D))
    assert (EX.x, TYPE, EX.D) in r
    assert r.is_explicit((EX.x, TYPE, EX.D))
    assert r.explain((EX.x, TYPE, EX.D)) == ["explicit"]
    assert_matches_closure(r, facts)


def test_retracting_explicit_fact_that_is_still_derivable_keeps_it():
    facts = diamond_facts() + [(EX.x, TYPE, EX.D)]
    r = make_reasoner(facts)
    r.retract((EX.x, TYPE, EX.D))
    facts.remove((EX.x, TYPE, EX.D))
    # no longer explicit, but still derivable via B and C
    assert (EX.x, TYPE, EX.D) in r
    assert not r.is_explicit((EX.x, TYPE, EX.D))
    assert r.is_derived((EX.x, TYPE, EX.D))
    assert_matches_closure(r, facts)


# ---------------------------------------------------------------------
# explanations
# ---------------------------------------------------------------------
def test_explain_reports_rule_and_antecedents():
    r = make_reasoner(property_facts())
    reasons = r.explain((EX.alice, TYPE, EX.Person))
    assert len(reasons) == 1
    just = reasons[0]
    assert just.rule == "domain"
    assert (EX.knows, DOM, EX.Person) in just.antecedents
    assert (EX.alice, EX.knows, EX.bob) in just.antecedents


def test_explain_invalidated_after_retraction():
    facts = property_facts()
    r = make_reasoner(facts)
    before = r.explain((EX.alice, TYPE, EX.Person))
    assert before
    r.retract((EX.alice, EX.worksWith, EX.bob))
    with pytest.raises(KeyError):
        r.explain((EX.alice, TYPE, EX.Person))


def test_proof_tree_marks_cycles():
    # A sc B and A sc X end up supporting each other (via the explicit
    # facts X sc B and B sc X); the proof guard marks the cycle instead
    # of recursing forever.
    r = make_reasoner(
        [(EX.A, SC, EX.Y), (EX.Y, SC, EX.B), (EX.X, SC, EX.B), (EX.B, SC, EX.X)]
    )
    assert (EX.A, SC, EX.X) in r
    proof = r.proof((EX.A, SC, EX.X))
    assert proof[0] == "derived"
    assert "cycle" in repr(proof)


def test_proof_tree_is_grounded_in_explicit_facts():
    r = make_reasoner([(EX.A, SC, EX.B), (EX.B, SC, EX.A)])
    proof = r.proof((EX.A, SC, EX.A))
    assert proof[0] == "derived"
    assert "explicit" in repr(proof)


def test_retract_unknown_fact_raises():
    r = make_reasoner()
    with pytest.raises(KeyError):
        r.retract((EX.a, SC, EX.b))


# ---------------------------------------------------------------------
# incremental state always equals a from-scratch closure
# ---------------------------------------------------------------------
def test_random_mutation_sequence_matches_full_closure():
    rng = random.Random(20261007)
    nodes = [EX[f"n{i}"] for i in range(6)]
    classes = [EX[f"C{i}"] for i in range(5)]
    props = [EX[f"p{i}"] for i in range(4)]
    pool = (
        [(a, SC, b) for a in classes for b in classes if a != b]
        + [(p, SP, q) for p in props for q in props if p != q]
        + [(p, DOM, c) for p in props for c in classes]
        + [(p, RNG, c) for p in props for c in classes]
        + [(n, TYPE, c) for n in nodes for c in classes]
        + [(a, p, b) for a in nodes for p in props for b in nodes]
    )
    reasoner = IncrementalRDFSReasoner()
    explicit: list = []
    for step in range(120):
        if explicit and rng.random() < 0.4:
            victim = rng.choice(explicit)
            reasoner.retract(victim)
            explicit.remove(victim)
        else:
            fact = rng.choice(pool)
            if fact not in explicit:
                reasoner.add(fact)
                explicit.append(fact)
        assert set(reasoner.conclusions()) == full_closure(explicit), f"step {step}"
