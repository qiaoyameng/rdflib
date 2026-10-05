"""
These method recursively evaluate the SPARQL Algebra

evalQuery is the entry-point, it will setup context and
return the SPARQLResult object

evalPart is called on each level and will delegate to the right method

A `rdflib.plugins.sparql.sparql.QueryContext` is passed along, keeping
information needed for evaluation

A list of dicts (solution mappings) is returned, apart from GroupBy which may
also return a dict of list of dicts
"""

from __future__ import annotations

import collections
import itertools
import re
from collections.abc import Callable, Generator, Iterable, Mapping
from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
    Union,
)
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pyparsing import ParseException

from rdflib.graph import Graph
from rdflib.plugins.sparql import CUSTOM_EVALS, parser
from rdflib.plugins.sparql.aggregates import Aggregator
from rdflib.plugins.sparql.evalutils import (
    _ebv,
    _eval,
    _fillTemplate,
    _join,
    _minus,
    _val,
)
from rdflib.plugins.sparql.parserutils import CompValue, value
from rdflib.plugins.sparql.sparql import (
    AlreadyBound,
    FrozenBindings,
    FrozenDict,
    Query,
    QueryContext,
    SPARQLError,
)
from rdflib.term import BNode, Identifier, Literal, URIRef, Variable

if TYPE_CHECKING:
    from rdflib.paths import Path

import json

try:
    import orjson

    _HAS_ORJSON = True
except ImportError:
    orjson = None  # type: ignore[assignment, unused-ignore]
    _HAS_ORJSON = False

_Triple = tuple[Identifier, Identifier, Identifier]

#: Number of compatible outer solutions bundled into a single remote SERVICE
#: request when bound-join batching is used.  ``1`` reproduces the classic
#: row-by-row evaluation.
SERVICE_BOUND_JOIN_BATCH_SIZE = 8

#: Monotonically increasing counters used to scope cross-response blank
#: node labels and to generate unique batch marker URIs.
_service_response_counter = itertools.count()
_service_batch_counter = itertools.count()


def _default_service_transport(
    service_url: str, service_query: str
) -> dict[str, Any]:
    """Execute *service_query* against *service_url* over HTTP.

    Returns the decoded SPARQL JSON result document.  Any failure to obtain
    a valid result raises an exception; callers translate that into the
    appropriate SERVICE/SERVICE SILENT semantics.

    The indirection lets tests (and advanced integrations) substitute an
    injected transport without performing real network IO.
    """

    query_settings = {"query": service_query, "output": "json"}
    headers = {
        "accept": "application/sparql-results+json",
        "user-agent": "rdflibForAnUser",
    }
    # GET is easier to cache so prefer that if the query is not to long
    if len(service_query) < 600:
        response = urlopen(
            Request(service_url + "?" + urlencode(query_settings), headers=headers)
        )
    else:
        response = urlopen(
            Request(
                service_url,
                data=urlencode(query_settings).encode(),
                headers=headers,
            )
        )
    if response.status != 200:
        raise Exception(
            "Service: %s responded with code: %s" % (service_url, response.status)
        )
    payload = response.read()
    if _HAS_ORJSON:
        return orjson.loads(payload)
    return json.loads(payload)


#: Pluggable transport used for every remote SERVICE request.
#: Signature: ``(service_url: str, service_query: str) -> dict`` returning a
#: SPARQL JSON result document.
service_transport: Callable[[str, str], dict[str, Any]] = _default_service_transport


def evalBGP(
    ctx: QueryContext, bgp: list[_Triple]
) -> Generator[FrozenBindings, None, None]:
    """
    A basic graph pattern
    """

    if not bgp:
        yield ctx.solution()
        return

    s, p, o = bgp[0]

    _s = ctx[s]
    _p = ctx[p]
    _o = ctx[o]

    # type error: Item "None" of "Optional[Graph]" has no attribute "triples"
    # type Argument 1 to "triples" of "Graph" has incompatible type "tuple[Union[str, Path, None], Union[str, Path, None], Union[str, Path, None]]"; expected "tuple[Optional[Node], Optional[Node], Optional[Node]]"
    for ss, sp, so in ctx.graph.triples((_s, _p, _o)):  # type: ignore[union-attr, arg-type]
        if None in (_s, _p, _o):
            c = ctx.push()
        else:
            c = ctx

        if _s is None:
            # type error: Incompatible types in assignment (expression has type "Union[Node, Any]", target has type "Identifier")
            c[s] = ss  # type: ignore[assignment]

        try:
            if _p is None:
                # type error: Incompatible types in assignment (expression has type "Union[Node, Any]", target has type "Identifier")
                c[p] = sp  # type: ignore[assignment]
        except AlreadyBound:
            continue

        try:
            if _o is None:
                # type error: Incompatible types in assignment (expression has type "Union[Node, Any]", target has type "Identifier")
                c[o] = so  # type: ignore[assignment]
        except AlreadyBound:
            continue

        for x in evalBGP(c, bgp[1:]):
            yield x


def evalExtend(
    ctx: QueryContext, extend: CompValue
) -> Generator[FrozenBindings, None, None]:
    # TODO: Deal with dict returned from evalPart from GROUP BY

    for c in evalPart(ctx, extend.p):
        try:
            e = _eval(extend.expr, c.forget(ctx, _except=extend._vars))
            if isinstance(e, SPARQLError):
                raise e

            yield c.merge({extend.var: e})

        except SPARQLError:
            yield c


