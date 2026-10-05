r"""
This module implements the SPARQL 1.1 Property path operators, as
defined in:
[http://www.w3.org/TR/sparql11-query/#propertypaths](http://www.w3.org/TR/sparql11-query/#propertypaths)

In SPARQL the syntax is as follows:

| Syntax              | Matches                                                                 |
|---------------------|-------------------------------------------------------------------------|
| `iri`               | An IRI. A path of length one.                                           |
| `^elt`              | Inverse path (object to subject).                                       |
| `elt1 / elt2`       | A sequence path of `elt1` followed by `elt2`.                           |
| `elt1 \| elt2`      | An alternative path of `elt1` or `elt2` (all possibilities are tried).  |
| `elt*`              | A path that connects subject and object by zero or more matches of `elt`.|
| `elt+`              | A path that connects subject and object by one or more matches of `elt`.|
| `elt?`              | A path that connects subject and object by zero or one matches of `elt`.|
| `!iri` or <br> `!(iri1 \| ... \| irin)` | Negated property set. An IRI not among `iri1` to `irin`. <br> `!iri` is short for `!(iri)`. |
| `!^iri` or <br> `!(^iri1 \| ... \| ^irin)` | Negated reverse property set. Excludes `^iri1` to `^irin` as reverse paths. <br> `!^iri` is short for `!(^iri)`. |
| `!(iri1 \| ... \| irij \| ^irij+1 \| ... \| ^irin)` | A combination of forward and reverse properties in a negated property set. |
| `(elt)`             | A grouped path `elt`, where parentheses control precedence.             |

This module is used internally by the SPARQL engine, but the property paths
can also be used to query RDFLib Graphs directly.

Where possible the SPARQL syntax is mapped to Python operators, and property
path objects can be constructed from existing URIRefs.

```python
>>> from rdflib import Graph, Namespace
>>> from rdflib.namespace import FOAF

>>> ~FOAF.knows
Path(~http://xmlns.com/foaf/0.1/knows)

>>> FOAF.knows/FOAF.name
Path(http://xmlns.com/foaf/0.1/knows / http://xmlns.com/foaf/0.1/name)

>>> FOAF.name|FOAF.givenName
Path(http://xmlns.com/foaf/0.1/name | http://xmlns.com/foaf/0.1/givenName)

```

Modifiers (?, \*, +) are done using \* (the multiplication operator) and
the strings '\*', '?', '+', also defined as constants in this file.

```python
>>> FOAF.knows*OneOrMore
Path(http://xmlns.com/foaf/0.1/knows+)

```

The path objects can also be used with the normal graph methods.

First some example data:

```python
>>> g=Graph()

>>> g=g.parse(data='''
... @prefix : <ex:> .
...
... :a :p1 :c ; :p2 :f .
... :c :p2 :e ; :p3 :g .
... :g :p3 :h ; :p2 :j .
... :h :p3 :a ; :p2 :g .
...
... :q :px :q .
...
... ''', format='n3') # doctest: +ELLIPSIS

>>> e = Namespace('ex:')

```

Graph contains:

```python
>>> (e.a, e.p1/e.p2, e.e) in g
True

```

Graph generator functions, triples, subjects, objects, etc. :

```python
>>> list(g.objects(e.c, (e.p3*OneOrMore)/e.p2)) # doctest: +NORMALIZE_WHITESPACE
[rdflib.term.URIRef('ex:j'), rdflib.term.URIRef('ex:g'),
    rdflib.term.URIRef('ex:f')]

```

A more complete set of tests:

```python
>>> list(eval_path(g, (None, e.p1/e.p2, None)))==[(e.a, e.e)]
True
>>> list(eval_path(g, (e.a, e.p1|e.p2, None)))==[(e.a,e.c), (e.a,e.f)]
True
>>> list(eval_path(g, (e.c, ~e.p1, None))) == [ (e.c, e.a) ]
True
>>> list(eval_path(g, (e.a, e.p1*ZeroOrOne, None))) == [(e.a, e.a), (e.a, e.c)]
True
>>> list(eval_path(g, (e.c, e.p3*OneOrMore, None))) == [
...     (e.c, e.g), (e.c, e.h), (e.c, e.a)]
True
>>> list(eval_path(g, (e.c, e.p3*ZeroOrMore, None))) == [(e.c, e.c),
...     (e.c, e.g), (e.c, e.h), (e.c, e.a)]
True
>>> list(eval_path(g, (e.a, -e.p1, None))) == [(e.a, e.f)]
True
>>> list(eval_path(g, (e.a, -(e.p1|e.p2), None))) == []
True
>>> list(eval_path(g, (e.g, -~e.p2, None))) == [(e.g, e.j)]
True
>>> list(eval_path(g, (e.e, ~(e.p1/e.p2), None))) == [(e.e, e.a)]
True
>>> list(eval_path(g, (e.a, e.p1/e.p3/e.p3, None))) == [(e.a, e.h)]
True

>>> list(eval_path(g, (e.q, e.px*OneOrMore, None)))
[(rdflib.term.URIRef('ex:q'), rdflib.term.URIRef('ex:q'))]

>>> list(eval_path(g, (None, e.p1|e.p2, e.c)))
[(rdflib.term.URIRef('ex:a'), rdflib.term.URIRef('ex:c'))]

>>> list(eval_path(g, (None, ~e.p1, e.a))) == [ (e.c, e.a) ]
True
>>> list(eval_path(g, (None, e.p1*ZeroOrOne, e.c))) # doctest: +NORMALIZE_WHITESPACE
[(rdflib.term.URIRef('ex:c'), rdflib.term.URIRef('ex:c')),
 (rdflib.term.URIRef('ex:a'), rdflib.term.URIRef('ex:c'))]

>>> list(eval_path(g, (None, e.p3*OneOrMore, e.a))) # doctest: +NORMALIZE_WHITESPACE
[(rdflib.term.URIRef('ex:h'), rdflib.term.URIRef('ex:a')),
 (rdflib.term.URIRef('ex:g'), rdflib.term.URIRef('ex:a')),
 (rdflib.term.URIRef('ex:c'), rdflib.term.URIRef('ex:a'))]

>>> list(eval_path(g, (None, e.p3*ZeroOrMore, e.a))) # doctest: +NORMALIZE_WHITESPACE
[(rdflib.term.URIRef('ex:a'), rdflib.term.URIRef('ex:a')),
 (rdflib.term.URIRef('ex:h'), rdflib.term.URIRef('ex:a')),
 (rdflib.term.URIRef('ex:g'), rdflib.term.URIRef('ex:a')),
 (rdflib.term.URIRef('ex:c'), rdflib.term.URIRef('ex:a'))]

>>> list(eval_path(g, (None, -e.p1, e.f))) == [(e.a, e.f)]
True
>>> list(eval_path(g, (None, -(e.p1|e.p2), e.c))) == []
True
>>> list(eval_path(g, (None, -~e.p2, e.j))) == [(e.g, e.j)]
True
>>> list(eval_path(g, (None, ~(e.p1/e.p2), e.a))) == [(e.e, e.a)]
True
>>> list(eval_path(g, (None, e.p1/e.p3/e.p3, e.h))) == [(e.a, e.h)]
True

>>> list(eval_path(g, (e.q, e.px*OneOrMore, None)))
[(rdflib.term.URIRef('ex:q'), rdflib.term.URIRef('ex:q'))]

>>> list(eval_path(g, (e.c, (e.p2|e.p3)*ZeroOrMore, e.j)))
[(rdflib.term.URIRef('ex:c'), rdflib.term.URIRef('ex:j'))]

```

No vars specified:

```python
>>> sorted(list(eval_path(g, (None, e.p3*OneOrMore, None)))) #doctest: +NORMALIZE_WHITESPACE
[(rdflib.term.URIRef('ex:c'), rdflib.term.URIRef('ex:a')),
 (rdflib.term.URIRef('ex:c'), rdflib.term.URIRef('ex:g')),
 (rdflib.term.URIRef('ex:c'), rdflib.term.URIRef('ex:h')),
 (rdflib.term.URIRef('ex:g'), rdflib.term.URIRef('ex:a')),
 (rdflib.term.URIRef('ex:g'), rdflib.term.URIRef('ex:h')),
 (rdflib.term.URIRef('ex:h'), rdflib.term.URIRef('ex:a'))]

```

Witness paths
-------------

`eval_path_witness` adds the concrete triples (and the direction in which
they were traversed) that explain how an endpoint pair is connected.  Each
pair gets a single shortest witness; a zero-length match is an empty sequence.

```python
>>> [(s, o, len(w), w.directions)
...  for s, o, w in eval_path_witness(g, (e.a, e.p1/e.p2, None))]
[(rdflib.term.URIRef('ex:a'), rdflib.term.URIRef('ex:e'), 2, (False, False))]

>>> [(s, o, len(w))
...  for s, o, w in eval_path_witness(g, (None, ~(e.p1/e.p2), e.a))]
[(rdflib.term.URIRef('ex:e'), rdflib.term.URIRef('ex:a'), 2)]

>>> # zero-length matches are empty, they never invent an edge
>>> [len(w) for s, o, w in eval_path_witness(g, (e.q, e.px*ZeroOrMore, e.q))]
[0]

```
"""

