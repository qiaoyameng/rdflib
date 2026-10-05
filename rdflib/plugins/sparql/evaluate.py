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
    NamedTuple,
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

#: Maximum number of outer rows whose binding patterns are handed to one
#: remote SERVICE query by the bound-join strategy.
SERVICE_BOUND_JOIN_BATCH_SIZE = 20

#: Monotonic counter used to scope blank nodes per remote call and to mint
#: unique marker variable names.
_SERVICE_CALL_COUNTER = itertools.count(1)

#: Matches SERVICE [SILENT] <endpoint> { ... } against the original query
#: text (the fragment body is matched greedily up to the final brace).
_SERVICE_STRING_RE = re.compile(
    r"^service(?:\s+silent)?[ \n]*<(?P<url>[^>]*)>[ \n]*{(?P<body>.*)}[ \n]*$",
    re.DOTALL | re.IGNORECASE,
)


class BoundServiceJoinPlan(NamedTuple):
    """Static information for a bound-join SERVICE evaluation."""

    url: str
    prologue: str
    raw_fragment: str
    silent: bool
    inner_vars: frozenset[Variable]
    send_vars: frozenset[Variable]


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

    if join.lazy:
        # A SERVICE pattern with a fixed endpoint can be evaluated as an
        # explicit bound-join: a small batch of compatible outer bindings is
        # handed to one remote query instead of one query per outer row.
        service_join = _boundServiceJoinPlan(ctx, join.p2)
        if service_join is not None:
            return evalBoundServiceJoin(ctx, join.p1, service_join)
        return evalLazyJoin(ctx, join)
    else:
        a = evalPart(ctx, join.p1)
        b = set(evalPart(ctx, join.p2))
        return _join(a, b)


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
    """Reference (per-outer-row) evaluation path for a SERVICE pattern.

    The pattern is executed once with the bindings currently visible in the
    context.  See :func:`evalBoundServiceJoin` for the batched bound-join
    variant used by the join evaluator.
    """
    plan = _servicePlan(part)
    if plan is None:
        # A variable endpoint (SERVICE ?ep ...) cannot be reached from the
        # service string, so there is nothing to evaluate here.
        return

    service_url, fragment, silent, _inner_vars = plan
    service_query = _buildQueryStringForServiceCall(ctx, fragment)

    try:
        yield from _runServiceOnce(ctx, service_url, service_query, silent=silent)
    except _ServiceSilentFailure:
        return


def _runServiceOnce(
    ctx: QueryContext,
    service_url: str,
    service_query: str,
    silent: bool,
    scope_bnodes: bool = True,
) -> Generator[FrozenBindings, None, None]:
    """Execute a single remote query and yield the solutions.

    Blank nodes returned in a response are local to that particular call:
    labels shared by two different responses must not identify the same
    blank node.  ``scope_bnodes`` enables per-call relabelling (as required
    by SPARQL 1.1 federation semantics).

    On transport failure, SERVICE SILENT produces no solutions while a plain
    SERVICE propagates the error.
    """
    try:
        variables, results = service_transport(service_url, service_query)
    except Exception:
        if silent:
            # Signal the SILENT failure to the caller: per the spec a failed
            # SERVICE SILENT leaves the outer solutions in place.  A service
            # that simply returns zero solutions yields nothing instead.
            raise _ServiceSilentFailure
        raise

    bnode_prefix = _freshBNodeScopePrefix() if scope_bnodes else ""
    for r in results:
        for bound in _yieldBindingsFromServiceCallResult(
            ctx, r, variables, bnode_prefix
        ):
            yield bound


class _ServiceSilentFailure(Exception):
    """Internal signal that a SERVICE SILENT remote call failed."""


def _freshBNodeScopePrefix() -> str:
    return "srv%d_" % next(_SERVICE_CALL_COUNTER)