def evalLazyJoin(
    ctx: QueryContext, join: CompValue
) -> Generator[FrozenBindings, None, None]:
    """
    A lazy join will push the variables bound
    in the first part to the second part,
    essentially doing the join implicitly
    hopefully evaluating much fewer triples
    """
    for a in evalPart(ctx, join.p1):
        c = ctx.thaw(a)
        for b in evalPart(c, join.p2):
            yield b.merge(a)  # merge, as some bindings may have been forgotten


def evalJoin(ctx: QueryContext, join: CompValue) -> Generator[FrozenDict, None, None]:
    # TODO: Deal with dict returned from evalPart from GROUP BY
    # only ever for join.p1

    service_info = _serviceInfo(join.p2)
    if service_info is not None:
        service_url, service_body, silent = service_info
        batched = _evalBatchedServiceJoin(
            ctx, join, join.p2, service_url, service_body, silent
        )
        if batched is not None:
            # A generator branch turns this function into a generator, so
            # yield from (rather than return) is required here.
            yield from batched
            return

    if join.lazy:
        yield from evalLazyJoin(ctx, join)
        return
    else:
        a = evalPart(ctx, join.p1)
        b = set(evalPart(ctx, join.p2))
        yield from _join(a, b)
        return


def evalUnion(ctx: QueryContext, union: CompValue) -> list[Any]:
    branch1_branch2 = []
    for x in evalPart(ctx, union.p1):
        branch1_branch2.append(x)
    for x in evalPart(ctx, union.p2):
        branch1_branch2.append(x)
    return branch1_branch2


def evalMinus(ctx: QueryContext, minus: CompValue) -> Generator[FrozenDict, None, None]:
    a = evalPart(ctx, minus.p1)
    b = set(evalPart(ctx, minus.p2))
    return _minus(a, b)


def evalLeftJoin(
    ctx: QueryContext, join: CompValue
) -> Generator[FrozenBindings, None, None]:
    # import pdb; pdb.set_trace()
    service_info = _serviceInfo(join.p2)
    if service_info is not None:
        service_url, service_body, silent = service_info
        batched = _evalBatchedServiceLeftJoin(
            ctx, join, join.p2, service_url, service_body, silent
        )
        if batched is not None:
            yield from batched
            return

    for a in evalPart(ctx, join.p1):
        ok = False
        c = ctx.thaw(a)
        for b in evalPart(c, join.p2):
            if _ebv(join.expr, b.forget(ctx)):
                ok = True
                yield b.merge(a)
        if not ok:
            # we've cheated, the ctx above may contain
            # vars bound outside our scope
            # before we yield a solution without the OPTIONAL part
            # check that we would have had no OPTIONAL matches
            # even without prior bindings...
            p1_vars = join.p1._vars
            if p1_vars is None or not any(
                _ebv(join.expr, b)
                for b in evalPart(ctx.thaw(a.remember(p1_vars)), join.p2)
            ):
                yield a


def evalFilter(
    ctx: QueryContext, part: CompValue
) -> Generator[FrozenBindings, None, None]:
    # TODO: Deal with dict returned from evalPart!
    for c in evalPart(ctx, part.p):
        if _ebv(
            part.expr,
            c.forget(ctx, _except=part._vars) if not part.no_isolated_scope else c,
        ):
            yield c


def evalGraph(
    ctx: QueryContext, part: CompValue
) -> Generator[FrozenBindings, None, None]:
    if ctx.dataset is None:
        raise Exception(
            "Non-conjunctive-graph doesn't know about "
            + "graphs. Try a query without GRAPH."
        )

    ctx = ctx.clone()
    graph: Union[str, Path, None, Graph] = ctx[part.term]
    prev_graph = ctx.graph
    if graph is None:
        for graph in ctx.dataset.contexts():
            # in SPARQL the default graph is NOT a named graph
            if graph == ctx.dataset.default_context:
                continue

            c = ctx.pushGraph(graph)
            c = c.push()
            graphSolution = [{part.term: graph.identifier}]
            for x in _join(evalPart(c, part.p), graphSolution):
                x.ctx.graph = prev_graph
                yield x

    else:
        if TYPE_CHECKING:
            assert not isinstance(graph, Graph)
        # type error: Argument 1 to "get_context" of "ConjunctiveGraph" has incompatible type "Union[str, Path]"; expected "Union[Node, str, None]"
        c = ctx.pushGraph(ctx.dataset.get_context(graph))  # type: ignore[arg-type]
        for x in evalPart(c, part.p):
            x.ctx.graph = prev_graph
            yield x


def evalValues(
    ctx: QueryContext, part: CompValue
) -> Generator[FrozenBindings, None, None]:
    for r in part.p.res:
        c = ctx.push()
        try:
            for k, v in r.items():
                if v != "UNDEF":
                    c[k] = v
        except AlreadyBound:
            continue

        yield c.solution()


def evalMultiset(ctx: QueryContext, part: CompValue):
    if part.p.name == "values":
        return evalValues(ctx, part)

    return evalPart(ctx, part.p)


