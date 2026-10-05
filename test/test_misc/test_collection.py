import logging

import pytest

from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.collection import Collection, MalformedCollectionError, replace_range
from rdflib.namespace import RDF


EX = Namespace("http://example.org/")


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


def test_replace_range_copies_referenced_prefix_and_preserves_shared_tail() -> None:
    g = Graph()
    first_head, second_head = BNode("first"), BNode("second")
    shared_b, shared_c, shared_d = BNode("b"), BNode("c"), BNode("d")
    external = EX.external
    note = EX.note

    g.add((first_head, RDF.first, Literal("A")))
    g.add((first_head, RDF.rest, shared_b))
    g.add((shared_b, RDF.first, Literal("B")))
    g.add((shared_b, RDF.rest, shared_c))
    g.add((shared_c, RDF.first, Literal("C")))
    g.add((shared_c, RDF.rest, shared_d))
    g.add((shared_d, RDF.first, Literal("D")))
    g.add((shared_d, RDF.rest, RDF.nil))

    g.add((second_head, RDF.first, Literal("E")))
    g.add((second_head, RDF.rest, shared_b))

    g.add((external, EX.refersTo, first_head))
    g.add((first_head, note, Literal("business property")))
    g.add((shared_b, EX.note, Literal("shared business property")))

    edited_head = replace_range(g, first_head, 1, 2, [Literal("Z")])
    copied_b = g.value(edited_head, RDF.rest)

    assert list(Collection(g, edited_head)) == [
        Literal("A"),
        Literal("Z"),
        Literal("C"),
        Literal("D"),
    ]
    assert list(Collection(g, second_head)) == [
        Literal("E"),
        Literal("B"),
        Literal("C"),
        Literal("D"),
    ]
    assert list(Collection(g, first_head)) == [
        Literal("A"),
        Literal("B"),
        Literal("C"),
        Literal("D"),
    ]
    assert (external, EX.refersTo, first_head) in g
    assert (first_head, note, Literal("business property")) in g
    assert (edited_head, note, Literal("business property")) in g
    assert (shared_b, EX.note, Literal("shared business property")) in g
    assert (copied_b, EX.note, Literal("shared business property")) in g
    assert g.value(edited_head, RDF.rest) != shared_b
    assert g.value(copied_b, RDF.rest) == shared_c


def test_replace_range_keeps_prefix_identity_and_side_properties() -> None:
    g = Graph()
    head, second, removed, tail = BNode("a"), BNode("e"), BNode("r"), BNode("t")

    g.add((head, RDF.first, Literal("A")))
    g.add((head, RDF.rest, second))
    g.add((second, RDF.first, Literal("B")))
    g.add((second, RDF.rest, removed))
    g.add((removed, RDF.first, Literal("C")))
    g.add((removed, RDF.rest, tail))
    g.add((tail, RDF.first, Literal("D")))
    g.add((tail, RDF.rest, RDF.nil))

    other_head = BNode("other")
    g.add((other_head, RDF.first, Literal("X")))
    g.add((other_head, RDF.rest, tail))
    g.add((removed, EX.note, Literal("keep me")))

    result = replace_range(g, head, 2, 3, [Literal("Z")])

    assert result == head
    assert g.value(head, RDF.rest) == second
    assert list(Collection(g, head)) == [
        Literal("A"),
        Literal("B"),
        Literal("Z"),
        Literal("D"),
    ]
    assert list(Collection(g, other_head)) == [Literal("X"), Literal("D")]
    assert (removed, EX.note, Literal("keep me")) in g
    assert g.value(removed, RDF.first) == Literal("Z")
    assert g.value(removed, RDF.rest) == tail