def _defaultServiceTransport(
    service_url: str, service_query: str
) -> tuple[list[str], list[dict[str, dict[str, str]]]]:
    """HTTP transport used for SERVICE calls.

    Returns the list of result variables and the raw JSON binding rows.
    Raises on any transport error or non-OK HTTP status.
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
    if _HAS_ORJSON:
        json_dict = orjson.loads(response.read())
    else:
        json_dict = json.loads(response.read())
    return json_dict["head"]["vars"], json_dict["results"]["bindings"]


# Transport used for all SERVICE requests.  May be replaced (for example with
# an injected fake transport) for testing.  It receives the endpoint URL and
# the serialized SPARQL query and returns (variables, raw-json-bindings).
service_transport: Callable[
    [str, str], tuple[list[str], list[dict[str, dict[str, str]]]]
] = _defaultServiceTransport


def _servicePlan(
    part: CompValue,
) -> Optional[tuple[str, str, bool, frozenset[Variable]]]:
    """Extract (url, fragment, silent, inner-variables) from a SERVICE part.

    Only fixed-IRI endpoints are usable.  The inner fragment is taken from the
    original query text so it can be safely embedded in a fresh query.
    """
    match = _SERVICE_STRING_RE.match(
        # type error: Argument 2 to "get" of "CompValue" has incompatible type "str"; expected "bool"  [arg-type]
        part.get("service_string", "")  # type: ignore[arg-type]
    )
    if not match:
        return None
    service_url = match.group("url")
    fragment = match.group("body").strip()
    # CompValue.get() returns the key itself when the key is absent, so the
    # presence of the key is the only reliable test for the SILENT modifier.
    silent = "silent" in part
    inner_vars = frozenset(part.graph.get("_vars", set()))
    return service_url, fragment, silent, inner_vars


def _buildQueryStringForServiceCall(ctx: QueryContext, service_query: str) -> str:
    """
    Build a query string to be used by the service call.
    It is supposed to pass in the existing bound solutions.
    Re-adds prefixes if added and sets the base.
    Wraps it in select if needed.
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
    sol = [v for v in ctx.solution() if isinstance(v, Variable)]
    if len(sol) > 0:
        variables = " ".join([v.n3() for v in sol])
        variables_bound = " ".join([ctx.get(v).n3() for v in sol])
        service_query = (
            service_query + "VALUES (" + variables + ") {(" + variables_bound + ")}"
        )
    return service_query


def _boundServiceJoinPlan(
    ctx: QueryContext, part: CompValue
) -> Optional[BoundServiceJoinPlan]:
    """Prepare the bound-join batch strategy for a SERVICE join, if safe.

    Only SERVICE fragments that are plain group graph patterns which parse
    once embedded in a SELECT (using the outer query's prefixes/base) are
    eligible.  Variable endpoints, self-contained SELECT subqueries and any
    other fragment that cannot be rewritten this way fall back to the per-row
    reference path.
    """
    if part.name != "ServiceGraphPattern":
        return None
    if not isinstance(part.get("term"), URIRef):
        return None

    plan = _servicePlan(part)
    if plan is None:
        return None
    service_url, fragment, silent, inner_vars = plan

    # A group containing a sub-SELECT may hide variables from the outer
    # scope; an outer VALUES clause could then fail to constrain the service
    # results, so such fragments are not considered safely rewritable.
    if re.search(r"\bSELECT\b", fragment, flags=re.IGNORECASE):
        return None

    send_vars = frozenset(var for var in inner_vars if isinstance(var, Variable))

    prologue = _servicePrologue(ctx)
    try:
        parser.parseQuery(prologue + "SELECT * WHERE {" + fragment + "}")
    except ParseException:
        return None

    return BoundServiceJoinPlan(
        url=service_url,
        prologue=prologue,
        raw_fragment=fragment,
        silent=silent,
        inner_vars=inner_vars,
        send_vars=send_vars,
    )


def _servicePrologue(ctx: QueryContext) -> str:
    prologue = ""
    # type error: Item "None" of "Optional[Prologue]" has no attribute "namespace_manager"
    for prefix, namespace in ctx.prologue.namespace_manager.store.namespaces():  # type: ignore[union-attr]
        prologue += "PREFIX " + prefix + ":" + namespace.n3() + " "
    # type error: Item "None" of "Optional[Prologue]" has no attribute "base"
    base = ctx.prologue.base  # type: ignore[union-attr]
    if base is not None and len(base) > 0:
        prologue += "BASE <" + base + "> "
    return prologue


def evalBoundServiceJoin(
    ctx: QueryContext,
    p1: CompValue,
    plan: BoundServiceJoinPlan,
) -> Generator[FrozenBindings, None, None]:
    """Bound-join: send small batches of compatible outer bindings per request.

    Rows whose shared variables are bound to blank nodes cannot be shipped to
    the remote endpoint (blank node labels are not shared between graphs);
    those rows take the per-row reference path.  All other rows are grouped
    into batches of at most ``SERVICE_BOUND_JOIN_BATCH_SIZE`` rows; their
    distinct binding patterns are sent in one remote query and multiplicities
    of the outer multiset are preserved by the local join.  SERVICE SILENT
    uses one request per row so that a failed call can suppress the service
    solutions for precisely the corresponding outer row.
    """
    outer_rows = list(evalPart(ctx, p1))

    legacy_rows: list[FrozenBindings] = []
    batchable_rows: list[FrozenBindings] = []
    for row in outer_rows:
        if any(
            var in plan.send_vars and isinstance(row.get(var), BNode)
            for var in row
            if isinstance(var, Variable)
        ):
            legacy_rows.append(row)
        else:
            batchable_rows.append(row)

    for row in legacy_rows:
        row_ctx = ctx.thaw(row)
        service_query = _buildQueryStringForServiceCall(row_ctx, plan.raw_fragment)
        try:
            for inner in _runServiceOnce(
                row_ctx, plan.url, service_query, silent=plan.silent
            ):
                yield inner.merge(row)
        except _ServiceSilentFailure:
            # The outer row survives with the service variables unbound.
            yield row

    if not batchable_rows:
        return

    send_vars = sorted(plan.send_vars, key=str)

    # The marker identifies which VALUES tuple of a batch a remote solution
    # matches, so solutions cannot be attributed to the wrong outer row
    # (unbound variables or differing bindings) and multiplicities stay
    # exact.
    marker = Variable("__rdflib_service_boundjoin_%d" % next(_SERVICE_CALL_COUNTER))
    while marker in plan.inner_vars:
        marker = Variable(
            "__rdflib_service_boundjoin_%d" % next(_SERVICE_CALL_COUNTER)
        )

    # With SERVICE SILENT a transport failure must only suppress the
    # service-side solutions for exactly the outer row of that call, so rows
    # cannot be grouped into one request: fall back to one request per row.
    batch_size = 1 if plan.silent else SERVICE_BOUND_JOIN_BATCH_SIZE

    for start in range(0, len(batchable_rows), batch_size):
        chunk = batchable_rows[start : start + batch_size]
        yield from _evalServiceBatch(ctx, chunk, plan, send_vars, marker)