from __future__ import annotations

import warnings
from abc import ABC, abstractmethod
from collections.abc import Callable, Generator, Iterator
from functools import total_ordering
from typing import (
    TYPE_CHECKING,
    NamedTuple,
    Any,
    Optional,
    Sequence,
    Union,
)

from rdflib.term import Node, URIRef

if TYPE_CHECKING:
    from rdflib._type_checking import _MulPathMod
    from rdflib.graph import (
        Graph,
        _ObjectType,
        _PredicateType,
        _SubjectType,
        _TripleType,
    )
    from rdflib.namespace import NamespaceManager


# property paths

ZeroOrMore = "*"
OneOrMore = "+"
ZeroOrOne = "?"


def _n3(
    arg: Union[URIRef, Path], namespace_manager: Optional[NamespaceManager] = None
) -> str:
    if isinstance(arg, (SequencePath, AlternativePath)) and len(arg.args) > 1:
        return "(%s)" % arg.n3(namespace_manager)
    return arg.n3(namespace_manager)


@total_ordering
class Path(ABC):
    """Base class for all property paths."""

    __or__: Callable[[Path, Union[URIRef, Path]], AlternativePath]
    __invert__: Callable[[Path], InvPath]
    __neg__: Callable[[Path], NegatedPath]
    __truediv__: Callable[[Path, Union[URIRef, Path]], SequencePath]
    __mul__: Callable[[Path, str], MulPath]

    @abstractmethod
    def eval(
        self,
        graph: Graph,
        subj: Optional[_SubjectType] = None,
        obj: Optional[_ObjectType] = None,
    ) -> Iterator[tuple[_SubjectType, _ObjectType]]: ...

    @abstractmethod
    def n3(self, namespace_manager: Optional[NamespaceManager] = None) -> str: ...

    def __hash__(self):
        return hash(repr(self))

    def __eq__(self, other):
        return repr(self) == repr(other)

    def __lt__(self, other: Any) -> bool:
        if not isinstance(other, (Path, Node)):
            raise TypeError(
                "unorderable types: %s() < %s()" % (repr(self), repr(other))
            )
        return repr(self) < repr(other)


