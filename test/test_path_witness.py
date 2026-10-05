"""Tests for shortest witness paths returned by :func:`eval_path_witness`.

The witnesses are validated by replaying every recorded step against the
original graph, with special attention to inverse edges and sequence/branch
join points.
"""

from __future__ import annotations

import time

import pytest

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.paths import (
    FORWARD,
    INVERSE,
    AlternativePath,
    InvPath,
    MulPath,
    OneOrMore,
    PathLimitExceeded,
    PathWitness,
    SequencePath,
    WitnessStep,
    ZeroOrMore,
    ZeroOrOne,
    eval_path,
    eval_path_witness,
)

EX = Namespace("https://example.org/")


def _make_graph() -> Graph:
    g = Graph()
    g.parse(
        data="""
        @prefix : <https://example.org/> .
        :a :p1 :c ; :p2 :f .
        :c :p2 :e ; :p3 :g .
        :g :p3 :h ; :p2 :j .
        :h :p3 :a ; :p2 :g .
        :q :px :q .
        """,
        format="n3",
    )
    return g


GRAPH = _make_graph()


def _replay(graph: Graph, subject, obj, witness: PathWitness) -> None:
    """Replay a witness step by step and assert it connects subject -> obj."""
    assert witness.subject == subject
    assert witness.object == obj
    assert witness.nodes[0] == subject
    assert witness.nodes[-1] == obj
    assert len(witness.nodes) == len(witness) + 1

    current = subject
    for (triple_s, predicate, triple_o), reversed_dir in witness.steps:
        assert (triple_s, predicate, triple_o) in graph
        if reversed_dir == FORWARD:
            assert current == triple_s
            current = triple_o
        else:
            assert current == triple_o
            current = triple_s
    assert current == obj


def _pairs(result):
    return {(s, o) for s, o, _ in result}


def test_plain_predicate_witness():
    result = list(eval_path_witness(GRAPH, (EX.a, EX.p1, None)))
    assert _pairs(result) == {(EX.a, EX.c)}
    s, o, witness = result[0]
    assert witness.triples == ((EX.a, EX.p1, EX.c),)
    assert witness.directions == (FORWARD,)
    _replay(GRAPH, s, o, witness)


def test_sequence_witness_splices_at_join():
    result = list(eval_path_witness(GRAPH, (EX.a, EX.p1 / EX.p2, None)))
    assert _pairs(result) == {(EX.a, EX.e)}
    s, o, witness = result[0]
    assert witness.triples == (
        (EX.a, EX.p1, EX.c),
        (EX.c, EX.p2, EX.e),
    )
    assert witness.directions == (FORWARD, FORWARD)
    assert witness.nodes == (EX.a, EX.c, EX.e)
    _replay(GRAPH, s, o, witness)


def test_alternative_picks_one_edge():
    result = list(eval_path_witness(GRAPH, (EX.a, EX.p1 | EX.p2, None)))
    assert _pairs(result) == {(EX.a, EX.c), (EX.a, EX.f)}
    for s, o, witness in result:
        assert len(witness) == 1
        _replay(GRAPH, s, o, witness)


def test_inverse_atom_witness():
    result = list(eval_path_witness(GRAPH, (EX.c, ~EX.p1, None)))
    assert _pairs(result) == {(EX.c, EX.a)}
    s, o, witness = result[0]
    assert witness.triples == ((EX.a, EX.p1, EX.c),)
    assert witness.directions == (INVERSE,)
    assert witness.nodes == (EX.c, EX.a)
    _replay(GRAPH, s, o, witness)


def test_inverse_sequence_object_bound_witness():
    # ^(p1/p2) connects e -> a, read via two inverse edges.
    result = list(eval_path_witness(GRAPH, (None, ~(EX.p1 / EX.p2), EX.a)))
    assert _pairs(result) == {(EX.e, EX.a)}
    s, o, witness = result[0]
    assert witness.triples == (
        (EX.c, EX.p2, EX.e),
        (EX.a, EX.p1, EX.c),
    )
    assert witness.directions == (INVERSE, INVERSE)
    assert witness.nodes == (EX.e, EX.c, EX.a)
    _replay(GRAPH, s, o, witness)


