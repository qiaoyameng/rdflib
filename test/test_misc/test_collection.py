import logging

import pytest

from rdflib import BNode, Graph, Literal, Namespace, RDF, URIRef
from rdflib.collection import Collection, replace_range

EX = Namespace("http://example.org/")


def make_list(
    graph: Graph, items: list, cells: list | None = None, head: BNode | None = None
) -> BNode:
    head = head or BNode()
    current = head
    for index, item in enumerate(items):
        graph.add((current, RDF.first, item))
        if cells is not None:
            cells.append(current)
        if index == len(items) - 1:
            graph.add((current, RDF.rest, RDF.nil))
        else:
            next_cell = BNode()
            graph.add((current, RDF.rest, next_cell))
            current = next_cell
    return head


def test_scenario() -> None:
    # Taken from https://github.com/RDFLib/rdflib/blob/8a92d3565bf2e502a7c4cadb34b29db72c89d623/rdflib/collection.py#L272-L304
    g = Graph()

    c = Collection(g, BNode())

    assert len(c) == 0

    c = Collection(g, BNode(), [Literal("1"), Literal("2"), Literal("3"), Literal("4")])

    assert len(c) == 4

    assert c[1] == Literal("2"), c[1]

    del c[1]

    assert list(c) == [Literal("1"), Literal("3"), Literal("4")], list(c)

    with pytest.raises(IndexError):
        del c[500]

    c.append(Literal("5"))

    logging.debug("list(c) = %s", list(c))

    for i in c:
        logging.debug("i = %s", i)

    del c[3]

    c.clear()

    assert len(c) == 0


def test_empty_list() -> None:
    nil = URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#nil")
    g = Graph()

    c = Collection(g, nil)

    assert set(g) == set(), "Collection changed the graph"

    assert len(c) == 0


def test_replace_range_preserves_shared_tail_and_external_references() -> None:
    graph = Graph()
    main_cells: list[BNode] = []
    main_head = make_list(
        graph,
        [Literal("a"), Literal("b"), Literal("c"), Literal("d")],
        main_cells,
    )
    second_head = BNode()
    graph.add((second_head, RDF.first, Literal("x")))
    graph.add((second_head, RDF.rest, main_cells[2]))

    graph.add((EX.list2, EX.items, second_head))
    graph.add((EX.removed, EX.pointsTo, main_cells[1]))
    graph.add((main_cells[1], EX.note, Literal("removed cell")))
    graph.add((main_cells[2], EX.note, Literal("shared tail")))

    edited_head = replace_range(
        graph, main_head, 1, 2, [Literal("b1"), Literal("b2")]
    )

    assert edited_head is main_head
    assert list(Collection(graph, edited_head)) == [
        Literal("a"),
        Literal("b1"),
        Literal("b2"),
        Literal("c"),
        Literal("d"),
    ]
    assert list(Collection(graph, second_head)) == [
        Literal("x"),
        Literal("c"),
        Literal("d"),
    ]
    assert list(Collection(graph, main_cells[1])) == [
        Literal("b"),
        Literal("c"),
        Literal("d"),
    ]
    assert (main_cells[1], EX.note, Literal("removed cell")) in graph
    assert (main_cells[2], EX.note, Literal("shared tail")) in graph
    assert (EX.removed, EX.pointsTo, main_cells[1]) in graph


def test_replace_range_keeps_unaffected_prefix_identity() -> None:
    graph = Graph()
    main_cells: list[BNode] = []
    main_head = make_list(
        graph,
        [Literal("a"), Literal("b"), Literal("c"), Literal("d")],
        main_cells,
    )
    second_head = BNode()
    graph.add((second_head, RDF.first, Literal("x")))
    graph.add((second_head, RDF.rest, main_cells[1]))
    graph.add((main_cells[1], EX.note, Literal("shared removed cell")))

    edited_head = replace_range(graph, main_head, 1, 2, [Literal("b1")])

    assert edited_head is main_head
    assert list(Collection(graph, edited_head)) == [
        Literal("a"),
        Literal("b1"),
        Literal("c"),
        Literal("d"),
    ]
    assert list(Collection(graph, second_head)) == [
        Literal("x"),
        Literal("b"),
        Literal("c"),
        Literal("d"),
    ]
    assert (main_cells[1], EX.note, Literal("shared removed cell")) in graph


def test_replace_range_copies_shared_prefix_before_edit() -> None:
    graph = Graph()
    main_cells: list[BNode] = []
    main_head = make_list(
        graph,
        [Literal("a"), Literal("b"), Literal("c"), Literal("d")],
        main_cells,
    )
    second_head = BNode()
    graph.add((second_head, RDF.first, Literal("x")))
    graph.add((second_head, RDF.rest, main_head))
    graph.add((main_cells[0], EX.note, Literal("prefix property")))

    edited_head = replace_range(graph, main_head, 1, 2, [Literal("b1")])

    assert edited_head != main_head
    assert list(Collection(graph, edited_head)) == [
        Literal("a"),
        Literal("b1"),
        Literal("c"),
        Literal("d"),
    ]
    assert list(Collection(graph, second_head)) == [
        Literal("x"),
        Literal("a"),
        Literal("b"),
        Literal("c"),
        Literal("d"),
    ]
    assert (main_cells[0], EX.note, Literal("prefix property")) in graph
    assert not list(graph.triples((edited_head, EX.note, None)))