class InvPath(Path):
    def __init__(self, arg: Union[Path, URIRef]):
        self.arg = arg

    def eval(
        self,
        graph: Graph,
        subj: Optional[_SubjectType] = None,
        obj: Optional[_ObjectType] = None,
    ) -> Generator[tuple[_ObjectType, _SubjectType], None, None]:
        for s, o in eval_path(graph, (obj, self.arg, subj)):
            yield o, s

    def __repr__(self) -> str:
        return "Path(~%s)" % (self.arg,)

    def n3(self, namespace_manager: Optional[NamespaceManager] = None) -> str:
        return "^%s" % _n3(self.arg, namespace_manager)


class SequencePath(Path):
    def __init__(self, *args: Union[Path, URIRef]):
        self.args: list[Union[Path, URIRef]] = []
        for a in args:
            if isinstance(a, SequencePath):
                self.args += a.args
            else:
                self.args.append(a)

    def eval(
        self,
        graph: Graph,
        subj: Optional[_SubjectType] = None,
        obj: Optional[_ObjectType] = None,
    ) -> Generator[tuple[_SubjectType, _ObjectType], None, None]:
        def _eval_seq(
            paths: list[Union[Path, URIRef]],
            subj: Optional[_SubjectType],
            obj: Optional[_ObjectType],
        ) -> Generator[tuple[_SubjectType, _ObjectType], None, None]:
            if paths[1:]:
                for s, o in eval_path(graph, (subj, paths[0], None)):
                    for r in _eval_seq(paths[1:], o, obj):
                        yield s, r[1]

            else:
                for s, o in eval_path(graph, (subj, paths[0], obj)):
                    yield s, o

        def _eval_seq_bw(
            paths: list[Union[Path, URIRef]],
            subj: Optional[_SubjectType],
            obj: _ObjectType,
        ) -> Generator[tuple[_SubjectType, _ObjectType], None, None]:
            if paths[:-1]:
                for s, o in eval_path(graph, (None, paths[-1], obj)):
                    for r in _eval_seq(paths[:-1], subj, s):
                        yield r[0], o

            else:
                for s, o in eval_path(graph, (subj, paths[0], obj)):
                    yield s, o

        if subj:
            return _eval_seq(self.args, subj, obj)
        elif obj:
            return _eval_seq_bw(self.args, subj, obj)
        else:  # no vars bound, we can start anywhere
            return _eval_seq(self.args, subj, obj)

    def __repr__(self) -> str:
        return "Path(%s)" % " / ".join(str(x) for x in self.args)

    def n3(self, namespace_manager: Optional[NamespaceManager] = None) -> str:
        return "/".join(_n3(a, namespace_manager) for a in self.args)


class AlternativePath(Path):
    def __init__(self, *args: Union[Path, URIRef]):
        self.args: list[Union[Path, URIRef]] = []
        for a in args:
            if isinstance(a, AlternativePath):
                self.args += a.args
            else:
                self.args.append(a)

    def eval(
        self,
        graph: Graph,
        subj: Optional[_SubjectType] = None,
        obj: Optional[_ObjectType] = None,
    ) -> Generator[tuple[_SubjectType, _ObjectType], None, None]:
        for x in self.args:
            for y in eval_path(graph, (subj, x, obj)):
                yield y

    def __repr__(self) -> str:
        return "Path(%s)" % " | ".join(str(x) for x in self.args)

    def n3(self, namespace_manager: Optional[NamespaceManager] = None) -> str:
        return "|".join(_n3(a, namespace_manager) for a in self.args)