def evalPart(ctx: QueryContext, part: CompValue) -> Any:
    # try custom evaluation functions
    for name, c in CUSTOM_EVALS.items():
        try:
            return c(ctx, part)
        except NotImplementedError:
            pass  # the given custome-function did not handle this part

    if part.name == "BGP":
        # Reorder triples patterns by number of bound nodes in the current ctx
        # Do patterns with more bound nodes first
        triples = sorted(
            part.triples, key=lambda t: len([n for n in t if ctx[n] is None])
        )

        return evalBGP(ctx, triples)
    elif part.name == "Filter":
        return evalFilter(ctx, part)
    elif part.name == "Join":
        return evalJoin(ctx, part)
    elif part.name == "LeftJoin":
        return evalLeftJoin(ctx, part)
    elif part.name == "Graph":
        return evalGraph(ctx, part)
    elif part.name == "Union":
        return evalUnion(ctx, part)
    elif part.name == "ToMultiSet":
        return evalMultiset(ctx, part)
    elif part.name == "Extend":
        return evalExtend(ctx, part)
    elif part.name == "Minus":
        return evalMinus(ctx, part)

    elif part.name == "Project":
        return evalProject(ctx, part)
    elif part.name == "Slice":
        return evalSlice(ctx, part)
    elif part.name == "Distinct":
        return evalDistinct(ctx, part)
    elif part.name == "Reduced":
        return evalReduced(ctx, part)

    elif part.name == "OrderBy":
        return evalOrderBy(ctx, part)
    elif part.name == "Group":
        return evalGroup(ctx, part)
    elif part.name == "AggregateJoin":
        return evalAggregateJoin(ctx, part)

    elif part.name == "SelectQuery":
        return evalSelectQuery(ctx, part)
    elif part.name == "AskQuery":
        return evalAskQuery(ctx, part)
    elif part.name == "ConstructQuery":
        return evalConstructQuery(ctx, part)

    elif part.name == "ServiceGraphPattern":
        return evalServiceQuery(ctx, part)

    elif part.name == "DescribeQuery":
        return evalDescribeQuery(ctx, part)

    else:
        raise Exception("I dont know: %s" % part.name)


def evalServiceQuery(ctx: QueryContext, part: CompValue):
    info = _serviceInfo(part)
    if info is None:
        # Variable SERVICE endpoints, unusual whitespace/formatting: the old
        # regular expression never produced a usable endpoint either, so
        # there is nothing to evaluate.
        return

    service_url, service_body, silent = info
    solution = ctx.solution()
    service_query = _buildQueryStringForServiceCall(ctx, service_body, solution)

    try:
        for b in _runServiceRequest(ctx, service_url, service_query):
            yield b
    except Exception:
        if silent:
            # Per SPARQL 1.1 §18 SERVICE SILENT yields an empty multiset.
            return
        raise


def _runServiceRequest(
    ctx: QueryContext, service_url: str, service_query: str
) -> list[FrozenBindings]:
    """Execute one remote request and materialise its bindings.

    Blank node labels are scoped per response, so blank nodes reported by
    two different requests never compare equal even when the remote endpoint
    reuses the same label.
    """

    scope = "rs%08d" % next(_service_response_counter)
    json_dict = service_transport(service_url, service_query)
    variables = json_dict["head"]["vars"]
    results: list[FrozenBindings] = []
    for r in json_dict["results"]["bindings"]:
        results.append(
            _bindingFromServiceResult(ctx, r, variables, bnode_scope=scope)
        )
    return results


"""
    Build a query string to be used by the service call.
    It is supposed to pass in the existing bound solutions.
    Re-adds prefixes if added and sets the base.
    Wraps it in select if needed.
"""


def _buildServiceQueryBase(ctx: QueryContext, service_query: str) -> str:
    """Return the service fragment wrapped in a SELECT if needed.

    Prefixes and the base of the enclosing query are re-attached when the
    service fragment is a bare group graph pattern.
    """

    try:
        parser.parseQuery(service_query)
    except ParseException:
        # This could be because we don't have a select around the service call.
        service_query = "SELECT REDUCED * WHERE {" + service_query + "}"
        # type error: Item "None" of "Optional[Prologue]" has no attribute "namespace_manager"
        for p in ctx.prologue.namespace_manager.store.namespaces():  # type: ignore[union-attr]
            service_query = "PREFIX " + p[0] + ":" + p[1].n3() + " " + service_query
        # re add the base if one was defined
        # type error: Item "None" of "Optional[Prologue]" has no attribute "base"
        base = ctx.prologue.base  # type: ignore[union-attr]
        if base is not None and len(base) > 0:
            service_query = "BASE <" + base + "> " + service_query
    return service_query