def _evalServiceBatch(
    ctx: QueryContext,
    chunk: list[FrozenBindings],
    plan: BoundServiceJoinPlan,
    send_vars: list[Variable],
    marker: Variable,
) -> Generator[FrozenBindings, None, None]:
    # Only the distinct binding patterns of the chunk are sent as VALUES
    # tuples; each pattern is tagged with a marker literal so the remote
    # solutions can be attributed back to the exact outer rows exhibiting
    # that pattern.  Duplicate outer rows (same binding twice) are restored
    # by the local join below, preserving the multiset multiplicity.
    row_patterns: list[tuple[tuple[tuple[Variable, Identifier], ...], Literal]] = []
    patterns: dict[Literal, dict[Variable, Identifier]] = {}
    pattern_by_key: dict[
        tuple[tuple[Variable, Identifier], ...], Literal
    ] = {}
    for row in chunk:
        pattern = {
            var: value
            for var in send_vars
            if (value := row.get(var)) is not None
            and not isinstance(value, BNode)
        }
        key = tuple(sorted(pattern.items(), key=lambda item: str(item[0])))
        marker_value = pattern_by_key.get(key)
        if marker_value is None:
            marker_value = Literal("m%d" % len(patterns))
            pattern_by_key[key] = marker_value
            patterns[marker_value] = pattern
        row_patterns.append((key, marker_value))

    values_header = marker.n3() + " " + " ".join(var.n3() for var in send_vars)
    values_rows = []
    for marker_value, pattern in patterns.items():
        values_rows.append(
            "("
            + marker_value.n3()
            + " "
            + " ".join(
                pattern[var].n3() if var in pattern else "UNDEF" for var in send_vars
            )
            + ")"
        )
    values_clause = "VALUES (" + values_header + ") {" + " ".join(values_rows) + "}"

    service_query = (
        plan.prologue
        + "SELECT * WHERE { "
        + values_clause
        + " { "
        + plan.raw_fragment
        + " } }"
    )

    try:
        variables, results = service_transport(plan.url, service_query)
    except Exception:
        if plan.silent:
            # SERVICE SILENT: per the spec the join of a failed service call
            # leaves the outer solutions in place with the service variables
            # unbound, rather than dropping the whole batch of outer rows.
            # The remaining batches are still evaluated.
            yield from chunk
            return
        raise

    bnode_prefix = _freshBNodeScopePrefix()
    remote_solutions: list[FrozenBindings] = []
    for r in results:
        for inner in _yieldBindingsFromServiceCallResult(
            ctx, r, variables, bnode_prefix
        ):
            remote_solutions.append(inner)

    for row, (_key, row_marker) in zip(chunk, row_patterns):
        for inner in remote_solutions:
            # A solution belongs to this outer row only if it carries the
            # marker of that row's own VALUES tuple.  This also makes the
            # local check immune to unbound variables or differing bindings
            # of other rows in the batch.
            if inner.get(marker) != row_marker:
                continue
            # Drop the marker variable again, it is not part of the query.
            yield FrozenBindings(
                ctx,
                itertools.chain(
                    row.items(),
                    (
                        (key, value)
                        for key, value in inner.items()
                        if key != marker
                    ),
                ),
            )


def _yieldBindingsFromServiceCallResult(
    ctx: QueryContext,
    r: dict[str, dict[str, str]],
    variables: list[str],
    bnode_prefix: str = "",
) -> Generator[FrozenBindings, None, None]:
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
                res_dict[Variable(var)] = BNode(bnode_prefix + var_binding["value"])
            else:
                raise ValueError(f"invalid type {var_type!r} for variable {var!r}")
    yield FrozenBindings(ctx, res_dict)


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
