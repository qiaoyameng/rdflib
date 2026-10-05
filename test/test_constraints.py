from itertools import combinations

import pytest

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.constraints import (
    MutexPredicateGroup,
    PropertyConstraint,
    Repair,
    SearchStatus,
    ValueType,
    ViolationType,
    check_constraints,
)

EX = "http://example.org/"
focus = URIRef(EX + "node")
required = URIRef(EX + "required")
alternative = URIRef(EX + "alternative")
existing_required = (focus, required, URIRef(EX + "old"))
alternative_candidate = (focus, alternative, BNode("candidate"))


def brute_force(graph, focus, constraints, candidates, max_solutions):
    current = set(graph.triples((focus, None, None)))
    properties = {
        constraint.predicate: constraint
        for constraint in constraints
        if isinstance(constraint, PropertyConstraint)
    }
    groups = [
        constraint
        for constraint in constraints
        if isinstance(constraint, MutexPredicateGroup)
    ]
    predicates = set(properties)
    for group in groups:
        predicates.update(group.predicates)

    additions = set()
    for subject, predicate, obj in candidates:
        if subject != focus or predicate not in predicates:
            continue
        constraint = properties.get(predicate)
        if constraint is not None and not constraint.allows_value(obj):
            continue
        additions.add((subject, predicate, obj))
    additions.difference_update(current)

    actions = [("delete", triple) for triple in sorted(current)]
    actions.extend(("add", triple) for triple in sorted(additions))

    def valid(triples):
        for constraint in properties.values():
            values = [
                obj
                for _, predicate, obj in triples
                if predicate == constraint.predicate
            ]
            if constraint.min_count is not None and len(values) < constraint.min_count:
                return False
            if constraint.max_count is not None and len(values) > constraint.max_count:
                return False
            if any(not constraint.allows_value(obj) for obj in values):
                return False
        for group in groups:
            used = {
                predicate
                for _, predicate, _ in triples
                if predicate in group.predicates
            }
            if len(used) > 1:
                return False
        return True

    expected = []
    for cost in range(len(actions) + 1):
        for selected in combinations(actions, cost):
            deleted = {
                triple for operation, triple in selected if operation == "delete"
            }
            added = {
                triple for operation, triple in selected if operation == "add"
            }
            if valid((current - deleted) | added):
                expected.append(
                    Repair(tuple(sorted(deleted)), tuple(sorted(added)))
                )
        if expected:
            break
    return tuple(expected[:max_solutions])


def test_reports_all_minimum_repairs_and_does_not_mutate_graph():
    graph = Graph()
    other = URIRef(EX + "other")
    one = (focus, required, URIRef(EX + "one"))
    two = (focus, required, URIRef(EX + "two"))
    ignored = (other, required, URIRef(EX + "ignored"))
    graph.add(one)
    graph.add(two)
    graph.add(ignored)
    constraint = PropertyConstraint(required, max_count=1)

    report = check_constraints(graph, focus, [constraint])

    assert report.conforms is False
    assert report.status is SearchStatus.COMPLETE
    assert report.truncated is False
    assert report.minimum_changes == 1
    assert report.repairs == brute_force(graph, focus, [constraint], [], 100)
    assert report.repairs == (
        Repair((one,), ()),
        Repair((two,), ()),
    )
    assert report.violations[0].type is ViolationType.MAX_COUNT
    assert set(report.violations[0].facts) == {one, two}
    assert set(graph) == {one, two, ignored}


def test_value_type_repair_preserves_bnode_identity():
    graph = Graph()
    wrong = (focus, required, Literal("node"))
    replacement = (focus, required, BNode("node"))
    graph.add(wrong)
    constraint = PropertyConstraint(
        required,
        min_count=1,
        value_types=[ValueType.BLANK_NODE],
    )

    report = check_constraints(graph, focus, [constraint], [replacement])

    assert report.status is SearchStatus.COMPLETE
    assert report.minimum_changes == 2
    assert report.repairs == (Repair((wrong,), (replacement,)),)
    assert report.violations[0].type is ViolationType.VALUE_TYPE
    assert report.violations[0].facts == (wrong,)


def test_language_tagged_literal_is_not_stringified():
    graph = Graph()
    english = (focus, required, Literal("name", lang="en"))
    plain = (focus, required, Literal("name"))
    graph.add(english)
    graph.add(plain)
    constraint = PropertyConstraint(required, max_count=1, value_types=[Literal])

    report = check_constraints(graph, focus, [constraint])

    assert report.repairs == brute_force(graph, focus, [constraint], [], 100)
    assert {repair.deletions[0] for repair in report.repairs} == {english, plain}