def _wrapServiceGroupForBatch(
    ctx: QueryContext,
    service_body: str,
    variables: list[Variable],
    rows: list[tuple[Optional[Identifier], ...]],
) -> Optional[str]:
    """Build a service query with an in-group inline-data block.

    Placing the VALUES inside the WHERE group makes the wrapping
    ``SELECT *`` project the join columns (in particular the synthetic batch
    marker), which a trailing VALUES block does not guarantee.
    Returns ``None`` when the fragment is not a bare group pattern.
    """

    try:
        parser.parseQuery(service_body)
        return None
    except ParseException:
        pass

    values_block = _appendValuesBlock("", variables, rows).strip()
    wrapped = "SELECT REDUCED * WHERE {" + service_body + " " + values_block + "}"
    # type error: Item "None" of "Optional[Prologue]" has no attribute "namespace_manager"
    for p in ctx.prologue.namespace_manager.store.namespaces():  # type: ignore[union-attr]
        wrapped = "PREFIX " + p[0] + ":" + p[1].n3() + " " + wrapped
    # type error: Item "None" of "Optional[Prologue]" has no attribute "base"
    base = ctx.prologue.base  # type: ignore[union-attr]
    if base is not None and len(base) > 0:
        wrapped = "BASE <" + base + "> " + wrapped
    return wrapped


def _appendValuesBlock(
    service_query: str,
    variables: list[Variable],
    rows: list[tuple[Optional[Identifier], ...]],
) -> str:
    """Append a SPARQL inline-data block binding *variables* to *rows*.

    A ``None`` entry becomes ``UNDEF``.  Every row is materialised
    explicitly so that repeated identical rows keep their multiplicity
    (SPARQL multiset semantics).
    """

    if not variables:
        return service_query
    var_list = " ".join(v.n3() for v in variables)
    row_strings = []
    for row in rows:
        terms = " ".join("UNDEF" if term is None else term.n3() for term in row)
        row_strings.append("(" + terms + ")")
    return service_query + "VALUES (" + var_list + ") {" + "".join(row_strings) + "}"


#: Template for a non-conflicting marker variable used to tag each row of a
#: batched inline-data block, making every answer attributable to its row.
_BATCH_MARKER_BASE = "http://rdflib.net/service/batch-row/"


def _buildQueryStringForServiceCall(
    ctx: QueryContext,
    service_query: str,
    solution: Optional[Mapping[Identifier, Identifier]] = None,
) -> str:
    """Build the row-by-row service query for the current context."""

    if solution is None:
        solution = ctx.solution()
    base = _buildServiceQueryBase(ctx, service_query)
    sol = [v for v in solution if isinstance(v, Variable)]
    if len(sol) > 0:
        rows = [tuple(solution.get(v) for v in sol)]
        base = _appendValuesBlock(base, sol, rows)
    return base


def _boundJoinEligible(
    service_part: CompValue, solutions: list[FrozenBindings]
) -> bool:
    """Decide whether bound-join batching is safe for these outer rows."""

    if SERVICE_BOUND_JOIN_BATCH_SIZE <= 1:
        return False
    if not isinstance(service_part.term, URIRef):
        return False
    # rdflib's grammar cannot parse blank nodes inside VALUES data blocks,
    # rows carrying BNode bindings must keep using the legacy path.
    for solution in solutions:
        for value in solution.values():
            if isinstance(value, BNode):
                return False
    # The service term itself must be a concrete IRI, which is guaranteed
    # by the caller through the service_string regular expression.
    variables = {v for s in solutions for v in s if isinstance(v, Variable)}
    if not _serviceProjectsAllVars(service_part, variables):
        return False
    # The synthetic marker variable must not collide with any variable the
    # service fragment itself binds.
    service_vars = set(service_part._vars or ())
    return _freshBatchMarker(variables | service_vars) is not None


def _serviceProjectsAllVars(
    service_part: CompValue, variables: Iterable[Variable]
) -> bool:
    """Check that *variables* injected through inline data will be returned.

    A bare group pattern is wrapped in ``SELECT *`` which exposes every
    bound variable.  An explicit sub-SELECT is only safe when it uses the
    wildcard projection; otherwise injected columns may be hidden, which
    would make it impossible to attribute remote solutions to a particular
    outer row.  Such fragments fall back to the row-by-row path.
    """

    service_body = re.match(
        "^service (?:silent[ \n]*)?<.*?>[ \n]*{(.*)}[ \n]*$",
        # type error: Argument 2 to "get" has incompatible type "str"; expected "bool"  [arg-type]
        service_part.get("service_string", ""),  # type: ignore[arg-type]
        re.DOTALL | re.I,
    ).group(1)
    try:
        parsed = parser.parseQuery(service_body)
    except ParseException:
        # Will be wrapped in SELECT REDUCED *, which projects everything.
        return True
    select = parsed[1]
    if select.name != "SelectQuery":
        return False
    if "projection" not in select:
        # SELECT *
        return True
    # An explicit projection list only returns the projected variables;
    # injected binding columns may be hidden from the result document.
    return False


def _freshBatchMarker(
    used_variables: set[Variable],
) -> Optional[Variable]:
    """Pick a marker variable name that does not collide with the query."""

    for index in range(100):
        candidate = Variable("__rdflib_svc_batch_row_%d__" % index)
        if candidate not in used_variables:
            return candidate
    return None


def _yieldBindingsFromServiceCallResult(
    ctx: QueryContext, r: dict[str, dict[str, str]], variables: list[str]
) -> Generator[FrozenBindings, None, None]:
    yield _bindingFromServiceResult(ctx, r, variables)


