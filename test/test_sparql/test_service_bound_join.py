"""Tests for the explicit bound-join batching strategy of SERVICE.

A fake, injected transport records how many remote requests are issued and
answers every service query against a local graph (the simulated remote
endpoint).  For every scenario the complete SPARQL binding multiset
produced by the batched strategy is compared against the classic
row-by-row evaluation, which serves as the reference implementation.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Iterator
from typing import Any
from urllib.error import URLError

import pytest

from rdflib import BNode, Graph, Literal, URIRef, Variable
from rdflib.plugins.sparql import evaluate

logging.disable(logging.CRITICAL)

PREFIX = "PREFIX ex: <http://example.org/> "
ENDPOINT = "http://fake-service.example/sparql"


class RecordingTransport:
    """Fake SERVICE transport backed by a local graph.

    Every issued query is recorded.  Individual calls (numbered from zero)
    can be forced to fail, which simulates a remote error affecting a single
    batch.
    """

    def __init__(
        self,
        remote: Graph,
        fail_on: tuple[int, ...] = (),
        error: type[Exception] = URLError,
    ) -> None:
        self.remote = remote
        self.fail_on = set(fail_on)
        self.error = error
        self.requests: list[str] = []

    def __call__(self, service_url: str, service_query: str) -> dict[str, Any]:
        call_number = len(self.requests)
        self.requests.append(service_query)
        assert service_url == ENDPOINT
        if call_number in self.fail_on:
            raise self.error("simulated remote failure")
        serialized = self.remote.query(service_query).serialize(format="json")
        if isinstance(serialized, bytes):
            serialized = serialized.decode()
        return json.loads(serialized)


class ScriptedTransport:
    """Transport returning hand-written JSON responses."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.requests: list[str] = []

    def __call__(self, service_url: str, service_query: str) -> dict[str, Any]:
        self.requests.append(service_query)
        return self.responses[len(self.requests) - 1]


@pytest.fixture(autouse=True)
def restore_transport_and_batch_size() -> Iterator[None]:
    original_transport = evaluate.service_transport
    original_batch_size = evaluate.SERVICE_BOUND_JOIN_BATCH_SIZE
    yield
    evaluate.service_transport = original_transport
    evaluate.SERVICE_BOUND_JOIN_BATCH_SIZE = original_batch_size


def _run(graph: Graph, query: str, batch_size: int, transport: Any) -> tuple[Counter, int]:
    evaluate.SERVICE_BOUND_JOIN_BATCH_SIZE = batch_size
    evaluate.service_transport = transport
    result = graph.query(query)
    multiset = Counter(
        tuple((variable, binding.get(variable)) for variable in result.vars)
        for binding in result.bindings
    )
    return multiset, len(transport.requests)


def _service(silent: bool = False) -> str:
    keyword = "SERVICE SILENT" if silent else "SERVICE"
    return f"{keyword} <{ENDPOINT}>"


def _query(service: str, pattern: str = "?x ex:q ?o") -> str:
    return PREFIX + (
        f"SELECT ?s ?x ?o WHERE {{ ?s ex:p ?x . {service} {{ {pattern} }} }}"
    )


