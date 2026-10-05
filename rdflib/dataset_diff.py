"""
Attribution-aware comparison of RDF datasets.

The set operators on [`rdflib.graph.Graph`][] and
[`rdflib.compare.graph_diff`][] only look at triples, so they cannot
distinguish "the value did not change, but the set of sources asserting it
did".  This module compares two [`rdflib.graph.Dataset`][] snapshots quad by
quad and reports, per triple, which source graphs assert it in each version:

* **added** -- triples only present in the *after* dataset.
* **removed** -- triples only present in the *before* dataset.
* **source changes** -- triples present in both, but asserted by a different
  set of sources.

The default graph is treated as its own source, marked with
[`DEFAULT_GRAPH_SOURCE`][rdflib.dataset_diff.DEFAULT_GRAPH_SOURCE], so it
never gets mixed up with a named graph.

Blank nodes are *not* matched across versions by isomorphism guessing.  A
caller that has validated the correspondence may pass a one-to-one
``bnode_mapping`` from *before* blank nodes to *after* blank nodes; every
unmapped blank node is scoped to its own version and therefore never
silently equal to a blank node from the other version.

Example:

```python
>>> from rdflib import Dataset, URIRef
>>> from rdflib.dataset_diff import dataset_diff
>>> g1, g2 = URIRef("urn:g1"), URIRef("urn:g2")
>>> fact = (URIRef("urn:s"), URIRef("urn:p"), URIRef("urn:o"))
>>> before = Dataset()
>>> _ = before.graph(g1).add(fact)
>>> _ = before.graph(g2).add(fact)
>>> after = Dataset()
>>> _ = after.graph(g2).add(fact)
>>> diff = dataset_diff(before, after)
>>> len(diff.source_changes)
1
>>> change = diff.source_changes[0]
>>> change.before_sources == frozenset({g1, g2})
True
>>> change.after_sources == frozenset({g2})
True

```
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional, Set, Tuple, Union

from rdflib.graph import DATASET_DEFAULT_GRAPH_ID, ConjunctiveGraph, Graph
from rdflib.term import BNode, Node, URIRef

__all__ = [
    "DEFAULT_GRAPH_SOURCE",
    "DatasetDiff",
    "SourceSetChange",
    "TripleAttribution",
    "dataset_diff",
]

DEFAULT_GRAPH_SOURCE = URIRef("urn:x-rdflib:dataset-diff:default-graph")
"""Source marker used for triples asserted in the default graph of a dataset."""

_Triple = Tuple[Node, Node, Node]
_QuadContext = Union[Graph, Node, None]
_TermKey = Tuple[Any, ...]
_TripleKey = Tuple[_TermKey, _TermKey, _TermKey]


@dataclass(frozen=True)
class TripleAttribution:
    """A triple together with the set of sources asserting it in one version."""

    triple: _Triple
    sources: frozenset[Node]


@dataclass(frozen=True)
class SourceSetChange:
    """A triple present in both versions whose set of sources changed."""

    before_triple: _Triple
    after_triple: _Triple
    before_sources: frozenset[Node]
    after_sources: frozenset[Node]

    @property
    def added_sources(self) -> frozenset[Node]:
        """Sources asserting the triple only in the *after* dataset."""
        return self.after_sources - self.before_sources

    @property
    def removed_sources(self) -> frozenset[Node]:
        """Sources asserting the triple only in the *before* dataset."""
        return self.before_sources - self.after_sources


@dataclass(frozen=True)
class DatasetDiff:
    """Attribution-aware difference between two dataset versions."""

    added: Tuple[TripleAttribution, ...] = ()
    removed: Tuple[TripleAttribution, ...] = ()
    source_changes: Tuple[SourceSetChange, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.added or self.removed or self.source_changes)

    def filter(
        self,
        sources: Optional[Iterable[Node]] = None,
        predicates: Optional[Iterable[Node]] = None,
    ) -> DatasetDiff:
        """Return a new diff restricted to the given sources and/or predicates.

        An entry matches ``sources`` if any of its sources (before or after)
        is in the given collection, and matches ``predicates`` if its
        triple's predicate is in the given collection.
        """
        source_set = frozenset(sources) if sources is not None else None
        predicate_set = frozenset(predicates) if predicates is not None else None

        def keep(triple: _Triple, entry_sources: frozenset[Node]) -> bool:
            if source_set is not None and not (entry_sources & source_set):
                return False
            if predicate_set is not None and triple[1] not in predicate_set:
                return False
            return True

        return DatasetDiff(
            added=tuple(e for e in self.added if keep(e.triple, e.sources)),
            removed=tuple(e for e in self.removed if keep(e.triple, e.sources)),
            source_changes=tuple(
                e
                for e in self.source_changes
                if keep(e.before_triple, e.before_sources | e.after_sources)
            ),
        )

    def by_source(self, source: Node) -> DatasetDiff:
        """Return a new diff restricted to entries involving ``source``."""
        return self.filter(sources={source})

    def by_predicate(self, predicate: Node) -> DatasetDiff:
        """Return a new diff restricted to entries with ``predicate``."""
        return self.filter(predicates={predicate})


def _validate_bnode_mapping(
    bnode_mapping: Mapping[BNode, BNode],
) -> Dict[BNode, BNode]:
    validated: Dict[BNode, BNode] = {}
    targets: Set[BNode] = set()
    for source, target in bnode_mapping.items():
        if not isinstance(source, BNode):
            raise ValueError(
                "bnode_mapping keys must be blank nodes, "
                f"got {source!r} of type {type(source).__name__}"
            )
        if not isinstance(target, BNode):
            raise ValueError(
                "bnode_mapping values must be blank nodes, "
                f"got {target!r} of type {type(target).__name__}"
            )
        if target in targets:
            raise ValueError(
                f"bnode_mapping is not one-to-one: duplicate target {target!r}"
            )
        targets.add(target)
        validated[source] = target
    return validated


def _source_marker(context: _QuadContext) -> Node:
    if context is None:
        return DEFAULT_GRAPH_SOURCE
    if isinstance(context, Graph):
        context = context.identifier
    if context == DATASET_DEFAULT_GRAPH_ID:
        return DEFAULT_GRAPH_SOURCE
    return context


def _index_dataset(
    dataset: ConjunctiveGraph,
    version: str,
    shared_bnode_ids: Mapping[BNode, str],
) -> Dict[_TripleKey, Dict[str, Any]]:
    def term_key(term: Node) -> _TermKey:
        if isinstance(term, BNode):
            shared_id = shared_bnode_ids.get(term)
            if shared_id is not None:
                return ("shared", shared_id)
            return ("isolated", version, str(term))
        return ("term", term)

    index: Dict[_TripleKey, Dict[str, Any]] = {}
    for subject, predicate, obj, context in dataset.quads():
        key = (term_key(subject), term_key(predicate), term_key(obj))
        entry = index.get(key)
        if entry is None:
            entry = index[key] = {
                "triple": (subject, predicate, obj),
                "sources": set(),
            }
        entry["sources"].add(_source_marker(context))
    return index


def _sort_key(triple: _Triple) -> Tuple[Tuple[str, str], ...]:
    return tuple((type(term).__name__, str(term)) for term in triple)


def dataset_diff(
    before: ConjunctiveGraph,
    after: ConjunctiveGraph,
    bnode_mapping: Optional[Mapping[BNode, BNode]] = None,
    sources: Optional[Iterable[Node]] = None,
    predicates: Optional[Iterable[Node]] = None,
) -> DatasetDiff:
    """Compare two dataset versions and report per-triple attribution changes.

    :param before: the earlier dataset (a `Dataset` or `ConjunctiveGraph`).
    :param after: the later dataset.
    :param bnode_mapping: optional one-to-one mapping from *before* blank
        nodes to *after* blank nodes, validated by the caller.  Keys and
        values must be blank nodes and each target may occur only once;
        anything else raises `ValueError`.  Unmapped blank nodes are scoped
        to their own version.
    :param sources: if given, only report entries involving these sources.
    :param predicates: if given, only report entries with these predicates.
    :return: a `DatasetDiff` with `added`, `removed` and `source_changes`.
    """
    mapping = _validate_bnode_mapping(bnode_mapping or {})
    before_shared_ids = {source: str(source) for source in mapping}
    after_shared_ids = {target: str(source) for source, target in mapping.items()}

    before_index = _index_dataset(before, "before", before_shared_ids)
    after_index = _index_dataset(after, "after", after_shared_ids)

    added = sorted(
        (
            TripleAttribution(
                triple=entry["triple"], sources=frozenset(entry["sources"])
            )
            for key, entry in after_index.items()
            if key not in before_index
        ),
        key=lambda entry: _sort_key(entry.triple),
    )
    removed = sorted(
        (
            TripleAttribution(
                triple=entry["triple"], sources=frozenset(entry["sources"])
            )
            for key, entry in before_index.items()
            if key not in after_index
        ),
        key=lambda entry: _sort_key(entry.triple),
    )
    source_changes = sorted(
        (
            SourceSetChange(
                before_triple=before_index[key]["triple"],
                after_triple=after_index[key]["triple"],
                before_sources=frozenset(before_index[key]["sources"]),
                after_sources=frozenset(after_index[key]["sources"]),
            )
            for key in before_index.keys() & after_index.keys()
            if before_index[key]["sources"] != after_index[key]["sources"]
        ),
        key=lambda entry: _sort_key(entry.before_triple),
    )

    diff = DatasetDiff(
        added=tuple(added),
        removed=tuple(removed),
        source_changes=tuple(source_changes),
    )
    if sources is not None or predicates is not None:
        diff = diff.filter(sources=sources, predicates=predicates)
    return diff