def _bindingFromServiceResult(
    ctx: QueryContext,
    r: dict[str, dict[str, str]],
    variables: list[str],
    bnode_scope: Optional[str] = None,
) -> FrozenBindings:
    res_dict: dict[Variable, Identifier] = {}
    for var in variables:
        if var in r and r[var]:
            var_binding = r[var]
            var_type = var_binding["type"]
            if var_type == "uri":
                res_dict[Variable(var)] = URIRef(var_binding["value"])
            elif var_type == "literal":
                res_dict[Variable(var)] = Literal(
                    var_binding["value"],
                    datatype=var_binding.get("datatype"),
                    lang=var_binding.get("xml:lang"),
                )
            # This is here because of
            # https://www.w3.org/TR/2006/NOTE-rdf-sparql-json-res-20061004/#variable-binding-results
            elif var_type == "typed-literal":
                res_dict[Variable(var)] = Literal(
                    var_binding["value"], datatype=URIRef(var_binding["datatype"])
                )
            elif var_type == "bnode":
                label = var_binding["value"]
                if bnode_scope is not None:
                    label = "%s:%s" % (bnode_scope, label)
                res_dict[Variable(var)] = BNode(label)
            else:
                raise ValueError(f"invalid type {var_type!r} for variable {var!r}")
    return FrozenBindings(ctx, res_dict)


# ---------------------------------------------------------------------------
# Bound-join batching for SERVICE
# ---------------------------------------------------------------------------

_SERVICE_RE = re.compile(
    "^service (?:silent[ \n]*)?<(.*)>[ \n]*{(.*)}[ \n]*$", re.DOTALL | re.I
)


def _serviceInfo(part: CompValue) -> Optional[tuple[str, str, bool]]:
    """Return ``(endpoint_url, body, silent)`` for a SERVICE algebra node.

    Returns ``None`` when the node is not a SERVICE with a concrete IRI
    endpoint that can be matched.
    """

    if part.name != "ServiceGraphPattern" or not isinstance(part.term, URIRef):
        return None
    match = _SERVICE_RE.match(
        # type error: Argument 2 to "get" has incompatible type "str"; expected "bool"  [arg-type]
        part.get("service_string", "")  # type: ignore[arg-type]
    )
    if not match:
        return None
    # The parser fills the optional "silent" parameter with the lowercase
    # keyword text even when SILENT is absent; only the uppercase match
    # indicates that the keyword was actually written.
    silent = part.get("silent") == "SILENT"
    return match.group(1), match.group(2), silent


def _solutionDomain(
    solution: FrozenBindings,
) -> tuple[tuple[Variable, ...], bool]:
    """Return the sorted domain of bound variables and whether a BNode is
    among the values."""

    variables = sorted(
        (v for v in solution if isinstance(v, Variable)), key=lambda v: v.n3()
    )
    has_bnode = any(isinstance(solution[v], BNode) for v in variables)
    return tuple(variables), has_bnode


def _batchableOuterRows(
    solutions: list[FrozenBindings],
) -> dict[tuple[Variable, ...], list[FrozenBindings]]:
    """Group outer solutions by the exact set of variables they bind.

    Rows within one group carry values for the same columns, so a VALUES
    block over those columns lets the remote endpoint perform the join and
    every returned solution can be attributed back deterministically.
    """

    buckets: dict[tuple[Variable, ...], list[FrozenBindings]] = collections.defaultdict(
        list
    )
    for solution in solutions:
        domain, has_bnode = _solutionDomain(solution)
        if has_bnode:
            continue
        buckets[domain].append(solution)
    return buckets


def _mergeServiceResult(
    outer: FrozenBindings, remote: Mapping[Identifier, Identifier]
) -> FrozenBindings:
    """Join one outer solution with one remote solution.

    Conflicting bindings for the same variable make the pair incompatible
    and are signalled with ``None`` so callers can apply join/left-join
    semantics correctly.
    """

    for variable, value in outer.items():
        other = remote.get(variable)
        if other is not None and other != value:
            return None
    return outer.merge(remote)


def _serviceSilentOuterSolutions(
    outer_part: CompValue,
    service_part: CompValue,
    solutions: list[FrozenBindings],
) -> Generator[FrozenBindings, None, None]:
    """SPARQL 1.1 §18 failure semantics for SERVICE SILENT.

    If the service shares variables with the rest of the group (the join's
    left input) all outer solutions are dropped.  If no variables are shared
    the outer solutions are produced unchanged.
    """

    service_vars = set(service_part._vars or ())
    outer_vars = set(outer_part._vars or ())
    if not service_vars.intersection(outer_vars):
        yield from solutions


