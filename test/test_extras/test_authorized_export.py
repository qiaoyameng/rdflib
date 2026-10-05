from __future__ import annotations

from rdflib import BNode, Dataset, Literal, Namespace
from rdflib.compare import isomorphic
from rdflib.extras.authorized_export import (
    ExportAuthorization,
    extract_authorized_view,
)

EX = Namespace("http://example.org/")
FOAF = Namespace("http://xmlns.com/foaf/0.1/")

PUBLIC = EX["graph/public"]
PUBLIC2 = EX["graph/public2"]
PRIVATE = EX["graph/private"]


def build_dataset() -> Dataset:
    """A dataset that deliberately hangs public and private facts off the
    same blank node."""
    ds = Dataset()
    ds.bind("ex", EX)
    ds.bind("foaf", FOAF)

    profile = BNode("profile")
    address = BNode("address")
    shared = BNode("shared")
    loop_a = BNode("loopA")
    loop_b = BNode("loopB")
    dangling = BNode("dangling")
    hidden = BNode("hidden")

    # Default graph: only visible when explicitly enabled.
    ds.default_context.add((EX.alice, EX.internalId, Literal("A-001")))
    ds.default_context.add((EX.alice, EX.publicProfile, profile))

    public = ds.graph(PUBLIC)
    # Public description of the profile blank node.
    public.add((profile, FOAF.name, Literal("Alice")))
    public.add((profile, FOAF.knows, EX.bob))  # bob has no description anywhere
    public.add((profile, EX.address, address))
    public.add((address, EX.city, Literal("Paris")))
    # An edge to a blank node whose only description lives in the private
    # graph: the edge is visible, the description must not leak.
    public.add((address, EX.previousAddress, dangling))
    # A cycle reachable through authorized edges.
    public.add((address, EX.route, loop_a))
    public.add((loop_a, EX.next, loop_b))
    public.add((loop_b, EX.next, loop_a))
    # A predicate that is not allowed: neither the triple nor its object
    # may appear.
    public.add((profile, EX.secretNote, hidden))
    public.add((hidden, EX.secretDetail, Literal("hidden")))
    # Two visible edges sharing one blank node.
    public.add((profile, EX.friend, shared))
    public.add((address, EX.resident, shared))
    public.add((shared, FOAF.name, Literal("Shared Friend")))

    # The same blank node reused in a second readable graph.
    public2 = ds.graph(PUBLIC2)
    public2.add((shared, EX.nick, Literal("sharpy")))

    # Private graph: private facts about the *same* blank nodes.
    private = ds.graph(PRIVATE)
    private.add((profile, EX.ssn, Literal("123-45-6789")))
    private.add((profile, EX.salary, Literal("90000")))
    private.add((dangling, EX.city, Literal("Secret City")))
    private.add((shared, EX.debt, Literal("5000")))

    return ds


def make_authorization(**overrides) -> ExportAuthorization:
    params = {
        "start_resources": frozenset({EX.alice}),
        "allowed_predicates": frozenset(
            {
                EX.publicProfile,
                FOAF.name,
                FOAF.knows,
                EX.address,
                EX.city,
                EX.previousAddress,
                EX.route,
                EX.next,
                EX.friend,
                EX.resident,
                EX.nick,
                EX.internalId,
            }
        ),
        "readable_graphs": frozenset({PUBLIC, PUBLIC2}),
        "include_default_graph": True,
    }
    params.update(overrides)
    return ExportAuthorization(**params)


def all_objects(ds: Dataset):
    return {o for _, _, o, _ in ds.quads((None, None, None, None))}


def graph_identifier(context):
    return getattr(context, "identifier", context)


def assert_datasets_isomorphic(left: Dataset, right: Dataset) -> None:
    def named_graphs(ds):
        return {
            graph.identifier: graph
            for graph in ds.graphs()
            if graph.identifier != ds.default_graph.identifier
        }

    left_named, right_named = named_graphs(left), named_graphs(right)
    assert set(left_named) == set(right_named)
    for identifier, graph in left_named.items():
        assert isomorphic(graph, right_named[identifier])
    assert isomorphic(left.default_graph, right.default_graph)


def test_export_contains_authorized_structure():
    ds = build_dataset()
    out = extract_authorized_view(ds, make_authorization())
    triples = {(s, p, o) for s, p, o, _ in out.quads((None, None, None, None))}

    assert (EX.alice, EX.publicProfile, None) not in triples  # sanity on shape
    subjects = {s for s, _, _ in triples}
    assert EX.alice in subjects
    # Blank node chain was expanded.
    assert any(p == EX.address for _, p, _ in triples)
    assert any(p == EX.city and o == Literal("Paris") for _, p, o in triples)
    assert any(p == FOAF.name and o == Literal("Alice") for _, p, o in triples)
    assert any(p == EX.nick and o == Literal("sharpy") for _, p, o in triples)