@pytest.mark.parametrize("batch_size", [2, 3, 8, 100])
def test_batched_matches_row_by_row_multiset(batch_size: int) -> None:
    local = Graph()
    remote = Graph()
    local.parse(
        data="""
        @prefix ex: <http://example.org/> .
        ex:s1 ex:p ex:a . ex:s2 ex:p ex:b . ex:s3 ex:p ex:c .
        ex:s4 ex:p ex:a .
        """,
        format="turtle",
    )
    remote.parse(
        data="""
        @prefix ex: <http://example.org/> .
        ex:a ex:q "A1" . ex:a ex:q "A2" . ex:b ex:q "B1" . ex:c ex:q "C1" .
        """,
        format="turtle",
    )
    query = _query(_service())

    reference, reference_requests = _run(local, query, 1, RecordingTransport(remote))
    batched, batched_requests = _run(
        local, query, batch_size, RecordingTransport(remote)
    )

    assert batched == reference
    assert reference_requests == 4
    # ceil(4 / min(batch_size, 4)) requests
    expected_requests = -(-4 // min(batch_size, 4))
    assert batched_requests == expected_requests
    assert batched_requests <= reference_requests

    # ?x=ex:a occurs twice on the outside and matches two answers remotely,
    # so A1 and A2 each appear with multiplicity two.
    answer_values = Counter(
        row[2][1]
        for row in batched.elements()
        if row[1][1] == URIRef("http://example.org/a")
    )
    assert answer_values[Literal("A1")] == 2
    assert answer_values[Literal("A2")] == 2


def test_duplicate_identical_outer_bindings_keep_multiplicity() -> None:
    local = Graph()
    remote = Graph()
    local.parse(
        data="""
        @prefix ex: <http://example.org/> .
        ex:s1 ex:p ex:a . ex:s2 ex:p ex:a .
        """,
        format="turtle",
    )
    remote.parse(
        data="@prefix ex: <http://example.org/> . ex:a ex:q 'A' .",
        format="turtle",
    )
    query = _query(_service())

    reference, _ = _run(local, query, 1, RecordingTransport(remote))
    batched, requests = _run(local, query, 8, RecordingTransport(remote))

    assert batched == reference
    assert sum(batched.values()) == 2
    # The two identical bindings share one request but produce two rows.
    assert requests == 1


def test_unbound_variables_use_undef_and_match_reference() -> None:
    local = Graph()
    remote = Graph()
    local.parse(
        data="""
        @prefix ex: <http://example.org/> .
        ex:s1 ex:p ex:a ; ex:t ex:t1 .
        ex:s2 ex:p ex:b .
        """,
        format="turtle",
    )
    remote.parse(
        data="""
        @prefix ex: <http://example.org/> .
        ex:a ex:q 'A' . ex:b ex:q 'B' .
        """,
        format="turtle",
    )
    query = PREFIX + (
        "SELECT ?s ?x ?t ?o WHERE { ?s ex:p ?x . "
        f"OPTIONAL {{ ?s ex:t ?t }} {_service()} {{ ?x ex:q ?o }} }}"
    )

    reference, ref_requests = _run(local, query, 1, RecordingTransport(remote))
    batch_transport = RecordingTransport(remote)
    batched, batch_requests = _run(local, query, 8, batch_transport)

    assert batched == reference
    # Two distinct binding domains (?t bound vs unbound) -> two batches,
    # and the unbound column is sent as UNDEF.
    assert batch_requests == 2
    assert batch_requests <= ref_requests
    assert any("UNDEF" in request for request in batch_transport.requests)
    unbound_row = next(row for row in batched if row[1][1] == URIRef("http://example.org/b"))
    assert unbound_row[2][1] is None  # ?t unbound


def test_conflicting_binding_drops_row_in_both_modes() -> None:
    local = Graph()
    remote = Graph()
    local.parse(
        data="@prefix ex: <http://example.org/> . ex:s1 ex:p ex:a .",
        format="turtle",
    )
    remote.parse(
        data="@prefix ex: <http://example.org/> . ex:other ex:label 'L' .",
        format="turtle",
    )
    # The service rebinds ?x to a different term than the outer ?x=ex:a.
    query = PREFIX + (
        "SELECT ?s ?x ?y WHERE { ?s ex:p ?x . "
        f"{_service()} {{ BIND(ex:other AS ?x) ?x ex:label ?y }} }}"
    )

    reference, ref_requests = _run(local, query, 1, RecordingTransport(remote))
    batched, batch_requests = _run(local, query, 8, RecordingTransport(remote))

    assert dict(batched) == {}
    assert batched == reference
    assert batch_requests == ref_requests


def test_same_named_variable_scope_matches_reference() -> None:
    # ?shared appears both outside and inside the service and is also bound
    # independently inside the service pattern; scope handling must match
    # the row-by-row reference.
    local = Graph()
    remote = Graph()
    local.parse(
        data="""
        @prefix ex: <http://example.org/> .
        ex:s1 ex:p ex:a ; ex:shared ex:outer1 .
        ex:s2 ex:p ex:b ; ex:shared ex:outer2 .
        """,
        format="turtle",
    )
    remote.parse(
        data="""
        @prefix ex: <http://example.org/> .
        ex:a ex:q 'A' ; ex:shared ex:remote1 .
        ex:b ex:q 'B' ; ex:shared ex:remote2 .
        """,
        format="turtle",
    )
    query = PREFIX + (
        "SELECT ?s ?x ?shared ?o WHERE { ?s ex:p ?x ; ex:shared ?shared . "
        f"{_service()} {{ ?x ex:q ?o ; ex:shared ?shared }} }}"
    )

    reference, _ = _run(local, query, 1, RecordingTransport(remote))
    batched, requests = _run(local, query, 8, RecordingTransport(remote))

    assert batched == reference
    assert sum(batched.values()) == 0  # inner/outer ?shared always conflict
    assert requests == 1


def test_blank_nodes_from_different_responses_not_merged() -> None:
    local = Graph()
    local.parse(
        data="""
        @prefix ex: <http://example.org/> .
        ex:s1 ex:p ex:a . ex:s2 ex:p ex:b .
        """,
        format="turtle",
    )

    def response(x: str) -> dict[str, Any]:
        return {
            "head": {"vars": ["x", "o"]},
            "results": {
                "bindings": [
                    {
                        "x": {"type": "uri", "value": f"http://example.org/{x}"},
                        "o": {"type": "bnode", "value": "sharedLabel"},
                    }
                ]
            },
        }

    transport = ScriptedTransport([response("a"), response("b")])
    evaluate.SERVICE_BOUND_JOIN_BATCH_SIZE = 1
    evaluate.service_transport = transport
    query = _query(_service())
    rows = list(local.query(query).bindings)

    assert len(rows) == 2
    first = rows[0][Variable("o")]
    second = rows[1][Variable("o")]
    assert isinstance(first, BNode)
    assert isinstance(second, BNode)
    assert first != second
    assert str(first) != str(second)


def test_blank_node_label_shared_within_one_response() -> None:
    local = Graph()
    local.parse(
        data="@prefix ex: <http://example.org/> . ex:s1 ex:p ex:a .",
        format="turtle",
    )
    transport = ScriptedTransport(
        [
            {
                "head": {"vars": ["x", "o", "o2"]},
                "results": {
                    "bindings": [
                        {
                            "x": {"type": "uri", "value": "http://example.org/a"},
                            "o": {"type": "bnode", "value": "same"},
                            "o2": {"type": "bnode", "value": "same"},
                        }
                    ]
                },
            }
        ]
    )
    evaluate.SERVICE_BOUND_JOIN_BATCH_SIZE = 8
    evaluate.service_transport = transport
    query = _query(_service(), pattern="?x ex:q ?o , ?o2")
    rows = list(local.query(query).bindings)
    assert len(rows) == 1
    assert rows[0][Variable("o")] == rows[0][Variable("o2")]


def test_service_silent_drops_rows_when_variables_shared() -> None:
    local = Graph()
    local.parse(
        data="""
        @prefix ex: <http://example.org/> .
        ex:s1 ex:p ex:a . ex:s2 ex:p ex:b .
        """,
        format="turtle",
    )
    remote = Graph()
    query = _query(_service(silent=True))

    # Both batches fail; ?x is shared, so every outer row is dropped.
    multiset, requests = _run(local, query, 1, RecordingTransport(remote, fail_on=(0, 1)))
    assert sum(multiset.values()) == 0
    assert requests == 2

    multiset, requests = _run(local, query, 8, RecordingTransport(remote, fail_on=(0,)))
    assert sum(multiset.values()) == 0
    assert requests == 1


def test_service_without_silent_raises_on_batch_failure() -> None:
    local = Graph()
    local.parse(
        data="""
        @prefix ex: <http://example.org/> .
        ex:s1 ex:p ex:a . ex:s2 ex:p ex:b .
        """,
        format="turtle",
    )
    remote = Graph()
    query = _query(_service(silent=False))
    evaluate.SERVICE_BOUND_JOIN_BATCH_SIZE = 8
    evaluate.service_transport = RecordingTransport(remote, fail_on=(0,))
    with pytest.raises(URLError):
        list(local.query(query).bindings)


def test_service_silent_keeps_rows_when_no_variables_shared() -> None:
    local = Graph()
    local.parse(
        data="""
        @prefix ex: <http://example.org/> .
        ex:s1 ex:p ex:a . ex:s2 ex:p ex:b .
        """,
        format="turtle",
    )
    remote = Graph()
    query = PREFIX + (
        f"SELECT ?s ?x ?o WHERE {{ ?s ex:p ?x . "
        f"{_service(silent=True)} {{ ex:z ex:q ?o }} }}"
    )
    multiset, requests = _run(local, query, 8, RecordingTransport(remote, fail_on=(0, 1)))
    # No shared variables: the outer solutions pass through unchanged.
    assert sum(multiset.values()) == 2
    assert all(row[2][1] is None for row in multiset.elements())
    assert requests == 2


def test_service_silent_partial_batch_failure_keeps_other_rows() -> None:
    local = Graph()
    remote = Graph()
    local.parse(
        data="""
        @prefix ex: <http://example.org/> .
        ex:s1 ex:p ex:a . ex:s2 ex:p ex:b . ex:s3 ex:p ex:c .
        """,
        format="turtle",
    )
    remote.parse(
        data="""
        @prefix ex: <http://example.org/> .
        ex:a ex:q 'A' . ex:b ex:q 'B' . ex:c ex:q 'C' .
        """,
        format="turtle",
    )
    query = _query(_service(silent=True))
    # Batch size 2 -> batch 1 covers a,b (fails), batch 2 covers c (works).
    multiset, requests = _run(local, query, 2, RecordingTransport(remote, fail_on=(0,)))
    values = sorted(row[2][1] for row in multiset.elements())
    assert values == ["C"]
    assert requests == 2


def test_optional_service_silent_failure_retains_outer_rows() -> None:
    local = Graph()
    remote = Graph()
    local.parse(
        data="""
        @prefix ex: <http://example.org/> .
        ex:s1 ex:p ex:a . ex:s2 ex:p ex:b .
        """,
        format="turtle",
    )
    query = PREFIX + (
        "SELECT ?s ?x ?o WHERE { ?s ex:p ?x . "
        f"OPTIONAL {{ {_service(silent=True)} {{ ?x ex:q ?o }} }} }}"
    )
    multiset, _ = _run(local, query, 8, RecordingTransport(remote, fail_on=(0,)))
    assert sum(multiset.values()) == 2
    assert all(row[2][1] is None for row in multiset.elements())


def test_optional_service_unmatched_outer_row_is_retained() -> None:
    local = Graph()
    remote = Graph()
    local.parse(
        data="""
        @prefix ex: <http://example.org/> .
        ex:s1 ex:p ex:a . ex:s2 ex:p ex:c .
        """,
        format="turtle",
    )
    remote.parse(
        data="@prefix ex: <http://example.org/> . ex:a ex:q 'A' .",
        format="turtle",
    )
    query = PREFIX + (
        "SELECT ?s ?x ?o WHERE { ?s ex:p ?x . "
        f"OPTIONAL {{ {_service()} {{ ?x ex:q ?o }} }}"
    )
    reference, ref_requests = _run(local, query, 1, RecordingTransport(remote))
    batched, batch_requests = _run(local, query, 8, RecordingTransport(remote))
    assert batched == reference
    assert sum(batched.values()) == 2
    assert batch_requests <= ref_requests


def test_explicit_projection_falls_back_to_row_by_row() -> None:
    local = Graph()
    remote = Graph()
    local.parse(
        data="""
        @prefix ex: <http://example.org/> .
        ex:s1 ex:p ex:a . ex:s2 ex:p ex:b .
        """,
        format="turtle",
    )
    remote.parse(
        data="""
        @prefix ex: <http://example.org/> .
        ex:a ex:q 'A' . ex:b ex:q 'B' .
        """,
        format="turtle",
    )
    query = PREFIX + (
        "SELECT ?s ?x ?o WHERE { ?s ex:p ?x . "
        f"{_service()} {{ SELECT ?o WHERE {{ ?x ex:q ?o }} }} }}"
    )
    reference, ref_requests = _run(local, query, 1, RecordingTransport(remote))
    batched, batch_requests = _run(local, query, 8, RecordingTransport(remote))
    assert batched == reference
    # Explicit projection list cannot safely be batched -> one call per row.
    assert batch_requests == ref_requests == 2


def test_star_subquery_is_batched() -> None:
    local = Graph()
    remote = Graph()
    local.parse(
        data="""
        @prefix ex: <http://example.org/> .
        ex:s1 ex:p ex:a . ex:s2 ex:p ex:b .
        """,
        format="turtle",
    )
    remote.parse(
        data="""
        @prefix ex: <http://example.org/> .
        ex:a ex:q 'A' . ex:b ex:q 'B' .
        """,
        format="turtle",
    )
    query = PREFIX + (
        "SELECT ?s ?x ?o WHERE { ?s ex:p ?x . "
        f"{_service()} {{ SELECT * WHERE {{ ?x ex:q ?o }} }} }}"
    )
    reference, ref_requests = _run(local, query, 1, RecordingTransport(remote))
    batched, batch_requests = _run(local, query, 8, RecordingTransport(remote))
    assert batched == reference
    assert ref_requests == 2
    assert batch_requests == 1


def test_batch_size_one_disables_batching() -> None:
    local = Graph()
    remote = Graph()
    local.parse(
        data="""
        @prefix ex: <http://example.org/> .
        ex:s1 ex:p ex:a . ex:s2 ex:p ex:b . ex:s3 ex:p ex:c .
        """,
        format="turtle",
    )
    remote.parse(
        data="""
        @prefix ex: <http://example.org/> .
        ex:a ex:q 'A' . ex:b ex:q 'B' . ex:c ex:q 'C' .
        """,
        format="turtle",
    )
    query = _query(_service())
    transport = RecordingTransport(remote)
    _multiset, requests = _run(local, query, 1, transport)
    assert requests == 3