class MulPath(Path):
    def __init__(self, path: Union[Path, URIRef], mod: _MulPathMod):
        self.path = path
        self.mod = mod

        if mod == ZeroOrOne:
            self.zero = True
            self.more = False
        elif mod == ZeroOrMore:
            self.zero = True
            self.more = True
        elif mod == OneOrMore:
            self.zero = False
            self.more = True
        else:
            raise Exception("Unknown modifier %s" % mod)

    def eval(
        self,
        graph: Graph,
        subj: Optional[_SubjectType] = None,
        obj: Optional[_ObjectType] = None,
        first: bool = True,
    ) -> Generator[tuple[_SubjectType, _ObjectType], None, None]:
        if self.zero and first:
            if subj and obj:
                if subj == obj:
                    yield subj, obj
            elif subj:
                yield subj, subj
            elif obj:
                yield obj, obj

        def _fwd(
            subj: Optional[_SubjectType] = None,
            obj: Optional[_ObjectType] = None,
            seen: Optional[set[_SubjectType]] = None,
        ) -> Generator[tuple[_SubjectType, _ObjectType], None, None]:
            # type error: Item "None" of "Optional[set[Node]]" has no attribute "add"
            # type error: Argument 1 to "add" of "set" has incompatible type "Optional[Node]"; expected "Node"
            seen.add(subj)  # type: ignore[union-attr, arg-type]

            for s, o in eval_path(graph, (subj, self.path, None)):
                if not obj or o == obj:
                    yield s, o
                if self.more:
                    # type error: Unsupported right operand type for in ("Optional[set[Node]]")
                    if o in seen:  # type: ignore[operator]
                        continue
                    for s2, o2 in _fwd(o, obj, seen):
                        yield s, o2

        def _bwd(
            subj: Optional[_SubjectType] = None,
            obj: Optional[_ObjectType] = None,
            seen: Optional[set[_ObjectType]] = None,
        ) -> Generator[tuple[_SubjectType, _ObjectType], None, None]:
            # type error: Item "None" of "Optional[set[Node]]" has no attribute "add"
            # type error: Argument 1 to "add" of "set" has incompatible type "Optional[Node]"; expected "Node"
            seen.add(obj)  # type: ignore[union-attr, arg-type]

            for s, o in eval_path(graph, (None, self.path, obj)):
                if not subj or subj == s:
                    yield s, o
                if self.more:
                    # type error: Unsupported right operand type for in ("Optional[set[Node]]")
                    if s in seen:  # type: ignore[operator]
                        continue

                    for s2, o2 in _bwd(None, s, seen):
                        yield s2, o

        def _all_fwd_paths() -> Generator[tuple[_SubjectType, _ObjectType], None, None]:
            if self.zero:
                seen1 = set()
                # According to the spec, ALL nodes are possible solutions
                # (even literals)
                # we cannot do this without going through ALL triples
                # unless we keep an index of all terms somehow
                # but let's just hope this query doesn't happen very often...
                for s, o in graph.subject_objects(None):
                    if s not in seen1:
                        seen1.add(s)
                        yield s, s
                    if o not in seen1:
                        seen1.add(o)
                        yield o, o

            seen = set()
            for s, o in eval_path(graph, (None, self.path, None)):
                if not self.more:
                    yield s, o
                else:
                    if s not in seen:
                        seen.add(s)
                        f = list(_fwd(s, None, set()))
                        for s1, o1 in f:
                            assert s1 == s
                            yield s1, o1

        done = set()  # the spec does, by defn, not allow duplicates
        if subj:
            for x in _fwd(subj, obj, set()):
                if x not in done:
                    done.add(x)
                    yield x
        elif obj:
            for x in _bwd(subj, obj, set()):
                if x not in done:
                    done.add(x)
                    yield x
        else:
            for x in _all_fwd_paths():
                if x not in done:
                    done.add(x)
                    yield x

    def __repr__(self) -> str:
        return "Path(%s%s)" % (self.path, self.mod)

    def n3(self, namespace_manager: Optional[NamespaceManager] = None) -> str:
        return "%s%s" % (_n3(self.path, namespace_manager), self.mod)


class NegatedPath(Path):
    def __init__(self, arg: Union[AlternativePath, InvPath, URIRef]):
        self.args: list[Union[URIRef, Path]]
        if isinstance(arg, (URIRef, InvPath)):
            self.args = [arg]
        elif isinstance(arg, AlternativePath):
            self.args = arg.args
        else:
            raise Exception(
                "Can only negate URIRefs, InvPaths or "
                + "AlternativePaths, not: %s" % (arg,)
            )

    def eval(self, graph, subj=None, obj=None):
        for s, p, o in graph.triples((subj, None, obj)):
            for a in self.args:
                if isinstance(a, URIRef):
                    if p == a:
                        break
                elif isinstance(a, InvPath):
                    if (o, a.arg, s) in graph:
                        break
                else:
                    raise Exception("Invalid path in NegatedPath: %s" % a)
            else:
                yield s, o

    def __repr__(self) -> str:
        return "Path(! %s)" % ",".join(str(x) for x in self.args)

    def n3(self, namespace_manager: Optional[NamespaceManager] = None) -> str:
        return "!(%s)" % ("|".join(_n3(arg, namespace_manager) for arg in self.args))


