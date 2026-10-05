from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Optional

from rdflib.namespace import RDF
from rdflib.term import BNode, Node, URIRef

if TYPE_CHECKING:
    from rdflib.graph import Graph

__all__ = ["Collection", "MalformedCollectionError", "replace_range"]


class MalformedCollectionError(ValueError):
    """Raised when an RDF collection is not a well-formed linked list."""


def _ensure_nil_is_terminal(graph: Graph) -> None:
    has_nil_first = next(graph.triples((RDF.nil, RDF.first, None)), None)
    has_nil_rest = next(graph.triples((RDF.nil, RDF.rest, None)), None)
    if has_nil_first is not None or has_nil_rest is not None:
        raise MalformedCollectionError("rdf:nil must not be used as a list node")


def _read_collection(graph: Graph, head: Node) -> list[tuple[Node, Node, Node]]:
    if not isinstance(head, Node):
        raise TypeError("list head must be an rdflib Node")

    if head == RDF.nil:
        _ensure_nil_is_terminal(graph)
        return []

    if not isinstance(head, (BNode, URIRef)):
        raise MalformedCollectionError(
            "RDF collection nodes must be BNodes or URIRefs"
        )

    chain: list[tuple[Node, Node, Node]] = []
    seen: set[Node] = set()
    current: Node = head

    while current != RDF.nil:
        if current in seen:
            raise MalformedCollectionError("RDF collection contains a cycle")
        seen.add(current)

        first_values = set(graph.objects(current, RDF.first))
        rest_values = set(graph.objects(current, RDF.rest))
        if len(first_values) != 1 or len(rest_values) != 1:
            raise MalformedCollectionError(
                "RDF collection node must have exactly one rdf:first and rdf:rest"
            )

        first = next(iter(first_values))
        rest = next(iter(rest_values))
        if not isinstance(rest, (BNode, URIRef)):
            raise MalformedCollectionError("rdf:rest must identify another list node")

        chain.append((current, first, rest))
        current = rest

    _ensure_nil_is_terminal(graph)
    return chain


def _has_external_reference(
    graph: Graph, node: Node, predecessor: Optional[Node]
) -> bool:
    for subject, predicate, _ in graph.triples((None, None, node)):
        if predicate != RDF.rest or predecessor is None or subject != predecessor:
            return True
    return False


def _copy_non_collection_properties(
    graph: Graph, old_node: Node, new_node: Node
) -> None:
    for _, predicate, object_ in graph.triples((old_node, None, None)):
        if predicate not in (RDF.first, RDF.rest):
            graph.add((new_node, predicate, object_))


