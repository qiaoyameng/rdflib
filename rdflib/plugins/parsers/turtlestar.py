"""
Turtle-star parser for RDFLib.

Turtle-star extends `Turtle <https://www.w3.org/TR/turtle/>`_ with quoted
triples written ``<< subject predicate object >>``, allowing a statement to
be referred to - for example to record the source of an unconfirmed fact -
without asserting it.

This parser only supports the explicit quoted-triple syntax: quoted triples
may appear in subject or object position and may be nested up to a
(configurable) maximum depth.  Only the outer, explicitly asserted triples
are added to the graph; the content of a quoted triple is represented by an
immutable `rdflib.term.QuotedTriple` term and is never asserted as a side
effect of parsing.  No other RDF 1.2 / RDF-star syntax (such as annotation
syntax or triple term datatypes) is claimed to be supported.
"""

from __future__ import annotations

from collections.abc import MutableSequence
from typing import IO, Any, Optional, Union, cast

from rdflib.exceptions import ParserError
from rdflib.graph import Graph
from rdflib.parser import InputSource
from rdflib.plugins.parsers.notation3 import (
    RDFSink,
    RDF_type_URI,
    SinkParser,
    TurtleParser,
)
from rdflib.term import BNode, Literal, Node, QuotedTriple, URIRef

__all__ = ["TurtleStarParser", "TurtleStarSinkParser"]

#: Default maximum nesting depth for quoted triples, i.e. how many
#: ``<< ... >>`` constructs may appear inside one another.
DEFAULT_MAX_QUOTED_DEPTH = 8