class PathList(list):
    pass


def path_alternative(self: Union[URIRef, Path], other: Union[URIRef, Path]):
    """
    alternative path
    """
    if not isinstance(other, (URIRef, Path)):
        raise Exception("Only URIRefs or Paths can be in paths!")
    return AlternativePath(self, other)


def path_sequence(self: Union[URIRef, Path], other: Union[URIRef, Path]):
    """
    sequence path
    """
    if not isinstance(other, (URIRef, Path)):
        raise Exception("Only URIRefs or Paths can be in paths!")
    return SequencePath(self, other)


def evalPath(  # noqa: N802
    graph: Graph,
    t: tuple[
        Optional[_SubjectType],
        Union[None, Path, _PredicateType],
        Optional[_ObjectType],
    ],
) -> Iterator[tuple[_SubjectType, _ObjectType]]:
    warnings.warn(
        DeprecationWarning(
            "rdflib.path.evalPath() is deprecated, use the (snake-cased) eval_path(). "
            "The mixed-case evalPath() function name is incompatible with PEP8 "
            "recommendations and will be replaced by eval_path() in rdflib 7.0.0."
        )
    )
    return eval_path(graph, t)


def eval_path(
    graph: Graph,
    t: tuple[
        Optional[_SubjectType],
        Union[None, Path, _PredicateType],
        Optional[_ObjectType],
    ],
) -> Iterator[tuple[_SubjectType, _ObjectType]]:
    return ((s, o) for s, p, o in graph.triples(t))


def mul_path(p: Union[URIRef, Path], mul: _MulPathMod) -> MulPath:
    """
    cardinality path
    """
    return MulPath(p, mul)


def inv_path(p: Union[URIRef, Path]) -> InvPath:
    """
    inverse path
    """
    return InvPath(p)


def neg_path(p: Union[URIRef, AlternativePath, InvPath]) -> NegatedPath:
    """
    negated path
    """
    return NegatedPath(p)


# ---------------------------------------------------------------------------
# Witness paths
# ---------------------------------------------------------------------------
#
# In addition to the endpoint pairs produced by :func:`eval_path`, a caller
# may want to know *how* two nodes are connected: the actual triples that
# make up the match, together with the direction in which each one was
# traversed.  ``eval_path_witness`` returns one shortest witness per distinct
# endpoint pair.
#
# The implementation compiles the path expression into a Thompson-style
# non-deterministic finite automaton (NFA) and runs a breadth-first search of
# *configurations* ``(graph_node, nfa_state)``.  BFS guarantees that the first
# time an accepting configuration is reached, it is reached with the fewest
# edges, while the finite number of NFA states guarantees termination even in
# the presence of graph cycles and nested ``?``/``*``/``+`` subexpressions
# (the NFA only contains epsilon cycles, which are handled with a closure
# fixpoint rather than recursion).

FORWARD = False
"""Traversal direction marker: the triple was followed subject -> object."""

INVERSE = True
"""Traversal direction marker: the triple was followed object -> subject."""


class PathLimitExceeded(Exception):
    """Raised when a witness search expands more states than the caller allowed.

    This is distinct from an empty result: reaching the limit means the answer
    is *unknown*, not that there is no match.
    """


class WitnessStep(NamedTuple):
    """A single edge of a witness path.

    Attributes:
        triple: The concrete triple in the graph that provides the edge.
        reversed: :data:`FORWARD` (``False``) if the triple was traversed
            subject to object, :data:`INVERSE` (``True``) if it was traversed
            object to subject.
    """

    triple: _TripleType
    reversed: bool


class PathWitness:
    """A shortest witness connecting two nodes through a property path.

    A zero-length match (possible with ``?`` and ``*`` paths) is represented by
    an empty sequence of steps; no triple is invented for it.

    Args:
        subject: The node at which the witness starts.
        object: The node at which the witness ends.
        steps: The ordered edges making up the witness.
    """

    __slots__ = ("subject", "object", "steps")

    def __init__(
        self,
        subject: _SubjectType,
        object: _ObjectType,
        steps: Sequence[WitnessStep],
    ):
        self.subject = subject
        self.object = object
        self.steps = tuple(steps)

    @property
    def triples(self) -> tuple[_TripleType, ...]:
        """The concrete triples used by the witness, in traversal order."""
        return tuple(step.triple for step in self.steps)

    @property
    def directions(self) -> tuple[bool, ...]:
        """The traversed direction of each triple (:data:`FORWARD`/`INVERSE`)."""
        return tuple(step.reversed for step in self.steps)

    @property
    def nodes(self) -> tuple[Node, ...]:
        """All nodes visited, in order (a one-element tuple for a zero match)."""
        chain: list[Node] = [self.subject]
        current: Node = self.subject
        for step in self.steps:
            triple_s, _triple_p, triple_o = step.triple
            current = triple_s if step.reversed else triple_o
            chain.append(current)
        return tuple(chain)

    def __len__(self) -> int:
        return len(self.steps)

    def __iter__(self) -> Iterator[WitnessStep]:
        return iter(self.steps)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PathWitness):
            return NotImplemented
        return (
            self.subject == other.subject
            and self.object == other.object
            and self.steps == other.steps
        )

    def __hash__(self) -> int:
        return hash((self.subject, self.object, self.steps))

    def __repr__(self) -> str:
        return (
            f"PathWitness({self.subject!r}, {self.object!r}, "
            f"{list(self.steps)!r})"
        )