def replace_range(
    graph: Graph,
    head: Node,
    start: int,
    stop: int,
    replacement: Iterable[Node],
) -> Node:
    """Replace the half-open range ``[start, stop)`` in an RDF collection.

    The original linked-list structure is left intact wherever another list or
    another triple references a node that must change. Unreachable list cells
    have only their ``rdf:first`` and ``rdf:rest`` triples removed, so unrelated
    business properties are preserved.
    """
    if not isinstance(start, int) or isinstance(start, bool):
        raise TypeError("start must be an integer")
    if not isinstance(stop, int) or isinstance(stop, bool):
        raise TypeError("stop must be an integer")
    if start < 0 or stop < 0 or start > stop:
        raise ValueError("expected 0 <= start <= stop")

    values = tuple(replacement)
    for value in values:
        if not isinstance(value, Node):
            raise TypeError("replacement values must be rdflib Nodes")

    chain = _read_collection(graph, head)
    length = len(chain)
    if stop > length:
        raise IndexError("range extends beyond the RDF collection")
    if start == stop and not values:
        return head

    external_references = [
        _has_external_reference(
            graph,
            node,
            chain[index - 1][0] if index > 0 else None,
        )
        for index, (node, _, _) in enumerate(chain)
    ]

    tail = chain[stop][0] if stop < length else RDF.nil
    first_referenced_range_node: Optional[int] = next(
        (
            index
            for index in range(start, stop)
            if external_references[index]
        ),
        None,
    )
    replacement_nodes: list[Node] = []
    for index, _ in enumerate(values):
        old_index = start + index
        if (
            old_index < stop
            and (
                first_referenced_range_node is None
                or old_index < first_referenced_range_node
            )
        ):
            replacement_nodes.append(chain[old_index][0])
        else:
            new_node = BNode()
            if old_index < stop:
                _copy_non_collection_properties(graph, chain[old_index][0], new_node)
            replacement_nodes.append(new_node)
    replacement_head = replacement_nodes[0] if replacement_nodes else tail

    copied_prefix_start: Optional[int] = None
    copied_prefix_nodes: list[BNode] = []
    if start > 0:
        referenced_prefix = external_references[:start]
        if any(referenced_prefix):
            copied_prefix_start = referenced_prefix.index(True)
            copied_prefix_nodes = [
                BNode() for _ in range(start - copied_prefix_start)
            ]

    result_head = replacement_head if start == 0 else chain[0][0]
    if start > 0 and copied_prefix_start is not None:
        result_head = (
            copied_prefix_nodes[0] if copied_prefix_start == 0 else chain[0][0]
        )

    for index, value in enumerate(values):
        node = replacement_nodes[index]
        next_node = (
            replacement_nodes[index + 1]
            if index + 1 < len(replacement_nodes)
            else tail
        )
        graph.set((node, RDF.first, value))
        graph.set((node, RDF.rest, next_node))

    if start > 0:
        if copied_prefix_start is not None:
            for index in range(copied_prefix_start, start):
                node = copied_prefix_nodes[index - copied_prefix_start]
                _, value, _ = chain[index]
                next_node = (
                    copied_prefix_nodes[index - copied_prefix_start + 1]
                    if index + 1 < start
                    else replacement_head
                )
                graph.add((node, RDF.first, value))
                graph.add((node, RDF.rest, next_node))
                _copy_non_collection_properties(graph, chain[index][0], node)

            if copied_prefix_start > 0:
                predecessor = chain[copied_prefix_start - 1][0]
                graph.remove((predecessor, RDF.rest, None))
                graph.add((predecessor, RDF.rest, copied_prefix_nodes[0]))
        else:
            predecessor = chain[start - 1][0]
            graph.remove((predecessor, RDF.rest, None))
            graph.add((predecessor, RDF.rest, replacement_head))

    if start == 0 or copied_prefix_start is None:
        reused_nodes = set(replacement_nodes)
        for index in range(start, stop):
            if external_references[index]:
                break
            node = chain[index][0]
            if node in reused_nodes:
                continue
            graph.remove((node, RDF.first, None))
            graph.remove((node, RDF.rest, None))

    return result_head


