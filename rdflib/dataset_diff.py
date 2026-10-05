"""Attribution-aware comparison of two RDF datasets.

`rdflib.compare` compares graphs (and, via canonicalization, graphs with
blank nodes) at the triple level. It deliberately does not handle quads, so
it cannot tell *which source graph* backed a triple. Merging several RDF
feeds into one warehouse dataset means that the same triple may survive a
daily merge even though the set of data sources that assert it has changed;
plain set comparison or comparing the size of the union graph misses that.

This module compares the triples of two
[`Dataset`][rdflib.graph.Dataset] instances (before and after) and reports,
per triple:

- ``added``: the triple is only asserted in ``after``,
- ``removed``: the triple is only asserted in ``before``,
- ``changed``: the triple is present in both, but the set of source graphs
  that assert it differs, and
- ``unchanged``: the triple and its source set are identical (reported when
  ``include_unchanged=True``).

Every entry keeps the RDF term objects (and therefore their term types:
`URIRef`, `BNode`, `Literal` with datatype/language, ...). Source sets are
sets of graph-name nodes. The default graph has no name; it is reported
with the distinct marker [`DEFAULT_GRAPH_SOURCE`][rdflib.dataset_diff.DEFAULT_GRAPH_SOURCE]
so that it is never confused with a named graph.

Blank nodes from different datasets are local to their version. No
isomorphism guessing is performed across versions. The caller may supply a
validated one-to-one ``bnode_map`` from a blank node in ``before`` to the
corresponding blank node in ``after``; unmapped blank nodes stay isolated
by version (a triple involving an unmapped blank node can never line up
across versions, even if the rest of the triple is identical).

Example:

```python
>>> from rdflib import Dataset, Literal, URIRef
>>> from rdflib.namespace import FOAF
>>> before = Dataset()
>>> after = Dataset()
>>> g1 = URIRef("http://example.org/source-1")
>>> g2 = URIRef("http://example.org/source-2")
>>> triple = (URIRef("http://example.org/alice"), FOAF.name, Literal("Alice"))
>>> # both sources assert the fact before
>>> before.addN(  # doctest: +ELLIPSIS
...     [(*triple, before.graph(g1)), (*triple, before.graph(g2))]
... )
<Graph identifier=... (<class 'rdflib.graph.Dataset'>)>
>>> # only source 2 asserts it afterwards: the value is unchanged, but the
>>> # set of supporting data sources changed
>>> after.addN(  # doctest: +ELLIPSIS
...     [(*triple, after.graph(g2))]
... )
<Graph identifier=... (<class 'rdflib.graph.Dataset'>)>
>>> report = dataset_diff(before, after)
>>> entry = report.changed[0]
>>> entry.triple == triple
True
>>> entry.sources_before == {g1, g2}
True
>>> entry.sources_after == {g2}
True
>>> report.added
[]
>>> report.removed
[]
"""

from __future__ import annotations

__all__ = [
    "DEFAULT_GRAPH_SOURCE",
    "AttributionChange",
    "DatasetAttributionDiff",
    "dataset_diff",
    "validate_bnode_map",
]

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Optional, Union

from rdflib.graph import DATASET_DEFAULT_GRAPH_ID, Dataset
from rdflib.term import BNode, Node, URIRef

#: Marker used in source sets for triples asserted in the default graph.
#: The default graph has no name, so `None` (the graph slot used by
#: ``Dataset.quads``) is replaced by this distinct URIRef internally and in
#: every reported source set.
DEFAULT_GRAPH_SOURCE: URIRef = DATASET_DEFAULT_GRAPH_ID

_NODE_SENTINEL_PREFIX = "urn:x-rdflib:dataset-diff:bnode:"

_BNodeMap = Mapping[BNode, BNode]
_Triple = tuple[Node, Node, Node]
_NodeFilter = Optional[Union[Node, tuple[Node, ...], set[Node], frozenset[Node]]]


@dataclass(frozen=True)
class AttributionChange:
    """A single triple-level result of an attribution comparison.

    Attributes:
        triple: The triple, expressed using RDF term objects so term types
            (IRIs, blank nodes, literals with datatype/language) are
            preserved. For ``changed`` and ``added`` entries, blank nodes
            that were matched through ``bnode_map`` are expressed with the
            ``after`` term; for ``removed`` entries the ``before`` term is
            used.
        kind: One of ``"added"``, ``"removed"``, ``"changed"`` or
            ``"unchanged"``.
        sources_before: Source graphs asserting the triple in the before
            dataset; empty for ``added`` entries.
        sources_after: Source graphs asserting the triple in the after
            dataset; empty for ``removed`` entries.
        triple_before: The triple exactly as it appeared before, or `None`
            for ``added`` entries.
        triple_after: The triple exactly as it appeared after, or `None`
            for ``removed`` entries.
    """

    triple: _Triple
    kind: str
    sources_before: frozenset[Node] = frozenset()
    sources_after: frozenset[Node] = frozenset()
    triple_before: Optional[_Triple] = None
    triple_after: Optional[_Triple] = None

    @property
    def sources_added(self) -> frozenset[Node]:
        """Sources that newly assert the triple in the after dataset."""
        return self.sources_after - self.sources_before

    @property
    def sources_removed(self) -> frozenset[Node]:
        """Sources that no longer assert the triple in the after dataset."""
        return self.sources_before - self.sources_after


