"""Tests for source-attribution aware dataset comparison."""

from __future__ import annotations

import pytest

from rdflib import BNode, Dataset, Literal, URIRef
from rdflib.namespace import FOAF, RDF, XSD
from rdflib.dataset_diff import (
    DEFAULT_GRAPH_SOURCE,
    AttributionChange,
    DatasetAttributionDiff,
    dataset_diff,
    validate_bnode_map,
)

EX = "http://example.org/"
ALICE = URIRef(EX + "alice")
BOB = URIRef(EX + "bob")
SRC1 = URIRef(EX + "source-1")
SRC2 = URIRef(EX + "source-2")
SRC3 = URIRef(EX + "source-3")
NAME = (ALICE, FOAF.name, Literal("Alice"))
AGE = (ALICE, FOAF.age, Literal(42, datatype=XSD.integer))
KNOWS = (ALICE, FOAF.knows, BOB)


def _add(dataset: Dataset, triple, *sources):
    graphs = []
    for source in sources:
        if source is None:
            graphs.append(dataset.default_graph)
        else:
            graphs.append(dataset.graph(source))
    dataset.addN([(*triple, graph) for graph in graphs])


def test_shared_then_each_removes_same_fact():
    """The headline scenario: prove attribution, not union size.

    The same fact is asserted by two named graphs in both versions: the
    two graphs first share it, then each graph drops it in turn. The union
    graph loses nothing until the very last step, so comparing union graph
    sizes cannot detect the intermediate source-set change.
    """
    before = Dataset()
    middle = Dataset()
    after = Dataset()

    _add(before, NAME, SRC1, SRC2)
    _add(middle, NAME, SRC2)
    _add(after, NAME)

    # Both before and middle contain exactly one distinct triple in their
    # union graphs, so a size-based comparison would call them identical.
    union_before = {(s, p, o) for s, p, o, _ in before.quads((None, None, None, None))}
    union_middle = {(s, p, o) for s, p, o, _ in middle.quads((None, None, None, None))}
    assert len(union_before) == 1
    assert len(union_middle) == 1

    first = dataset_diff(before, middle)
    assert first.summary() == {"added": 0, "removed": 0, "changed": 1, "unchanged": 0}
    (change,) = first.changed
    assert change.kind == "changed"
    assert change.triple == NAME
    assert change.sources_before == frozenset({SRC1, SRC2})
    assert change.sources_after == frozenset({SRC2})
    assert change.sources_added == frozenset()
    assert change.sources_removed == frozenset({SRC1})

    second = dataset_diff(middle, after)
    (removed,) = second.removed
    assert removed.triple == NAME
    assert removed.sources_before == frozenset({SRC2})
    assert removed.sources_after == frozenset()

    full = dataset_diff(before, after)
    assert [entry.triple for entry in full.removed] == [NAME]
    assert full.changed == []
    assert full.added == []


def test_added_triple_with_multiple_sources():
    before = Dataset()
    after = Dataset()
    _add(after, NAME, SRC1, SRC2)

    report = dataset_diff(before, after)
    (entry,) = report.added
    assert entry.kind == "added"
    assert entry.triple == NAME
    assert entry.sources_before == frozenset()
    assert entry.sources_after == frozenset({SRC1, SRC2})
    assert entry.triple_before is None
    assert entry.triple_after == NAME


def test_source_swap_same_triple_is_changed_not_removed_and_added():
    """Value unchanged, supporting source changed: one 'changed' entry."""
    before = Dataset()
    after = Dataset()
    _add(before, KNOWS, SRC1)
    _add(after, KNOWS, SRC2)

    report = dataset_diff(before, after)
    assert report.added == []
    assert report.removed == []
    (change,) = report.changed
    assert change.triple == KNOWS
    assert change.sources_before == frozenset({SRC1})
    assert change.sources_after == frozenset({SRC2})
    assert change.sources_added == frozenset({SRC2})
    assert change.sources_removed == frozenset({SRC1})


