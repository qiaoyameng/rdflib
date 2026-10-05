from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Optional

from rdflib.namespace import RDF
from rdflib.term import BNode, Node

if TYPE_CHECKING:
    from rdflib.graph import Graph

__all__ = ["Collection", "replace_range"]


def replace_range(
    graph: Graph,
    head: Node,
    start: int,
    end: int,
    replacement: Iterable[Node],
) -> Node:
    """Replace the half-open range ``[start, end)`` in an RDF collection.

    The original collection is never destructively spliced through a node that
    may be referenced by another collection or another triple. Cells on the
    edited prefix that have another incoming reference are copied, while cells
    before that point retain their identity. Cells removed from the new
    collection are left intact when retaining them is necessary to preserve an
    existing reference; otherwise only their ``rdf:first`` and ``rdf:rest``
    triples are removed.

    The collection is read and validated before any triples are written. A
    cycle, more than one ``rdf:first`` or ``rdf:rest`` value, or a missing
    ``rdf:first``/``rdf:rest`` value therefore leaves the graph unchanged.

    Args:
        graph: Graph containing the collection.
        head: First collection cell, or ``RDF.nil`` for the empty list.
        start: Start position of the range to replace (inclusive).
        end: End position of the range to replace (exclusive).
        replacement: Items that replace the removed range.

    Returns:
        The head of the resulting collection.
    """
    if not isinstance(head, Node):
        raise TypeError("head must be an RDF term")
    if not isinstance(start, int) or not isinstance(end, int):
        raise TypeError("start and end must be integers")

    replacement_items = list(replacement)
    for item in replacement_items:
        if not isinstance(item, Node):
            raise TypeError("replacement items must be RDF terms")

    cells = _read_collection_cells(graph, head)
    length = len(cells)
    if start < 0 or end < 0 or start > length or end > length or start > end:
        raise IndexError("invalid collection range")

    external_references = [
        _external_reference_count(graph, cell, cells[index - 1][0] if index else None)
        for index, (cell, _, _) in enumerate(cells)
    ]

    copy_from: Optional[int] = None
    if start > 0:
        for index in range(start):
            if external_references[index]:
                copy_from = index
                break

    suffix = cells[end][0] if end < length else RDF.nil
    replacement_head = _build_chain(graph, replacement_items, suffix)

    if start > 0:
        copied_prefix: dict[int, BNode] = {}
        if copy_from is not None:
            for index in range(start - 1, copy_from - 1, -1):
                copied_cell = BNode()
                next_cell = copied_prefix.get(index + 1, replacement_head)
                graph.add((copied_cell, RDF.first, cells[index][1]))
                graph.add((copied_cell, RDF.rest, next_cell))
                copied_prefix[index] = copied_cell

            if copy_from == 0:
                new_head: Node = copied_prefix[0]
            else:
                new_head = head
                _set_rest(graph, cells[copy_from - 1][0], copied_prefix[copy_from])
        else:
            new_head = head
            _set_rest(graph, cells[start - 1][0], replacement_head)
    else:
        new_head = replacement_head

    if copy_from is not None:
        cleanup_end = start
    else:
        cleanup_end = end
        for index in range(start, end):
            if external_references[index]:
                cleanup_end = index
                break
        for index in range(start, cleanup_end):
            cell = cells[index][0]
            graph.remove((cell, RDF.first, None))
            graph.remove((cell, RDF.rest, None))

    return new_head


def _read_collection_cells(
    graph: Graph, head: Node
) -> list[tuple[Node, Node, Node]]:
    cells: list[tuple[Node, Node, Node]] = []
    current = head
    seen: set[Node] = set()

    while current != RDF.nil:
        if current in seen:
            raise ValueError("Malformed RDF collection: cyclic rdf:rest reference")
        seen.add(current)

        first_values = list(graph.objects(current, RDF.first))
        rest_values = list(graph.objects(current, RDF.rest))
        if len(first_values) != 1:
            raise ValueError("Malformed RDF collection: expected exactly one rdf:first")
        if len(rest_values) != 1:
            raise ValueError("Malformed RDF collection: expected exactly one rdf:rest")

        first_value = first_values[0]
        rest_value = rest_values[0]
        cells.append((current, first_value, rest_value))
        current = rest_value

    return cells


def _external_reference_count(
    graph: Graph, node: Node, predecessor: Optional[Node]
) -> int:
    return sum(
        1
        for subject, predicate in graph.subject_predicates(node)
        if subject != predecessor or predicate != RDF.rest
    )


def _build_chain(graph: Graph, items: list[Node], terminal: Node) -> Node:
    cells = [BNode() for _ in items]
    for index, item in enumerate(items):
        next_cell = cells[index + 1] if index + 1 < len(cells) else terminal
        graph.add((cells[index], RDF.first, item))
        graph.add((cells[index], RDF.rest, next_cell))
    return cells[0] if cells else terminal


def _set_rest(graph: Graph, cell: Node, next_cell: Node) -> None:
    graph.remove((cell, RDF.rest, None))
    graph.add((cell, RDF.rest, next_cell))


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

    def replace_range(
        self, start: int, end: int, replacement: Iterable[Node]
    ) -> Node:
        """Safely replace a positional range and return the new head."""
        self.uri = replace_range(self.graph, self.uri, start, end, replacement)
        return self.uri

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