@dataclass
class DatasetAttributionDiff:
    """Result of comparing two datasets with source attribution.

    Every list is deterministically ordered. Use the ``added``,
    ``removed``, ``changed`` and ``unchanged`` attributes, or iterate the
    object to get all entries.
    """

    added: list[AttributionChange] = field(default_factory=list)
    removed: list[AttributionChange] = field(default_factory=list)
    changed: list[AttributionChange] = field(default_factory=list)
    unchanged: list[AttributionChange] = field(default_factory=list)

    def __iter__(self):
        yield from self.added
        yield from self.removed
        yield from self.changed
        yield from self.unchanged

    def summary(self) -> dict[str, int]:
        """Return counts of each kind of change."""
        return {
            "added": len(self.added),
            "removed": len(self.removed),
            "changed": len(self.changed),
            "unchanged": len(self.unchanged),
        }


def validate_bnode_map(
    bnode_map: Optional[_BNodeMap],
) -> dict[BNode, BNode]:
    """Validate a caller-supplied one-to-one blank-node correspondence.

    The map goes from a blank node in the *before* dataset to the
    corresponding blank node in the *after* dataset. Existence of the
    nodes in the datasets is not checked here; the correspondence is
    validated structurally:

    - every key and value must be a `BNode` (mapping to or from a named
      node or a literal is rejected), and
    - no two keys may map to the same target (it must be one-to-one).

    Returns:
        A plain ``dict`` copy of the validated mapping.

    Raises:
        TypeError: if a key or value is not a `BNode`.
        ValueError: if a target blank node is used more than once.
    """
    if bnode_map is None:
        return {}
    validated: dict[BNode, BNode] = {}
    seen_targets: set[BNode] = set()
    for key, value in bnode_map.items():
        if not isinstance(key, BNode):
            raise TypeError(
                f"bnode_map keys must be BNode instances, got {type(key).__name__}: {key!r}"
            )
        if not isinstance(value, BNode):
            raise TypeError(
                "bnode_map values must be BNode instances; mapping a blank node "
                f"to a non-blank node is not allowed, got {type(value).__name__}: {value!r}"
            )
        if value in seen_targets:
            raise ValueError(
                f"bnode_map is not one-to-one: target blank node {value!r} is "
                "mapped to more than once"
            )
        seen_targets.add(value)
        validated[key] = value
    return validated


class _TermRewriter:
    """Rewrite blank nodes for cross-version comparison.

    Mapped blank nodes are represented with the after version's node.
    Unmapped blank nodes are replaced with version-specific sentinel nodes,
    which guarantees that the same local label in two versions can never
    accidentally line up (version isolation).
    """

    def __init__(
        self,
        bnode_map: dict[BNode, BNode],
        side: str,
    ):
        self._side = side
        self._cache: dict[BNode, BNode] = {}
        if side == "before":
            self._mapped = bnode_map
        else:
            # On the after side a mapped node keeps its own identity.
            self._mapped = {value: value for value in bnode_map.values()}

    def rewrite(self, term: Optional[Node]) -> Optional[Node]:
        if term is None:
            return DEFAULT_GRAPH_SOURCE
        if not isinstance(term, BNode):
            return term
        mapped = self._mapped.get(term)
        if mapped is not None:
            return mapped
        cached = self._cache.get(term)
        if cached is None:
            cached = BNode(f"{_NODE_SENTINEL_PREFIX}{self._side}:{term}")
            self._cache[term] = cached
        return cached


def _as_node_set(value: _NodeFilter) -> Optional[frozenset[Node]]:
    if value is None:
        return None
    if isinstance(value, (set, frozenset, tuple, list)):
        nodes = frozenset(value)
    else:
        nodes = frozenset({value})
    return nodes


def _index_dataset(
    dataset: Dataset,
    rewriter: _TermRewriter,
) -> dict[_Triple, dict[Node, _Triple]]:
    """Build ``{canonical triple: {canonical source: original triple}}``."""
    index: dict[_Triple, dict[Node, _Triple]] = defaultdict(dict)
    for subject, predicate, obj, graph in dataset.quads((None, None, None, None)):
        canonical_triple = (
            rewriter.rewrite(subject),
            rewriter.rewrite(predicate),
            rewriter.rewrite(obj),
        )
        canonical_source = rewriter.rewrite(graph)
        # Re-adding the same quad is a no-op; keep one representative of the
        # original term objects.
        index[canonical_triple].setdefault(
            canonical_source, (subject, predicate, obj)
        )
    return index


