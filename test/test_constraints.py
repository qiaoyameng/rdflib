from __future__ import annotations

from typing import Union

from rdflib import BNode, Graph, Literal, URIRef, XSD
from rdflib.constraints import (
    CardinalityConstraint,
    MutuallyExclusivePredicateGroup,
    NodeKind,
    RepairPlan,
    SearchStatus,
    ValueType,
    ValueTypeConstraint,
    check_focus_constraints,
)

EG = "urn:example:"
FOCUS = URIRef(EG + "focus")
NAME = URIRef(EG + "name")
NICK = URIRef(EG + "nick")
PRIMARY = URIRef(EG + "primary")
SECONDARY = URIRef(EG + "secondary")


def triple(
    predicate: URIRef, value: Union[URIRef, BNode, Literal]
) -> tuple[URIRef, URIRef, Union[URIRef, BNode, Literal]]:
    return (FOCUS, predicate, value)


def test_reports_facts_and_finds_all_minimal_type_and_count_repairs() -> None:
    bnode = BNode("blank")
    graph = Graph()
    graph.add(triple(NAME, bnode))
    graph.add(triple(NAME, Literal("first")))
    graph.add(triple(NAME, Literal("second")))

    candidate = triple(NAME, Literal("candidate"))
    result = check_focus_constraints(
        graph,
        FOCUS,
        [
            CardinalityConstraint(NAME, min_count=1, max_count=1),
            ValueTypeConstraint(NAME, [NodeKind.LITERAL]),
        ],
        [candidate],
    )

    assert {violation.kind.value for violation in result.violations} == {
        "max_count",
        "value_type",
    }
    assert triple(NAME, bnode) in result.violations[0].facts
    value_violation = next(
        violation
        for violation in result.violations
        if violation.kind.value == "value_type"
    )
    assert value_violation.facts == (triple(NAME, bnode),)
    assert result.status is SearchStatus.COMPLETE
    assert len(graph) == 3

    assert len(result.repairs) == 2
    assert all(plan.modification_count == 2 for plan in result.repairs)
    assert {plan.remove for plan in result.repairs} == {
        (triple(NAME, bnode), triple(NAME, Literal("second"))),
        (triple(NAME, bnode), triple(NAME, Literal("first"))),
    }
    assert all(plan.add == () for plan in result.repairs)


def test_required_predicate_interacts_with_mutually_exclusive_group() -> None:
    graph = Graph()
    graph.add(triple(PRIMARY, Literal("primary")))
    before = set(graph)

    primary_candidate = triple(PRIMARY, Literal("another primary"))
    secondary_candidate = triple(SECONDARY, Literal("secondary"))
    result = check_focus_constraints(
        graph,
        FOCUS,
        [
            CardinalityConstraint(SECONDARY, min_count=1),
            MutuallyExclusivePredicateGroup([PRIMARY, SECONDARY]),
        ],
        [primary_candidate, secondary_candidate],
    )

    assert len(result.violations) == 1
    assert result.violations[0].kind.value == "min_count"
    assert result.violations[0].facts == ()
    assert result.status is SearchStatus.COMPLETE
    assert result.repairs == (
        RepairPlan(
            remove=(triple(PRIMARY, Literal("primary")),),
            add=(triple(SECONDARY, Literal("secondary")),),
        ),
    )
    assert set(graph) == before


def test_mutually_exclusive_violation_reports_both_fact_groups() -> None:
    graph = Graph()
    graph.add(triple(PRIMARY, Literal("primary")))
    graph.add(triple(SECONDARY, Literal("secondary")))

    result = check_focus_constraints(
        graph,
        FOCUS,
        [MutuallyExclusivePredicateGroup([PRIMARY, SECONDARY])],
    )

    assert result.status is SearchStatus.COMPLETE
    assert result.violations[0].facts == (
        triple(PRIMARY, Literal("primary")),
        triple(SECONDARY, Literal("secondary")),
    )
    assert {plan.remove for plan in result.repairs} == {
        (triple(PRIMARY, Literal("primary")),),
        (triple(SECONDARY, Literal("secondary")),),
    }


def test_bnodes_and_language_tagged_literals_are_not_stringified() -> None:
    graph = Graph()
    graph.add(triple(NAME, BNode("x")))
    graph.add(triple(NICK, Literal("x")))

    bnode_candidate = triple(NAME, BNode("y"))
    en_candidate = triple(NAME, Literal("x", lang="en"))
    plain_candidate = triple(NAME, Literal("x"))
    result = check_focus_constraints(
        graph,
        FOCUS,
        [
            CardinalityConstraint(NAME, min_count=1, max_count=1),
            ValueTypeConstraint(
                NAME,
                [ValueType(NodeKind.LITERAL, language="en")],
            ),
        ],
        [bnode_candidate, en_candidate, plain_candidate],
    )

    assert result.status is SearchStatus.COMPLETE
    assert len(result.repairs) == 1
    assert result.repairs[0].remove == (triple(NAME, BNode("x")),)
    assert result.repairs[0].add == (en_candidate,)
    assert plain_candidate not in result.repairs[0].add

    literal_result = check_focus_constraints(
        graph,
        FOCUS,
        [
            CardinalityConstraint(NICK, min_count=1, max_count=1),
            ValueTypeConstraint(NICK, [XSD.string]),
        ],
        [triple(NICK, Literal("x", lang="en"))],
    )
    assert literal_result.status is SearchStatus.UNSATISFIABLE


def test_truncated_unsatisfiable_and_incomplete_are_distinct() -> None:
    graph = Graph()
    graph.add(triple(NAME, Literal("one")))
    graph.add(triple(NAME, Literal("two")))

    constraints = [CardinalityConstraint(NAME, min_count=1, max_count=1)]
    truncated = check_focus_constraints(
        graph,
        FOCUS,
        constraints,
        max_repairs=1,
    )
    assert truncated.status is SearchStatus.TRUNCATED
    assert len(truncated.repairs) == 1

    unsat_graph = Graph()
    unsat_result = check_focus_constraints(
        unsat_graph,
        FOCUS,
        [CardinalityConstraint(NAME, min_count=1)],
    )
    assert unsat_result.status is SearchStatus.UNSATISFIABLE
    assert unsat_result.repairs == ()

    incomplete = check_focus_constraints(
        graph,
        FOCUS,
        constraints,
        max_modifications=0,
    )
    assert incomplete.status is SearchStatus.INCOMPLETE
    assert incomplete.repairs == ()

    deeper_graph = Graph()
    deeper_graph.add(triple(PRIMARY, Literal("primary")))
    deeper = check_focus_constraints(
        deeper_graph,
        FOCUS,
        [
            CardinalityConstraint(SECONDARY, min_count=1),
            MutuallyExclusivePredicateGroup([PRIMARY, SECONDARY]),
        ],
        [triple(SECONDARY, Literal("secondary"))],
        max_modifications=0,
    )
    assert deeper.status is SearchStatus.INCOMPLETE
    assert deeper.repairs == ()

    only_at_two = check_focus_constraints(
        deeper_graph,
        FOCUS,
        [
            CardinalityConstraint(SECONDARY, min_count=1),
            MutuallyExclusivePredicateGroup([PRIMARY, SECONDARY]),
        ],
        [triple(SECONDARY, Literal("secondary"))],
        max_modifications=1,
    )
    assert only_at_two.status is SearchStatus.INCOMPLETE
    assert only_at_two.repairs == ()