def test_object_bound_plain_sequence_witness():
    result = list(eval_path_witness(GRAPH, (None, EX.p1 / EX.p2, EX.e)))
    assert _pairs(result) == {(EX.a, EX.e)}
    s, o, witness = result[0]
    assert witness.directions == (FORWARD, FORWARD)
    _replay(GRAPH, s, o, witness)


def test_zero_or_one_zero_length_is_empty():
    result = list(eval_path_witness(GRAPH, (EX.a, EX.p1 * ZeroOrOne, EX.a)))
    assert _pairs(result) == {(EX.a, EX.a)}
    s, o, witness = result[0]
    assert len(witness) == 0
    assert witness.triples == ()
    assert witness.directions == ()
    assert witness.nodes == (EX.a,)
    assert list(witness) == []


def test_zero_or_more_zero_length_is_empty():
    result = list(eval_path_witness(GRAPH, (EX.q, EX.px * ZeroOrMore, EX.q)))
    # A zero-length match and a one-edge self-loop both exist; exactly one
    # witness per pair, the shortest (zero length) must be reported.
    assert _pairs(result) == {(EX.q, EX.q)}
    s, o, witness = result[0]
    assert len(witness) == 0
    assert witness.triples == ()
    _replay(GRAPH, s, o, witness)


def test_one_or_more_self_loop_terminates_with_edge():
    result = list(eval_path_witness(GRAPH, (EX.q, EX.px * OneOrMore, None)))
    assert _pairs(result) == {(EX.q, EX.q)}
    s, o, witness = result[0]
    assert len(witness) == 1
    assert witness.triples == ((EX.q, EX.px, EX.q),)
    _replay(GRAPH, s, o, witness)


def test_one_or_more_cycle_returns_shortest_per_pair():
    result = list(eval_path_witness(GRAPH, (EX.c, EX.p3 * OneOrMore, None)))
    by_object = {o: witness for s, o, witness in result}
    assert set(by_object) == {EX.g, EX.h, EX.a}
    assert len(by_object[EX.g]) == 1
    assert len(by_object[EX.h]) == 2
    assert len(by_object[EX.a]) == 3
    for o, witness in by_object.items():
        _replay(GRAPH, EX.c, o, witness)


def test_no_match_is_empty_not_exception():
    assert list(eval_path_witness(GRAPH, (EX.a, EX.p1, EX.a))) == []
    assert list(eval_path_witness(GRAPH, (EX.f, EX.p3 * OneOrMore, None))) == []


def test_limit_distinct_from_no_match():
    # A dead end cannot exceed any limit: no configuration is expanded.
    assert (
        list(
            eval_path_witness(
                GRAPH, (EX.f, EX.p3 * OneOrMore, None), max_states=0
            )
        )
        == []
    )
    # An expansive search must raise rather than silently return a partial set.
    with pytest.raises(PathLimitExceeded):
        list(
            eval_path_witness(
                GRAPH, (EX.c, EX.p3 * OneOrMore, None), max_states=2
            )
        )


def test_no_duplicate_endpoint_pairs_for_equal_paths():
    path = EX.p1 | EX.p1
    result = list(eval_path_witness(GRAPH, (EX.a, path, EX.c)))
    assert _pairs(result) == {(EX.a, EX.c)}
    assert len(result) == 1


def test_unbound_search_dedups_pairs():
    path = (EX.p2 | EX.p3) * ZeroOrMore
    result = list(eval_path_witness(GRAPH, (None, path, None)))
    pairs = [(s, o) for s, o, _ in result]
    assert len(pairs) == len(set(pairs))
    for s, o, witness in result:
        _replay(GRAPH, s, o, witness)


