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

Explaining *how* two nodes are connected:

:func:`eval_path_witness` works like :func:`eval_path`, but every solution
also carries the witness: the actual triples of one shortest matching path,
in traversal order. Each step records the stored triple and the direction it
was walked in (`reversed=True` for an inverse edge).

```python
>>> for s, o, w in eval_path_witness(g, (e.a, e.p1 / e.p3, None)):
...     print(s, o)
...     for step in w:
...         print("   ", step.triple[1], "reversed" if step.reversed else "", sep="")
ex:a ex:g
   ex:p1
   ex:p3

```

A zero-length match (``p*``/``p?`` from a node to itself) is witnessed by an
empty tuple, never by a fabricated edge:

```python
>>> list(eval_path_witness(g, (e.a, e.p1*ZeroOrMore, e.a)))
[(rdflib.term.URIRef('ex:a'), rdflib.term.URIRef('ex:a'), ())]

```
"""

from __future__ import annotations

import warnings
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Callable, Generator, Iterator
from functools import total_ordering
from typing import (
    TYPE_CHECKING,
    Any,
    NamedTuple,
    Optional,
    Union,
)

from rdflib.term import Node, URIRef

if TYPE_CHECKING:
    from rdflib._type_checking import _MulPathMod
    from rdflib.graph import Graph, _ObjectType, _PredicateType, _SubjectType
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

    def eval_witness(
        self,
        graph: Graph,
        subj: Optional[_SubjectType] = None,
        obj: Optional[_ObjectType] = None,
        max_states: Optional[int] = None,
    ) -> Iterator[tuple[_SubjectType, _ObjectType, PathWitness]]:
        """
        Like :func:`eval_path`, but each solution also carries a *witness*:
        the actual triples of a shortest matching path, in traversal order.

        See :func:`eval_path_witness` for details.
        """
        yield from eval_path_witness(
            graph, (subj, self, obj), max_states=max_states
        )

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


# witness paths


class WitnessStep(NamedTuple):
    """
    A single step of a witness path.

    ``triple`` is an actual triple of the graph, always stored in its stored
    ``(subject, predicate, object)`` orientation.

    ``reversed`` records the direction the triple was traversed in: ``False``
    for subject -> object, ``True`` for object -> subject.
    """

    triple: tuple[_SubjectType, _PredicateType, _ObjectType]
    reversed: bool


# A witness is the ordered sequence of traversed triples. A zero-length
# match is represented by an empty tuple, never by a fabricated edge.
PathWitness = tuple[WitnessStep, ...]


class PathWitnessLimitExceeded(Exception):
    """
    Raised by :func:`eval_path_witness` when the caller supplied
    ``max_states`` and the search had to expand more states than allowed.

    This is deliberately distinct from an empty result: exceeding the limit
    means that matches may exist but could not be certified, whereas an empty
    result means that no matches exist within the given budget.
    """


class _EdgeLabel:
    """
    A labelled (one-triple) transition of the path automaton.

    ``forward`` records how the label sits relative to the automaton's
    traversal direction: ``True`` means the matched triple is traversed from
    subject to object, ``False`` from object to subject.
    """

    __slots__ = ("predicate", "forward", "excluded")

    def __init__(
        self,
        predicate: Optional[URIRef],
        forward: bool,
        excluded: tuple[tuple[bool, URIRef], ...],
    ):
        # ``None`` matches every predicate (used by negated property sets);
        # otherwise only triples with this predicate match.
        self.predicate = predicate
        self.forward = forward
        # Each excluded item is ``(excluded_forward, predicate)``, i.e. the
        # excluded triple traverses ``predicate`` forward when True, as an
        # inverse edge when False.
        self.excluded = excluded


def _normalize_path(
    path: Union[Path, URIRef, None], inverse: bool = False
) -> Union[Path, URIRef, None]:
    """
    Push every :class:`InvPath` node down towards the leaves.

    After normalization, an :class:`InvPath` only ever wraps a plain
    :class:`URIRef` (denoting an inverse edge) or the excluded members of a
    :class:`NegatedPath`. The standard inverse-path identities are applied:

    - ``^^p = p``;
    - ``^(a/b) = ^b/^a`` (the sequence order is reversed);
    - ``^(a|b) = ^a|^b``;
    - ``^(p?) = (^p)?``, ``^(p*) = (^p)*``, ``^(p+) = (^p)+``;
    - ``^(!(p|^q))`` swaps forward and inverse excluded members.
    """
    if path is None or isinstance(path, URIRef):
        return InvPath(path) if inverse else path

    if isinstance(path, InvPath):
        return _normalize_path(path.arg, not inverse)

    if isinstance(path, SequencePath):
        args = path.args if not inverse else list(reversed(path.args))
        return SequencePath(
            *[_normalize_path(arg, inverse) for arg in args]
        )

    if isinstance(path, AlternativePath):
        return AlternativePath(
            *[_normalize_path(arg, inverse) for arg in path.args]
        )

    if isinstance(path, MulPath):
        return MulPath(  # type: ignore[arg-type]
            _normalize_path(path.path, inverse), path.mod
        )

    if isinstance(path, NegatedPath):
        # The excluded property-set members keep their own direction under an
        # outer inverse: ``^(!p)`` still excludes edges whose predicate is p.
        # Only the traversal direction of the negated edge itself flips.
        normalized_args: list[Union[URIRef, Path]] = []
        for arg in path.args:
            if isinstance(arg, (URIRef, InvPath)):
                normalized_args.append(arg)
            else:  # pragma: no cover
                raise Exception("Invalid path in NegatedPath: %s" % arg)
        negated = _NormalizedNegatedPath(normalized_args, inverse)
        return negated

    raise TypeError("Unsupported property path: %r" % (path,))


class _NormalizedNegatedPath:
    """
    Internal marker for a negated property set after inverse normalization.

    ``inverse`` records whether the matching edge itself is traversed from
    object to subject (as in ``^(!p)``). The excluded members keep their own
    forward/inverse markers, independent of this traversal direction.
    """

    __slots__ = ("args", "inverse")

    def __init__(
        self, args: list[Union[URIRef, Path]], inverse: bool
    ) -> None:
        self.args = args
        self.inverse = inverse


class _NFA:
    """
    A non-deterministic finite automaton with epsilon transitions.

    States are small integers. Every labelled transition consumes exactly
    one triple of the queried graph.
    """

    __slots__ = ("start", "accept", "labelled", "epsilon")

    def __init__(
        self,
        start: int,
        accept: int,
        labelled: dict[int, list[tuple[_EdgeLabel, int]]],
        epsilon: list[tuple[int, int]],
    ):
        self.start = start
        self.accept = accept
        self.labelled = labelled
        self.epsilon = epsilon


def _compile_nfa(
    path: Union[Path, URIRef, None],
    next_state: list[int],
) -> tuple[int, int]:
    """
    Compile ``path`` into the shared automaton, returning ``(start, accept)``.

    ``next_state`` is a one-element list used to allocate fresh states.
    Inverse paths are pushed down to the edge labels before compilation
    (``^(a/b) = ^b/^a``), so a compiled automaton only contains labels that
    walk triples in a single, recorded direction.
    """
    labelled: dict[int, list[tuple[_EdgeLabel, int]]] = {}
    epsilon: list[tuple[int, int]] = []

    def state() -> int:
        s = next_state[0]
        next_state[0] += 1
        labelled.setdefault(s, [])
        return s

    def compile(sub_path: Union[Path, URIRef, None]) -> tuple[int, int]:
        if sub_path is None or isinstance(sub_path, URIRef):
            start = state()
            accept = state()
            labelled[start].append((_EdgeLabel(sub_path, True, ()), accept))
            return start, accept

        if isinstance(sub_path, InvPath):
            # Normalization guarantees InvPath wraps a plain predicate here.
            assert isinstance(sub_path.arg, (URIRef, type(None)))
            start = state()
            accept = state()
            labelled[start].append((_EdgeLabel(sub_path.arg, False, ()), accept))
            return start, accept

        if isinstance(sub_path, (NegatedPath, _NormalizedNegatedPath)):
            excluded: list[tuple[bool, URIRef]] = []
            traverses_forward = True
            if isinstance(sub_path, _NormalizedNegatedPath):
                traverses_forward = not sub_path.inverse
            for a in sub_path.args:
                if isinstance(a, URIRef):
                    excluded.append((True, a))
                elif isinstance(a, InvPath):
                    excluded.append((False, a.arg))  # type: ignore[arg-type]
                else:  # pragma: no cover
                    raise Exception("Invalid path in NegatedPath: %s" % a)
            start = state()
            accept = state()
            labelled[start].append(
                (_EdgeLabel(None, traverses_forward, tuple(excluded)), accept)
            )
            return start, accept

        if isinstance(sub_path, SequencePath):
            start = state()
            prev = start
            for arg in sub_path.args:
                arg_start, arg_accept = compile(arg)
                epsilon.append((prev, arg_start))
                prev = arg_accept
            accept = state()
            epsilon.append((prev, accept))
            return start, accept

        if isinstance(sub_path, AlternativePath):
            start = state()
            accept = state()
            for arg in sub_path.args:
                arg_start, arg_accept = compile(arg)
                epsilon.append((start, arg_start))
                epsilon.append((arg_accept, accept))
            return start, accept

        if isinstance(sub_path, MulPath):
            inner_start, inner_accept = compile(sub_path.path)
            start = state()
            accept = state()
            if sub_path.zero:
                epsilon.append((start, accept))
            epsilon.append((start, inner_start))
            if sub_path.more:
                epsilon.append((inner_accept, inner_start))
            epsilon.append((inner_accept, accept))
            return start, accept

        raise TypeError("Unsupported property path: %r" % (sub_path,))

    start, accept = compile(_normalize_path(path))
    return _NFA(start, accept, labelled, epsilon)


def _build_nfa(path: Union[Path, URIRef, None]) -> _NFA:
    return _compile_nfa(path, [0])


def _reverse_nfa(nfa: _NFA) -> _NFA:
    """
    Return the automaton that accepts the reversed language: edges point
    the other way and start/accept are swapped. Labels keep their witness
    orientation; the caller walks triples in the opposite graph direction.
    """
    reversed_labelled: dict[int, list[tuple[_EdgeLabel, int]]] = {}
    for source, edges in nfa.labelled.items():
        for label, target in edges:
            reversed_labelled.setdefault(target, []).append((label, source))
    reversed_epsilon = [(b, a) for a, b in nfa.epsilon]
    return _NFA(
        nfa.accept, nfa.start, reversed_labelled, reversed_epsilon
    )


def _epsilon_closures(nfa: _NFA) -> list[frozenset[int]]:
    states = set(nfa.labelled)
    states.add(nfa.start)
    states.add(nfa.accept)
    for a, b in nfa.epsilon:
        states.add(a)
        states.add(b)
    size = max(states) + 1
    edges: dict[int, list[int]] = {i: [] for i in range(size)}
    for a, b in nfa.epsilon:
        edges.setdefault(a, []).append(b)
        edges.setdefault(b, [])
    closures: list[frozenset[int]] = []
    for source in range(size):
        seen = {source}
        pending = deque([source])
        while pending:
            s = pending.popleft()
            for nxt in edges.get(s, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    pending.append(nxt)
        closures.append(frozenset(seen))
    return closures


def _label_steps(
    graph: Graph, label: _EdgeLabel, node: Node, reverse_search: bool
) -> Iterator[tuple[Node, WitnessStep]]:
    """
    Yield triples that match ``label`` when leaving ``node`` along the
    automaton's traversal direction, as ``(next_node, step)``.
    """
    # A forward label is walked subject -> object in a forward search and
    # object -> subject in a reversed search; the inverse holds for inverse
    # labels. The witness direction depends only on the label.
    enumerate_by_subject = label.forward != reverse_search
    witness_reversed = not label.forward

    if enumerate_by_subject:
        triples: Iterator[tuple[_SubjectType, _PredicateType, _ObjectType]] = (
            graph.triples((node, label.predicate, None))
        )
        for s, p, o in triples:
            if _is_excluded(graph, label, s, p, o):
                continue
            yield o, WitnessStep((s, p, o), witness_reversed)
    else:
        triples = graph.triples((None, label.predicate, node))
        for s, p, o in triples:
            if _is_excluded(graph, label, s, p, o):
                continue
            yield s, WitnessStep((s, p, o), witness_reversed)


def _is_excluded(
    graph: Graph,
    label: _EdgeLabel,
    s: _SubjectType,
    p: _PredicateType,
    o: _ObjectType,
) -> bool:
    # Negated property set membership per SPARQL 1.1. Membership is an
    # intrinsic property of the candidate stored triple ``(s, p, o)``, so
    # the check does not depend on whether that triple is walked forwards or
    # backwards:
    #
    # - a forward excluded member ``p`` matches when the predicate is p;
    # - an inverse excluded member ``^p`` matches when the reverse triple
    #   ``(o, p, s)`` is present in the graph.
    for excluded_forward, predicate in label.excluded:
        if excluded_forward:
            if p == predicate:
                return True
        else:
            if (o, predicate, s) in graph:
                return True
    return False


def _shortest_witness(
    graph: Graph,
    nfa: _NFA,
    closures: list[frozenset[int]],
    root: Node,
    reverse_search: bool,
    target: Optional[Node],
    budget: list[Optional[int]],
) -> Iterator[tuple[Node, PathWitness]]:
    """
    BFS over ``(graph_node, nfa_state)`` configurations from ``root``.

    Yields ``(endpoint, witness)`` for every endpoint reachable by an
    accepting configuration, at most one witness per endpoint. The search
    expands configurations layer by layer in the number of consumed triples
    (epsilon moves stay within a layer), so accepting configurations found in
    an earlier layer certify witnesses with fewer edges; the first witness
    found for an endpoint is therefore a shortest one.

    When ``target`` is given, the search stops as soon as that endpoint has
    been accepted and at most one solution is produced.
    """
    initial = closures[nfa.start]
    # parent[config] = (previous_config, step used to reach it)
    parent: dict[tuple[Node, int], tuple[tuple[Node, int], WitnessStep]] = {}
    visited: set[tuple[Node, int]] = {(root, state) for state in initial}
    current: list[tuple[Node, int]] = [(root, state) for state in initial]

    def reconstruct(end_config: tuple[Node, int]) -> PathWitness:
        steps: list[WitnessStep] = []
        config = end_config
        while config in parent:
            prev, step = parent[config]
            steps.append(step)
            config = prev
        if not reverse_search:
            # In a forward search parent links run from the accepting
            # configuration back towards the root, hence the reversal. In a
            # reversed search the automaton itself is reversed, so the
            # accepting configuration is at the query root and links point
            # towards the original path's start: the collected order is
            # already the forward witness order.
            steps.reverse()
        return tuple(steps)

    accepted: set[Node] = set()
    while current:
        next_layer: list[tuple[Node, int]] = []
        # Acceptance for this whole layer is decided before any edges are
        # expanded: configurations here all consumed the same number of
        # triples, and the zero-length acceptance of the root must not be
        # blocked by spending the expansion budget on a sibling config.
        for node, state in current:
            if state == nfa.accept and node not in accepted:
                if target is None or node == target:
                    accepted.add(node)
                    yield node, reconstruct((node, state))
                    if target is not None:
                        return
        for node, state in current:
            for label, next_state0 in nfa.labelled.get(state, ()):
                for next_node, step in _label_steps(
                    graph, label, node, reverse_search
                ):
                    for reached in closures[next_state0]:
                        key = (next_node, reached)
                        if key in visited:
                            continue
                        visited.add(key)
                        parent[key] = ((node, state), step)
                        next_layer.append(key)
                        if budget[0] is not None:
                            budget[0] -= 1
                            if budget[0] < 0:
                                raise PathWitnessLimitExceeded(
                                    "Property path witness search exceeded "
                                    "the configured max_states limit; matches "
                                    "may exist but could not be certified."
                                )
        current = next_layer


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


def eval_path_witness(
    graph: Graph,
    t: tuple[
        Optional[_SubjectType],
        Union[None, Path, _PredicateType],
        Optional[_ObjectType],
    ],
    max_states: Optional[int] = None,
) -> Iterator[tuple[_SubjectType, _ObjectType, PathWitness]]:
    """
    Evaluate a property path like :func:`eval_path`, but yield
    ``(subject, object, witness)`` triples, where ``witness`` is the ordered
    sequence of actual graph triples (:class:`WitnessStep`) that certifies
    the connection.

    For every endpoint pair only a single witness is returned, and it is one
    with the minimum number of edges (when several shortest witnesses exist,
    any one may be chosen). The same endpoint pair is never yielded twice.

    A zero-length match (``p*`` or ``p?`` from a node to itself) yields an
    empty witness tuple; no edge is fabricated.

    The search is a breadth-first search over the path automaton, not an
    enumeration of simple paths: cycles and nested repetitions such as
    ``(p+)+`` terminate because visited ``(graph node, automaton state)``
    configurations are not expanded again.

    Args:
        graph: The graph to query.
        t: A ``(subject, path, object)`` pattern; any of the components may
            be ``None``, in which case all possible values are considered.
        max_states: Optional bound on the number of configurations expanded
            during the whole query. When exceeded, a
            :class:`PathWitnessLimitExceeded` is raised; this must not be
            confused with the query simply having no matches.

    Example:

    >>> from rdflib import Graph, Namespace
    >>> g = Graph()
    >>> g.parse(
    ...     data='''
    ...     @prefix : <ex:> .
    ...     :a :p :b .
    ...     :b :q :c .
    ...     :a :r :c .
    ...     ''',
    ...     format="n3",
    ... )  # doctest: +ELLIPSIS
    <Graph identifier=...>

    >>> e = Namespace("ex:")
    >>> for s, o, w in eval_path_witness(g, (e.a, e.p / e.q, None)):
    ...     print(s, o, len(w))
    ex:a ex:c 2

    Zero-length matches carry an empty witness:

    >>> list(eval_path_witness(g, (e.a, e.p * ZeroOrMore, e.a))) == [
    ...     (e.a, e.a, ())
    ... ]
    True
    """
    subj, path, obj = t

    nfa = _build_nfa(path)
    # Only an object-bound, subject-unbound query is run against the
    # reversed automaton, starting from the given object. With both ends
    # unbound we enumerate graph nodes as subjects and walk forward, which is
    # what the endpoint semantics require.
    reverse_search = subj is None and obj is not None
    if reverse_search:
        nfa = _reverse_nfa(nfa)
    closures = _epsilon_closures(nfa)

    if max_states is not None and max_states < 0:
        raise ValueError("max_states must be non-negative")
    budget: list[Optional[int]] = [max_states]

    if subj is not None:
        for endpoint, witness in _shortest_witness(
            graph, nfa, closures, subj, reverse_search, obj, budget
        ):
            yield subj, endpoint, witness
    elif obj is not None:
        for endpoint, witness in _shortest_witness(
            graph, nfa, closures, obj, reverse_search, None, budget
        ):
            yield endpoint, obj, witness
    else:
        # Run one bounded BFS per graph node. Every root accepts a distinct
        # subject, and within a BFS each endpoint is accepted once, so no
        # endpoint pair can be emitted twice.
        for root in sorted(graph.all_nodes(), key=str):
            for endpoint, witness in _shortest_witness(
                graph, nfa, closures, root, reverse_search, None, budget
            ):
                yield root, endpoint, witness


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