class _Atom(NamedTuple):
    """A consuming NFA transition, backed by one or more concrete triples."""

    predicate: Optional[URIRef]
    inverted: bool
    negated_args: tuple[Union[URIRef, InvPath], ...]

    @property
    def negated(self) -> bool:
        return bool(self.negated_args)


class _Frag:
    """A fragment of the Thompson NFA under construction."""

    __slots__ = ("start", "end", "edges")

    def __init__(
        self,
        start: int,
        end: int,
        edges: dict[int, list[tuple[int, Optional[_Atom]]]],
    ):
        self.start = start
        self.end = end
        self.edges = edges


def _compile_path(path: Union[Path, URIRef]) -> _Frag:
    """Compile ``path`` into an NFA."""
    counter = 0

    def new_state() -> int:
        nonlocal counter
        state = counter
        counter += 1
        return state

    def atom_frag(atom: _Atom) -> _Frag:
        source = new_state()
        target = new_state()
        return _Frag(source, target, {source: [(target, atom)], target: []})

    def merge(frag: _Frag, other: _Frag) -> None:
        for state, outs in other.edges.items():
            frag.edges[state] = outs

    def link(frag: _Frag, source: int, target: int) -> None:
        frag.edges.setdefault(source, []).append((target, None))
        frag.edges.setdefault(target, [])

    def compile(node: Union[Path, URIRef]) -> _Frag:
        if isinstance(node, URIRef):
            return atom_frag(_Atom(node, False, ()))

        if isinstance(node, InvPath):
            return _reverse_frag(compile(node.arg))

        if isinstance(node, SequencePath):
            frag = compile(node.args[0])
            for arg in node.args[1:]:
                nxt = compile(arg)
                link(frag, frag.end, nxt.start)
                merge(frag, nxt)
                frag.end = nxt.end
            return frag

        if isinstance(node, AlternativePath):
            start = new_state()
            end = new_state()
            frag = _Frag(start, end, {start: [], end: []})
            for arg in node.args:
                sub = compile(arg)
                link(frag, start, sub.start)
                merge(frag, sub)
                link(frag, sub.end, end)
            return frag

        if isinstance(node, MulPath):
            body = compile(node.path)
            start = new_state()
            end = new_state()
            frag = _Frag(start, end, body.edges)
            frag.edges.setdefault(start, [])
            frag.edges.setdefault(end, [])
            link(frag, start, body.start)
            if node.zero:
                link(frag, start, end)
            if node.more:
                link(frag, body.end, body.start)
            link(frag, body.end, end)
            return frag

        if isinstance(node, NegatedPath):
            return atom_frag(_Atom(None, False, tuple(node.args)))

        raise TypeError(f"Unsupported property path expression: {node!r}")

    return compile(path)


def _reverse_frag(frag: _Frag) -> _Frag:
    """Return the NFA fragment matching the inverse language of ``frag``."""
    inverted: dict[int, list[tuple[int, Optional[_Atom]]]] = {
        state: [] for state in frag.edges
    }
    for source, outs in frag.edges.items():
        for target, atom in outs:
            flipped: Optional[_Atom]
            if atom is None:
                flipped = None
            else:
                flipped = _Atom(
                    atom.predicate, not atom.inverted, atom.negated_args
                )
            inverted.setdefault(target, []).append((source, flipped))
    return _Frag(frag.end, frag.start, inverted)


def _expand_atom(
    graph: Graph, node: Node, atom: _Atom
) -> Iterator[tuple[Node, _TripleType, bool]]:
    """Yield ``(next_node, triple, reversed)`` for one consuming transition."""
    follow_inverse = atom.inverted
    if atom.negated:
        if not follow_inverse:
            for triple in graph.triples((node, None, None)):
                s, p, o = triple
                if _negated_forward_matches(
                    p, s, o, graph, atom.negated_args
                ):
                    continue
                yield o, triple, FORWARD
        else:
            for triple in graph.triples((None, None, node)):
                s, p, o = triple
                if _negated_inverse_matches(p, s, o, graph, atom.negated_args):
                    continue
                yield s, triple, INVERSE
    elif not follow_inverse:
        for triple in graph.triples((node, atom.predicate, None)):
            s, _p, o = triple
            yield o, triple, FORWARD
    else:
        for triple in graph.triples((None, atom.predicate, node)):
            s, _p, o = triple
            yield s, triple, INVERSE


