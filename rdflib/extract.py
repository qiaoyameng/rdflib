from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from typing import Union

from rdflib.graph import (
    DATASET_DEFAULT_GRAPH_ID,
    ConjunctiveGraph,
    Dataset,
    Graph,
)
from rdflib.term import BNode, IdentifiedNode, Node, URIRef

__all__ = ["extract_dataset"]

Triple = tuple[Node, URIRef, Node]

GraphLike = Union[Graph, IdentifiedNode, str]
ResourceLike = Union[IdentifiedNode, str]
PredicateLike = Union[URIRef, str]


def _identified_node(value: ResourceLike) -> IdentifiedNode:
    if isinstance(value, ConjunctiveGraph):
        raise TypeError("resources must be URIRefs or blank nodes")
    if isinstance(value, Graph):
        value = value.identifier
    if isinstance(value, str) and not isinstance(value, Node):
        value = URIRef(value)
    if not isinstance(value, (URIRef, BNode)):
        raise TypeError("resources must be URIRefs or blank nodes")
    return value


def _graph_identifier(value: GraphLike) -> IdentifiedNode:
    if isinstance(value, ConjunctiveGraph):
        raise TypeError("named graphs must be graphs or graph identifiers")
    if isinstance(value, Graph):
        value = value.identifier
    if isinstance(value, str) and not isinstance(value, Node):
        value = URIRef(value)
    if not isinstance(value, (URIRef, BNode)):
        raise TypeError("graph identifiers must be URIRefs or blank nodes")
    return value


def _predicate(value: PredicateLike) -> URIRef:
    if isinstance(value, str) and not isinstance(value, Node):
        value = URIRef(value)
    if not isinstance(value, URIRef):
        raise TypeError("predicates must be URIRefs")
    return value


def _set(value: None | object, factory: Callable[[object], Node]) -> set[Node]:
    if value is None:
        return set()
    if isinstance(value, (str, Node, Graph)):
        values = [value]
    elif isinstance(value, Iterable):
        values = value
    else:
        raise TypeError("authorization values must be terms or an iterable of terms")
    return {factory(item) for item in values}


def extract_dataset(
    dataset: Dataset,
    resources: Union[ResourceLike, Iterable[ResourceLike]],
    named_graphs: None | Union[GraphLike, Iterable[GraphLike]],
    allowed_predicates: Union[PredicateLike, Iterable[PredicateLike]],
    *,
    default_graph_visible: bool = False,
    target_dataset: Dataset | None = None,
) -> Dataset:
    """Extract the authorized blank-node-bounded description of resources.

    Authorization is checked independently for every quad. A triple is only
    selected when its graph is readable, its predicate is allowed, and its
    subject was reached in that same graph. Blank-node objects are only
    followed in the graph through which the authorized edge was reached, so
    descriptions of the same blank node from another graph are never used to
    complete it.

    Args:
        dataset: Dataset from which quads are extracted.
        resources: Starting URIRef or blank node, or an iterable of them.
        named_graphs: Identifiers of readable named graphs. The default graph
            is not included unless ``default_graph_visible`` is true.
        allowed_predicates: Predicates that may be exported and traversed.
        default_graph_visible: Whether the dataset's default graph is readable.
        target_dataset: Existing dataset to receive the export. By default a
            new dataset is returned.

    Returns:
        The dataset containing the exported quads. Blank-node labels are
        replaced, while one original blank node is consistently represented
        by the same replacement blank node everywhere it is visible.
    """

    if not isinstance(dataset, Dataset):
        raise TypeError("dataset must be a Dataset")
    if target_dataset is None:
        target_dataset = Dataset()
    elif not isinstance(target_dataset, Dataset):
        raise TypeError("target_dataset must be a Dataset")
    elif target_dataset is dataset:
        raise ValueError("target_dataset must not be the source dataset")

    starts = _set(resources, _identified_node)
    if not starts:
        raise ValueError("at least one resource is required")

    readable_graphs = {
        graph_id
        for graph_id in _set(named_graphs, _graph_identifier)
        if graph_id != DATASET_DEFAULT_GRAPH_ID
    }
    if default_graph_visible:
        readable_graphs.add(DATASET_DEFAULT_GRAPH_ID)

    predicates = _set(allowed_predicates, _predicate)
    if not predicates:
        raise ValueError("at least one allowed predicate is required")

    used_bnode_ids = set()
    for graph in target_dataset.graphs():
        graph_id = graph.identifier
        if isinstance(graph_id, BNode):
            used_bnode_ids.add(str(graph_id))

    bnode_map: dict[BNode, BNode] = {}

    def remap(term: Node) -> Node:
        if not isinstance(term, BNode):
            return term
        mapped = bnode_map.get(term)
        if mapped is not None:
            return mapped
        while True:
            mapped_id = f"n{len(used_bnode_ids):032x}"
            if mapped_id not in used_bnode_ids:
                used_bnode_ids.add(mapped_id)
                mapped = BNode(mapped_id)
                break
        bnode_map[term] = mapped
        return mapped

    visited: set[tuple[IdentifiedNode, IdentifiedNode]] = set()
    pending: deque[tuple[IdentifiedNode, IdentifiedNode]] = deque(
        (resource, graph_id)
        for resource in sorted(starts, key=str)
        for graph_id in sorted(readable_graphs, key=str)
    )

    while pending:
        subject, graph_id = pending.popleft()
        if (subject, graph_id) in visited:
            continue
        visited.add((subject, graph_id))

        graph = dataset.get_context(graph_id)
        for predicate, object_ in graph.predicate_objects(subject):
            if predicate not in predicates:
                continue

            mapped_triple: Triple = (
                remap(subject),
                predicate,
                remap(object_),
            )
            if graph_id == DATASET_DEFAULT_GRAPH_ID:
                target_dataset.default_graph.add(mapped_triple)
            else:
                target_dataset.add((*mapped_triple, remap(graph_id)))

            if isinstance(object_, BNode):
                pending.append((object_, graph_id))

    return target_dataset
