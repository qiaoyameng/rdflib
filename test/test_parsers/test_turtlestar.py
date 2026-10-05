"""
Tests for the Turtle-star quoted triple (``<< s p o >>``) support: the
``turtle-star`` parser/serializer format entry and the immutable
`rdflib.term.QuotedTriple` term.
"""

from __future__ import annotations

import pytest

from rdflib import BNode, Graph, Literal, Namespace, QuotedTriple, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import RDF
from rdflib.plugins.parsers.notation3 import BadSyntax
from rdflib.plugins.parsers.turtlestar import DEFAULT_MAX_QUOTED_DEPTH

EX = Namespace("http://example.org/")

PREFIXES = "@prefix ex: <http://example.org/> .\n"


def parse(data: str, **kwargs) -> Graph:
    graph = Graph()
    graph.parse(data=PREFIXES + data, format="turtle-star", **kwargs)
    return graph


class TestQuotedTripleTerm:
    def test_accessors(self):
        qt = QuotedTriple((EX.s, EX.p, EX.o))
        assert qt.subject == EX.s
        assert qt.predicate == EX.p
        assert qt.object == EX.o
        assert tuple(qt) == (EX.s, EX.p, EX.o)
        assert len(qt) == 3
        assert qt[0] == EX.s

    def test_equality_and_hash(self):
        qt1 = QuotedTriple((EX.s, EX.p, EX.o))
        qt2 = QuotedTriple((EX.s, EX.p, EX.o))
        qt3 = QuotedTriple((EX.s, EX.p, EX.other))
        assert qt1 == qt2
        assert hash(qt1) == hash(qt2)
        assert qt1 != qt3
        assert qt1 != EX.s
        assert len({qt1, qt2, qt3}) == 2

    def test_immutable(self):
        qt = QuotedTriple((EX.s, EX.p, EX.o))
        with pytest.raises(AttributeError):
            qt.subject = EX.x  # type: ignore[misc]
        with pytest.raises(AttributeError):
            qt.predicate = EX.x  # type: ignore[misc]
        with pytest.raises(AttributeError):
            qt.object = EX.x  # type: ignore[misc]

    def test_invalid_terms_rejected(self):
        with pytest.raises(TypeError):
            QuotedTriple((Literal("lit"), EX.p, EX.o))
        with pytest.raises(TypeError):
            QuotedTriple((EX.s, BNode("b"), EX.o))
        with pytest.raises(TypeError):
            QuotedTriple((EX.s, EX.p, "not-a-term"))  # type: ignore[arg-type]

    def test_n3(self):
        qt = QuotedTriple((EX.s, EX.p, Literal("lit")))
        assert (
            qt.n3()
            == "<< <http://example.org/s> <http://example.org/p> "
            + Literal("lit").n3()
            + " >>"
        )

    def test_nested_n3(self):
        qt = QuotedTriple((QuotedTriple((EX.a, EX.b, EX.c)), EX.p, EX.o))
        assert qt.n3().startswith("<< <<")