def _negated_forward_matches(
    predicate: Node,
    s: Node,
    o: Node,
    graph: Graph,
    args: tuple[Union[URIRef, InvPath], ...],
) -> bool:
    """Whether triple (s, predicate, o) is excluded by a negated set (forward)."""
    for arg in args:
        if isinstance(arg, URIRef):
            if predicate == arg:
                return True
        else:
            # negated inverse property: excluded when the plain triple
            # (o, arg.arg, s) exists, mirroring NegatedPath.eval
            if (o, arg.arg, s) in graph:
                return True
    return False


def _negated_inverse_matches(
    predicate: Node,
    s: Node,
    o: Node,
    graph: Graph,
    args: tuple[Union[URIRef, InvPath], ...],
) -> bool:
    """Whether (s, predicate, o) is excluded when traversing the set inversely."""
    for arg in args:
        if isinstance(arg, URIRef):
            if predicate == arg:
                return True
        else:
            if (s, arg.arg, o) in graph:
                return True
    return False


_Parent = tuple[Node, int, Optional[WitnessStep]]


def _search_from(
    graph: Graph,
    nfa: _Frag,
    start: Node,
    target: Optional[Node],
    walk_reverse: bool,
    used: list[int],
    max_states: Optional[int],
) -> Iterator[tuple[Node, Node, PathWitness]]:
    """BFS over (graph node, NFA state) configurations from one start node."""

    def charge() -> None:
        if max_states is not None and used[0] > max_states:
            raise PathLimitExceeded(
                f"witness search expanded more than {max_states} states"
            )

    start_key = (start, nfa.start)
    # First discovered parent wins, which preserves BFS shortest-distance
    # information for witness reconstruction.
    parent_of: dict[tuple[Node, int], _Parent] = {}

    def closure(
        layer: dict[tuple[Node, int], _Parent],
    ) -> dict[tuple[Node, int], _Parent]:
        """Return ``layer`` extended over all reachable epsilon transitions."""
        work = list(layer.keys())
        while work:
            node, state = work.pop()
            for nxt_state, atom in nfa.edges.get(state, ()):
                if atom is not None:
                    continue
                key = (node, nxt_state)
                if key in layer:
                    continue
                # Keep the epsilon chain reachable for reconstruction, without
                # adding a (non-existent) edge to the witness.
                epsilon_parent = (node, state, None)
                layer[key] = epsilon_parent
                work.append(key)
        for key, parent in layer.items():
            parent_of.setdefault(key, parent)
        return layer

    def consume(
        layer: dict[tuple[Node, int], _Parent],
    ) -> dict[tuple[Node, int], _Parent]:
        """Consume one graph edge from every configuration in ``layer``."""
        nxt_layer: dict[tuple[Node, int], _Parent] = {}
        for key in layer:
            node, state = key
            for nxt_state, atom in nfa.edges.get(state, ()):
                if atom is None:
                    continue
                for nxt_node, triple, reversed_dir in _expand_atom(
                    graph, node, atom
                ):
                    nxt_key = (nxt_node, nxt_state)
                    if nxt_key in parent_of or nxt_key in nxt_layer:
                        continue
                    nxt_layer[nxt_key] = (
                        node,
                        state,
                        WitnessStep(triple, reversed_dir),
                    )
                    used[0] += 1
                    parent_of[nxt_key] = nxt_layer[nxt_key]
        return nxt_layer

    # The root is its own sentinel parent.  Its closure forms depth zero.
    root: dict[tuple[Node, int], _Parent] = {
        start_key: (start, nfa.start, None),
    }
    layer = closure(root)
    charge()

    emitted: set[Node] = set()
    while layer:
        for key in sorted(layer, key=lambda key: (str(key[0]), key[1])):
            node, state = key
            if state == nfa.end and (target is None or node == target):
                if node not in emitted:
                    emitted.add(node)
                    witness = _reconstruct(
                        start,
                        node,
                        key,
                        parent_of,
                        walk_reverse,
                    )
                    if walk_reverse:
                        yield node, start, witness
                    else:
                        yield start, node, witness
        layer = closure(consume(layer))
        charge()


def _reconstruct(
    bfs_start: Node,
    bfs_end: Node,
    accept_key: tuple[Node, int],
    parent_of: dict[tuple[Node, int], _Parent],
    walk_reverse: bool,
) -> PathWitness:
    steps: list[WitnessStep] = []
    node, state = accept_key
    root_state = _nfa_start_state(parent_of, bfs_start)
    while not (node == bfs_start and state == root_state):
        parent_node, parent_state, step = parent_of[(node, state)]
        if step is not None:
            steps.append(step)
        node, state = parent_node, parent_state
    if walk_reverse:
        # BFS ran from the original object towards the original subject;
        # the witness is presented subject -> object.
        subject, obj = bfs_end, bfs_start
        # ``steps`` is collected from the accepting configuration back towards
        # the BFS root, so it is already in reverse traversal order.  Negate
        # every direction (edges are read subject -> object in the output) and
        # keep the concrete triples as stored.
        flipped = [
            WitnessStep(triple, not reversed_dir)
            for triple, reversed_dir in steps
        ]
        return PathWitness(subject, obj, flipped)
    steps.reverse()
    return PathWitness(bfs_start, bfs_end, steps)


