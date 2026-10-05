from __future__ import annotations

import logging
from typing import Union

import pytest

from rdflib import RDF, RDFS, Graph, Namespace, URIRef
from rdflib.namespace import DCAT, DCTERMS
from rdflib.paths import (
    AlternativePath,
    InvPath,
    MulPath,
    NegatedPath,
    OneOrMore,
    Path,
    PathWitnessLimitExceeded,
    SequencePath,
    WitnessStep,
    eval_path,
    eval_path_witness,
    ZeroOrMore,
    ZeroOrOne,
)

g = Graph()
nsm = g.namespace_manager


@pytest.mark.parametrize(
    "path, no_nsm, with_nsm",
    [
        (~RDF.type, f"^<{RDF.type}>", "^rdf:type"),
        (
            RDF.type / RDFS.subClassOf,
            f"<{RDF.type}>/<{RDFS.subClassOf}>",
            "rdf:type/rdfs:subClassOf",
        ),
        (
            RDF.type | RDFS.subClassOf,
            f"<{RDF.type}>|<{RDFS.subClassOf}>",
            "rdf:type|rdfs:subClassOf",
        ),
        (-RDF.type, f"!(<{RDF.type}>)", "!(rdf:type)"),
        # type errors: Unsupported operand types for * ("URIRef" and "str")
        # note on type errors: The operator is defined but in an odd place
        (
            RDFS.subClassOf * ZeroOrMore,  # type: ignore[operator]
            f"<{RDFS.subClassOf}>*",
            "rdfs:subClassOf*",
        ),
        (
            RDFS.subClassOf * OneOrMore,  # type: ignore[operator]
            f"<{RDFS.subClassOf}>+",
            "rdfs:subClassOf+",
        ),
        (
            RDFS.subClassOf * ZeroOrOne,  # type: ignore[operator]
            f"<{RDFS.subClassOf}>?",
            "rdfs:subClassOf?",
        ),
        (
            RDF.type / MulPath(RDFS.subClassOf, "*"),
            f"<{RDF.type}>/<{RDFS.subClassOf}>*",
            "rdf:type/rdfs:subClassOf*",
        ),
        (
            RDF.type / ((SequencePath(RDFS.subClassOf)) * "*"),
            f"<{RDF.type}>/<{RDFS.subClassOf}>*",
            "rdf:type/rdfs:subClassOf*",
        ),
        (
            RDF.type / RDFS.subClassOf * "*",
            f"(<{RDF.type}>/<{RDFS.subClassOf}>)*",
            "(rdf:type/rdfs:subClassOf)*",
        ),
        (
            -(RDF.type | RDFS.subClassOf),
            f"!(<{RDF.type}>|<{RDFS.subClassOf}>)",
            "!(rdf:type|rdfs:subClassOf)",
        ),
        (
            -(RDF.type | ((SequencePath(RDFS.subClassOf)) * "*")),
            f"!(<{RDF.type}>|<{RDFS.subClassOf}>*)",
            "!(rdf:type|rdfs:subClassOf*)",
        ),
        (
            SequencePath(RDFS.subClassOf),
            f"<{RDFS.subClassOf}>",
            "rdfs:subClassOf",
        ),
        (
            AlternativePath(RDFS.subClassOf),
            f"<{RDFS.subClassOf}>",
            "rdfs:subClassOf",
        ),
    ],
)
def test_paths_n3(
    path: Union[InvPath, SequencePath, AlternativePath, MulPath, NegatedPath],
    no_nsm: str,
    with_nsm: str,
) -> None:
    logging.debug("path = %s", path)
    assert path.n3() == no_nsm
    assert path.n3(nsm) == with_nsm


def test_mulpath_n3():
    uri = "http://example.com/foo"
    n3 = (URIRef(uri) * ZeroOrMore).n3()
    assert n3 == "<" + uri + ">*"


@pytest.mark.parametrize(
    ["lhs", "rhs"],
    [
        (DCTERMS.temporal / DCAT.endDate, DCTERMS.temporal / DCAT.endDate),
        (SequencePath(DCTERMS.temporal, DCAT.endDate), DCTERMS.temporal / DCAT.endDate),
    ],
)
def test_eq(lhs: Path, rhs: Path) -> None:
    logging.debug("lhs = %s/%r, rhs = %s/%r", type(lhs), lhs, type(rhs), rhs)
    assert lhs == rhs


