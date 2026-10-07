"""
A small, incremental RDFS reasoner with justification tracking.

This module provides :class:`IncrementalRDFSReasoner`, an in-memory reasoner
that maintains a derived graph on top of an explicit :class:`rdflib.graph.Graph`
without recomputing the whole closure on every change.

Supported rules (a deliberate subset of RDFS, not a complete implementation):

- ``rdfs:subClassOf`` transitivity
- ``rdfs:subPropertyOf`` transitivity
- class inheritance: ``(x rdf:type A)`` + ``(A rdfs:subClassOf B)`` =>
  ``(x rdf:type B)``
- property inheritance: ``(x p y)`` + ``(p rdfs:subPropertyOf q)`` =>
  ``(x q y)``
- ``rdfs:domain``: ``(p rdfs:domain C)`` + ``(x p y)`` => ``(x rdf:type C)``
- ``rdfs:range``: ``(p rdfs:range C)`` + ``(x p y)`` => ``(y rdf:type C)``

Explicit facts and derived facts are managed separately. Every derived fact
carries the set of its *justifications*: each justification is the set of
explicit triples that suffices to derive it. This gives the following
guarantees:

- After ``add``/``retract`` the current conclusions and their justifications
  can be queried (``conclusions()``, ``explain()``).
- A conclusion with several independent justifications survives the retraction
  of any one of them.
- Cyclic inheritance (e.g. ``A subClassOf B`` and ``B subClassOf A``) never
  becomes a self-sustaining support: every justification is grounded in
  explicit triples only, so once those are retracted the cycle-supported
  conclusions disappear.
- A conclusion the user asserted directly is stored as an explicit fact and
  is never removed by cascading retractions.

Only *minimal* justifications are kept: a justification that is a strict
superset of another justification of the same fact is dominated -- it
provides no extra robustness against retractions -- and is discarded. This
keeps the retained justifications exactly the minimal sufficient sets of
explicit premises, and bounds the bookkeeping on dense graphs (the worst
case is still exponential, as with any all-justifications reasoner, so the
reasoner is meant for small to medium graphs).

Retraction is purely subtractive: because justifications reference explicit
triples only, retracting one explicit triple simply invalidates every
justification containing it; facts left without any justification are dropped.
No re-derivation and no full-closure recomputation is ever needed.

For validation, :func:`rdfs_closure` recomputes the closure of a graph from
scratch with the same rule subset, so callers can cross-check the incremental
state against a naive full recomputation at any time.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Iterable, Iterator, List, Optional, Set, Tuple

from rdflib.graph import Graph
from rdflib.namespace import RDF, RDFS
from rdflib.term import Node

__all__ = ["IncrementalRDFSReasoner", "rdfs_closure"]

Triple = Tuple[Node, Node, Node]
Support = FrozenSet[Triple]

_TYPE = RDF.type
_SUBCLASSOF = RDFS.subClassOf
_SUBPROPERTYOF = RDFS.subPropertyOf
_DOMAIN = RDFS.domain
_RANGE = RDFS.range


class IncrementalRDFSReasoner:
    """Maintains RDFS derivations over an explicit graph, incrementally.

    The explicit facts live in ``self.graph`` (a plain in-memory
    :class:`rdflib.graph.Graph`). Derived facts are kept in separate internal
    indexes together with their justifications, so the two kinds of facts are
    never mixed up.
    """

    def __init__(self, graph: Optional[Graph] = None) -> None:
        #: The graph of explicitly asserted facts.
        self.graph: Graph = graph if graph is not None else Graph()
        self._explicit: Set[Triple] = set()
        # fact -> set of justifications; each justification is a frozenset of
        # explicit triples that derives the fact.
        self._supports: Dict[Triple, Set[Support]] = {}
        # Indexes over all known facts (explicit and derived).
        self._by_sp: Dict[Tuple[Node, Node], Set[Node]] = {}
        self._by_po: Dict[Tuple[Node, Node], Set[Node]] = {}
        self._by_p: Dict[Node, Set[Tuple[Node, Node]]] = {}
        # Pending (fact, justification) pairs that still need propagation.
        self._worklist: List[Tuple[Triple, Support]] = []
        for triple in self.graph:
            self.add(triple)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def add(self, triple: Triple) -> None:
        """Assert an explicit fact and propagate its consequences."""
        s, p, o = triple
        if triple in self._explicit:
            return
        self._explicit.add(triple)
        self.graph.add((s, p, o))
        self._add_support(triple, frozenset({triple}))
        self._propagate()

    def retract(self, triple: Triple) -> None:
        """Withdraw an explicit assertion.

        Every justification that mentions the triple is invalidated. Facts
        that lose their last justification stop being entailed, unless the
        user asserted them directly as well. A fact that remains derivable
        from other explicit premises stays entailed as a derived fact.
        """
        if triple not in self._explicit:
            return
        self._explicit.discard(triple)
        self.graph.remove(triple)
        doomed: List[Triple] = []
        for fact, supports in self._supports.items():
            invalid = [support for support in supports if triple in support]
            supports.difference_update(invalid)
            if not supports:
                doomed.append(fact)
        for fact in doomed:
            self._remove_fact(fact)

    def __contains__(self, triple: Triple) -> bool:
        return triple in self._supports

    def is_entailed(self, triple: Triple) -> bool:
        """Whether the triple currently holds, explicitly or by derivation."""
        return triple in self._supports

    def is_explicit(self, triple: Triple) -> bool:
        """Whether the triple was directly asserted by the user."""
        return triple in self._explicit

    def conclusions(self) -> Iterator[Triple]:
        """Iterate over all currently entailed triples (explicit + derived)."""
        return iter(self._supports)

    def derived(self) -> Iterator[Triple]:
        """Iterate over entailed triples that were never asserted explicitly."""
        return (fact for fact in self._supports if fact not in self._explicit)

    def explain(self, triple: Triple) -> Tuple[Support, ...]:
        """Return the current justifications of a triple.

        Each justification is a minimal frozenset of explicit triples that
        suffices to derive the triple. An explicitly asserted triple always
        has the singleton justification ``{triple}``, plus any derived ones.
        An empty tuple means the triple is not currently entailed.
        """
        return tuple(self._supports.get(triple, ()))

    def closure_graph(self) -> Graph:
        """Materialize all current conclusions (explicit + derived)."""
        graph = Graph()
        for triple in self._supports:
            graph.add(triple)
        return graph

    # ------------------------------------------------------------------
    # internal machinery
    # ------------------------------------------------------------------

    def _add_support(self, fact: Triple, support: Support) -> bool:
        supports = self._supports.get(fact)
        if supports is None:
            supports = self._supports[fact] = set()
            self._index_add(fact)
        for existing in supports:
            if existing <= support:
                # Dominated by (or equal to) an existing justification:
                # anything that invalidates ``existing`` also invalidates
                # ``support``, so keeping it would add no robustness.
                return False
        # The new justification is strictly smaller than some existing
        # ones; those are now dominated and can be dropped. Consequences
        # already propagated through them will in turn be dominated by the
        # consequences propagated through ``support``.
        supports.difference_update({e for e in supports if support < e})
        supports.add(support)
        self._worklist.append((fact, support))
        return True

    def _remove_fact(self, fact: Triple) -> None:
        del self._supports[fact]
        self._index_remove(fact)

    def _index_add(self, fact: Triple) -> None:
        s, p, o = fact
        self._by_sp.setdefault((s, p), set()).add(o)
        self._by_po.setdefault((p, o), set()).add(s)
        self._by_p.setdefault(p, set()).add((s, o))

    def _index_remove(self, fact: Triple) -> None:
        s, p, o = fact
        self._by_sp[(s, p)].discard(o)
        self._by_po[(p, o)].discard(s)
        self._by_p[p].discard((s, o))

    def _objects(self, s: Node, p: Node) -> Iterable[Node]:
        return self._by_sp.get((s, p), ())

    def _subjects(self, p: Node, o: Node) -> Iterable[Node]:
        return self._by_po.get((p, o), ())

    def _pairs(self, p: Node) -> Iterable[Tuple[Node, Node]]:
        return self._by_p.get(p, ())

    def _supports_of(self, fact: Triple) -> Iterable[Support]:
        return self._supports.get(fact, ())

    def _propagate(self) -> None:
        """Drain the worklist, firing rules for each new justification.

        Only rule instances involving the new (fact, justification) pair are
        considered; previously derived consequences are never recomputed.
        """
        while self._worklist:
            (s, p, o), support = self._worklist.pop()
            known = self._supports.get((s, p, o))
            if known is None or support not in known:
                # Stale entry: the support was superseded by a smaller one
                # before its turn came up.
                continue

            # Property-driven rules fire for any predicate: the fact
            # (s, p, o) may be an instance of a super-property and may
            # trigger domain/range typing.
            for q in list(self._objects(p, _SUBPROPERTYOF)):
                for other in list(self._supports_of((p, _SUBPROPERTYOF, q))):
                    self._add_support((s, q, o), support | other)
            for c in list(self._objects(p, _DOMAIN)):
                for other in list(self._supports_of((p, _DOMAIN, c))):
                    self._add_support((s, _TYPE, c), support | other)
            for c in list(self._objects(p, _RANGE)):
                for other in list(self._supports_of((p, _RANGE, c))):
                    self._add_support((o, _TYPE, c), support | other)

            if p == _TYPE:
                # (s, rdf:type, o) with (o, subClassOf, c) => (s, type, c)
                for c in list(self._objects(o, _SUBCLASSOF)):
                    for other in list(self._supports_of((o, _SUBCLASSOF, c))):
                        self._add_support((s, _TYPE, c), support | other)
            elif p == _DOMAIN:
                # A new domain declaration (s, domain, o) types the subjects
                # of all existing assertions that use s.
                for x, y in list(self._pairs(s)):
                    for other in list(self._supports_of((x, s, y))):
                        self._add_support((x, _TYPE, o), support | other)
            elif p == _RANGE:
                # A new range declaration (s, range, o) types the objects
                # of all existing assertions that use s.
                for x, y in list(self._pairs(s)):
                    for other in list(self._supports_of((x, s, y))):
                        self._add_support((y, _TYPE, o), support | other)
            elif p == _SUBCLASSOF:
                # (s, subClassOf, o): transitivity in both directions and
                # class inheritance for instances of s.
                for c in list(self._objects(o, _SUBCLASSOF)):
                    for other in list(self._supports_of((o, _SUBCLASSOF, c))):
                        self._add_support((s, _SUBCLASSOF, c), support | other)
                for a in list(self._subjects(_SUBCLASSOF, s)):
                    for other in list(self._supports_of((a, _SUBCLASSOF, s))):
                        self._add_support((a, _SUBCLASSOF, o), support | other)
                for x in list(self._subjects(_TYPE, s)):
                    for other in list(self._supports_of((x, _TYPE, s))):
                        self._add_support((x, _TYPE, o), support | other)
            elif p == _SUBPROPERTYOF:
                # (s, subPropertyOf, o): transitivity in both directions and
                # property inheritance for assertions using s.
                for r in list(self._objects(o, _SUBPROPERTYOF)):
                    for other in list(self._supports_of((o, _SUBPROPERTYOF, r))):
                        self._add_support((s, _SUBPROPERTYOF, r), support | other)
                for q in list(self._subjects(_SUBPROPERTYOF, s)):
                    for other in list(self._supports_of((q, _SUBPROPERTYOF, s))):
                        self._add_support((q, _SUBPROPERTYOF, o), support | other)
                for x, y in list(self._pairs(s)):
                    for other in list(self._supports_of((x, s, y))):
                        self._add_support((x, o, y), support | other)


def rdfs_closure(facts: Iterable[Triple]) -> Set[Triple]:
    """Compute the closure of *facts* under the same rule subset, from scratch.

    This is the naive, non-incremental reference implementation: it starts
    from the given triples and repeatedly applies the rules until a fixpoint
    is reached. It exists so the incremental reasoner's state can be
    cross-checked against a full recomputation.
    """
    known: Set[Triple] = set(facts)
    changed = True
    while changed:
        changed = False

        def infer(triple: Triple) -> None:
            nonlocal changed
            if triple not in known:
                known.add(triple)
                changed = True

        subclass: Dict[Node, Set[Node]] = {}
        subproperty: Dict[Node, Set[Node]] = {}
        domain: Dict[Node, Set[Node]] = {}
        range_: Dict[Node, Set[Node]] = {}
        for s, p, o in known:
            if p == _SUBCLASSOF:
                subclass.setdefault(s, set()).add(o)
            elif p == _SUBPROPERTYOF:
                subproperty.setdefault(s, set()).add(o)
            elif p == _DOMAIN:
                domain.setdefault(s, set()).add(o)
            elif p == _RANGE:
                range_.setdefault(s, set()).add(o)
        for s, p, o in list(known):
            # General property rules fire for every predicate: the fact may
            # be an instance of a super-property and may trigger
            # domain/range typing.
            for q in subproperty.get(p, ()):
                infer((s, q, o))
            for c in domain.get(p, ()):
                infer((s, _TYPE, c))
            for c in range_.get(p, ()):
                infer((o, _TYPE, c))
            if p == _SUBCLASSOF:
                for c in subclass.get(o, ()):
                    infer((s, _SUBCLASSOF, c))
            elif p == _SUBPROPERTYOF:
                for r in subproperty.get(o, ()):
                    infer((s, _SUBPROPERTYOF, r))
            elif p == _TYPE:
                for c in subclass.get(o, ()):
                    infer((s, _TYPE, c))
    return known