def _nfa_start_state(
    parent_of: dict[tuple[Node, int], _Parent], start: Node
) -> int:
    """Find the root configuration state (its parent points at itself)."""
    for (node, state), (parent_node, parent_state, _step) in parent_of.items():
        if node == start and parent_node == start and parent_state == state:
            return state
    raise RuntimeError("internal error: NFA root configuration missing")


def eval_path_witness(
    graph: Graph,
    t: tuple[
        Optional[_SubjectType],
        Union[Path, _PredicateType],
        Optional[_ObjectType],
    ],
    max_states: Optional[int] = None,
) -> Iterator[tuple[_SubjectType, _ObjectType, PathWitness]]:
    """Yield endpoint pairs for ``t`` together with a shortest witness.

    Each element is a ``(subject, object, witness)`` triple where ``witness``
    is a :class:`PathWitness` holding the concrete graph triples and the
    direction in which they were traversed.  At most one witness is returned
    per distinct endpoint pair, and BFS guarantees it uses the fewest edges.

    A zero-length match yields an empty witness rather than a fabricated edge.

    Args:
        graph: The graph to search.
        t: ``(subject, path, object)`` where any of ``subject``/``object`` may
            be ``None`` and ``path`` is a URIRef or property path expression.
        max_states: Optional cap on the number of distinct
            ``(node, automaton_state)`` configurations reached by consuming a
            graph edge while searching from a start node (the start itself and
            zero-length epsilon moves are not counted). Exceeding it raises
            :class:`PathLimitExceeded`, which must not be confused with "no
            match". ``None`` means unlimited; ``0`` permits only zero-length
            matches.
    """
    subj, path, obj = t
    if path is None or not isinstance(path, (Path, URIRef)):
        raise TypeError(
            "eval_path_witness() requires a URIRef or Path as predicate"
        )

    if subj is not None and obj is not None:
        nfa = _compile_path(path)
        for s, o, witness in _search_from(
            graph, nfa, subj, obj, False, [0], max_states
        ):
            yield s, o, witness
        return

    if subj is not None:
        nfa = _compile_path(path)
        yielded: set[tuple[Node, Node]] = set()
        for s, o, witness in _search_from(
            graph, nfa, subj, None, False, [0], max_states
        ):
            if (s, o) not in yielded:
                yielded.add((s, o))
                yield s, o, witness
        return

    if obj is not None:
        # s P o  <=>  o ^P s; search from the bound object using the reversed
        # automaton and present the witness back in subject -> object order.
        nfa = _reverse_frag(_compile_path(path))
        yielded = set()
        for s, o, witness in _search_from(
            graph, nfa, obj, None, True, [0], max_states
        ):
            if (s, o) not in yielded:
                yielded.add((s, o))
                yield s, o, witness
        return

    # Neither endpoint bound: search from every node appearing in the graph.
    nfa = _compile_path(path)
    yielded = set()
    starts: list[Node] = []
    seen_nodes: set[Node] = set()
    for s, _p, o in graph.triples((None, None, None)):
        if s not in seen_nodes:
            seen_nodes.add(s)
            starts.append(s)
        if o not in seen_nodes:
            seen_nodes.add(o)
            starts.append(o)
    shared_used = [0]
    for start in starts:
        for s, o, witness in _search_from(
            graph, nfa, start, None, False, shared_used, max_states
        ):
            if (s, o) not in yielded:
                yielded.add((s, o))
                yield s, o, witness


if __name__ == "__main__":
    pass
else:
    # monkey patch
    # (these cannot be directly in terms.py
    #  as it would introduce circular imports)

    URIRef.__or__ = path_alternative  # type: ignore[method-assign, assignment]
    # ignore typing here as URIRef inherits from str,
    # which has an incompatible definition of __mul__.
    URIRef.__mul__ = mul_path  # type: ignore
    URIRef.__invert__ = inv_path
    URIRef.__neg__ = neg_path
    URIRef.__truediv__ = path_sequence

    Path.__invert__ = inv_path
    # type error: Incompatible types in assignment (expression has type "Callable[[Union[URIRef, AlternativePath, InvPath]], NegatedPath]", variable has type "Callable[[Path], NegatedPath]")
    Path.__neg__ = neg_path  # type: ignore[assignment]
    # type error: Incompatible types in assignment (expression has type "Callable[[Union[URIRef, Path], Literal['*', '+', '?']], MulPath]", variable has type "Callable[[Path, str], MulPath]")
    Path.__mul__ = mul_path  # type: ignore[assignment]
    Path.__or__ = path_alternative  # type: ignore[method-assign, assignment]
    Path.__truediv__ = path_sequence