@pytest.mark.parametrize(
    ["lhs", "rhs"],
    [
        (DCTERMS.temporal / DCAT.endDate, DCTERMS.temporal / DCAT.endDate),
        (SequencePath(DCTERMS.temporal, DCAT.endDate), DCTERMS.temporal / DCAT.endDate),
    ],
)
def test_hash(lhs: Path, rhs: Path) -> None:
    logging.debug("lhs = %s/%r, rhs = %s/%r", type(lhs), lhs, type(rhs), rhs)
    assert hash(lhs) == hash(rhs)


@pytest.mark.parametrize(
    ["insert_path", "check_path"],
    [
        (DCTERMS.temporal / DCAT.endDate, DCTERMS.temporal / DCAT.endDate),
        (SequencePath(DCTERMS.temporal, DCAT.endDate), DCTERMS.temporal / DCAT.endDate),
    ],
)
def test_dict_key(insert_path: Path, check_path: Path) -> None:
    d = {insert_path: "foo"}
    assert d[check_path] == "foo"


EX = Namespace("http://example.org/")

WITNESS_GRAPH_DATA = """
@prefix : <http://example.org/> .
:a :p1 :c ; :p2 :f .
:c :p2 :e ; :p3 :g .
:g :p3 :h ; :p2 :j .
:h :p3 :a ; :p2 :g .
:q :px :q .
"""


@pytest.fixture
def witness_graph() -> Graph:
    graph = Graph()
    graph.parse(data=WITNESS_GRAPH_DATA, format="n3")
    return graph


def replay_witness(
    graph: Graph,
    subj: object,
    obj: object,
    witness: tuple[WitnessStep, ...],
) -> None:
    """Put every step of a witness back into the graph and walk it."""
    if len(witness) == 0:
        assert subj == obj
        return
    current = subj
    for step in witness:
        triple = step.triple
        assert triple in graph
        triple_subj, _predicate, triple_obj = triple
        if step.reversed:
            assert triple_obj == current
            current = triple_subj
        else:
            assert triple_subj == current
            current = triple_obj
    assert current == obj


@pytest.mark.parametrize(
    "path",
    [
        EX.p1 / EX.p2,
        ~EX.p1,
        EX.p1 * ZeroOrOne,
        EX.p3 * OneOrMore,
        (EX.p2 | EX.p3) * ZeroOrMore,
        EX.px * OneOrMore,
        ~(EX.p1 / EX.p2),
        EX.p1 / EX.p3 / EX.p3,
        (EX.p3 * OneOrMore) * OneOrMore,
        (EX.p3 * OneOrMore) * ZeroOrMore,
        (~EX.p3) * OneOrMore,
        -EX.p1,
        -(EX.p1 | EX.p2),
        EX.p2 / EX.p3,
        (EX.p1 | EX.px) / EX.p2,
        (EX.p3 * ZeroOrMore) / EX.p2,
        (~(EX.p2 | EX.p3)) * ZeroOrMore,
    ],
)
@pytest.mark.parametrize(
    ["subj_bound", "obj_bound"],
    [
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    ],
)
def test_path_witness_replay(
    witness_graph: Graph, path: Path, subj_bound: bool, obj_bound: bool
) -> None:
    subj = EX.a if subj_bound else None
    obj = EX.a if obj_bound else None

    rows = list(eval_path_witness(witness_graph, (subj, path, obj)))

    # No endpoint pair may occur twice.
    pairs = [(s, o) for s, o, _w in rows]
    assert len(pairs) == len(set(pairs))

    # Every witness must replay against the actual graph triples.
    for s, o, witness in rows:
        replay_witness(witness_graph, s, o, witness)

    # The endpoint set must agree with the plain endpoint evaluation.
    assert set(pairs) == set(eval_path(witness_graph, (subj, path, obj)))


def test_path_witness_is_shortest(witness_graph: Graph) -> None:
    # :a -> :c via :q takes one edge, :a -> :p -> :b -> :p -> :c takes two.
    witness_graph.parse(
        data="""
        @prefix : <http://example.org/> .
        :x :p :y . :y :p :c . :x :q :c .
        """,
        format="n3",
    )
    rows = list(
        eval_path_witness(witness_graph, (EX.x, EX.q | (EX.p / EX.p), None))
    )
    assert [(s, o, len(w)) for s, o, w in rows] == [(EX.x, EX.c, 1)]


