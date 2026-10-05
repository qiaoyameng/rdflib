import pytest

from rdflib import BNode, Dataset, Literal, URIRef
from rdflib.dataset_diff import (
    DEFAULT_GRAPH_SOURCE,
    DatasetDiff,
    dataset_diff,
)

G1 = URIRef("urn:example:g1")
G2 = URIRef("urn:example:g2")
S = URIRef("urn:example:s")
P = URIRef("urn:example:p")
O = URIRef("urn:example:o")
P2 = URIRef("urn:example:p2")
FACT = (S, P, O)


def make_dataset(quads):
    dataset = Dataset()
    for triple, graph_id in quads:
        if graph_id is None:
            dataset.default_graph.add(triple)
        else:
            dataset.graph(graph_id).add(triple)
    return dataset


def test_shared_fact_removed_from_one_graph_is_a_source_change():
    before = make_dataset([(FACT, G1), (FACT, G2)])
    after = make_dataset([(FACT, G2)])

    # The union graphs of both versions contain exactly one triple, so a
    # plain triple-set comparison cannot see this change at all.
    def union_triples(dataset):
        return {(s, p, o) for s, p, o, _ in dataset.quads()}

    assert len(union_triples(before)) == len(union_triples(after)) == 1

    diff = dataset_diff(before, after)
    assert diff.added == ()
    assert diff.removed == ()
    assert len(diff.source_changes) == 1

    change = diff.source_changes[0]
    assert change.before_triple == FACT
    assert change.after_triple == FACT
    assert change.before_sources == frozenset({G1, G2})
    assert change.after_sources == frozenset({G2})
    assert change.removed_sources == frozenset({G1})
    assert change.added_sources == frozenset()


def test_shared_fact_removed_from_both_graphs_is_removed():
    before = make_dataset([(FACT, G1), (FACT, G2)])
    middle = make_dataset([(FACT, G2)])
    after = make_dataset([])

    second_step = dataset_diff(middle, after)
    assert second_step.source_changes == ()
    assert second_step.added == ()
    assert len(second_step.removed) == 1
    assert second_step.removed[0].triple == FACT
    assert second_step.removed[0].sources == frozenset({G2})

    whole_process = dataset_diff(before, after)
    assert len(whole_process.removed) == 1
    assert whole_process.removed[0].sources == frozenset({G1, G2})


def test_added_triple_reports_its_sources():
    before = make_dataset([])
    after = make_dataset([(FACT, G1), (FACT, G2)])

    diff = dataset_diff(before, after)
    assert diff.removed == ()
    assert diff.source_changes == ()
    assert len(diff.added) == 1
    assert diff.added[0].triple == FACT
    assert diff.added[0].sources == frozenset({G1, G2})


def test_identical_datasets_produce_empty_diff():
    before = make_dataset([(FACT, G1), ((S, P2, Literal("x")), None)])
    after = make_dataset([(FACT, G1), ((S, P2, Literal("x")), None)])

    diff = dataset_diff(before, after)
    assert diff.added == ()
    assert diff.removed == ()
    assert diff.source_changes == ()
    assert not diff


def test_default_graph_has_independent_source_marker():
    before = make_dataset([(FACT, None), (FACT, G1)])
    after = make_dataset([(FACT, G1)])

    diff = dataset_diff(before, after)
    assert len(diff.source_changes) == 1
    change = diff.source_changes[0]
    assert change.before_sources == frozenset({DEFAULT_GRAPH_SOURCE, G1})
    assert change.after_sources == frozenset({G1})
    assert change.removed_sources == frozenset({DEFAULT_GRAPH_SOURCE})


def test_term_types_are_preserved():
    literal_fact = (S, P, Literal("value"))
    before = make_dataset([])
    after = make_dataset([(FACT, G1), (literal_fact, None)])

    diff = dataset_diff(before, after)
    assert len(diff.added) == 2
    by_predicate = {entry.triple[1]: entry for entry in diff.added}
    entry = by_predicate[P]
    assert isinstance(entry.triple[0], URIRef)
    assert isinstance(entry.triple[2], (URIRef, Literal))
    literal_entry = next(e for e in diff.added if isinstance(e.triple[2], Literal))
    assert literal_entry.triple[2] == Literal("value")
    assert literal_entry.sources == frozenset({DEFAULT_GRAPH_SOURCE})