def _evalServiceBatch(
    ctx: QueryContext,
    service_url: str,
    service_body: str,
    marker: Variable,
    domain: tuple[Variable, ...],
    outer_rows: list[FrozenBindings],
) -> list[tuple[FrozenBindings, FrozenBindings]]:
    """Issue one batched request for *outer_rows*.

    *domain* must be non-empty; rows without join columns are evaluated with
    one request per row by the caller so that independent remote answers
    remain independent and blank-node scopes stay separate.

    Returns the list of ``(outer, merged)`` pairs with the exact SPARQL
    multiset multiplicity.  Raises on remote failure or when the endpoint
    answers in a way that cannot be attributed to specific outer rows.

    A fresh marker variable is added to the inline-data block and projected
    back, so every answer identifies the single outer row it belongs to -
    even when several outer rows are identical and when one row produces
    multiple answers.
    """

    columns = [marker, *domain]
    marker_uris = [
        URIRef(_BATCH_MARKER_BASE + str(next(_service_batch_counter)))
        for _ in outer_rows
    ]
    rows_values: list[tuple[Optional[Identifier], ...]] = [
        (marker_uri, *(outer.get(v) for v in domain))
        for marker_uri, outer in zip(marker_uris, outer_rows)
    ]
    try:
        parser.parseQuery(service_body)
        body_is_group = False
    except ParseException:
        body_is_group = True

    if body_is_group:
        # VALUES inside the group guarantees the marker is projected.
        service_query = _wrapServiceGroupForBatch(
            ctx, service_body, columns, rows_values
        )
    else:
        # The fragment is itself a full query; wrap it once more so the
        # inline data lives inside an outer group and its columns - marker
        # included - survive wildcard projection.
        values_block = _appendValuesBlock("", columns, rows_values).strip()
        service_query = "SELECT * WHERE { { %s } %s }" % (
            service_body,
            values_block,
        )
    remote_solutions = _runServiceRequest(ctx, service_url, service_query)

    by_marker = dict(zip(marker_uris, outer_rows))
    pairs: list[tuple[FrozenBindings, FrozenBindings]] = []
    for remote in remote_solutions:
        marker_value = remote.get(marker)
        outer = by_marker.get(marker_value) if marker_value is not None else None
        if outer is None:
            # The marker column must be echoed unchanged.  Anything else
            # means the fragment is not safe to batch against this endpoint.
            raise RuntimeError(
                "SERVICE response is missing the batch marker %r" % (marker,)
            )
        cleaned = FrozenBindings(
            ctx,
            (item for item in remote.items() if item[0] != marker),
        )
        merged = _mergeServiceResult(outer, cleaned)
        if merged is None:
            raise RuntimeError(
                "SERVICE response conflicts with outer binding: %r vs %r"
                % (outer, cleaned)
            )
        pairs.append((outer, merged))
    return pairs


def _evalBatchedServiceJoin(
    ctx: QueryContext,
    join: CompValue,
    service_part: CompValue,
    service_url: str,
    service_body: str,
    silent: bool,
) -> Optional[Generator[FrozenBindings, None, None]]:
    """Bound-join evaluation of a join whose right side is a SERVICE.

    Returns ``None`` when the concrete outer solutions cannot safely be
    batched, in which case callers fall back to the original row-by-row
    evaluation.
    """

    outer_solutions = list(evalPart(ctx, join.p1))
    if not _boundJoinEligible(service_part, outer_solutions):
        return None

    used_variables = set(service_part._vars or ())
    used_variables.update(
        variable
        for solution in outer_solutions
        for variable in solution
        if isinstance(variable, Variable)
    )
    marker = _freshBatchMarker(used_variables)

    batchable = _batchableOuterRows(outer_solutions)
    bnode_rows = [
        solution
        for solution in outer_solutions
        if _solutionDomain(solution)[1]
    ]

    def generate() -> Generator[FrozenBindings, None, None]:
        for domain, rows in batchable.items():
            if not domain:
                # Empty solutions produce independent service evaluations;
                # keep one request per row to preserve bnode/identity scope.
                for outer in rows:
                    yield from _evalServiceJoinRowByRow(
                        ctx, join.p1, service_part, outer, silent
                    )
                continue
            for start in range(0, len(rows), SERVICE_BOUND_JOIN_BATCH_SIZE):
                chunk = rows[start : start + SERVICE_BOUND_JOIN_BATCH_SIZE]
                try:
                    pairs = _evalServiceBatch(
                        ctx, service_url, service_body, marker, domain, chunk
                    )
                except Exception:
                    if not silent:
                        raise
                    for answer in _serviceSilentOuterSolutions(
                        join.p1, service_part, chunk
                    ):
                        yield answer
                    continue
                for _outer, merged in pairs:
                    yield merged
        # Rows that carry blank node bindings cannot be expressed in a
        # remote VALUES block, use the legacy per-row requests for them.
        for outer in bnode_rows:
            try:
                yield from _evalServiceJoinRowByRow(
                    ctx, join.p1, service_part, outer, silent
                )
            except Exception:
                if not silent:
                    raise

    return generate()


def _evalServiceJoinRowByRow(
    ctx: QueryContext,
    outer_part: CompValue,
    service_part: CompValue,
    outer: FrozenBindings,
    silent: bool,
) -> Generator[FrozenBindings, None, None]:
    """Inner join of one outer solution against the SERVICE via one request.

    Used for outer solutions that cannot be represented in a batched
    VALUES block (e.g. blank node bindings) and for failed batches when the
    service is not SILENT is not applicable.
    """

    child_ctx = ctx.thaw(outer)
    try:
        for remote in evalPart(child_ctx, service_part):
            yield remote.merge(outer)
    except Exception:
        if not silent:
            raise
        yield from _serviceSilentOuterSolutions(
            outer_part, service_part, [outer]
        )