class TestTurtleStarParsing:
    def test_quoted_triple_in_subject_position(self):
        graph = parse("<< ex:s ex:p ex:o >> ex:statedBy ex:alice .")
        assert len(graph) == 1
        (triple,) = list(graph)
        assert isinstance(triple[0], QuotedTriple)
        assert triple[0] == QuotedTriple((EX.s, EX.p, EX.o))
        assert triple[1:] == (EX.statedBy, EX.alice)

    def test_quoted_triple_in_object_position(self):
        graph = parse("ex:bob ex:claims << ex:s ex:p ex:o >> .")
        assert len(graph) == 1
        (triple,) = list(graph)
        assert triple[:2] == (EX.bob, EX.claims)
        assert isinstance(triple[2], QuotedTriple)
        assert triple[2] == QuotedTriple((EX.s, EX.p, EX.o))

    def test_quoted_content_is_not_asserted(self):
        # A fact that is only quoted, never asserted: only the outer,
        # explicitly asserted triple may end up in the graph.
        graph = parse("<< ex:s ex:p ex:o >> ex:statedBy ex:alice .")
        assert len(graph) == 1
        assert (EX.s, EX.p, EX.o) not in graph
        assert (EX.s, None, None) not in graph
        assert (None, EX.p, None) not in graph
        assert (QuotedTriple((EX.s, EX.p, EX.o)), EX.statedBy, EX.alice) in graph

    def test_quoted_triple_with_literal_and_a_predicate(self):
        graph = parse('<< ex:s a ex:Thing >> ex:note "hi" , << ex:x ex:p 42 >> .')
        assert len(graph) == 2
        assert (
            QuotedTriple((EX.s, RDF.type, EX.Thing)),
            EX.note,
            Literal("hi"),
        ) in graph
        quoted_obj = QuotedTriple((EX.x, EX.p, Literal(42)))
        assert (QuotedTriple((EX.s, RDF.type, EX.Thing)), EX.note, quoted_obj) in graph
        assert (EX.x, EX.p, Literal(42)) not in graph

    def test_nested_quoted_triple(self):
        graph = parse("<< << ex:a ex:b ex:c >> ex:d ex:e >> ex:nested ex:f .")
        assert len(graph) == 1
        (triple,) = list(graph)
        inner = QuotedTriple((EX.a, EX.b, EX.c))
        assert triple[0] == QuotedTriple((inner, EX.d, EX.e))
        assert (EX.a, EX.b, EX.c) not in graph
        assert (inner, EX.d, EX.e) not in graph

    def test_bnode_identity_shared_within_document(self):
        graph = parse(
            "ex:bob ex:claims << _:a ex:p ex:o >> , << _:a ex:q ex:r >> .\n"
            "_:a ex:r ex:s ."
        )
        quoted_bnodes = set()
        asserted_bnodes = set()
        for s, p, o in graph:
            if isinstance(o, QuotedTriple) and isinstance(o.subject, BNode):
                quoted_bnodes.add(o.subject)
            if isinstance(s, BNode):
                asserted_bnodes.add(s)
        # one single blank node identity shared by both quoted triples and
        # the asserted triple
        assert len(quoted_bnodes) == 1
        assert quoted_bnodes == asserted_bnodes

    def test_quoted_triple_in_predicate_position_rejected(self):
        with pytest.raises(BadSyntax) as excinfo:
            parse("ex:s << ex:a ex:b ex:c >> ex:o .")
        assert "predicate" in str(excinfo.value)
        assert "line" in str(excinfo.value)

    def test_quoted_triple_predicate_must_be_iri(self):
        with pytest.raises(BadSyntax) as excinfo:
            parse("<< ex:s _:b ex:o >> ex:p ex:q .")
        assert "predicate of quoted triple must be an IRI" in str(excinfo.value)
        with pytest.raises(BadSyntax):
            parse('<< ex:s "lit" ex:o >> ex:p ex:q .')

    def test_quoted_triple_subject_must_be_node(self):
        with pytest.raises(BadSyntax) as excinfo:
            parse('<< "lit" ex:p ex:o >> ex:p ex:q .')
        assert "subject of quoted triple" in str(excinfo.value)

    def test_unclosed_quoted_triple_rejected(self):
        with pytest.raises(BadSyntax) as excinfo:
            parse("<< ex:s ex:p ex:o > ex:p ex:q .")
        assert "line" in str(excinfo.value)

    def test_nesting_depth_limit(self):
        # depth 2 nesting is fine with the default limit
        graph = parse("<< << ex:a ex:b ex:c >> ex:d ex:e >> ex:p ex:o .")
        assert len(graph) == 1

        def nest(depth: int) -> str:
            # returns a document with ``depth + 1`` levels of nesting
            data = "<< ex:a ex:b ex:c >>"
            for n in range(depth):
                data = "<< %s ex:p%d ex:o%d >>" % (data, n, n)
            return data + " ex:p ex:o ."

        # default limit exceeded
        with pytest.raises(BadSyntax) as excinfo:
            parse(nest(DEFAULT_MAX_QUOTED_DEPTH))
        message = str(excinfo.value)
        assert "maximum depth" in message
        # the error is locatable: it carries line information
        assert "line" in message

        # exactly at the default limit is fine
        assert len(parse(nest(DEFAULT_MAX_QUOTED_DEPTH - 1))) == 1

        # configurable limit
        with pytest.raises(BadSyntax):
            parse(nest(1), max_quoted_depth=1)
        assert len(parse(nest(1), max_quoted_depth=2)) == 1

    def test_plain_turtle_still_rejects_quoted_triples(self):
        graph = Graph()
        with pytest.raises(BadSyntax):
            graph.parse(
                data=PREFIXES + "<< ex:s ex:p ex:o >> ex:statedBy ex:alice .",
                format="turtle",
            )


class TestTurtleStarRoundTrip:
    def test_roundtrip_unasserted_fact(self):
        # A fact that is only quoted (referred to), never asserted, must
        # survive a serialize/parse round trip unchanged: same number of
        # triples, same membership, and the quoted fact still unasserted.
        graph = parse("<< ex:s ex:p ex:o >> ex:statedBy ex:alice .")
        assert len(graph) == 1
        assert (EX.s, EX.p, EX.o) not in graph

        data = graph.serialize(format="turtle-star")
        assert "<<" in data and ">>" in data

        graph2 = Graph()
        graph2.parse(data=data, format="turtle-star")
        assert len(graph2) == 1
        assert (EX.s, EX.p, EX.o) not in graph2
        assert (QuotedTriple((EX.s, EX.p, EX.o)), EX.statedBy, EX.alice) in graph2
        assert isomorphic(graph, graph2)

    def test_roundtrip_isomorphic_with_shared_bnodes(self):
        graph = parse(
            "ex:bob ex:claims << _:a ex:p ex:o >> , << _:a ex:q ex:r >> .\n"
            "_:a ex:r ex:s .\n"
            "<< << ex:a ex:b ex:c >> ex:d _:a >> ex:nested ex:f ."
        )
        data = graph.serialize(format="turtle-star")
        graph2 = Graph()
        graph2.parse(data=data, format="turtle-star")
        assert len(graph2) == len(graph)
        assert isomorphic(graph, graph2)

    def test_roundtrip_nested(self):
        graph = parse("<< << ex:a ex:b ex:c >> ex:d ex:e >> ex:nested ex:f .")
        data = graph.serialize(format="turtle-star")
        graph2 = Graph()
        graph2.parse(data=data, format="turtle-star")
        assert len(graph2) == 1
        assert isomorphic(graph, graph2)

    def test_serialize_declares_namespaces_used_in_quoted_triples(self):
        graph = Graph()
        graph.add(
            (
                QuotedTriple((EX.s, EX.p, EX.o)),
                EX.statedBy,
                EX.alice,
            )
        )
        data = graph.serialize(format="turtle-star")
        # the namespace used only inside the quoted triple must be declared
        assert "http://example.org/" in data
        graph2 = Graph()
        graph2.parse(data=data, format="turtle-star")
        assert len(graph2) == 1
