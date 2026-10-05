from __future__ import annotations

from rdflib import BNode, Dataset, Literal, URIRef
from rdflib.extract import extract_dataset
from rdflib.graph import DATASET_DEFAULT_GRAPH_ID

EX = "http://example.org/"
PUBLIC = URIRef(EX + "public")
PRIVATE = URIRef(EX + "private")
OTHER = URIRef(EX + "other")
ROOT = URIRef(EX + "root")
LABEL = URIRef(EX + "label")
SECRET = URIRef(EX + "secret")
NEXT = URIRef(EX + "next")
LINK = URIRef(EX + "link")
DANGLING = URIRef(EX + "dangling")
RDF_FIRST = URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#first")
RDF_REST = URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#rest")
RDF_NIL = URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#nil")


def quad_set(dataset: Dataset) -> set:
    return set(dataset.quads((None, None, None, None)))


def objects(dataset: Dataset, subject: URIRef | BNode, predicate: URIRef) -> list:
    return [
        object_
        for current_subject, current_predicate, object_, _ in dataset.quads(
            (subject, predicate, None, None)
        )
        if current_subject == subject and current_predicate == predicate
    ]


def predicate_objects(dataset: Dataset, subject: BNode) -> set:
    return {
        (predicate, object_)
        for current_subject, predicate, object_, _ in dataset.quads(
            (subject, None, None, None)
        )
        if current_subject == subject
    }


def normalized_quads(dataset: Dataset) -> set:
    quads = quad_set(dataset)
    colors = {
        term: 0
        for quad in quads
        for term in quad
        if isinstance(term, BNode)
    }

    def color(term: BNode | URIRef | Literal | None) -> str | BNode:
        return f"bnode:{colors[term]}" if isinstance(term, BNode) else term

    while True:
        signatures = {}
        for node in colors:
            signature = frozenset(
                (
                    position,
                    tuple(color(term) for term in quad),
                )
                for quad in quads
                for position, term in enumerate(quad)
                if term == node
            )
            signatures[node] = signature

        signature_ids = {
            signature: index
            for index, signature in enumerate(sorted(set(signatures.values()), key=str))
        }
        next_colors = {
            node: signature_ids[signature] for node, signature in signatures.items()
        }
        if next_colors == colors:
            break
        colors = next_colors

    return {
        tuple(
            URIRef(f"urn:bnode:{colors[term]}") if isinstance(term, BNode) else term
            for term in quad
        )
        for quad in quads
    }


def test_extract_only_follows_authorized_edges_and_keeps_bnode_sharing() -> None:
    source = Dataset()
    shared = BNode("source-shared")
    cycle = BNode("source-cycle")

    public_graph = source.graph(PUBLIC)
    public_graph.add((ROOT, LABEL, Literal("visible root")))
    public_graph.add((ROOT, NEXT, shared))
    public_graph.add((ROOT, SECRET, Literal("blocked predicate")))
    public_graph.add((shared, LABEL, Literal("visible shared")))
    public_graph.add((shared, SECRET, Literal("public graph private predicate")))
    public_graph.add((shared, LINK, cycle))
    public_graph.add((cycle, LABEL, Literal("cycle")))
    public_graph.add((cycle, LINK, shared))

    private_graph = source.graph(PRIVATE)
    private_graph.add((shared, SECRET, Literal("private graph secret")))
    private_graph.add((shared, LABEL, Literal("must not complete shared node")))

    other_graph = source.graph(OTHER)
    other_graph.add((ROOT, LABEL, Literal("unreadable graph")))

    result = source.extract(
        ROOT,
        named_graphs=[PUBLIC],
        allowed_predicates=[LABEL, NEXT, LINK],
    )

    result_quads = quad_set(result)
    assert (ROOT, LABEL, Literal("visible root"), PUBLIC) in result_quads
    assert (ROOT, SECRET, Literal("blocked predicate"), PUBLIC) not in result_quads
    assert (ROOT, LABEL, Literal("unreadable graph"), OTHER) not in result_quads

    exported_shared = objects(result, ROOT, NEXT)[0]
    exported_cycle = objects(result, exported_shared, LINK)[0]
    assert isinstance(exported_shared, BNode)
    assert isinstance(exported_cycle, BNode)
    assert str(exported_shared) != "source-shared"
    assert str(exported_cycle) != "source-cycle"
    assert objects(result, exported_cycle, LINK)[0] is exported_shared
    assert list(objects(result, exported_shared, SECRET)) == []
    assert set(objects(result, exported_shared, LABEL)) == {Literal("visible shared")}
    assert set(objects(result, exported_cycle, LABEL)) == {Literal("cycle")}


def test_extract_blank_node_resource_is_graph_scoped() -> None:
    source = Dataset()
    start = BNode("blank-start")
    source.graph(PUBLIC).add((start, LABEL, Literal("start in public graph")))
    source.graph(OTHER).add((start, LABEL, Literal("start in other graph")))

    result = source.extract(
        start,
        named_graphs=[PUBLIC],
        allowed_predicates=[LABEL],
    )

    exported = next(
        subject
        for subject, predicate, _, _ in result
        if predicate == LABEL
    )
    assert isinstance(exported, BNode)
    assert set(objects(result, exported, LABEL)) == {
        Literal("start in public graph")
    }