class Collection:
    """See "Emulating container types": <https://docs.python.org/reference/datamodel.html#emulating-container-types>

    ```python
    >>> from rdflib.term import Literal
    >>> from rdflib.graph import Graph
    >>> from pprint import pprint
    >>> listname = BNode()
    >>> g = Graph('Memory')
    >>> listItem1 = BNode()
    >>> listItem2 = BNode()
    >>> g.add((listname, RDF.first, Literal(1))) # doctest: +ELLIPSIS
    <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
    >>> g.add((listname, RDF.rest, listItem1)) # doctest: +ELLIPSIS
    <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
    >>> g.add((listItem1, RDF.first, Literal(2))) # doctest: +ELLIPSIS
    <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
    >>> g.add((listItem1, RDF.rest, listItem2)) # doctest: +ELLIPSIS
    <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
    >>> g.add((listItem2, RDF.rest, RDF.nil)) # doctest: +ELLIPSIS
    <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
    >>> g.add((listItem2, RDF.first, Literal(3))) # doctest: +ELLIPSIS
    <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
    >>> c = Collection(g,listname)
    >>> pprint([term.n3() for term in c])
    ['"1"^^<http://www.w3.org/2001/XMLSchema#integer>',
     '"2"^^<http://www.w3.org/2001/XMLSchema#integer>',
     '"3"^^<http://www.w3.org/2001/XMLSchema#integer>']
    >>> Literal(1) in c
    True
    >>> len(c)
    3
    >>> c._get_container(1) == listItem1
    True
    >>> c.index(Literal(2)) == 1
    True

    ```

    The collection is immutable if `uri` is the empty list (`http://www.w3.org/1999/02/22-rdf-syntax-ns#nil`).
    """

    def __init__(self, graph: Graph, uri: Node, seq: list[Node] = []):
        self.graph = graph
        self.uri = uri or BNode()
        if seq:
            self += seq

    def n3(self) -> str:
        """
        ```python
        >>> from rdflib.term import Literal
        >>> from rdflib.graph import Graph
        >>> listname = BNode()
        >>> g = Graph('Memory')
        >>> listItem1 = BNode()
        >>> listItem2 = BNode()
        >>> g.add((listname, RDF.first, Literal(1))) # doctest: +ELLIPSIS
        <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
        >>> g.add((listname, RDF.rest, listItem1)) # doctest: +ELLIPSIS
        <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
        >>> g.add((listItem1, RDF.first, Literal(2))) # doctest: +ELLIPSIS
        <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
        >>> g.add((listItem1, RDF.rest, listItem2)) # doctest: +ELLIPSIS
        <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
        >>> g.add((listItem2, RDF.rest, RDF.nil)) # doctest: +ELLIPSIS
        <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
        >>> g.add((listItem2, RDF.first, Literal(3))) # doctest: +ELLIPSIS
        <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
        >>> c = Collection(g, listname)
        >>> print(c.n3()) #doctest: +NORMALIZE_WHITESPACE
        ( "1"^^<http://www.w3.org/2001/XMLSchema#integer>
            "2"^^<http://www.w3.org/2001/XMLSchema#integer>
            "3"^^<http://www.w3.org/2001/XMLSchema#integer> )

        ```
        """
        return "( %s )" % (" ".join([i.n3() for i in self]))

    def _get_container(self, index: int) -> Optional[Node]:
        """Gets the first, rest holding node at index."""
        assert isinstance(index, int)
        graph = self.graph
        container: Optional[Node] = self.uri
        i = 0
        while i < index:
            i += 1
            container = graph.value(container, RDF.rest)
            if container is None:
                break
        return container

    def __len__(self) -> int:
        """length of items in collection."""
        return len(list(self.graph.items(self.uri)))

    def index(self, item: Node) -> int:
        """
        Returns the 0-based numerical index of the item in the list
        """
        listname = self.uri
        index = 0
        while True:
            if (listname, RDF.first, item) in self.graph:
                return index
            else:
                newlink = list(self.graph.objects(listname, RDF.rest))
                index += 1
                if newlink == [RDF.nil]:
                    raise ValueError("%s is not in %s" % (item, self.uri))
                elif not newlink:
                    raise Exception("Malformed RDF Collection: %s" % self.uri)
                else:
                    assert len(newlink) == 1, "Malformed RDF Collection: %s" % self.uri
                    listname = newlink[0]

    def __getitem__(self, key: int) -> Node:
        """TODO"""
        c = self._get_container(key)
        if c:
            v = self.graph.value(c, RDF.first)
            if v:
                return v
            else:
                raise KeyError(key)
        else:
            raise IndexError(key)

    def __setitem__(self, key: int, value: Node) -> None:
        """TODO"""
        c = self._get_container(key)
        if c:
            self.graph.set((c, RDF.first, value))
        else:
            raise IndexError(key)

    def __delitem__(self, key: int) -> None:
        """
        ```python
        >>> from rdflib.namespace import RDF, RDFS
        >>> from rdflib import Graph
        >>> from pprint import pformat
        >>> g = Graph()
        >>> a = BNode('foo')
        >>> b = BNode('bar')
        >>> c = BNode('baz')
        >>> g.add((a, RDF.first, RDF.type)) # doctest: +ELLIPSIS
        <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
        >>> g.add((a, RDF.rest, b)) # doctest: +ELLIPSIS
        <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
        >>> g.add((b, RDF.first, RDFS.label)) # doctest: +ELLIPSIS
        <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
        >>> g.add((b, RDF.rest, c)) # doctest: +ELLIPSIS
        <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
        >>> g.add((c, RDF.first, RDFS.comment)) # doctest: +ELLIPSIS
        <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
        >>> g.add((c, RDF.rest, RDF.nil)) # doctest: +ELLIPSIS
        <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
        >>> len(g)
        6
        >>> def listAncestry(node, graph):
        ...   for i in graph.subjects(RDF.rest, node):
        ...     yield i
        >>> [str(node.n3())
        ...   for node in g.transitiveClosure(listAncestry, RDF.nil)]
        ['_:baz', '_:bar', '_:foo']
        >>> lst = Collection(g, a)
        >>> len(lst)
        3
        >>> b == lst._get_container(1)
        True
        >>> c == lst._get_container(2)
        True
        >>> del lst[1]
        >>> len(lst)
        2
        >>> len(g)
        4

        ```
        """
        self[key]  # to raise any potential key exceptions
        graph = self.graph
        current = self._get_container(key)
        assert current
        if len(self) == 1 and key > 0:
            pass
        elif key == len(self) - 1:
            # the tail
            priorlink = self._get_container(key - 1)
            # type error: Argument 1 to "set" of "Graph" has incompatible type "tuple[Optional[Node], URIRef, URIRef]"; expected "tuple[Node, Node, Any]"
            self.graph.set((priorlink, RDF.rest, RDF.nil))  # type: ignore[arg-type]
            graph.remove((current, None, None))
        else:
            next = self._get_container(key + 1)
            prior = self._get_container(key - 1)
            assert next and prior
            graph.remove((current, None, None))
            graph.set((prior, RDF.rest, next))

    def __iter__(self) -> Iterator[Node]:
        """Iterator over items in Collections"""
        return self.graph.items(self.uri)

    def replace_range(
        self, start: int, stop: int, replacement: Iterable[Node]
    ) -> Node:
        """Replace a half-open range and update this collection's list head."""
        self.uri = replace_range(self.graph, self.uri, start, stop, replacement)
        return self.uri

    def _end(self) -> Node:
        # find end of list
        container = self.uri
        while True:
            rest = self.graph.value(container, RDF.rest)
            if rest is None or rest == RDF.nil:
                return container
            else:
                container = rest

    def append(self, item: Node) -> Collection:
        """
        ```python
        >>> from rdflib.term import Literal
        >>> from rdflib.graph import Graph
        >>> listname = BNode()
        >>> g = Graph()
        >>> c = Collection(g,listname,[Literal(1),Literal(2)])
        >>> links = [
        ...     list(g.subjects(object=i, predicate=RDF.first))[0] for i in c]
        >>> len([i for i in links if (i, RDF.rest, RDF.nil) in g])
        1

        ```
        """
        end = self._end()
        if end == RDF.nil:
            raise ValueError("Cannot append to empty list")

        if (end, RDF.first, None) in self.graph:
            # append new node to the end of the linked list
            node = BNode()
            self.graph.set((end, RDF.rest, node))
            end = node

        self.graph.add((end, RDF.first, item))
        self.graph.add((end, RDF.rest, RDF.nil))
        return self

    def __iadd__(self, other: Iterable[Node]):
        end = self._end()
        if end == RDF.nil:
            raise ValueError("Cannot append to empty list")
        self.graph.remove((end, RDF.rest, None))

        for item in other:
            if (end, RDF.first, None) in self.graph:
                nxt = BNode()
                self.graph.add((end, RDF.rest, nxt))
                end = nxt

            self.graph.add((end, RDF.first, item))

        self.graph.add((end, RDF.rest, RDF.nil))
        return self

    def clear(self):
        container: Optional[Node] = self.uri
        graph = self.graph
        while container:
            rest = graph.value(container, RDF.rest)
            graph.remove((container, RDF.first, None))
            graph.remove((container, RDF.rest, None))
            container = rest
        return self