def test_path_witness_tie_arbitrary_but_unique(witness_graph: Graph) -> None:
    # Two equally long ways from :a to :c (direct :q and :p/:p): exactly one
    # pair is returned, with a witness of minimum length.
    witness_graph.parse(
        data="""
        @prefix : <http://example.org/> .
        :a :q :c .
        """,
        format="n3",
    )
    path = EX.q | (EX.p1 / EX.p2 / EX.p3)
    rows = list(eval_path_witness(witness_graph, (EX.a, path, EX.c)))
    assert len(rows) == 1
    _s, _o, witness = rows[0]
    assert len(witness) == 1


def test_path_witness_zero_length_is_empty(witness_graph: Graph) -> None:
    # p* and p? match a node against itself with an empty witness.
    for path in [EX.p1 * ZeroOrMore, EX.p1 * ZeroOrOne]:
        rows = list(eval_path_witness(witness_graph, (EX.a, path, EX.a)))
        assert rows == [(EX.a, EX.a, ())]


def test_path_witness_zero_length_unbound(witness_graph: Graph) -> None:
    rows = list(
        eval_path_witness(witness_graph, (EX.a, EX.p1 * ZeroOrMore, None))
    )
    pairs = [(s, o, len(w)) for s, o, w in rows]
    # The zero-length self pair appears alongside the real edge.
    assert (EX.a, EX.a, 0) in pairs
    assert (EX.a, EX.c, 1) in pairs


def test_path_witness_reverse_direction(witness_graph: Graph) -> None:
    rows = list(eval_path_witness(witness_graph, (None, ~EX.p1, EX.a)))
    assert len(rows) == 1
    subj, obj, witness = rows[0]
    assert (subj, obj) == (EX.c, EX.a)
    assert len(witness) == 1
    step = witness[0]
    # The stored triple keeps its stored orientation; the direction flag says
    # it was traversed object -> subject.
    assert step.triple == (EX.a, EX.p1, EX.c)
    assert step.reversed is True


def test_path_witness_sequence_order_and_direction(witness_graph: Graph) -> None:
    rows = list(
        eval_path_witness(witness_graph, (None, EX.p1 / EX.p3 / EX.p3, EX.h))
    )
    assert len(rows) == 1
    _subj, _obj, witness = rows[0]
    predicates = [step.triple[1] for step in witness]
    assert predicates == [EX.p1, EX.p3, EX.p3]
    assert all(step.reversed is False for step in witness)

    # The same path searched backwards must still present the witness in the
    # forward traversal order, with correct inverse-edge flags.
    rows_back = list(
        eval_path_witness(
            witness_graph, (None, ~(EX.p1 / EX.p3 / EX.p3), EX.a)
        )
    )
    assert len(rows_back) == 1
    _subj, _obj, witness_back = rows_back[0]
    assert [step.triple[1] for step in witness_back] == [
        EX.p3,
        EX.p3,
        EX.p1,
    ]
    assert [step.reversed for step in witness_back] == [True, True, True]


def test_path_witness_branch_splice(witness_graph: Graph) -> None:
    # Alternative in the middle of a sequence: each step must connect to the
    # next one when replayed.
    path = EX.p1 / (EX.p2 | EX.p3) / EX.p3
    rows = list(eval_path_witness(witness_graph, (EX.a, path, None)))
    assert len(rows) == 1
    subj, obj, witness = rows[0]
    assert (subj, obj) == (EX.a, EX.h)
    replay_witness(witness_graph, subj, obj, witness)
    assert len(witness) == 3


def test_path_witness_negated_paths(witness_graph: Graph) -> None:
    rows = list(eval_path_witness(witness_graph, (EX.a, -EX.p1, None)))
    assert [(s, o) for s, o, _w in rows] == [(EX.a, EX.f)]

    rows = list(
        eval_path_witness(witness_graph, (EX.g, -~EX.p2, None))
    )
    # Negated inverse property set ``!^p2``: a forward edge (s, p, o)
    # qualifies unless the inverse edge (o, p2, s) exists. From :g the
    # :p2 edge to :j qualifies (no :j :p2 :g triple), the :p3 edge to :h
    # does not (:h :p2 :g exists).
    assert {(s, o) for s, o, _w in rows} == {(EX.g, EX.j)}
    for s, o, witness in rows:
        replay_witness(witness_graph, s, o, witness)
        assert len(witness) == 1
        assert witness[0].triple == (EX.g, EX.p2, EX.j)
        assert witness[0].reversed is False


