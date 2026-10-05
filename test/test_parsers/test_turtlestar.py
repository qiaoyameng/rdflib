"""Tests for Turtle-star quoted triple (``<< s p o >>``) support.

Turtle-star extends Turtle with RDF-star quoted triples, which refer to a
statement without asserting it. Only the explicitly asserted outer triples
end up in the graph.
"""

import pickle

import pytest

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.compare import isomorphic
from rdflib.exceptions import Error
from rdflib.namespace import RDF
from rdflib.plugins.parsers.notation3 import BadSyntax, SinkParser
from rdflib.term import QuotedTriple

EX = "http://example.org/"
EX_S = URIRef(EX + "s")
EX_P = URIRef(EX + "p")
EX_A = URIRef(EX + "a")
EX_B = URIRef(EX + "b")
EX_C = URIRef(EX + "c")

PREFIX = "@prefix : <%s> .\n" % EX


def parse(data: str, format: str = "turtlestar") -> Graph:
    graph = Graph()
    graph.parse(data=PREFIX + data, format=format)
    return graph


class TestQuotedTripleTerm:
    def test_construction_and_accessors(self):
        qt = QuotedTriple((EX_A, EX_B, EX_C))
        assert qt.subject == EX_A
        assert qt.predicate == EX_B
        assert qt.object == EX_C
        assert tuple(qt) == (EX_A, EX_B, EX_C)
        assert len(qt) == 3
        assert qt[1] == EX_B

    def test_immutable(self):
        qt = QuotedTriple((EX_A, EX_B, EX_C))
        with pytest.raises(AttributeError):
            qt.subject = EX_A  # type: ignore[misc]

    def test_predicate_must_be_iri(self):
        with pytest.raises(ValueError):
            QuotedTriple((EX_A, BNode(), EX_C))
        with pytest.raises(ValueError):
            QuotedTriple((EX_A, Literal("p"), EX_C))

    def test_members_must_be_nodes(self):
        with pytest.raises(ValueError):
            QuotedTriple((EX_A, EX_B, "not a node"))  # type: ignore[arg-type]

    def test_arity(self):
        with pytest.raises(ValueError):
            QuotedTriple((EX_A, EX_B))  # type: ignore[arg-type]

    def test_equality_and_hash(self):
        qt1 = QuotedTriple((EX_A, EX_B, EX_C))
        qt2 = QuotedTriple((EX_A, EX_B, EX_C))
        qt3 = QuotedTriple((EX_A, EX_B, URIRef(EX + "d")))
        assert qt1 == qt2
        assert hash(qt1) == hash(qt2)
        assert qt1 != qt3
        assert qt1 != (EX_A, EX_B, EX_C)

    def test_n3(self):
        qt = QuotedTriple((EX_A, EX_B, Literal("lit")))
        assert qt.n3() == '<< %s %s "lit" >>' % (EX_A.n3(), EX_B.n3())

    def test_n3_nested(self):
        inner = QuotedTriple((EX_A, EX_B, EX_C))
        outer = QuotedTriple((inner, EX_B, Literal(1)))
        assert outer.n3() == (
            "<< %s %s %s >>" % (inner.n3(), EX_B.n3(), Literal(1).n3())
        )

    def test_pickle_roundtrip(self):
        qt = QuotedTriple((EX_A, EX_B, QuotedTriple((EX_A, EX_B, EX_C))))
        assert pickle.loads(pickle.dumps(qt)) == qt