def test_replace_range_cleanup_keeps_non_list_properties() -> None:
    graph = Graph()
    cells: list[BNode] = []
    head = make_list(graph, [Literal("a"), Literal("b"), Literal("c")], cells)
    graph.add((cells[1], EX.note, Literal("business property")))

    edited_head = replace_range(graph, head, 1, 2, [Literal("x")])

    assert edited_head is head
    assert list(Collection(graph, edited_head)) == [
        Literal("a"),
        Literal("x"),
        Literal("c"),
    ]
    assert (cells[1], RDF.first, None) not in graph
    assert (cells[1], RDF.rest, None) not in graph
    assert (cells[1], EX.note, Literal("business property")) in graph


def test_replace_range_on_empty_list_creates_collection() -> None:
    graph = Graph()

    head = replace_range(graph, RDF.nil, 0, 0, [Literal("a"), Literal("b")])

    assert head != RDF.nil
    assert list(Collection(graph, head)) == [Literal("a"), Literal("b")]
    assert not list(graph.triples((RDF.nil, None, None)))


def test_replace_range_deletes_complete_private_collection() -> None:
    graph = Graph()
    cells: list[BNode] = []
    head = make_list(graph, [Literal("a"), Literal("b")], cells)
    graph.add((cells[0], EX.note, Literal("head property")))

    edited_head = replace_range(graph, head, 0, 2, [])

    assert edited_head == RDF.nil
    assert list(Collection(graph, edited_head)) == []
    assert (cells[0], EX.note, Literal("head property")) in graph
    assert not list(graph.triples((cells[0], RDF.first, None)))
    assert not list(graph.triples((cells[0], RDF.rest, None)))
    assert not list(graph.triples((cells[1], RDF.first, None)))
    assert not list(graph.triples((cells[1], RDF.rest, None)))


def test_replace_range_preserves_other_use_of_shared_head() -> None:
    graph = Graph()
    cells: list[BNode] = []
    head = make_list(graph, [Literal("a"), Literal("b")], cells)
    graph.add((EX.list1, EX.items, head))
    graph.add((EX.list2, EX.items, head))

    edited_head = replace_range(graph, head, 0, 1, [Literal("x")])

    assert edited_head != head
    assert list(Collection(graph, edited_head)) == [Literal("x"), Literal("b")]
    assert list(Collection(graph, head)) == [Literal("a"), Literal("b")]


def test_collection_replace_range_updates_collection_head() -> None:
    graph = Graph()
    cells: list[BNode] = []
    head = make_list(graph, [Literal("a"), Literal("b")], cells)
    collection = Collection(graph, head)

    returned_head = collection.replace_range(1, 2, [Literal("c")])

    assert returned_head is collection.uri
    assert list(collection) == [Literal("a"), Literal("c")]


@pytest.mark.parametrize(
    "triples",
    ["cycle", "fork", "missing_first", "missing_rest"],
)
def test_replace_range_leaves_graph_unchanged_for_bad_collection(triples: str) -> None:
    graph = Graph()
    head = BNode()
    second = BNode()

    if triples == "cycle":
        graph.add((head, RDF.first, Literal("a")))
        graph.add((head, RDF.rest, second))
        graph.add((second, RDF.first, Literal("b")))
        graph.add((second, RDF.rest, second))
    elif triples == "fork":
        third = BNode()
        graph.add((head, RDF.first, Literal("a")))
        graph.add((head, RDF.rest, second))
        graph.add((head, RDF.rest, third))
        graph.add((second, RDF.first, Literal("b")))
        graph.add((second, RDF.rest, RDF.nil))
        graph.add((third, RDF.first, Literal("c")))
        graph.add((third, RDF.rest, RDF.nil))
    elif triples == "missing_first":
        graph.add((head, RDF.rest, second))
        graph.add((second, RDF.first, Literal("b")))
        graph.add((second, RDF.rest, RDF.nil))
    else:
        graph.add((head, RDF.first, Literal("a")))

    before = set(graph)

    with pytest.raises(ValueError):
        replace_range(graph, head, 0, 1, [Literal("x")])

    assert set(graph) == before


def test_replace_range_leaves_graph_unchanged_for_invalid_range() -> None:
    graph = Graph()
    head = make_list(graph, [Literal("a")])
    before = set(graph)

    with pytest.raises(IndexError):
        replace_range(graph, head, 1, 0, [Literal("x")])
    with pytest.raises(IndexError):
        replace_range(graph, head, 0, 2, [Literal("x")])

    assert set(graph) == before