def test_path_witness_negated_inverse_sequence(witness_graph: Graph) -> None:
    # ^(!p1) walks one edge backwards, excluding triples whose forward p1
    # edge between the same two nodes exists.
    rows = list(eval_path_witness(witness_graph, (EX.a, ~(-EX.p1), None)))
    pairs = {(s, o) for s, o, _w in rows}
    # The incoming :p3 edge from :h qualifies; the incoming :p1 edge from
    # :c is excluded because (:a :p1 :c) exists.
    assert pairs == {(EX.a, EX.h)}
    for s, o, witness in rows:
        replay_witness(witness_graph, s, o, witness)
        assert witness[0].triple == (EX.h, EX.p3, EX.a)
        assert witness[0].reversed is True


def test_path_witness_negated_property_set_truth_table() -> None:
    # Explicit SPARQL 1.1 negated property set semantics.
    # From :x the incoming edges are (:u :r :x) and (:v :r :x); in
    # addition :x :p :u exists.
    graph = Graph()
    graph.parse(
        data="""
        @prefix : <http://example.org/> .
        :u :r :x .
        :x :p :u .
        :v :r :x .
        """,
        format="n3",
    )

    def endpoints(path: Path) -> set[object]:
        return {o for _s, o, _w in eval_path_witness(graph, (EX.x, path, None))}

    # ^!p walked backwards: excluded members keep their direction; the
    # incoming edges both have predicate r (not p), so both qualify.
    assert endpoints(~(-EX.p)) == {EX.u, EX.v}
    # ^!^p: the inverse member ^p matches (u, r, x) because (x, p, u)
    # exists, so :u is excluded and only :v qualifies.
    assert endpoints(~(-~EX.p)) == {EX.v}
    # Plain negation forward: the only outgoing edge uses p, so none match.
    assert endpoints(-EX.p) == set()
    # Negated inverse member forward: (x, p, u) matches ^p only if
    # (u, p, x) exists, which it does not, so it qualifies.
    assert endpoints(-~EX.p) == {EX.u}


def test_path_witness_cycles_terminate() -> None:
    graph = Graph()
    graph.parse(
        data="""
        @prefix : <http://example.org/> .
        :z :r :z . :z :r :y .
        """,
        format="n3",
    )
    nested = (EX.r * OneOrMore) * OneOrMore
    rows = list(eval_path_witness(graph, (None, nested, None)))
    assert {(s, o) for s, o, _w in rows} == {
        (EX.z, EX.z),
        (EX.z, EX.y),
    }
    for s, o, witness in rows:
        replay_witness(graph, s, o, witness)
        # z -> z is witnessed by a single self-loop edge, not a cycle stack.
        assert len(witness) == 1


def test_path_witness_max_states_distinct_from_no_match(
    witness_graph: Graph,
) -> None:
    # No match: empty result, no exception.
    assert list(eval_path_witness(witness_graph, (EX.q, EX.p1, None))) == []
    assert (
        list(
            eval_path_witness(
                witness_graph, (EX.q, EX.p1, None), max_states=1000
            )
        )
        == []
    )

    # Exceeding the budget on a graph with possible matches must raise.
    with pytest.raises(PathWitnessLimitExceeded):
        list(
            eval_path_witness(
                witness_graph,
                (None, (EX.p2 | EX.p3) * ZeroOrMore, None),
                max_states=1,
            )
        )


def test_path_witness_zero_length_needs_no_budget(
    witness_graph: Graph,
) -> None:
    rows = list(
        eval_path_witness(
            witness_graph, (EX.a, EX.p1 * ZeroOrMore, EX.a), max_states=0
        )
    )
    assert rows == [(EX.a, EX.a, ())]


def test_path_witness_via_path_method(witness_graph: Graph) -> None:
    path = EX.p1 / EX.p2
    rows = list(path.eval_witness(witness_graph, EX.a))
    assert [(s, o) for s, o, _w in rows] == [(EX.a, EX.e)]
    replay_witness(witness_graph, *rows[0])


def test_witness_step_fields() -> None:
    step = WitnessStep((EX.a, EX.p1, EX.c), True)
    assert step.triple == (EX.a, EX.p1, EX.c)
    assert step.reversed is True