def test_private_data_on_same_bnode_does_not_leak():
    ds = build_dataset()
    out = extract_authorized_view(ds, make_authorization())
    objects = all_objects(out)
    predicates = {p for _, p, _, _ in out.quads((None, None, None, None))}

    assert Literal("123-45-6789") not in objects
    assert Literal("90000") not in objects
    assert Literal("5000") not in objects
    assert Literal("Secret City") not in objects
    assert EX.ssn not in predicates
    assert EX.salary not in predicates
    assert EX.debt not in predicates

    serialized = out.serialize(format="trig")
    for secret in ("123-45-6789", "90000", "5000", "Secret City", "ssn", "salary"):
        assert secret not in serialized


def test_unauthorized_predicate_stops_traversal():
    ds = build_dataset()
    out = extract_authorized_view(ds, make_authorization())
    objects = all_objects(out)
    assert Literal("hidden") not in objects
    assert EX.secretNote not in {
        p for _, p, _, _ in out.quads((None, None, None, None))
    }
    assert EX.secretDetail not in {
        p for _, p, _, _ in out.quads((None, None, None, None))
    }


def test_cycle_terminates_and_is_preserved():
    ds = build_dataset()
    out = extract_authorized_view(ds, make_authorization())
    triples = {(s, p, o) for s, p, o, _ in out.quads((None, None, None, None))}
    next_edges = [(s, o) for s, p, o in triples if p == EX.next]
    assert len(next_edges) == 2
    (s1, o1), (s2, o2) = next_edges
    # The two loop nodes point at each other.
    assert {s1, o1} == {s2, o2}
    assert s1 != o1


def test_shared_bnode_keeps_single_identity_after_relabeling():
    ds = build_dataset()
    out = extract_authorized_view(ds, make_authorization())
    triples = {(s, p, o) for s, p, o, _ in out.quads((None, None, None, None))}

    friend_targets = {o for _, p, o in triples if p == EX.friend}
    resident_targets = {o for _, p, o in triples if p == EX.resident}
    assert len(friend_targets) == 1
    assert friend_targets == resident_targets

    shared = friend_targets.pop()
    # Both readable graphs' descriptions attach to that one node.
    shared_props = {(p, o) for s, p, o in triples if s == shared}
    assert (FOAF.name, Literal("Shared Friend")) in shared_props
    assert (EX.nick, Literal("sharpy")) in shared_props


def test_bnode_ids_are_reassigned():
    ds = build_dataset()
    source_ids = {str(t) for t in ds.all_nodes() if isinstance(t, BNode)}
    out = extract_authorized_view(ds, make_authorization())
    out_ids = {str(t) for t in out.all_nodes() if isinstance(t, BNode)}
    assert out_ids, "expected blank nodes in the export"
    assert source_ids.isdisjoint(out_ids)


def test_edge_to_node_without_readable_description_is_kept():
    ds = build_dataset()
    out = extract_authorized_view(ds, make_authorization())
    triples = {(s, p, o) for s, p, o, _ in out.quads((None, None, None, None))}

    # URI object with no description anywhere: edge kept, node not expanded.
    assert any(p == FOAF.knows and o == EX.bob for _, p, o in triples)
    assert not [t for t in triples if t[0] == EX.bob]

    # Blank node whose description exists only in the private graph:
    # the edge is kept but nothing is filled in from the private graph.
    dangling = {o for _, p, o in triples if p == EX.previousAddress}
    assert len(dangling) == 1
    node = dangling.pop()
    assert isinstance(node, BNode)
    assert not [t for t in triples if t[0] == node]


def test_default_graph_visibility_flag():
    ds = build_dataset()

    hidden_default = extract_authorized_view(
        ds, make_authorization(include_default_graph=False)
    )
    assert Literal("A-001") not in all_objects(hidden_default)
    # The entry edge lives in the default graph, so nothing else is reachable.
    assert len(hidden_default) == 0

    visible_default = extract_authorized_view(
        ds, make_authorization(include_default_graph=True)
    )
    assert Literal("A-001") in all_objects(visible_default)
    quads = list(visible_default.quads((None, None, None, None)))
    internal = [c for s, p, o, c in quads if p == EX.internalId]
    assert len(internal) == 1
    assert graph_identifier(internal[0]) == visible_default.default_graph.identifier


def test_unreadable_graph_never_consulted_even_for_same_subject():
    ds = build_dataset()
    # Only the default graph is visible: the entry edge is exported, but
    # the profile blank node's descriptions (public and private alike)
    # stay unreachable.
    out = extract_authorized_view(
        ds,
        make_authorization(readable_graphs=frozenset()),
    )
    objects = all_objects(out)
    assert Literal("A-001") in objects
    assert Literal("Alice") not in objects
    assert Literal("123-45-6789") not in objects


def test_serialize_and_reread_preserves_visible_structure():
    ds = build_dataset()
    out = extract_authorized_view(ds, make_authorization())

    trig = out.serialize(format="trig")
    reread = Dataset()
    reread.parse(data=trig, format="trig")

    assert_datasets_isomorphic(out, reread)
    # And the leak boundary survives the round trip.
    for secret in ("123-45-6789", "90000", "Secret City"):
        assert secret not in trig


def test_deterministic_result_across_runs():
    ds = build_dataset()
    auth = make_authorization()
    first = extract_authorized_view(ds, auth)
    second = extract_authorized_view(ds, auth)
    assert_datasets_isomorphic(first, second)