def test_default_graph_has_independent_source_marker():
    before = Dataset()
    after = Dataset()
    _add(before, NAME, None)
    _add(after, NAME, SRC1)

    report = dataset_diff(before, after)
    (change,) = report.changed
    assert DEFAULT_GRAPH_SOURCE in change.sources_before
    assert SRC1 not in change.sources_before
    assert change.sources_after == frozenset({SRC1})
    # the default-graph marker is a distinct node, not the named graph
    assert DEFAULT_GRAPH_SOURCE != SRC1

    # a triple asserted in the default graph on both sides is unchanged
    both = Dataset()
    _add(both, NAME, None)
    report_same = dataset_diff(before, both, include_unchanged=True)
    (entry,) = report_same.unchanged
    assert entry.sources_before == frozenset({DEFAULT_GRAPH_SOURCE})
    assert entry.sources_after == frozenset({DEFAULT_GRAPH_SOURCE})


def test_default_graph_plus_named_graph_sharing_a_fact():
    before = Dataset()
    after = Dataset()
    _add(before, NAME, None, SRC1)
    _add(after, NAME, None, SRC2)

    report = dataset_diff(before, after)
    (change,) = report.changed
    assert change.sources_before == frozenset({DEFAULT_GRAPH_SOURCE, SRC1})
    assert change.sources_after == frozenset({DEFAULT_GRAPH_SOURCE, SRC2})
    assert change.sources_removed == frozenset({SRC1})
    assert change.sources_added == frozenset({SRC2})


def test_term_types_are_preserved():
    before = Dataset()
    after = Dataset()
    _add(before, AGE, SRC1)
    _add(after, AGE, SRC1)
    _add(after, (ALICE, RDF.type, FOAF.Person), SRC2)

    report = dataset_diff(before, after, include_unchanged=True)
    (unchanged,) = report.unchanged
    subject, predicate, obj = unchanged.triple
    assert type(subject) is URIRef
    assert type(predicate) is URIRef
    assert type(obj) is Literal
    assert obj.datatype == XSD.integer
    assert obj.value == 42

    (added,) = report.added
    assert isinstance(added.triple[2], URIRef)


def test_filter_by_predicate():
    before = Dataset()
    after = Dataset()
    _add(before, NAME, SRC1)
    _add(before, AGE, SRC1)
    _add(after, NAME, SRC2)
    _add(after, AGE, SRC1)

    by_name = dataset_diff(before, after, predicates=FOAF.name)
    assert [entry.triple for entry in by_name.changed] == [NAME]
    assert by_name.removed == [] and by_name.added == []

    by_age = dataset_diff(before, after, predicates={FOAF.age})
    assert by_age.changed == [] and by_age.added == [] and by_age.removed == []

    both = dataset_diff(before, after, predicates=(FOAF.name, FOAF.age))
    assert len(both.changed) == 1


def test_filter_by_source_keeps_full_source_set():
    before = Dataset()
    after = Dataset()
    _add(before, NAME, SRC1, SRC2)
    _add(after, NAME, SRC2, SRC3)
    _add(before, AGE, SRC3)
    _add(after, AGE, SRC3)

    # filtering on SRC1 surfaces the triple, but the report still shows all
    # supporting sources on both sides
    report = dataset_diff(before, after, sources=SRC1)
    (change,) = report.changed
    assert change.triple == NAME
    assert change.sources_before == frozenset({SRC1, SRC2})
    assert change.sources_after == frozenset({SRC2, SRC3})

    # AGE never involves SRC1 and is therefore excluded
    assert all(entry.triple != AGE for entry in report)

    # the default graph marker (or None inside a collection) selects the
    # default graph; sources=None alone means unfiltered
    default_before = Dataset()
    default_after = Dataset()
    _add(default_before, NAME, None)
    _add(default_after, NAME, SRC1)
    default_report = dataset_diff(
        default_before, default_after, sources={DEFAULT_GRAPH_SOURCE}
    )
    assert len(default_report.changed) == 1
    default_report_none = dataset_diff(
        default_before, default_after, sources=(None,)
    )
    assert len(default_report_none.changed) == 1