def _sort_entries(entries: list[AttributionChange]) -> list[AttributionChange]:
    return sorted(
        entries,
        key=lambda entry: " ".join(term.n3() for term in entry.triple),
    )


def dataset_diff(
    before: Dataset,
    after: Dataset,
    *,
    bnode_map: Optional[_BNodeMap] = None,
    sources: _NodeFilter = None,
    predicates: _NodeFilter = None,
    include_unchanged: bool = False,
) -> DatasetAttributionDiff:
    """Compare two datasets and attribute triple changes to source graphs.

    Args:
        before: The dataset as it was before the merge/change.
        after: The dataset as it is after the merge/change.
        bnode_map: Optional one-to-one mapping from blank nodes in
            ``before`` to corresponding blank nodes in ``after``. No
            isomorphism guessing is done; blank nodes absent from the map
            are isolated by version. The map is validated structurally by
            [`validate_bnode_map`][rdflib.dataset_diff.validate_bnode_map]
        sources: Restrict the report to triples touched by these source
            graphs. A bare `None` means "any source"; to select the default
            graph explicitly, include
            [`DEFAULT_GRAPH_SOURCE`][rdflib.dataset_diff.DEFAULT_GRAPH_SOURCE]
            (or `None`) in the filter collection. Each matching triple is
            still reported with its *complete* source set on both sides.
        predicates: Restrict the report to triples using these predicates.
        include_unchanged: Include ``unchanged`` entries in the result.

    Returns:
        A `DatasetAttributionDiff` with ``added``, ``removed``,
        ``changed`` and (optionally) ``unchanged`` entries.
    """
    validated_map = validate_bnode_map(bnode_map)

    source_filter = _as_node_set(sources)
    predicate_filter = _as_node_set(predicates)

    before_rewriter = _TermRewriter(validated_map, "before")
    after_rewriter = _TermRewriter(validated_map, "after")

    if source_filter is not None:
        # ``None`` inside the filter collection denotes the default graph;
        # ``sources=None`` itself means "no filter".
        source_filter = frozenset(
            {DEFAULT_GRAPH_SOURCE if node is None else node for node in source_filter}
        )
        source_filter = frozenset(
            {before_rewriter.rewrite(node) for node in source_filter}
        ) | frozenset({after_rewriter.rewrite(node) for node in source_filter})
    if predicate_filter is not None:
        predicate_filter = (
            frozenset({before_rewriter.rewrite(node) for node in predicate_filter})
            | frozenset({after_rewriter.rewrite(node) for node in predicate_filter})
        )

    before_index = _index_dataset(before, before_rewriter)
    after_index = _index_dataset(after, after_rewriter)

    diff = DatasetAttributionDiff()
    for triple in sorted(
        set(before_index) | set(after_index),
        key=lambda terms: " ".join(term.n3() for term in terms),
    ):
        if predicate_filter is not None and triple[1] not in predicate_filter:
            continue

        before_sources = frozenset(before_index.get(triple, {}))
        after_sources = frozenset(after_index.get(triple, {}))

        if source_filter is not None and not (
            before_sources & source_filter or after_sources & source_filter
        ):
            continue

        before_originals = before_index.get(triple, {})
        after_originals = after_index.get(triple, {})
        triple_before = next(iter(before_originals.values()), None)
        triple_after = next(iter(after_originals.values()), None)

        if triple_before is None:
            entry = AttributionChange(
                triple=triple_after,
                kind="added",
                sources_before=frozenset(),
                sources_after=after_sources,
                triple_before=None,
                triple_after=triple_after,
            )
            diff.added.append(entry)
        elif triple_after is None:
            entry = AttributionChange(
                triple=triple_before,
                kind="removed",
                sources_before=before_sources,
                sources_after=frozenset(),
                triple_before=triple_before,
                triple_after=None,
            )
            diff.removed.append(entry)
        elif before_sources == after_sources:
            if include_unchanged:
                entry = AttributionChange(
                    triple=triple,
                    kind="unchanged",
                    sources_before=before_sources,
                    sources_after=after_sources,
                    triple_before=triple_before,
                    triple_after=triple_after,
                )
                diff.unchanged.append(entry)
        else:
            entry = AttributionChange(
                triple=triple,
                kind="changed",
                sources_before=before_sources,
                sources_after=after_sources,
                triple_before=triple_before,
                triple_after=triple_after,
            )
            diff.changed.append(entry)

    diff.added = _sort_entries(diff.added)
    diff.removed = _sort_entries(diff.removed)
    diff.changed = _sort_entries(diff.changed)
    diff.unchanged = _sort_entries(diff.unchanged)
    return diff