class TestTurtleStarParser:
    def test_quoted_object_not_asserted(self):
        graph = parse(":s :p << :a :b :c >> .")
        assert len(graph) == 1
        assert (EX_A, EX_B, EX_C) not in graph
        quoted = QuotedTriple((EX_A, EX_B, EX_C))
        assert (EX_S, EX_P, quoted) in graph

    def test_quoted_subject_not_asserted(self):
        graph = parse("<< :a :b :c >> :p :o .")
        assert len(graph) == 1
        assert (EX_A, EX_B, EX_C) not in graph
        quoted = QuotedTriple((EX_A, EX_B, EX_C))
        assert (quoted, EX_P, URIRef(EX + "o")) in graph

    def test_quoted_predicate_is_iri(self):
        graph = parse(":s :p << :a :b :c >> .")
        quoted = graph.value(EX_S, EX_P)
        assert isinstance(quoted, QuotedTriple)
        assert isinstance(quoted.predicate, URIRef)

    def test_quoted_predicate_keyword_a(self):
        graph = parse(":s :p << :a a :C >> .")
        quoted = QuotedTriple((EX_A, RDF.type, URIRef(EX + "C")))
        assert (EX_S, EX_P, quoted) in graph

    def test_quoted_literal_object(self):
        graph = parse(':s :p << :a :b "lit"@en >> .')
        quoted = QuotedTriple((EX_A, EX_B, Literal("lit", lang="en")))
        assert (EX_S, EX_P, quoted) in graph

    def test_nested_quoted_triple(self):
        graph = parse(":s :p << << :a :b :c >> :q :r >> .")
        assert len(graph) == 1
        inner = QuotedTriple((EX_A, EX_B, EX_C))
        outer = QuotedTriple((inner, URIRef(EX + "q"), URIRef(EX + "r")))
        assert (EX_S, EX_P, outer) in graph

    def test_max_nesting_depth_accepted(self):
        inner = "<< :a :b :c >>"
        for i in range(SinkParser.max_quoted_depth - 1):
            inner = "<< %s :p%d :o%d >>" % (inner, i, i)
        graph = parse(":s :p %s ." % inner)
        assert len(graph) == 1

    def test_nesting_depth_limit_locatable(self):
        inner = "<< :a :b :c >>"
        for i in range(SinkParser.max_quoted_depth):
            inner = "<< %s :p%d :o%d >>" % (inner, i, i)
        with pytest.raises(BadSyntax) as excinfo:
            parse(":s :p %s ." % inner)
        message = str(excinfo.value)
        assert "maximum depth" in message
        assert "line" in message

    def test_quoted_triple_as_predicate_rejected(self):
        with pytest.raises(BadSyntax) as excinfo:
            parse(":s << :a :b :c >> :o .")
        assert "line" in str(excinfo.value)

    def test_blank_node_predicate_rejected(self):
        with pytest.raises(BadSyntax) as excinfo:
            parse(":s :p << :a _:b :c >> .")
        assert "line" in str(excinfo.value)

    def test_unterminated_quoted_triple(self):
        with pytest.raises(BadSyntax):
            parse(":s :p << :a :b :c .")

    def test_bnode_property_list_inside_quoted_rejected(self):
        with pytest.raises(BadSyntax) as excinfo:
            parse(":s :p << [ :q :r ] :b :c >> .")
        assert "line" in str(excinfo.value)

    def test_plain_turtle_rejects_quoted_triple(self):
        with pytest.raises(BadSyntax) as excinfo:
            parse(":s :p << :a :b :c >> .", format="turtle")
        assert "line" in str(excinfo.value)

    def test_bnode_identity_shared_within_document(self):
        graph = parse(":s :p << _:b :q :r >> .\n_:b :name \"shared\" .")
        assert len(graph) == 2
        quoted = graph.value(EX_S, EX_P)
        assert isinstance(quoted, QuotedTriple)
        inner_bnode = quoted.subject
        assert isinstance(inner_bnode, BNode)
        # the very same blank node is the subject of the asserted triple
        assert (inner_bnode, URIRef(EX + "name"), Literal("shared")) in graph


class TestTurtleStarRoundTrip:
    def roundtrip(self, data: str) -> tuple[Graph, Graph, str]:
        graph = parse(data)
        serialized = graph.serialize(format="turtlestar")
        reparsed = Graph()
        reparsed.parse(data=serialized, format="turtlestar")
        return graph, reparsed, serialized

    def test_roundtrip_quoted_object(self):
        graph, reparsed, serialized = self.roundtrip(":s :p << :a :b :c >> .")
        assert "<< :a :b :c >>" in serialized
        assert len(reparsed) == 1
        assert (EX_A, EX_B, EX_C) not in reparsed
        assert isomorphic(graph, reparsed)

    def test_roundtrip_quoted_subject(self):
        graph, reparsed, _ = self.roundtrip("<< :a :b :c >> :p :o .")
        assert isomorphic(graph, reparsed)

    def test_roundtrip_nested(self):
        graph, reparsed, _ = self.roundtrip(':s :p << << :a :b :c >> :q "lit" >> .')
        assert isomorphic(graph, reparsed)

    def test_roundtrip_shared_bnode(self):
        graph, reparsed, serialized = self.roundtrip(
            ':s :p << _:b :q :r >> .\n_:b :name "shared" .'
        )
        assert len(reparsed) == 2
        assert isomorphic(graph, reparsed)

    def test_roundtrip_only_quoted_fact(self):
        # a fact that is only ever referred to, never asserted
        graph, reparsed, _ = self.roundtrip(
            ":s :p << :a :b :c >> .\n<< :a :b :c >> :source :doc ."
        )
        assert len(graph) == 2
        assert (EX_A, EX_B, EX_C) not in graph
        assert len(reparsed) == 2
        assert (EX_A, EX_B, EX_C) not in reparsed
        assert isomorphic(graph, reparsed)

    def test_serialized_quoted_content_not_asserted(self):
        graph = parse(":s :p << :a :b :c >> .")
        serialized = graph.serialize(format="turtlestar")
        reparsed = Graph()
        reparsed.parse(data=serialized, format="turtlestar")
        assert len(reparsed) == len(graph) == 1

    def test_plain_turtle_serializer_rejects_quoted_triple(self):
        graph = parse(":s :p << :a :b :c >> .")
        with pytest.raises(Error):
            graph.serialize(format="turtle")