def test_unmapped_blank_nodes_are_version_isolated():
    before = Dataset()
    after = Dataset()
    b_before = BNode("shared-label")
    b_after = BNode("shared-label")
    t_before = (ALICE, FOAF.knows, b_before)
    t_after = (ALICE, FOAF.knows, b_after)
    _add(before, t_before, SRC1)
    _add(after, t_after, SRC1)

    # without a map, identical local labels must NOT line up: one removed,
    # one added — no guessed isomorphism
    report = dataset_diff(before, after)
    assert [entry.triple for entry in report.removed] == [t_before]
    assert [entry.triple for entry in report.added] == [t_after]
    assert report.changed == []
    # term identity/type is preserved on the original triples
    assert report.removed[0].triple is not report.added[0].triple
    assert isinstance(report.removed[0].triple[2], BNode)


def test_mapped_blank_nodes_line_up():
    before = Dataset()
    after = Dataset()
    b_before = BNode("b1")
    b_after = BNode("x9")
    t_before = (ALICE, FOAF.knows, b_before)
    t_after = (ALICE, FOAF.knows, b_after)
    _add(before, t_before, SRC1)
    _add(after, t_after, SRC2)

    report = dataset_diff(before, after, bnode_map={b_before: b_after})
    assert report.added == [] and report.removed == []
    (change,) = report.changed
    # the aligned triple uses the after version's blank node
    assert change.triple == t_after
    assert isinstance(change.triple[2], BNode)
    assert change.sources_before == frozenset({SRC1})
    assert change.sources_after == frozenset({SRC2})
    assert change.triple_before == t_before
    assert change.triple_after == t_after


def test_blank_node_graph_names_follow_mapping():
    before = Dataset()
    after = Dataset()
    g_before = BNode("g1")
    g_after = BNode("g2")
    _add(before, NAME, g_before)
    _add(after, NAME, g_after)

    mapped = dataset_diff(
        before, after, bnode_map={g_before: g_after}, include_unchanged=True
    )
    (entry,) = list(mapped)
    assert entry.kind == "unchanged"
    assert entry.sources_before == frozenset({g_after})
    assert entry.sources_after == frozenset({g_after})

    # without a map the triple itself still exists on both sides, but the
    # blank-node-named source graph is not aligned: source-set change
    isolated = dataset_diff(before, after)
    (entry,) = list(isolated)
    assert entry.kind == "changed"
    assert entry.sources_before != entry.sources_after
    assert entry.triple_before == NAME
    assert entry.triple_after == NAME
    assert entry.sources_removed == entry.sources_before
    assert entry.sources_added == entry.sources_after


def test_duplicate_bnode_map_target_is_rejected():
    b1, b2, target = BNode(), BNode(), BNode()
    with pytest.raises(ValueError, match="one-to-one"):
        validate_bnode_map({b1: target, b2: target})
    with pytest.raises(ValueError, match="one-to-one"):
        dataset_diff(
            Dataset(),
            Dataset(),
            bnode_map={b1: target, b2: target},
        )


def test_mapping_to_non_bnode_is_rejected():
    b = BNode()
    with pytest.raises(TypeError, match="non-blank"):
        validate_bnode_map({b: ALICE})
    with pytest.raises(TypeError, match="non-blank"):
        validate_bnode_map({b: Literal("x")})


def test_mapping_from_non_bnode_is_rejected():
    target = BNode()
    with pytest.raises(TypeError, match="keys must be BNode"):
        validate_bnode_map({ALICE: target})


def test_validate_bnode_map_returns_copy_and_accepts_empty():
    assert validate_bnode_map(None) == {}
    assert validate_bnode_map({}) == {}
    source, target = BNode(), BNode()
    result = validate_bnode_map({source: target})
    assert result == {source: target}
    result[source] = BNode()
    # mutating the returned dict does not corrupt a re-validation
    assert validate_bnode_map({source: target}) == {source: target}


def test_report_is_dataclass_and_iterable_with_deterministic_order():
    before = Dataset()
    after = Dataset()
    _add(before, NAME, SRC1)
    _add(before, AGE, SRC1)
    _add(after, KNOWS, SRC1)

    report = dataset_diff(before, after)
    assert isinstance(report, DatasetAttributionDiff)
    assert all(isinstance(entry, AttributionChange) for entry in report)
    kinds = [entry.kind for entry in report]
    assert kinds.count("removed") == 2
    assert kinds.count("added") == 1

    again = dataset_diff(before, after)
    assert [entry.triple for entry in again.removed] == [
        entry.triple for entry in report.removed
    ]
