"""
Turtle-star serializer for RDFLib.

Serializes a graph as Turtle, writing any `rdflib.term.QuotedTriple` terms
in subject or object position using the Turtle-star quoted triple syntax
``<< s p o >>``.  Only asserted triples are written; quoted triples appear
solely as terms of the asserted triples that contain them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rdflib.exceptions import Error
from rdflib.plugins.serializers.turtle import (
    OBJECT,
    SUBJECT,
    VERB,
    TurtleSerializer,
    _GEN_QNAME_FOR_DT,
)
from rdflib.term import BNode, Literal, Node, QuotedTriple

if TYPE_CHECKING:
    from rdflib.graph import _TripleType

__all__ = ["TurtleStarSerializer"]


class TurtleStarSerializer(TurtleSerializer):
    """Turtle serializer with support for Turtle-star quoted triples."""

    short_name = "turtle-star"

    def preprocessTriple(self, triple: _TripleType) -> None:
        super().preprocessTriple(triple)
        for term in (triple[SUBJECT], triple[OBJECT]):
            self._preprocessQuoted(term)

    def _preprocessQuoted(self, term: Node) -> None:
        # Make sure namespaces used inside quoted triples are declared and
        # that blank nodes mentioned in quoted triples are always written
        # with an explicit, shared blank node label (never inlined as
        # ``[]`` or ``[ ... ]``), so that blank node identity is preserved
        # across a serialize/parse round trip.
        if not isinstance(term, QuotedTriple):
            return
        for position, inner in (
            (SUBJECT, term.subject),
            (VERB, term.predicate),
            (OBJECT, term.object),
        ):
            if isinstance(inner, QuotedTriple):
                self._preprocessQuoted(inner)
            else:
                self.get_pname(inner, gen_prefix=(position == VERB))
                if isinstance(inner, Literal) and inner.datatype:
                    self.get_pname(inner.datatype, gen_prefix=_GEN_QNAME_FOR_DT)
                if isinstance(inner, BNode):
                    self._references[inner] += 2

    def label(self, node: Node, position: int) -> str:
        if isinstance(node, QuotedTriple):
            if position == VERB:
                raise Error(
                    "Cannot serialize quoted triple in predicate position: %r"
                    % (node,)
                )
            return self.quotedTripleLabel(node)
        return super().label(node, position)

    def quotedTripleLabel(self, node: QuotedTriple) -> str:  # noqa: N802
        return "<< %s %s %s >>" % (
            self.label(node.subject, SUBJECT),
            self.label(node.predicate, VERB),
            self.label(node.object, OBJECT),
        )