def test_unmapped_bnodes_are_version_isolated():
    before_bnode = BNode()
    after_bnode = BNode()
    before = make_dataset([((before_bnode, P, O), G1)])
    after = make_dataset([((after_bnode, P, O), G1)])

    diff = dataset_diff(before, after)
    assert diff.source_changes == ()
    assert len(diff.removed) == 1
    assert len(diff.added) == 1
    assert diff.removed[0].triple == (before_bnode, P, O)
    assert diff.added[0].triple == (after_bnode, P, O)


def test_bnode_mapping_aligns_versions():
    before_bnode = BNode()
    after_bnode = BNode()
    before = make_dataset([((before_bnode, P, O), G1)])
    after = make_dataset([((after_bnode, P, O), G1)])

    diff = dataset_diff(before, after, bnode_mapping={before_bnode: after_bnode})
    assert diff.added == ()
    assert diff.removed == ()
    assert diff.source_changes == ()


def test_bnode_mapping_reports_source_change_for_mapped_nodes():
    before_bnode = BNode()
    after_bnode = BNode()
    before = make_dataset([((before_bnode, P, O), G1)])
    after = make_dataset([((after_bnode, P, O), G2)])

    diff = dataset_diff(before, after, bnode_mapping={before_bnode: after_bnode})
    assert diff.added == ()
    assert diff.removed == ()
    assert len(diff.source_changes) == 1
    change = diff.source_changes[0]
    assert change.before_triple == (before_bnode, P, O)
    assert change.after_triple == (after_bnode, P, O)
    assert change.before_sources == frozenset({G1})
    assert change.after_sources == frozenset({G2})


def test_bnode_mapping_rejects_non_bnode_key():
    with pytest.raises(ValueError, match="keys must be blank nodes"):
        dataset_diff(Dataset(), Dataset(), bnode_mapping={S: BNode()})


def test_bnode_mapping_rejects_non_bnode_target():
    with pytest.raises(ValueError, match="values must be blank nodes"):
        dataset_diff(Dataset(), Dataset(), bnode_mapping={BNode(): S})


def test_bnode_mapping_rejects_duplicate_targets():
    target = BNode()
    with pytest.raises(ValueError, match="duplicate target"):
        dataset_diff(
            Dataset(),
            Dataset(),
            bnode_mapping={BNode(): target, BNode(): target},
        )


@pytest.fixture()
def rich_diff() -> DatasetDiff:
    before = make_dataset([(FACT, G1), (FACT, G2), ((S, P2, O), G2)])
    after = make_dataset([(FACT, G2), ((S, P2, O), G1), ((O, P2, S), G1)])
    return dataset_diff(before, after)


def test_filter_by_source(rich_diff: DatasetDiff):
    only_g1 = rich_diff.by_source(G1)
    assert len(only_g1.source_changes) == 2
    assert len(only_g1.added) == 1

    only_g2 = rich_diff.by_source(G2)
    assert len(only_g2.source_changes) == 2
    assert only_g2.added == ()


def test_filter_by_predicate(rich_diff: DatasetDiff):
    only_p = rich_diff.by_predicate(P)
    assert len(only_p.source_changes) == 1
    assert only_p.source_changes[0].before_triple == FACT
    assert only_p.added == ()

    only_p2 = rich_diff.by_predicate(P2)
    assert len(only_p2.source_changes) == 1
    assert len(only_p2.added) == 1


def test_filter_via_constructor_arguments():
    before = make_dataset([(FACT, G1), ((S, P2, O), G1)])
    after = make_dataset([])

    diff = dataset_diff(before, after, sources={G1}, predicates={P2})
    assert len(diff.removed) == 1
    assert diff.removed[0].triple == (S, P2, O)

    empty = dataset_diff(before, after, sources={G2})
    assert empty.removed == ()