def _evalBatchedServiceLeftJoin(
    ctx: QueryContext,
    join: CompValue,
    service_part: CompValue,
    service_url: str,
    service_body: str,
    silent: bool,
) -> Optional[Generator[FrozenBindings, None, None]]:
    """Bound-join evaluation of a left join (OPTIONAL) over a SERVICE."""

    outer_solutions = list(evalPart(ctx, join.p1))
    if not _boundJoinEligible(service_part, outer_solutions):
        return None

    used_variables = set(service_part._vars or ())
    used_variables.update(
        variable
        for solution in outer_solutions
        for variable in solution
        if isinstance(variable, Variable)
    )
    marker = _freshBatchMarker(used_variables)

    batchable = _batchableOuterRows(outer_solutions)
    bnode_rows = [
        solution
        for solution in outer_solutions
        if _solutionDomain(solution)[1]
    ]

    def generate() -> Generator[FrozenBindings, None, None]:
        for domain, rows in batchable.items():
            if not domain:
                for outer in rows:
                    yield from _evalServiceLeftJoinRowByRow(
                        ctx, join, service_part, outer, silent
                    )
                continue
            for start in range(0, len(rows), SERVICE_BOUND_JOIN_BATCH_SIZE):
                chunk = rows[start : start + SERVICE_BOUND_JOIN_BATCH_SIZE]
                failed = False
                try:
                    pairs = _evalServiceBatch(
                        ctx, service_url, service_body, marker, domain, chunk
                    )
                except Exception:
                    if not silent:
                        raise
                    failed = True
                if failed:
                    # SERVICE SILENT failure in a left join behaves like an
                    # empty right multiset: every outer row survives.
                    yield from chunk
                    continue
                produced = set()
                for outer, merged in pairs:
                    if _ebv(join.expr, merged.forget(ctx)):
                        produced.add(id(outer))
                        yield merged
                # Mirror evalLeftJoin: rows without a matching service
                # solution are retained unless they only "matched" because
                    # of bindings leaking from outside this OPTIONAL scope.
                for outer in chunk:
                    if id(outer) in produced:
                        continue
                    if _leftJoinRetains(ctx, join, outer):
                        yield outer
        for outer in bnode_rows:
            yield from _evalServiceLeftJoinRowByRow(
                ctx, join, service_part, outer, silent
            )

    return generate()


def _leftJoinRetains(
    ctx: QueryContext, join: CompValue, outer: FrozenBindings
) -> bool:
    """Replicate the "did it really not match?" re-check from evalLeftJoin.

    The pushed context may contain variables bound outside the OPTIONAL
    scope; before keeping an outer solution without an OPTIONAL match the
    service is re-evaluated with only the variables produced by the left
    side still bound.
    """

    p1_vars = join.p1._vars
    if p1_vars is None:
        return True
    recheck_ctx = ctx.thaw(outer.remember(p1_vars))
    return not any(
        _ebv(join.expr, remote) for remote in evalPart(recheck_ctx, join.p2)
    )


def _evalServiceLeftJoinRowByRow(
    ctx: QueryContext,
    join: CompValue,
    service_part: CompValue,
    outer: FrozenBindings,
    silent: bool,
) -> Generator[FrozenBindings, None, None]:
    ok = False
    child_ctx = ctx.thaw(outer)
    try:
        for remote in evalPart(child_ctx, service_part):
            if _ebv(join.expr, remote.forget(ctx)):
                ok = True
                yield remote.merge(outer)
    except Exception:
        if not silent:
            raise
    if not ok and _leftJoinRetains(ctx, join, outer):
        yield outer


def evalGroup(ctx: QueryContext, group: CompValue):
    """
    http://www.w3.org/TR/sparql11-query/#defn_algGroup
    """
    # grouping should be implemented by evalAggregateJoin
    return evalPart(ctx, group.p)


def evalAggregateJoin(
    ctx: QueryContext, agg: CompValue
) -> Generator[FrozenBindings, None, None]:
    # import pdb ; pdb.set_trace()
    p = evalPart(ctx, agg.p)
    # p is always a Group, we always get a dict back

    group_expr = agg.p.expr
    res: dict[Any, Any] = collections.defaultdict(
        lambda: Aggregator(aggregations=agg.A)
    )

    if group_expr is None:
        # no grouping, just COUNT in SELECT clause
        # get 1 aggregator for counting
        aggregator = res[True]
        for row in p:
            aggregator.update(row)
    else:
        for row in p:
            # determine right group aggregator for row
            k = tuple(_eval(e, row, False) for e in group_expr)
            res[k].update(row)

    # all rows are done; yield aggregated values
    for aggregator in res.values():
        yield FrozenBindings(ctx, aggregator.get_bindings())

    # there were no matches
    if len(res) == 0:
        yield FrozenBindings(ctx)


def evalOrderBy(
    ctx: QueryContext, part: CompValue
) -> Generator[FrozenBindings, None, None]:
    res = evalPart(ctx, part.p)

    for e in reversed(part.expr):
        reverse = bool(e.order and e.order == "DESC")
        res = sorted(
            res, key=lambda x: _val(value(x, e.expr, variables=True)), reverse=reverse
        )

    return res


def evalSlice(ctx: QueryContext, slice: CompValue):
    res = evalPart(ctx, slice.p)

    return itertools.islice(
        res,
        slice.start,
        slice.start + slice.length if slice.length is not None else None,
    )