def test_extract_cross_graph_bnode_does_not_use_other_graph_description() -> None:
    source = Dataset()
    shared = BNode("cross-graph-shared")

    source.graph(PUBLIC).add((ROOT, NEXT, shared))
    source.graph(OTHER).add((shared, LABEL, Literal("description in another graph")))
    source.graph(PRIVATE).add((shared, LABEL, Literal("description in private graph")))

    result = source.extract(
        ROOT,
        named_graphs=[PUBLIC, OTHER],
        allowed_predicates=[NEXT, LABEL],
    )

    exported_shared = objects(result, ROOT, NEXT)[0]
    assert predicate_objects(result, exported_shared) == set()
    assert (
        exported_shared,
        LABEL,
        Literal("description in another graph"),
        OTHER,
    ) not in quad_set(result)
    assert (
        exported_shared,
        LABEL,
        Literal("description in private graph"),
        PRIVATE,
    ) not in quad_set(result)


def test_extract_cross_graph_reuse_keeps_visible_shared_identity() -> None:
    source = Dataset()
    shared = BNode("reused-shared")

    source.graph(PUBLIC).add((ROOT, NEXT, shared))
    source.graph(PUBLIC).add((shared, LABEL, Literal("public description")))
    source.graph(OTHER).add((ROOT, LINK, shared))
    source.graph(OTHER).add((shared, LABEL, Literal("other description")))

    result = source.extract(
        [ROOT],
        named_graphs=[PUBLIC, OTHER],
        allowed_predicates=[NEXT, LINK, LABEL],
    )

    from_public = objects(result, ROOT, NEXT)[0]
    from_other = objects(result, ROOT, LINK)[0]
    assert from_public is from_other
    assert set(objects(result, from_public, LABEL)) == {
        Literal("public description"),
        Literal("other description"),
    }


def test_extract_keeps_edge_to_bnode_without_readable_description() -> None:
    source = Dataset()
    dangling = BNode("dangling")
    source.graph(PUBLIC).add((ROOT, DANGLING, dangling))
    source.graph(PRIVATE).add((dangling, LABEL, Literal("private description")))

    result = source.extract(
        ROOT,
        named_graphs=[PUBLIC],
        allowed_predicates=[DANGLING],
    )

    exported = objects(result, ROOT, DANGLING)[0]
    assert isinstance(exported, BNode)
    assert predicate_objects(result, exported) == set()
    assert list(objects(result, exported, LABEL)) == []


def test_extract_default_graph_visibility_is_separate() -> None:
    source = Dataset()
    bnode = BNode("default-shared")
    source.default_graph.add((ROOT, LABEL, Literal("default")))
    source.default_graph.add((ROOT, NEXT, bnode))
    source.graph(PUBLIC).add((bnode, LABEL, Literal("named graph")))

    without_default = source.extract(
        ROOT,
        named_graphs=[PUBLIC],
        allowed_predicates=[LABEL, NEXT],
    )
    assert len(without_default) == 0

    with_default = source.extract(
        ROOT,
        named_graphs=[PUBLIC],
        allowed_predicates=[LABEL, NEXT],
        default_graph_visible=True,
    )

    exported = objects(with_default, ROOT, NEXT)[0]
    assert (
        ROOT, LABEL, Literal("default"), DATASET_DEFAULT_GRAPH_ID
    ) in quad_set(with_default)
    assert predicate_objects(with_default, exported) == set()
    assert (exported, LABEL, Literal("named graph"), PUBLIC) not in quad_set(
        with_default
    )


def test_extract_adds_to_target_and_remaps_bnode_graph_names() -> None:
    source = Dataset()
    graph_name = BNode("source-graph-name")
    source.graph(graph_name).add((ROOT, LABEL, Literal("named by blank node")))

    target = Dataset()
    existing_graph_name = BNode("n00000000000000000000000000000000")
    target.graph(existing_graph_name).add(
        (ROOT, LABEL, Literal("existing"))
    )

    result = extract_dataset(
        source,
        ROOT,
        [graph_name],
        [LABEL],
        target_dataset=target,
    )

    assert result is target
    result_graph_names = {
        graph.identifier
        for graph in result.graphs()
        if isinstance(graph.identifier, BNode)
    }
    assert existing_graph_name in result_graph_names
    exported_graph_name = next(
        graph_id
        for graph_id in result_graph_names
        if graph_id != existing_graph_name
    )
    assert str(exported_graph_name) != "source-graph-name"
    assert (
        ROOT,
        LABEL,
        Literal("named by blank node"),
        exported_graph_name,
    ) in quad_set(result)


def test_extract_standalone_function_and_serialization_roundtrip() -> None:
    source = Dataset()
    shared = BNode("serialized-shared")

    public_graph = source.graph(PUBLIC)
    public_graph.add((ROOT, NEXT, shared))
    public_graph.add((shared, RDF_FIRST, Literal("one")))
    public_graph.add((shared, RDF_REST, RDF_NIL))
    source.default_graph.add((ROOT, LABEL, Literal("default visible")))

    result = extract_dataset(
        source,
        [ROOT],
        [PUBLIC],
        [NEXT, LABEL, RDF_FIRST, RDF_REST],
        default_graph_visible=True,
    )

    serialized = result.serialize(format="trig", encoding="utf-8")
    reread = Dataset()
    reread.parse(data=serialized, format="trig")

    assert normalized_quads(reread) == normalized_quads(result)
    exported_shared = objects(result, ROOT, NEXT)[0]
    reread_shared = objects(reread, ROOT, NEXT)[0]
    assert normalized_quads(reread) == normalized_quads(result)
    assert list(objects(reread, reread_shared, RDF_FIRST)) == [Literal("one")]