class TurtleStarSinkParser(SinkParser):
    """A `SinkParser` that also accepts Turtle-star quoted triples.

    Quoted triples (``<< s p o >>``) are accepted wherever a subject or
    object term is parsed, nested up to ``max_quoted_depth`` levels.  They
    are rejected, with a position-aware `BadSyntax` error, in predicate
    position and beyond the nesting limit.
    """

    def __init__(
        self,
        *args: Any,
        max_quoted_depth: int = DEFAULT_MAX_QUOTED_DEPTH,
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self.max_quoted_depth = max_quoted_depth
        self._quoted_depth = 0
        self._verb_nesting = 0

    def verb(self, argstr: str, i: int, res: MutableSequence[Any]) -> int:
        # Track predicate position so that quoted triples can be rejected
        # there: a predicate must be a plain IRI.
        self._verb_nesting += 1
        try:
            return super().verb(argstr, i, res)
        finally:
            self._verb_nesting -= 1

    def node(
        self,
        argstr: str,
        i: int,
        res: MutableSequence[Any],
        subjectAlready: Optional[Node] = None,
    ) -> int:
        j = self.skipSpace(argstr, i)
        if j >= 0 and argstr[j : j + 2] == "<<":
            if self._verb_nesting:
                self.BadSyntax(
                    argstr,
                    j,
                    "quoted triple << ... >> is not allowed in predicate"
                    " position, a predicate must be an IRI",
                )
            return self.quotedTriple(argstr, j, res)
        return super().node(argstr, i, res, subjectAlready)

    def quotedTriple(  # noqa: N802
        self, argstr: str, i: int, res: MutableSequence[Any]
    ) -> int:
        """Parse ``<< subject predicate object >>`` starting at ``i``,
        which must point at the opening ``<<``.  The resulting
        `rdflib.term.QuotedTriple` term is appended to ``res``; nothing is
        asserted into the graph."""
        if self._quoted_depth >= self.max_quoted_depth:
            self.BadSyntax(
                argstr,
                i,
                "quoted triple nesting exceeds the maximum depth of %d"
                % self.max_quoted_depth,
            )
        self._quoted_depth += 1
        try:
            subject, j = self._quotedSubject(argstr, i + 2)
            predicate, j = self._quotedPredicate(argstr, j)
            object_, j = self._quotedObject(argstr, j)

            j = self.skipSpace(argstr, j)
            if j < 0:
                self.BadSyntax(argstr, i, "EOF found, expected '>>'")
            if argstr[j : j + 2] != ">>":
                self.BadSyntax(argstr, j, "expected '>>' to close quoted triple")
            res.append(QuotedTriple((subject, predicate, object_)))
            return j + 2
        finally:
            self._quoted_depth -= 1

    def _quotedSubject(self, argstr: str, i: int) -> tuple[Node, int]:
        j = self.skipSpace(argstr, i)
        if j < 0:
            self.BadSyntax(argstr, i, "EOF found, expected subject of quoted triple")
        i = j
        if argstr[i : i + 2] == "<<":
            res: list[Any] = []
            j = self.quotedTriple(argstr, i, res)
            return res[0], j
        res = []
        j = self.uri_ref2(argstr, i, res)
        if j < 0:
            self.BadSyntax(
                argstr,
                i,
                "expected IRI, blank node label or nested quoted triple as"
                " subject of quoted triple",
            )
        term = res[0]
        if not isinstance(term, (URIRef, BNode)):
            self.BadSyntax(
                argstr,
                i,
                "subject of quoted triple must be an IRI or blank node",
            )
        return term, j

    def _quotedPredicate(self, argstr: str, i: int) -> tuple[URIRef, int]:
        j = self.skipSpace(argstr, i)
        if j < 0:
            self.BadSyntax(argstr, i, "EOF found, expected predicate of quoted triple")
        i = j
        k = self.tok("a", argstr, i)
        if k >= 0:
            return self._store.newSymbol(RDF_type_URI), k
        res: list[Any] = []
        j = self.uri_ref2(argstr, i, res)
        if j < 0:
            self.BadSyntax(argstr, i, "expected predicate IRI of quoted triple")
        predicate = res[0]
        if not isinstance(predicate, URIRef):
            self.BadSyntax(argstr, i, "predicate of quoted triple must be an IRI")
        return predicate, j

    def _quotedObject(self, argstr: str, i: int) -> tuple[Node, int]:
        j = self.skipSpace(argstr, i)
        if j < 0:
            self.BadSyntax(argstr, i, "EOF found, expected object of quoted triple")
        i = j
        if argstr[i : i + 2] == "<<":
            res: list[Any] = []
            j = self.quotedTriple(argstr, i, res)
            return res[0], j
        if argstr[i] in "[(":
            self.BadSyntax(
                argstr,
                i,
                "blank node property lists and collections are not allowed"
                " inside a quoted triple",
            )
        res = []
        j = self.nodeOrLiteral(argstr, i, res)
        if j < 0:
            self.BadSyntax(argstr, i, "expected object of quoted triple")
        term = self._store.normalise(self._context, res[0])
        if not isinstance(term, (URIRef, BNode, Literal)):
            self.BadSyntax(
                argstr,
                i,
                "object of quoted triple must be an IRI, blank node or literal",
            )
        return term, j


class TurtleStarParser(TurtleParser):
    """An RDFLib parser for Turtle-star.

    Parses Turtle plus quoted triples (``<< s p o >>``) in subject and
    object position.  Only explicitly asserted triples are added to the
    graph; quoted content is kept as `rdflib.term.QuotedTriple` terms.
    """

    def parse(
        self,
        source: InputSource,
        graph: Graph,
        encoding: Optional[str] = "utf-8",
        max_quoted_depth: int = DEFAULT_MAX_QUOTED_DEPTH,
        **kwargs: Any,
    ) -> None:
        if encoding not in [None, "utf-8"]:
            raise ParserError(
                "Turtle-star files are always utf-8 encoded, I was passed: %s"
                % encoding
            )

        sink = RDFSink(graph)

        baseURI = graph.absolutize(source.getPublicId() or source.getSystemId() or "")
        p = TurtleStarSinkParser(
            sink, baseURI=baseURI, turtle=True, max_quoted_depth=max_quoted_depth
        )
        # N3 parser prefers str stream
        stream: Union[IO[str], IO[bytes], None] = cast(
            "IO[str]", source.getCharacterStream()
        )
        if not stream:
            stream = cast("IO[bytes]", source.getByteStream())
        if not stream:
            raise Exception("No valid stream available for parsing.")
        p.loadStream(stream)

        for prefix, namespace in p._bindings.items():
            graph.bind(prefix, namespace)