def test_diamond_shortest_witness():
    g = Graph()
    g.parse(
        data="""
        @prefix : <https://example.org/> .
        :x :direct :y .
        :x :via :m .
        :m :via :y .
        """,
        format="n3",
    )
    path = EX.direct | (EX.via / EX.via)
    result = list(eval_path_witness(g, (EX.x, path, EX.y)))
    assert _pairs(result) == {(EX.x, EX.y)}
    s, o, witness = result[0]
    assert len(witness) == 1
    assert witness.triples[0][1] == EX.direct
    _replay(g, s, o, witness)


def test_nested_repetition_terminates_on_cyclic_graph():
    path = ((EX.p1 | EX.p2) * ZeroOrMore / EX.p3 * OneOrMore) * OneOrMore
    start = time.monotonic()
    result = list(eval_path_witness(GRAPH, (EX.a, path, None)))
    assert time.monotonic() - start < 10
    pairs = [(s, o) for s, o, _ in result]
    assert len(pairs) == len(set(pairs))
    for s, o, witness in result:
        _replay(GRAPH, s, o, witness)


def test_literal_endpoint_witness():
    g = Graph()
    g.add((EX.x, EX.label, Literal("hello")))
    result = list(eval_path_witness(g, (EX.x, EX.label, None)))
    assert _pairs(result) == {(EX.x, Literal("hello"))}
    s, o, witness = result[0]
    assert len(witness) == 1
    _replay(g, s, o, witness)


def test_endpoint_sets_match_existing_evaluator():
    paths = [
        EX.p1,
        EX.p1 / EX.p2,
        EX.p1 | EX.p2,
        ~EX.p1,
        ~(EX.p1 / EX.p2),
        EX.p1 * ZeroOrOne,
        EX.p3 * OneOrMore,
        EX.p3 * ZeroOrMore,
        EX.p1 / EX.p3 / EX.p3,
        (EX.p2 | EX.p3) * ZeroOrMore,
        EX.px * OneOrMore,
        (EX.p1 / EX.p3 * ZeroOrOne) * OneOrMore,
        EX.p1 / ~EX.p3,
        ~EX.p3 * ZeroOrMore,
        ~(~EX.p1),
    ]
    nodes = [EX.a, EX.c, EX.g, EX.h, EX.e, EX.f, EX.j, EX.q]
    for path in paths:
        for subj in [EX.a, EX.c, None]:
            for obj in [EX.a, EX.c, None]:
                t = (subj, path, obj)
                witness_result = list(eval_path_witness(GRAPH, t))
                assert _pairs(witness_result) == set(eval_path(GRAPH, t)), t
                for s, o, witness in witness_result:
                    _replay(GRAPH, s, o, witness)


def test_requires_predicate_path():
    with pytest.raises(TypeError):
        list(eval_path_witness(GRAPH, (EX.a, None, None)))
    with pytest.raises(TypeError):
        list(eval_path_witness(GRAPH, (EX.a, 42, None)))  # type: ignore[arg-type]


def test_witness_step_and_witness_value_semantics():
    triple = (EX.a, EX.p1, EX.c)
    step = WitnessStep(triple, FORWARD)
    assert step.triple == triple
    assert step.reversed is FORWARD

    witness = PathWitness(EX.a, EX.c, [step])
    assert len(witness) == 1
    assert list(witness) == [step]
    assert witness.triples == (triple,)
    assert witness.nodes == (EX.a, EX.c)
    assert witness == PathWitness(EX.a, EX.c, [step])
    assert isinstance(witness.steps, tuple)
    assert isinstance(witness, object)


def test_branch_and_inverse_join_inside_repetition():
    # (p1 | ^p3)? exercises a branch where one arm is an inverse edge, then a
    # repetition; witnesses must replay correctly at every join.
    path = (EX.p1 | ~EX.p3) * OneOrMore
    result = list(eval_path_witness(GRAPH, (EX.a, path, None)))
    pairs = [(s, o) for s, o, _ in result]
    assert len(pairs) == len(set(pairs))
    for s, o, witness in result:
        _replay(GRAPH, s, o, witness)
        assert len(witness) >= 1
