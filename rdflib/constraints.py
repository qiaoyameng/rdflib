"""
Limited constraint checking and repair suggestions for a single focus node.

The supported constraints intentionally form a small, closed set:

* minimum and maximum numbers of triples with a given predicate;
* RDF node kinds allowed as the values of a predicate;
* groups of predicates of which at most one may be present.

Repair suggestions never invent terms. They may only delete triples that
already exist on the focus node, or add complete triples supplied by the
caller as candidates. The graph passed to this module is not modified.
"""

from __future__ import annotations

__all__ = [
    "ValueType",
    "SearchStatus",
    "ViolationType",
    "PropertyConstraint",
    "PredicateConstraint",
    "MutexPredicateGroup",
    "ExclusivePredicateGroup",
    "ConstraintViolation",
    "Repair",
    "RepairSuggestion",
    "ConstraintReport",
    "RepairReport",
    "check_constraints",
    "check_focus_node",
    "validate_focus_node",
    "suggest_repairs",
    "repair_suggestions",
    "find_repairs",
    "validate_constraints",
]

from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from enum import Enum
from itertools import combinations
from typing import Optional, Union

from rdflib.graph import Graph
from rdflib.term import BNode, IdentifiedNode, Literal, Node, URIRef, Variable

Triple = tuple[IdentifiedNode, URIRef, Node]
Constraint = Union["PropertyConstraint", "MutexPredicateGroup"]


class ValueType(Enum):
    """Allowed RDF node kind for a property value."""

    IRI = "iri"
    URI = "iri"
    BLANK_NODE = "blank_node"
    LITERAL = "literal"


class ViolationType(Enum):
    """Kind of constraint violation found on the focus node."""

    MIN_COUNT = "min_count"
    MAX_COUNT = "max_count"
    VALUE_TYPE = "value_type"
    MUTEX = "mutex"


class SearchStatus(Enum):
    """Outcome of the bounded repair search."""

    COMPLETE = "complete"
    NO_SOLUTION = "no_solution"
    INCOMPLETE = "incomplete"


_VALUE_TYPE_CLASSES = {
    ValueType.IRI: URIRef,
    ValueType.BLANK_NODE: BNode,
    ValueType.LITERAL: Literal,
}