def test_replace_range_empty_full_delete_and_head() -> None:
    g = Graph()
    replacement_head = replace_range(g, RDF.nil, 0, 0, [Literal("A")])
    assert list(Collection(g, replacement_head)) == [Literal("A")]
    assert not list(g.triples((RDF.nil, None, None)))

    head, tail = BNode("head"), BNode("tail")
    g.add((head, RDF.first, Literal("old")))
    g.add((head, RDF.rest, tail))
    g.add((tail, RDF.first, Literal("tail")))
    g.add((tail, RDF.rest, RDF.nil))
    g.add((EX.head_ref, EX.refersTo, head))
    g.add((head, EX.note, Literal("keep head note")))

    changed_head = replace_range(g, head, 0, 1, [Literal("new")])
    assert changed_head != head
    assert list(Collection(g, changed_head)) == [Literal("new"), Literal("tail")]
    assert list(Collection(g, head)) == [Literal("old"), Literal("tail")]
    assert (head, EX.note, Literal("keep head note")) in g

    deleted_head = BNode("deleted")
    g.add((deleted_head, RDF.first, Literal("gone")))
    g.add((deleted_head, RDF.rest, RDF.nil))
    g.add((deleted_head, EX.note, Literal("still here")))

    assert replace_range(g, deleted_head, 0, 1, []) == RDF.nil
    assert (deleted_head, EX.note, Literal("still here")) in g


def test_replace_range_copies_referenced_range_node() -> None:
    g = Graph()
    head, target, tail = BNode("head"), BNode("target"), BNode("tail")

    g.add((head, RDF.first, Literal("A")))
    g.add((head, RDF.rest, target))
    g.add((target, RDF.first, Literal("B")))
    g.add((target, RDF.rest, tail))
    g.add((tail, RDF.first, Literal("C")))
    g.add((tail, RDF.rest, RDF.nil))
    g.add((EX.ref, EX.refersTo, target))
    g.add((target, EX.note, Literal("preserve old target")))

    result = replace_range(g, head, 1, 2, [Literal("Z")])

    assert result == head
    new_target = g.value(head, RDF.rest)
    assert new_target != target
    assert list(Collection(g, result)) == [Literal("A"), Literal("Z"), Literal("C")]
    assert list(Collection(g, target)) == [Literal("B"), Literal("C")]
    assert (target, EX.note, Literal("preserve old target")) in g
    assert (EX.ref, EX.refersTo, target) in g
    assert (new_target, EX.note, Literal("preserve old target")) in g


def test_collection_replace_range_updates_collection_head() -> None:
    g = Graph()
    head, tail = BNode("head"), BNode("tail")
    g.add((head, RDF.first, Literal("A")))
    g.add((head, RDF.rest, tail))
    g.add((tail, RDF.first, Literal("B")))
    g.add((tail, RDF.rest, RDF.nil))

    collection = Collection(g, head)
    result = collection.replace_range(0, 2, [Literal("C")])

    assert collection.uri == result
    assert list(collection) == [Literal("C")]


@pytest.mark.parametrize(
    "build_bad_graph",
    [
        lambda g, a, b: (
            g.add((a, RDF.first, Literal("A"))),
            g.add((a, RDF.rest, b)),
            g.add((b, RDF.first, Literal("B"))),
            g.add((b, RDF.rest, a)),
        ),
        lambda g, a, b: (
            g.add((a, RDF.first, Literal("A"))),
            g.add((a, RDF.rest, b)),
            g.add((a, RDF.rest, RDF.nil)),
            g.add((b, RDF.first, Literal("B"))),
            g.add((b, RDF.rest, RDF.nil)),
        ),
        lambda g, a, b: (
            g.add((a, RDF.first, Literal("A"))),
            g.add((a, RDF.rest, b)),
            g.add((b, RDF.rest, RDF.nil)),
        ),
        lambda g, a, b: (
            g.add((a, RDF.first, Literal("A"))),
            g.add((a, RDF.rest, b)),
            g.add((b, RDF.first, Literal("B"))),
        ),
        lambda g, a, b: (
            g.add((RDF.nil, RDF.first, Literal("A"))),
            g.add((a, RDF.first, Literal("A"))),
            g.add((a, RDF.rest, RDF.nil)),
        ),
    ],
)
def test_replace_range_bad_collection_leaves_graph_unchanged(
    build_bad_graph,
) -> None:
    g = Graph()
    head, second = BNode("head"), BNode("second")
    build_bad_graph(g, head, second)
    original = set(g)

    with pytest.raises(MalformedCollectionError):
        replace_range(g, head, 0, 1, [Literal("Z")])

    assert set(g) == original