def test_same_lexical_value_keeps_term_and_language_identity():
    graph = Graph()
    bnode_value = (focus, required, BNode("same"))
    uri_value = (focus, required, URIRef(EX + "same"))
    english = (focus, alternative, Literal("same", lang="en"))
    german = (focus, alternative, Literal("same", lang="de"))
    graph.add(bnode_value)
    graph.add(uri_value)
    graph.add(english)
    graph.add(german)

    bnode_report = check_constraints(
        graph,
        focus,
        [PropertyConstraint(required, max_count=1, value_types=[BNode])],
    )
    literal_report = check_constraints(
        graph,
        focus,
        [PropertyConstraint(alternative, max_count=1, value_types=[Literal])],
    )

    assert bnode_report.repairs == (Repair((uri_value,), ()),)
    assert {
        repair.deletions[0] for repair in literal_report.repairs
    } == {english, german}


def test_required_property_can_conflict_with_mutex_group():
    graph = Graph()
    graph.add(existing_required)
    constraints = [
        PropertyConstraint(alternative, min_count=1, value_types=[BNode]),
        MutexPredicateGroup([required, alternative]),
    ]

    report = check_constraints(
        graph,
        focus,
        constraints,
        [alternative_candidate],
    )

    assert report.status is SearchStatus.COMPLETE
    assert report.minimum_changes == 2
    assert report.repairs == brute_force(
        graph,
        focus,
        constraints,
        [alternative_candidate],
        100,
    )
    assert report.repairs == (
        Repair((existing_required,), (alternative_candidate,)),
    )
    assert [violation.type for violation in report.violations] == [
        ViolationType.MIN_COUNT,
    ]


def test_mutex_violation_reports_both_conflicting_facts():
    graph = Graph()
    existing_alternative = (focus, alternative, URIRef(EX + "other"))
    graph.add(existing_required)
    graph.add(existing_alternative)
    group = MutexPredicateGroup([required, alternative])

    report = check_constraints(graph, focus, [group])

    assert report.violations[0].type is ViolationType.MUTEX
    assert set(report.violations[0].facts) == {
        existing_required,
        existing_alternative,
    }
    assert report.repairs == brute_force(graph, focus, [group], [], 100)


def test_no_solution_is_distinct_from_incomplete_search():
    graph = Graph()
    graph.add(existing_required)
    constraints = [
        PropertyConstraint(alternative, min_count=1, value_types=[BNode]),
        MutexPredicateGroup([required, alternative]),
    ]

    complete = check_constraints(graph, focus, constraints, [])
    incomplete = check_constraints(
        graph,
        focus,
        constraints,
        [],
        max_states=1,
    )

    assert complete.status is SearchStatus.NO_SOLUTION
    assert complete.repairs == ()
    assert complete.minimum_changes is None
    assert incomplete.status is SearchStatus.INCOMPLETE
    assert incomplete.repairs == ()
    assert incomplete.minimum_changes is None
    assert incomplete.states_checked == 1


def test_truncated_when_solution_limit_is_reached():
    graph = Graph()
    triples = [
        (focus, required, URIRef(f"{EX}{index}"))
        for index in range(2)
    ]
    for triple in triples:
        graph.add(triple)

    report = check_constraints(
        graph,
        focus,
        [PropertyConstraint(required, max_count=1)],
        max_solutions=2,
    )

    assert report.status is SearchStatus.COMPLETE
    assert report.truncated is True
    assert len(report.repairs) == 2
    assert all(repair.size == 1 for repair in report.repairs)


def test_partial_results_mark_incomplete_search_as_truncated():
    graph = Graph()
    one = (focus, required, URIRef(EX + "one"))
    two = (focus, required, URIRef(EX + "two"))
    graph.add(one)
    graph.add(two)
    constraint = PropertyConstraint(required, max_count=1)

    report = check_constraints(
        graph,
        focus,
        [constraint],
        max_solutions=10,
        max_states=2,
    )

    assert report.status is SearchStatus.INCOMPLETE
    assert report.truncated is True
    assert len(report.repairs) == 1
    assert report.minimum_changes == 1


def test_candidate_triples_and_limit_aliases():
    graph = Graph()
    constraint = PropertyConstraint(required, min_count=1)
    candidate = (focus, required, BNode("provided"))

    report = check_constraints(
        graph,
        focus,
        [constraint],
        candidate_triples=[candidate],
        limit=1,
    )

    assert report.repairs == (Repair((), (candidate,)),)


def test_cannot_add_invented_or_malformed_triple():
    graph = Graph()
    constraint = PropertyConstraint(required, min_count=1)
    candidate = (focus, required, Literal("provided"))

    report = check_constraints(graph, focus, [constraint], [candidate])

    assert report.repairs == (Repair((), (candidate,)),)
    assert len(graph) == 0

    unrelated = (URIRef(EX + "other"), required, Literal("provided"))
    unrelated_report = check_constraints(graph, focus, [constraint], [unrelated])
    assert unrelated_report.status is SearchStatus.NO_SOLUTION
    assert unrelated_report.repairs == ()
    with pytest.raises(TypeError):
        check_constraints(
            graph,
            focus,
            [constraint],
            [(focus, required, "provided")],
        )
