"""Small, exhaustive local RDF constraint checks and repair suggestions."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from itertools import combinations
from typing import Optional, TypeAlias, Union

from rdflib.graph import Graph
from rdflib.term import BNode, Literal, URIRef

Subject: TypeAlias = Union[URIRef, BNode]
Predicate: TypeAlias = URIRef
Object: TypeAlias = Union[URIRef, BNode, Literal]
Triple: TypeAlias = tuple[Subject, Predicate, Object]


class NodeKind(str, Enum):
    """Kinds of RDF terms accepted by :class:`ValueTypeConstraint`."""

    IRI = "iri"
    BLANK_NODE = "blank_node"
    LITERAL = "literal"


class ViolationKind(str, Enum):
    """The local constraint rule violated by a focus node."""

    MIN_COUNT = "min_count"
    MAX_COUNT = "max_count"
    VALUE_TYPE = "value_type"
    MUTUALLY_EXCLUSIVE = "mutually_exclusive"


class SearchStatus(str, Enum):
    """The outcome of the repair search."""

    COMPLETE = "complete"
    TRUNCATED = "truncated"
    UNSATISFIABLE = "unsatisfiable"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class ValueType:
    """An RDF term type and optional literal datatype or language tag."""

    node_kind: NodeKind
    datatype: Optional[URIRef] = None
    language: Optional[str] = None

    def __post_init__(self) -> None:
        if self.datatype is not None and self.language is not None:
            raise ValueError("A literal cannot require both datatype and language")
        if self.datatype is not None and not isinstance(self.datatype, URIRef):
            raise TypeError("ValueType.datatype must be a URIRef")
        if self.language is not None and self.node_kind is not NodeKind.LITERAL:
            raise ValueError("A language may only be specified for a literal")
        if self.datatype is not None and self.node_kind is not NodeKind.LITERAL:
            raise ValueError("A datatype may only be specified for a literal")
        if self.language is not None and not self.language:
            raise ValueError("A language tag must not be empty")

    def matches(self, value: Object) -> bool:
        if self.node_kind is NodeKind.IRI and not isinstance(value, URIRef):
            return False
        if self.node_kind is NodeKind.BLANK_NODE and not isinstance(value, BNode):
            return False
        if self.node_kind is NodeKind.LITERAL and not isinstance(value, Literal):
            return False
        if self.datatype is None and self.language is None:
            return True
        if not isinstance(value, Literal):
            return False
        if self.datatype is not None and value.datatype != self.datatype:
            return False
        if self.language is not None:
            if value.language is None:
                return False
            return value.language.lower() == self.language.lower()
        return True


AllowedValueType: TypeAlias = Union[
    ValueType, NodeKind, type[URIRef], type[BNode], type[Literal], URIRef
]


@dataclass(frozen=True)
class CardinalityConstraint:
    """Require between ``min_count`` and ``max_count`` values for a predicate."""

    predicate: URIRef
    min_count: int = 0
    max_count: Optional[int] = None

    def __post_init__(self) -> None:
        if not isinstance(self.predicate, URIRef):
            raise TypeError("predicate must be a URIRef")
        if self.min_count < 0:
            raise ValueError("min_count must be non-negative")
        if self.max_count is not None and self.max_count < 0:
            raise ValueError("max_count must be non-negative")
        if self.max_count is not None and self.min_count > self.max_count:
            raise ValueError("min_count must not be greater than max_count")


@dataclass(frozen=True)
class ValueTypeConstraint:
    """Require every value for ``predicate`` to match one of ``allowed_types``."""

    predicate: URIRef
    allowed_types: tuple[ValueType, ...]

    def __init__(
        self,
        predicate: URIRef,
        allowed_types: Iterable[AllowedValueType],
    ) -> None:
        if not isinstance(predicate, URIRef):
            raise TypeError("predicate must be a URIRef")
        normalized: list[ValueType] = []
        for allowed_type in allowed_types:
            if isinstance(allowed_type, ValueType):
                normalized.append(allowed_type)
            elif isinstance(allowed_type, NodeKind):
                normalized.append(ValueType(allowed_type))
            elif allowed_type is URIRef:
                normalized.append(ValueType(NodeKind.IRI))
            elif allowed_type is BNode:
                normalized.append(ValueType(NodeKind.BLANK_NODE))
            elif allowed_type is Literal:
                normalized.append(ValueType(NodeKind.LITERAL))
            elif isinstance(allowed_type, URIRef):
                normalized.append(
                    ValueType(NodeKind.LITERAL, datatype=allowed_type)
                )
            else:
                raise TypeError(f"Unsupported allowed value type: {allowed_type!r}")
        if not normalized:
            raise ValueError("allowed_types must not be empty")
        object.__setattr__(self, "predicate", predicate)
        object.__setattr__(self, "allowed_types", tuple(dict.fromkeys(normalized)))

    def matches(self, value: Object) -> bool:
        return any(allowed_type.matches(value) for allowed_type in self.allowed_types)


@dataclass(frozen=True)
class MutuallyExclusivePredicateGroup:
    """Allow values for at most one predicate in the group."""

    predicates: frozenset[URIRef]

    def __init__(self, predicates: Iterable[URIRef]) -> None:
        values = frozenset(predicates)
        if len(values) < 2:
            raise ValueError("A mutually exclusive group needs at least two predicates")
        if not all(isinstance(predicate, URIRef) for predicate in values):
            raise TypeError("Every group predicate must be a URIRef")
        object.__setattr__(self, "predicates", values)


Constraint: TypeAlias = Union[
    CardinalityConstraint,
    ValueTypeConstraint,
    MutuallyExclusivePredicateGroup,
]


@dataclass(frozen=True)
class ConstraintViolation:
    """A violated constraint and the existing facts involved in the conflict."""

    constraint: Constraint
    kind: ViolationKind
    facts: tuple[Triple, ...]
    actual_count: Optional[int] = None
    expected_min_count: Optional[int] = None
    expected_max_count: Optional[int] = None


@dataclass(frozen=True)
class RepairPlan:
    """Existing triples to remove and caller-provided triples to add."""

    remove: tuple[Triple, ...]
    add: tuple[Triple, ...]

    @property
    def modification_count(self) -> int:
        return len(self.remove) + len(self.add)


@dataclass(frozen=True)
class ConstraintCheckResult:
    """Violations in the supplied graph and minimal repair suggestions."""

    focus: Subject
    violations: tuple[ConstraintViolation, ...]
    repairs: tuple[RepairPlan, ...]
    status: SearchStatus

    @property
    def conforms(self) -> bool:
        return not self.violations


@dataclass(frozen=True)
class _Action:
    triple: Triple
    add: bool


def check_focus_constraints(
    graph: Graph,
    focus: Subject,
    constraints: Iterable[Constraint],
    candidate_triples: Iterable[Triple] = (),
    *,
    max_repairs: Optional[int] = 100,
    max_modifications: Optional[int] = 10,
) -> ConstraintCheckResult:
    """Check local constraints and enumerate minimum-change repair plans.

    Existing triples may only be removed. A triple is added only when it is
    supplied by the caller, has ``focus`` as subject, and uses a constrained
    predicate. The graph is never modified.

    >>> from rdflib import BNode, Graph, Literal, URIRef
    >>> name = URIRef("urn:example:name")
    >>> focus = URIRef("urn:example:focus")
    >>> graph = Graph()
    >>> _ = graph.add((focus, name, BNode("invalid")))
    >>> result = check_focus_constraints(
    ...     graph,
    ...     focus,
    ...     [
    ...         CardinalityConstraint(name, min_count=1, max_count=1),
    ...         ValueTypeConstraint(name, [NodeKind.LITERAL]),
    ...     ],
    ...     [(focus, name, Literal("valid"))],
    ... )
    >>> result.status.value
    'complete'
    >>> len(graph)
    1
    >>> result.repairs[0].remove
    ((rdflib.term.URIRef('urn:example:focus'), rdflib.term.URIRef('urn:example:name'), rdflib.term.BNode('invalid')),)
    >>> result.repairs[0].add
    ((rdflib.term.URIRef('urn:example:focus'), rdflib.term.URIRef('urn:example:name'), rdflib.term.Literal('valid')),)
    """

    constraint_tuple = tuple(constraints)
    if not constraint_tuple:
        raise ValueError("constraints must not be empty")
    if not isinstance(focus, (URIRef, BNode)):
        raise TypeError("focus must be a URIRef or BNode")
    if max_repairs is not None and max_repairs < 1:
        raise ValueError("max_repairs must be at least 1")
    if max_modifications is not None and max_modifications < 0:
        raise ValueError("max_modifications must be non-negative")

    constrained_predicates = _constrained_predicates(constraint_tuple)
    existing = {
        triple
        for triple in graph.triples((focus, None, None))
        if triple[1] in constrained_predicates
    }

    actions: list[_Action] = [
        _Action(triple, add=False) for triple in sorted(existing)
    ]
    addable: set[Triple] = set()
    for candidate in candidate_triples:
        if (
            not isinstance(candidate, tuple)
            or len(candidate) != 3
            or not isinstance(candidate[0], (URIRef, BNode))
            or not isinstance(candidate[1], URIRef)
            or not isinstance(candidate[2], (URIRef, BNode, Literal))
        ):
            raise TypeError("candidate_triples must contain RDF triples")
        if candidate[0] != focus or candidate[1] not in constrained_predicates:
            continue
        if candidate not in existing:
            addable.add(candidate)
    actions.extend(_Action(triple, add=True) for triple in sorted(addable))

    violations = _find_violations(existing, constraint_tuple)
    repairs: list[RepairPlan] = []
    status = SearchStatus.COMPLETE

    if not violations:
        repairs.append(RepairPlan((), ()))
    else:
        limit = (
            min(max_modifications, len(actions))
            if max_modifications is not None
            else len(actions)
        )
        for modification_count in range(limit + 1):
            found_minimum = False
            for selected in combinations(actions, modification_count):
                removed = {
                    action.triple for action in selected if not action.add
                }
                added = {action.triple for action in selected if action.add}
                repaired = (existing - removed) | added
                if not _find_violations(repaired, constraint_tuple):
                    repairs.append(
                        RepairPlan(
                            tuple(sorted(removed)),
                            tuple(sorted(added)),
                        )
                    )
                    found_minimum = True
                    if max_repairs is not None and len(repairs) >= max_repairs:
                        status = SearchStatus.TRUNCATED
                        break
            if found_minimum or status is SearchStatus.TRUNCATED:
                break

        if not repairs:
            if max_modifications is not None and max_modifications < len(actions):
                status = SearchStatus.INCOMPLETE
            else:
                status = SearchStatus.UNSATISFIABLE

    return ConstraintCheckResult(
        focus=focus,
        violations=tuple(violations),
        repairs=tuple(repairs),
        status=status,
    )


def _constrained_predicates(constraints: tuple[Constraint, ...]) -> frozenset[URIRef]:
    predicates: set[URIRef] = set()
    for constraint in constraints:
        if isinstance(constraint, MutuallyExclusivePredicateGroup):
            predicates.update(constraint.predicates)
        elif isinstance(
            constraint, (CardinalityConstraint, ValueTypeConstraint)
        ):
            predicates.add(constraint.predicate)
        else:
            raise TypeError(f"Unsupported constraint: {constraint!r}")
    return frozenset(predicates)


def _find_violations(
    triples: set[Triple],
    constraints: tuple[Constraint, ...],
) -> list[ConstraintViolation]:
    violations: list[ConstraintViolation] = []
    by_predicate: dict[URIRef, set[Triple]] = {}
    for triple in triples:
        assert isinstance(triple[1], URIRef)
        by_predicate.setdefault(triple[1], set()).add(triple)

    for constraint in constraints:
        if isinstance(constraint, CardinalityConstraint):
            facts = frozenset(by_predicate.get(constraint.predicate, set()))
            count = len(facts)
            if count < constraint.min_count:
                violations.append(
                    ConstraintViolation(
                        constraint=constraint,
                        kind=ViolationKind.MIN_COUNT,
                        facts=tuple(sorted(facts)),
                        actual_count=count,
                        expected_min_count=constraint.min_count,
                        expected_max_count=constraint.max_count,
                    )
                )
            if constraint.max_count is not None and count > constraint.max_count:
                violations.append(
                    ConstraintViolation(
                        constraint=constraint,
                        kind=ViolationKind.MAX_COUNT,
                        facts=tuple(sorted(facts)),
                        actual_count=count,
                        expected_min_count=constraint.min_count,
                        expected_max_count=constraint.max_count,
                    )
                )
        elif isinstance(constraint, ValueTypeConstraint):
            invalid = frozenset(
                triple
                for triple in by_predicate.get(constraint.predicate, set())
                if not constraint.matches(triple[2])
            )
            if invalid:
                violations.append(
                    ConstraintViolation(
                        constraint=constraint,
                        kind=ViolationKind.VALUE_TYPE,
                        facts=tuple(sorted(invalid)),
                    )
                )
        else:
            used = {
                predicate
                for predicate in constraint.predicates
                if predicate in by_predicate
            }
            if len(used) > 1:
                facts = frozenset(
                    triple
                    for predicate in used
                    for triple in by_predicate[predicate]
                )
                violations.append(
                    ConstraintViolation(
                        constraint=constraint,
                        kind=ViolationKind.MUTUALLY_EXCLUSIVE,
                        facts=tuple(sorted(facts)),
                    )
                )

    return sorted(violations, key=lambda violation: violation.kind.value)