def _normalize_count(value: Optional[int], name: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer or None")
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return value


def _normalize_value_types(
    value_types: Optional[Iterable[Union[ValueType, type[Node]]]],
) -> tuple[ValueType, ...]:
    if value_types is None:
        return ()

    normalized: list[ValueType] = []
    for value_type in value_types:
        if isinstance(value_type, ValueType):
            result = value_type
        elif isinstance(value_type, type) and issubclass(value_type, Node):
            if value_type is URIRef:
                result = ValueType.IRI
            elif value_type is BNode:
                result = ValueType.BLANK_NODE
            elif value_type is Literal:
                result = ValueType.LITERAL
            else:
                raise TypeError(f"Unsupported value type: {value_type!r}")
        else:
            raise TypeError(f"Unsupported value type: {value_type!r}")
        if result not in normalized:
            normalized.append(result)
    return tuple(normalized)


@dataclass(frozen=True)
class PropertyConstraint:
    """Cardinality and value-kind constraints for one predicate."""

    predicate: URIRef
    min_count: Optional[int] = None
    max_count: Optional[int] = None
    value_types: tuple[ValueType, ...] = field(default=())

    def __post_init__(self) -> None:
        if not isinstance(self.predicate, URIRef):
            raise TypeError("predicate must be a URIRef")

        min_count = _normalize_count(self.min_count, "min_count")
        max_count = _normalize_count(self.max_count, "max_count")
        if (
            min_count is not None
            and max_count is not None
            and min_count > max_count
        ):
            raise ValueError("min_count must not be greater than max_count")

        object.__setattr__(self, "min_count", min_count)
        object.__setattr__(self, "max_count", max_count)
        object.__setattr__(
            self,
            "value_types",
            _normalize_value_types(self.value_types),
        )

    def allows_value(self, value: Node) -> bool:
        if not self.value_types:
            return True
        return any(
            isinstance(value, _VALUE_TYPE_CLASSES[value_type])
            for value_type in self.value_types
        )


PredicateConstraint = PropertyConstraint
CardinalityConstraint = PropertyConstraint
ValueTypeConstraint = PropertyConstraint


@dataclass(frozen=True)
class MutexPredicateGroup:
    """Predicates of which at most one may be present on the focus node."""

    predicates: tuple[URIRef, ...]

    def __init__(self, predicates: Iterable[URIRef]) -> None:
        normalized = tuple(predicates)
        if len(normalized) < 2:
            raise ValueError("A mutex group must contain at least two predicates")
        if any(not isinstance(predicate, URIRef) for predicate in normalized):
            raise TypeError("All predicates must be URIRef instances")
        if len(set(normalized)) != len(normalized):
            raise ValueError("A mutex group must not contain duplicate predicates")
        object.__setattr__(self, "predicates", normalized)


ExclusivePredicateGroup = MutexPredicateGroup
MutexGroup = MutexPredicateGroup
DisjointPredicateGroup = MutexPredicateGroup


@dataclass(frozen=True)
class ConstraintViolation:
    """A constraint violation and the original triples that evidence it."""

    type: ViolationType
    constraint: Constraint
    facts: tuple[Triple, ...]
    actual_count: Optional[int] = None


@dataclass(frozen=True)
class Repair:
    """A proposed set of deletions and additions, expressed as triples."""

    deletions: tuple[Triple, ...]
    additions: tuple[Triple, ...]

    @property
    def size(self) -> int:
        return len(self.deletions) + len(self.additions)


RepairSuggestion = Repair


@dataclass(frozen=True)
class ConstraintReport:
    """Violations in the current graph and bounded minimum-change repairs."""

    focus: IdentifiedNode
    violations: tuple[ConstraintViolation, ...]
    repairs: tuple[Repair, ...]
    status: SearchStatus
    truncated: bool
    minimum_changes: Optional[int]
    states_checked: int

    @property
    def conforms(self) -> bool:
        return not self.violations

    @property
    def search_complete(self) -> bool:
        return self.status is SearchStatus.COMPLETE


RepairReport = ConstraintReport


def _normalize_constraints(
    constraints: Iterable[Constraint],
) -> tuple[dict[URIRef, PropertyConstraint], tuple[MutexPredicateGroup, ...]]:
    properties: dict[URIRef, PropertyConstraint] = {}
    mutex_groups: list[MutexPredicateGroup] = []

    for constraint in constraints:
        if isinstance(constraint, PropertyConstraint):
            if constraint.predicate in properties:
                raise ValueError(
                    f"Duplicate property constraint: {constraint.predicate}"
                )
            properties[constraint.predicate] = constraint
        elif isinstance(constraint, MutexPredicateGroup):
            mutex_groups.append(constraint)
        else:
            raise TypeError(f"Unsupported constraint: {constraint!r}")

    return properties, tuple(mutex_groups)


def _triples_for(
    graph: Graph, focus: IdentifiedNode, predicate: URIRef
) -> list[Triple]:
    triples: Iterator[Triple] = graph.triples((focus, predicate, None))
    return sorted(triples)


def _as_triple(
    triple: Sequence[Node], focus: IdentifiedNode, predicates: set[URIRef]
) -> Optional[Triple]:
    try:
        subject, predicate, obj = triple
    except (TypeError, ValueError) as error:
        raise TypeError(
            "Each candidate must be a triple of (subject, predicate, object)"
        ) from error

    if not isinstance(subject, IdentifiedNode):
        raise TypeError("Triple subject must be a URIRef or BNode")
    if not isinstance(predicate, URIRef):
        raise TypeError("Triple predicate must be a URIRef")
    if not isinstance(obj, Node) or isinstance(obj, Variable):
        raise TypeError("Triple object must be a URIRef, BNode, or Literal")
    if subject != focus or predicate not in predicates:
        return None
    return subject, predicate, obj


def _collect_current_triples(
    graph: Graph,
    focus: IdentifiedNode,
    properties: dict[URIRef, PropertyConstraint],
    mutex_groups: tuple[MutexPredicateGroup, ...],
) -> dict[URIRef, list[Triple]]:
    predicates = set(properties)
    for group in mutex_groups:
        predicates.update(group.predicates)

    return {
        predicate: _triples_for(graph, focus, predicate)
        for predicate in sorted(predicates)
    }


def _check_projection(
    triples_by_predicate: dict[URIRef, list[Triple]],
    properties: dict[URIRef, PropertyConstraint],
    mutex_groups: tuple[MutexPredicateGroup, ...],
) -> list[ConstraintViolation]:
    violations: list[ConstraintViolation] = []

    for predicate in sorted(properties):
        constraint = properties[predicate]
        triples = triples_by_predicate.get(predicate, [])
        count = len(triples)

        if constraint.min_count is not None and count < constraint.min_count:
            violations.append(
                ConstraintViolation(
                    ViolationType.MIN_COUNT,
                    constraint,
                    tuple(triples),
                    count,
                )
            )
        if constraint.max_count is not None and count > constraint.max_count:
            violations.append(
                ConstraintViolation(
                    ViolationType.MAX_COUNT,
                    constraint,
                    tuple(triples),
                    count,
                )
            )

        invalid = [
            triple for triple in triples if not constraint.allows_value(triple[2])
        ]
        if invalid:
            violations.append(
                ConstraintViolation(
                    ViolationType.VALUE_TYPE,
                    constraint,
                    tuple(invalid),
                    count,
                )
            )

    for group in mutex_groups:
        present = [
            triple
            for predicate in group.predicates
            for triple in triples_by_predicate.get(predicate, [])
        ]
        used_predicates = {triple[1] for triple in present}
        if len(used_predicates) > 1:
            violations.append(
                ConstraintViolation(
                    ViolationType.MUTEX,
                    group,
                    tuple(sorted(present)),
                    len(used_predicates),
                )
            )

    return violations


def _actions(
    existing: set[Triple],
    candidates: Iterable[Sequence[Node]],
    focus: IdentifiedNode,
    predicates: set[URIRef],
    properties: dict[URIRef, PropertyConstraint],
) -> list[tuple[str, Triple]]:
    additions: set[Triple] = set()

    for candidate in candidates:
        triple = _as_triple(candidate, focus, predicates)
        if triple is None or triple in existing or triple in additions:
            continue

        constraint = properties.get(triple[1])
        if constraint is not None and not constraint.allows_value(triple[2]):
            continue

        additions.add(triple)

    actions = [("delete", triple) for triple in sorted(existing)]
    actions.extend(("add", triple) for triple in sorted(additions))
    return actions


def _report(
    focus: IdentifiedNode,
    violations: tuple[ConstraintViolation, ...],
    repairs: Iterable[Repair],
    status: SearchStatus,
    truncated: bool,
    minimum_changes: Optional[int],
    states_checked: int,
) -> ConstraintReport:
    return ConstraintReport(
        focus,
        violations,
        tuple(repairs),
        status,
        truncated,
        minimum_changes,
        states_checked,
    )


def check_constraints(
    graph: Graph,
    focus: IdentifiedNode,
    constraints: Iterable[Constraint],
    candidates: Iterable[Sequence[Node]] = (),
    *,
    candidate_triples: Optional[Iterable[Sequence[Node]]] = None,
    max_solutions: int = 100,
    max_states: int = 10_000,
    limit: Optional[int] = None,
) -> ConstraintReport:
    """Check one focus node and find minimum-change repair suggestions.

    Candidates are complete triples supplied by the caller. They are not
    modified or converted to strings. The search is exhaustive up to
    ``max_states`` projected graph states.
    """

    if not isinstance(focus, (URIRef, BNode)):
        raise TypeError("focus must be a URIRef or BNode")
    if limit is not None:
        max_solutions = limit
    if isinstance(max_solutions, bool) or not isinstance(max_solutions, int):
        raise TypeError("max_solutions must be an integer")
    if isinstance(max_states, bool) or not isinstance(max_states, int):
        raise TypeError("max_states must be an integer")
    if max_solutions < 1:
        raise ValueError("max_solutions must be at least 1")
    if max_states < 1:
        raise ValueError("max_states must be at least 1")

    properties, mutex_groups = _normalize_constraints(constraints)
    current = _collect_current_triples(graph, focus, properties, mutex_groups)
    existing = {triple for triples in current.values() for triple in triples}
    predicates = set(properties)
    for group in mutex_groups:
        predicates.update(group.predicates)

    violations = tuple(_check_projection(current, properties, mutex_groups))
    if candidate_triples is not None:
        candidates = candidate_triples
    actions = _actions(existing, candidates, focus, predicates, properties)

    repairs: list[Repair] = []
    states_checked = 0
    status = SearchStatus.NO_SOLUTION
    truncated = False
    minimum_changes: Optional[int] = None

    for cost in range(len(actions) + 1):
        found_at_cost: list[Repair] = []

        for selected in combinations(actions, cost):
            if states_checked >= max_states:
                status = SearchStatus.INCOMPLETE
                if found_at_cost:
                    repairs = sorted(
                        found_at_cost,
                        key=lambda repair: (repair.deletions, repair.additions),
                    )
                    minimum_changes = cost
                return _report(
                    focus,
                    violations,
                    repairs,
                    status,
                    bool(found_at_cost),
                    minimum_changes,
                    states_checked,
                )

            states_checked += 1
            deleted = {
                triple for operation, triple in selected if operation == "delete"
            }
            added = {
                triple for operation, triple in selected if operation == "add"
            }
            projected: dict[URIRef, list[Triple]] = defaultdict(list)

            for triple in existing - deleted:
                projected[triple[1]].append(triple)
            for triple in added:
                projected[triple[1]].append(triple)

            if not _check_projection(projected, properties, mutex_groups):
                repair = Repair(tuple(sorted(deleted)), tuple(sorted(added)))
                found_at_cost.append(repair)
                if len(found_at_cost) == max_solutions:
                    truncated = True
                    break

        if found_at_cost:
            repairs = sorted(
                found_at_cost,
                key=lambda repair: (repair.deletions, repair.additions),
            )
            status = SearchStatus.COMPLETE
            minimum_changes = cost
            break

    return _report(
        focus,
        violations,
        repairs,
        status,
        truncated,
        minimum_changes,
        states_checked,
    )


def suggest_repairs(
    graph: Graph,
    focus: IdentifiedNode,
    constraints: Iterable[Constraint],
    candidates: Iterable[Sequence[Node]] = (),
    *,
    candidate_triples: Optional[Iterable[Sequence[Node]]] = None,
    max_solutions: int = 100,
    max_states: int = 10_000,
    limit: Optional[int] = None,
) -> ConstraintReport:
    return check_constraints(
        graph,
        focus,
        constraints,
        candidates,
        candidate_triples=candidate_triples,
        max_solutions=max_solutions,
        max_states=max_states,
        limit=limit,
    )


find_repairs = suggest_repairs
validate_constraints = suggest_repairs
check_focus_node = check_constraints
validate_focus_node = check_constraints
repair_suggestions = suggest_repairs
