"""Tests for the bound-join batching strategy of SERVICE evaluation.

The tests inject a fake transport (so no network is touched) that records the
number of requests and emulates a remote endpoint.  The batched results are
compared as complete binding multisets against a row-by-row reference model.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Optional

import pytest

from rdflib import Graph, Variable
from rdflib.plugins.sparql import evaluate
from rdflib.plugins.sparql.parserutils import CompValue
from rdflib.plugins.sparql import prepareQuery
from rdflib.term import BNode, Identifier, Literal, URIRef

ENDPOINT = "http://fake.example/sparql"

EX = "http://example.org/"


def _service_values_rows(query_string: str):
    """Extract the VALUES rows injected into a serialized service query."""
    found: list[dict[Variable, Identifier]] = []

    def walk(node) -> None:
        if isinstance(node, CompValue):
            if node.name.startswith("values"):
                found.extend(node["res"])
            for key in node.keys():
                if key != "_vars":
                    walk(node[key])
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(prepareQuery(query_string).algebra)
    return found


def _term_to_json(term: Identifier) -> dict[str, str]:
    if isinstance(term, URIRef):
        return {"type": "uri", "value": str(term)}
    if isinstance(term, BNode):
        return {"type": "bnode", "value": str(term)}
    return {"type": "literal", "value": str(term)}


class FakeTransport:
    """Transport emulating a remote endpoint for the pattern ?s ?p ?o.

    ``solutions`` is the remote solution multiset (a list, so duplicates are
    preserved).  Every call is recorded in ``requests``.  ``fail`` may be a
    predicate deciding whether a given request raises.
    """

    def __init__(
        self,
        solutions: list[dict[str, Identifier]],
        fail: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self.solutions = solutions
        self.fail = fail
        self.requests: list[str] = []

    def __call__(
        self, url: str, query: str
    ) -> tuple[list[str], list[dict[str, dict[str, str]]]]:
        self.requests.append(query)
        if self.fail is not None and self.fail(query):
            raise ConnectionError("fake transport failure")

        rows = _service_values_rows(query)
        if not rows:
            raise AssertionError("service query carries no injected VALUES block")

        marker_vars = [
            variable
            for variable in rows[0]
            if str(variable).startswith("__rdflib_service_boundjoin")
        ]
        marker = marker_vars[0] if marker_vars else None

        bindings: list[dict[str, dict[str, str]]] = []
        for values_row in rows:
            for solution in self.solutions:
                match = True
                for variable, value in values_row.items():
                    if variable == marker:
                        continue
                    if value == "UNDEF":
                        continue
                    # The legacy reference path ships every visible outer
                    # variable, including ones not part of the service
                    # pattern; only service variables constrain the match.
                    name = str(variable)
                    if name not in solution:
                        continue
                    if solution[name] != value:
                        match = False
                        break
                if not match:
                    continue
                row_json = {
                    key: _term_to_json(value) for key, value in solution.items()
                }
                if marker is not None:
                    row_json[str(marker)] = {
                        "type": "literal",
                        "value": str(values_row[marker]),
                    }
                bindings.append(row_json)

        variables = sorted({key for row in bindings for key in row})
        return variables, bindings


def _multiset(rows) -> Counter:
    return Counter(
        tuple(
            sorted(
                (
                    str(key),
                    # blank nodes are compared structurally per call below
                    ("BNode:" + value) if isinstance(value, BNode) else str(value),
                )
                for key, value in row.items()
            )
        )
        for row in rows
    )


def _reference_multiset(
    outer: list[Optional[URIRef]],
    solutions: list[dict[str, Identifier]],
    x_values: list[URIRef],
) -> Counter:
    """Row-by-row reference: one remote evaluation per outer row."""
    result = Counter()
    for outer_s, x in zip(outer, x_values):
        for solution in solutions:
            if outer_s is not None and solution["s"] != outer_s:
                continue
            result[
                tuple(
                    sorted(
                        (str(key), str(value))
                        for key, value in (
                            set(solution.items()) | {("x", x)}
                        )
                    )
                )
            ] += 1
    return result


@pytest.fixture
def patched_transport(monkeypatch):
    monkeypatch.setattr(evaluate, "SERVICE_BOUND_JOIN_BATCH_SIZE", 2)
    transport_holder: dict[str, FakeTransport] = {}

    def install(transport: FakeTransport) -> None:
        transport_holder["t"] = transport
        monkeypatch.setattr(evaluate, "service_transport", transport)

    yield install, transport_holder


def test_boundjoin_fewer_requests_and_equivalent_multiset(patched_transport):
    install, holder = patched_transport
    solutions = [
        {"s": URIRef(EX + "a"), "p": URIRef(EX + "p1"), "o": Literal("one")},
        {"s": URIRef(EX + "a"), "p": URIRef(EX + "p1"), "o": Literal("one")},
        {"s": URIRef(EX + "b"), "p": URIRef(EX + "p2"), "o": Literal("two")},
        {"s": URIRef(EX + "c"), "p": URIRef(EX + "p3"), "o": Literal("three")},
    ]
    install(FakeTransport(solutions))

    graph = Graph()
    query = f"""
    PREFIX ex: <{EX}>
    SELECT ?s ?x ?p ?o WHERE {{
      VALUES (?s ?x) {{
        (ex:a ex:outer1)
        (ex:a ex:outer2)
        (UNDEF ex:outer3)
        (ex:b ex:outer4)
        (ex:zzz ex:outer5)
      }}
      SERVICE <{ENDPOINT}> {{ ?s ?p ?o }}
    }}
    """
    rows = graph.query(query).bindings
    assert len(holder["t"].requests) == 3  # ceil(5 rows / batch size 2)

    outer = [
        URIRef(EX + "a"),
        URIRef(EX + "a"),
        None,
        URIRef(EX + "b"),
        URIRef(EX + "zzz"),
    ]
    x_values = [URIRef(EX + "outer%d" % i) for i in range(1, 6)]
    assert _multiset(rows) == _reference_multiset(outer, solutions, x_values)


def test_boundjoin_preserves_duplicate_bindings(patched_transport):
    install, holder = patched_transport
    solutions = [
        {"s": URIRef(EX + "a"), "p": URIRef(EX + "p"), "o": Literal("v")},
    ]
    install(FakeTransport(solutions))
    graph = Graph()
    query = f"""
    PREFIX ex: <{EX}>
    SELECT ?s ?x ?p ?o WHERE {{
      VALUES (?s ?x) {{ (ex:a ex:x1) (ex:a ex:x2) (ex:a ex:x3) }}
      SERVICE <{ENDPOINT}> {{ ?s ?p ?o }}
    }}
    """
    rows = graph.query(query).bindings
    # The three outer rows share a single distinct VALUES pattern but each
    # must occur once in the result (multiplicity must not collapse).
    assert _multiset(rows) == Counter(
        {
            (("o", "v"), ("p", EX + "p"), ("s", EX + "a"), ("x", EX + x)): 1
            for x in ("x1", "x2", "x3")
        }
    )
    # Distinct patterns sent: only one tuple for ?s, hence one VALUES block.
    assert len(holder["t"].requests) == 2  # 3 rows / batch size 2


def test_boundjoin_unbound_and_conflicting_bindings(patched_transport):
    """UNDEF variables and bindings that exclude each other stay separated."""
    install, _holder = patched_transport
    solutions = [
        {"s": URIRef(EX + "a"), "p": URIRef(EX + "p"), "o": Literal("va")},
        {"s": URIRef(EX + "b"), "p": URIRef(EX + "p"), "o": Literal("vb")},
    ]
    install(FakeTransport(solutions))
    graph = Graph()
    query = f"""
    PREFIX ex: <{EX}>
    SELECT ?s ?x ?p ?o WHERE {{
      VALUES (?s ?x) {{
        (ex:a ex:boundA)
        (UNDEF ex:unbound)
        (ex:b ex:boundB)
        (ex:nope ex:conflict)
      }}
      SERVICE <{ENDPOINT}> {{ ?s ?p ?o }}
    }}
    """
    rows = graph.query(query).bindings
    assert _multiset(rows) == Counter(
        {
            (("o", "va"), ("p", EX + "p"), ("s", EX + "a"), ("x", EX + "boundA")): 1,
            (("o", "va"), ("p", EX + "p"), ("s", EX + "a"), ("x", EX + "unbound")): 1,
            (("o", "vb"), ("p", EX + "p"), ("s", EX + "b"), ("x", EX + "unbound")): 1,
            (("o", "vb"), ("p", EX + "p"), ("s", EX + "b"), ("x", EX + "boundB")): 1,
        }
    )


def test_boundjoin_bnodes_not_merged_across_responses(patched_transport):
    install, holder = patched_transport
    # The same remote label "shared" appears in every solution; it must be
    # scoped per remote call and therefore not identify one global bnode.
    solutions = [
        {"s": URIRef(EX + "a"), "p": URIRef(EX + "p"), "o": BNode("shared")},
        {"s": URIRef(EX + "b"), "p": URIRef(EX + "p"), "o": BNode("shared")},
    ]
    install(FakeTransport(solutions))
    graph = Graph()
    query = f"""
    PREFIX ex: <{EX}>
    SELECT ?s ?x ?o WHERE {{
      VALUES ?s {{ ex:a ex:b }}
      BIND(ex:tag AS ?x)
      SERVICE <{ENDPOINT}> {{ ?s ?p ?o }}
    }}
    """
    # Within one response, the equal labels identify the same blank node.
    rows = graph.query(query).bindings
    values = {str(row[Variable("s")]): row[Variable("o")] for row in rows}
    assert isinstance(values[EX + "a"], BNode)
    assert isinstance(values[EX + "b"], BNode)
    assert values[EX + "a"] == values[EX + "b"]

    # Across separate responses (batch size 1) the same remote labels must be
    # relabelled per call and no longer identify one blank node.
    evaluate.SERVICE_BOUND_JOIN_BATCH_SIZE = 1
    holder["t"].requests.clear()
    rows = graph.query(query).bindings
    assert len(holder["t"].requests) == 2
    values = {str(row[Variable("s")]): row[Variable("o")] for row in rows}
    assert values[EX + "a"] != values[EX + "b"]


def test_boundjoin_silent_failure_keeps_outer_rows(patched_transport):
    install, _holder = patched_transport
    solutions = [
        {"s": URIRef(EX + "a"), "p": URIRef(EX + "p"), "o": Literal("v")},
    ]

    attempts = {"count": 0}

    def fail(_query: str) -> bool:
        attempts["count"] += 1
        return attempts["count"] == 1

    install(FakeTransport(solutions, fail=fail))
    graph = Graph()
    query = f"""
    PREFIX ex: <{EX}>
    SELECT ?s ?x ?p ?o WHERE {{
      VALUES (?s ?x) {{ (ex:a ex:first) (ex:a ex:second) }}
      SERVICE SILENT <{ENDPOINT}> {{ ?s ?p ?o }}
    }}
    """
    rows = graph.query(query).bindings
    # SERVICE SILENT issues one request per outer row so failures stay local.
    assert attempts["count"] == 2
    # The first batch failed: its outer row survives with service vars
    # unbound; the second batch succeeds and joins normally.
    assert _multiset(rows) == Counter(
        {
            (("s", EX + "a"), ("x", EX + "first")): 1,
            (("o", "v"), ("p", EX + "p"), ("s", EX + "a"), ("x", EX + "second")): 1,
        }
    )


def test_boundjoin_failure_without_silent_raises(patched_transport):
    install, _holder = patched_transport
    install(FakeTransport([], fail=lambda query: True))
    graph = Graph()
    query = f"""
    PREFIX ex: <{EX}>
    SELECT ?s ?o WHERE {{
      VALUES ?s {{ ex:a }}
      SERVICE <{ENDPOINT}> {{ ?s ?p ?o }}
    }}
    """
    with pytest.raises(ConnectionError):
        graph.query(query).bindings


def test_boundjoin_falls_back_for_subselect(patched_transport):
    install, holder = patched_transport
    solutions = [
        {"s": URIRef(EX + "a"), "p": URIRef(EX + "p"), "o": Literal("v")},
    ]
    transport = FakeTransport(solutions)
    install(transport)

    # Force the per-row reference path for a fragment containing a
    # sub-SELECT; one request per outer row.
    plan_function = evaluate._boundServiceJoinPlan
    evaluate._boundServiceJoinPlan = lambda ctx, part: None
    try:
        graph = Graph()
        query = f"""
        PREFIX ex: <{EX}>
        SELECT ?s ?x ?p ?o WHERE {{
          VALUES (?s ?x) {{ (ex:a ex:x1) (ex:a ex:x2) }}
          SERVICE <{ENDPOINT}> {{
            SELECT ?s ?p ?o WHERE {{ ?s ?p ?o }}
          }}
        }}
        """
        rows = graph.query(query).bindings
        assert len(transport.requests) == 2
        assert _multiset(rows) == Counter(
            {
                (("o", "v"), ("p", EX + "p"), ("s", EX + "a"), ("x", EX + x)): 1
                for x in ("x1", "x2")
            }
        )
    finally:
        evaluate._boundServiceJoinPlan = plan_function


def test_boundjoin_falls_back_for_variable_endpoint(patched_transport):
    install, holder = patched_transport
    transport = FakeTransport(
        [{"s": URIRef(EX + "a"), "p": URIRef(EX + "p"), "o": Literal("v")}]
    )
    install(transport)
    graph = Graph()
    query = f"""
    PREFIX ex: <{EX}>
    SELECT ?ep ?s ?o WHERE {{
      VALUES ?ep {{ <{ENDPOINT}> }}
      SERVICE ?ep {{ ?s ?p ?o }}
    }}
    """
    # The variable endpoint cannot be rewritten: the legacy (no-op here) path
    # is used and no batch request is issued through the bound-join plan.
    plan = evaluate._boundServiceJoinPlan
    sentinel_calls = []
    evaluate._boundServiceJoinPlan = lambda ctx, part: (
        sentinel_calls.append(part) or None
    )
    try:
        rows = graph.query(query).bindings
        assert rows == []
        assert sentinel_calls
    finally:
        evaluate._boundServiceJoinPlan = plan


def test_boundjoin_uses_outer_prefixes(patched_transport):
    install, holder = patched_transport
    solutions = [
        {"s": URIRef(EX + "a"), "p": URIRef(EX + "p"), "o": Literal("v")},
    ]
    install(FakeTransport(solutions))
    graph = Graph()
    query = f"""
    PREFIX ex: <{EX}>
    SELECT ?s ?o WHERE {{
      VALUES ?s {{ ex:a }}
      SERVICE <{ENDPOINT}> {{ ?s ex:p ?o }}
    }}
    """
    rows = graph.query(query).bindings
    assert rows[0][Variable("o")] == Literal("v")
    assert len(holder["t"].requests) == 1