def evalReduced(
    ctx: QueryContext, part: CompValue
) -> Generator[FrozenBindings, None, None]:
    """apply REDUCED to result

    REDUCED is not as strict as DISTINCT, but if the incoming rows were sorted
    it should produce the same result with limited extra memory and time per
    incoming row.
    """

    # This implementation uses a most recently used strategy and a limited
    # buffer size. It relates to a LRU caching algorithm:
    # https://en.wikipedia.org/wiki/Cache_algorithms#Least_Recently_Used_.28LRU.29
    MAX = 1
    # TODO: add configuration or determine "best" size for most use cases
    # 0: No reduction
    # 1: compare only with the last row, almost no reduction with
    #    unordered incoming rows
    # N: The greater the buffer size the greater the reduction but more
    #    memory and time are needed

    # mixed data structure: set for lookup, deque for append/pop/remove
    mru_set = set()
    mru_queue: collections.deque[Any] = collections.deque()

    for row in evalPart(ctx, part.p):
        if row in mru_set:
            # forget last position of row
            mru_queue.remove(row)
        else:
            # row seems to be new
            yield row
            mru_set.add(row)
            if len(mru_set) > MAX:
                # drop the least recently used row from buffer
                mru_set.remove(mru_queue.pop())
        # put row to the front
        mru_queue.appendleft(row)


def evalDistinct(
    ctx: QueryContext, part: CompValue
) -> Generator[FrozenBindings, None, None]:
    res = evalPart(ctx, part.p)

    done = set()
    for x in res:
        if x not in done:
            yield x
            done.add(x)


def evalProject(ctx: QueryContext, project: CompValue):
    res = evalPart(ctx, project.p)
    return (row.project(project.PV) for row in res)


def evalSelectQuery(
    ctx: QueryContext, query: CompValue
) -> Mapping[str, Union[str, list[Variable], Iterable[FrozenDict]]]:
    res: dict[str, Union[str, list[Variable], Iterable[FrozenDict]]] = {}
    res["type_"] = "SELECT"
    res["bindings"] = evalPart(ctx, query.p)
    res["vars_"] = query.PV
    return res


def evalAskQuery(ctx: QueryContext, query: CompValue) -> Mapping[str, Union[str, bool]]:
    res: dict[str, Union[bool, str]] = {}
    res["type_"] = "ASK"
    res["askAnswer"] = False
    for x in evalPart(ctx, query.p):
        res["askAnswer"] = True
        break

    return res


def evalConstructQuery(
    ctx: QueryContext, query: CompValue
) -> Mapping[str, Union[str, Graph]]:
    template = query.template

    if not template:
        # a construct-where query
        template = query.p.p.triples  # query->project->bgp ...

    graph = Graph()

    for c in evalPart(ctx, query.p):
        graph += _fillTemplate(template, c)

    res: dict[str, Union[str, Graph]] = {}
    res["type_"] = "CONSTRUCT"
    res["graph"] = graph

    return res


def evalDescribeQuery(ctx: QueryContext, query) -> dict[str, Union[str, Graph]]:
    # Create a result graph and bind namespaces from the graph being queried
    graph = Graph()
    # type error: Item "None" of "Optional[Graph]" has no attribute "namespaces"
    for pfx, ns in ctx.graph.namespaces():  # type: ignore[union-attr]
        graph.bind(pfx, ns)

    to_describe = set()

    # Explicit IRIs may be provided to a DESCRIBE query.
    # If there is a WHERE clause, explicit IRIs may be provided in
    # addition to projected variables. Find those explicit IRIs and
    # prepare to describe them.
    for iri in query.PV:
        if isinstance(iri, URIRef):
            to_describe.add(iri)

    # If there is a WHERE clause, evaluate it then find the unique set of
    # resources to describe across all bindings and projected variables
    if query.p is not None:
        bindings = evalPart(ctx, query.p)
        to_describe.update(*(set(binding.values()) for binding in bindings))

    # Get a CBD for all resources identified to describe
    for resource in to_describe:
        # type error: Item "None" of "Optional[Graph]" has no attribute "cbd"
        ctx.graph.cbd(resource, target_graph=graph)  # type: ignore[union-attr]

    res: dict[str, Union[str, Graph]] = {}
    res["type_"] = "DESCRIBE"
    res["graph"] = graph

    return res


def evalQuery(
    graph: Graph,
    query: Query,
    initBindings: Optional[Mapping[str, Identifier]] = None,
    base: Optional[str] = None,
) -> Mapping[Any, Any]:
    """Evaluate a SPARQL query against a graph.

    !!! warning "Caution"

        This method can access indirectly requested network endpoints, for
        example, query processing will attempt to access network endpoints
        specified in `SERVICE` directives.

        When processing untrusted or potentially malicious queries, measures
        should be taken to restrict network and file access.

        For information on available security measures, see the RDFLib
        [Security Considerations](../security_considerations.md)
        documentation.
    """
    main = query.algebra

    initBindings = dict((Variable(k), v) for k, v in (initBindings or {}).items())

    ctx = QueryContext(
        graph, initBindings=initBindings, datasetClause=main.datasetClause
    )

    ctx.prologue = query.prologue

    return evalPart(ctx, main)
