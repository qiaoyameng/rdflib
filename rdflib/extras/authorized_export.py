"""
Authorized partial export of a :class:`~rdflib.graph.Dataset`.

This module implements an extraction API that exports exactly the part of a
dataset a client is authorized to see.  Authorization consists of three
inputs:

* a set of *readable* named graphs (plus a separate flag for the default
  graph),
* a set of *allowed* predicates, and
* a set of *start resources* from which traversal begins.

Blank nodes reachable from the start resources through authorized edges are
expanded recursively, so that the exported structure is complete.  Traversal
only ever follows authorized edges inside readable graphs: when an
unauthorized graph or predicate is encountered, traversal stops immediately.
In particular, if the same blank node is described in both a readable and a
non-readable graph, only the description in the readable graph is exported --
no data is "filled in" from the non-readable graph.

Blank node identifiers are re-assigned in the exported dataset, but sharing
of blank nodes within the visible portion is preserved: two visible
references to the same source blank node map to the same new blank node.

The result is deterministic with respect to cycles (each reachable blank
node is expanded exactly once), cross-graph reuse of nodes (a node is
expanded against every readable graph), and authorized edges that point to
nodes without any readable description (the edge is kept, the node is
included, and it simply has no outgoing description in the export).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterator, Set

from rdflib.graph import Dataset, _ContextType
from rdflib.term import BNode, Node

__all__ = ["ExportAuthorization", "extract_authorized_view"]


@dataclass(frozen=True)
class ExportAuthorization:
    """Describes what a client is allowed to see of a dataset."""

    start_resources: FrozenSet[Node]
    """Resources from which traversal of the dataset begins."""

    allowed_predicates: FrozenSet[Node]
    """Predicates whose triples may be exported and followed."""

    readable_graphs: FrozenSet[Node] = field(default_factory=frozenset)
    """Identifiers of the named graphs the client may read."""

    include_default_graph: bool = False
    """Whether the default graph of the dataset is visible to the client."""


def _readable_contexts(
    dataset: Dataset, authorization: ExportAuthorization
) -> Iterator[_ContextType]:
    default_id = dataset.default_graph.identifier
    for context in dataset.graphs():
        if context.identifier == default_id:
            if authorization.include_default_graph:
                yield context
        elif context.identifier in authorization.readable_graphs:
            yield context


def extract_authorized_view(
    dataset: Dataset, authorization: ExportAuthorization
) -> Dataset:
    """Export the part of ``dataset`` visible under ``authorization``.

    Returns a new :class:`~rdflib.graph.Dataset` containing exactly the
    authorized triples, with fresh blank node identifiers.  The graph a
    visible triple came from is preserved.
    """
    result = Dataset()
    for prefix, namespace in dataset.namespaces():
        result.bind(prefix, namespace)

    contexts = list(_readable_contexts(dataset, authorization))
    out_contexts: Dict[Node, _ContextType] = {}
    for context in contexts:
        if context.identifier == dataset.default_graph.identifier:
            out_contexts[context.identifier] = result.default_graph
        else:
            out_contexts[context.identifier] = result.graph(context.identifier)

    bnode_map: Dict[BNode, BNode] = {}

    def remap(term: Node) -> Node:
        if isinstance(term, BNode):
            new_node = bnode_map.get(term)
            if new_node is None:
                new_node = BNode()
                bnode_map[term] = new_node
            return new_node
        return term

    expanded: Set[Node] = set()
    worklist = list(authorization.start_resources)
    while worklist:
        subject = worklist.pop()
        if subject in expanded:
            continue
        expanded.add(subject)
        for context in contexts:
            for _, predicate, obj in context.triples((subject, None, None)):
                if predicate not in authorization.allowed_predicates:
                    # Unauthorized predicate: stop, do not follow this edge.
                    continue
                result.add(
                    (
                        remap(subject),
                        predicate,
                        remap(obj),
                        out_contexts[context.identifier],
                    )
                )
                if isinstance(obj, BNode) and obj not in expanded:
                    # Only blank nodes are traced further along authorized
                    # edges; other objects are included as-is but not
                    # expanded.
                    worklist.append(obj)
    return result
