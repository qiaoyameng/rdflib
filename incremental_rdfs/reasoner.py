"""Incremental RDFS-subset reasoner with justification tracking.

Rules handled (and nothing more -- this is not full RDFS):

  sc-trans        (a rdfs:subClassOf b) (b rdfs:subClassOf c)  -> (a rdfs:subClassOf c)
  type-inherit    (x rdf:type a) (a rdfs:subClassOf b)         -> (x rdf:type b)
  sp-trans        (p rdfs:subPropertyOf q) (q rdfs:subPropertyOf r) -> (p rdfs:subPropertyOf r)
  prop-inherit    (s p o) (p rdfs:subPropertyOf q)             -> (s q o)
  domain          (p rdfs:domain c) (s p o)                    -> (s rdf:type c)
  range           (p rdfs:range c) (s p o)                     -> (o rdf:type c)

Explicit facts live in ``explicit`` (an rdflib Graph), derived facts in
``derived`` (another Graph).  Every derived fact carries the set of
justifications that currently support it.  Retraction uses DRed-style
over-deletion plus re-derivation, so cyclic support that is no longer
grounded in explicit facts collapses instead of lingering forever.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterator, Optional, Set, Tuple

from rdflib import Graph, URIRef
from rdflib.namespace import RDF, RDFS

SC = RDFS.subClassOf
SP = RDFS.subPropertyOf
DOM = RDFS.domain
RNG = RDFS.range
TYPE = RDF.type

Triple = Tuple[URIRef, URIRef, URIRef]


class _Var:
    """A rule variable (distinct from any rdflib term)."""

    __slots__ = ("name",)

    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"?{self.name}"


def _is_var(x) -> bool:
    return isinstance(x, _Var)


def _unify(pattern, triple, binding) -> Optional[dict]:
    """Bind ``pattern`` against a concrete ``triple``; None on mismatch."""
    binding = dict(binding)
    for pat_el, trp_el in zip(pattern, triple):
        if _is_var(pat_el):
            bound = binding.get(pat_el.name)
            if bound is None:
                binding[pat_el.name] = trp_el
            elif bound != trp_el:
                return None
        elif pat_el != trp_el:
            return None
    return binding


def _resolve(pattern, binding) -> Triple:
    return tuple(
        binding[el.name] if _is_var(el) else el for el in pattern
    )  # type: ignore[return-value]


@dataclass(frozen=True)
class Justification:
    """One reason a derived fact holds: a rule plus its antecedent facts."""

    rule: str
    antecedents: Tuple[Triple, ...]


@dataclass(frozen=True)
class Rule:
    name: str
    antecedents: Tuple[tuple, ...]
    consequent: tuple

    def derive_from(self, fact: Triple, match) -> Iterator[Tuple[Justification, Triple]]:
        """All instantiations of this rule in which ``fact`` participates."""
        for i, pat in enumerate(self.antecedents):
            binding = _unify(pat, fact, {})
            if binding is None:
                continue
            others = [p for j, p in enumerate(self.antecedents) if j != i]
            yield from self._complete(others, binding, match)

    def derive_consequent(self, fact: Triple, match) -> Iterator[Tuple[Justification, Triple]]:
        """All instantiations of this rule whose consequent is ``fact``."""
        binding = _unify(self.consequent, fact, {})
        if binding is None:
            return
        yield from self._complete(list(self.antecedents), binding, match)

    def _complete(self, patterns, binding, match) -> Iterator[Tuple[Justification, Triple]]:
        if not patterns:
            just = Justification(
                self.name, tuple(_resolve(p, binding) for p in self.antecedents)
            )
            yield just, _resolve(self.consequent, binding)
            return
        pat, rest = patterns[0], patterns[1:]
        for candidate in match(pat, binding):
            new_binding = _unify(pat, candidate, binding)
            if new_binding is not None:
                yield from self._complete(rest, new_binding, match)


_a, _b, _c = _Var("a"), _Var("b"), _Var("c")
_x, _s, _o = _Var("x"), _Var("s"), _Var("o")
_p, _q, _r = _Var("p"), _Var("q"), _Var("r")

RULES: Tuple[Rule, ...] = (
    Rule("sc-trans", ((_a, SC, _b), (_b, SC, _c)), (_a, SC, _c)),
    Rule("type-inherit", ((_x, TYPE, _a), (_a, SC, _b)), (_x, TYPE, _b)),
    Rule("sp-trans", ((_p, SP, _q), (_q, SP, _r)), (_p, SP, _r)),
    Rule("prop-inherit", ((_s, _p, _o), (_p, SP, _q)), (_s, _q, _o)),
    Rule("domain", ((_p, DOM, _c), (_s, _p, _o)), (_s, TYPE, _c)),
    Rule("range", ((_p, RNG, _c), (_s, _p, _o)), (_o, TYPE, _c)),
)


class IncrementalRDFSReasoner:
    """Maintains the RDFS-subset closure of a set of explicit triples.

    Facts can be added and retracted one at a time; conclusions and
    their justifications are updated incrementally.
    """

    def __init__(self) -> None:
        self.explicit: Graph = Graph()
        self.derived: Graph = Graph()
        # fact -> set of justifications currently supporting it
        self._justs: Dict[Triple, Set[Justification]] = {}
        # antecedent fact -> set of (consequent, justification) using it
        self._dependents: Dict[Triple, Set[Tuple[Triple, Justification]]] = {}
        # indexes over every known fact (explicit or derived)
        self._known: Set[Triple] = set()
        self._by_subject: Dict[URIRef, Set[Triple]] = {}
        self._by_predicate: Dict[URIRef, Set[Triple]] = {}

    # ------------------------------------------------------------------
    # known-set bookkeeping
    # ------------------------------------------------------------------
    def _register(self, t: Triple) -> None:
        self._known.add(t)
        self._by_subject.setdefault(t[0], set()).add(t)
        self._by_predicate.setdefault(t[1], set()).add(t)

    def _unregister(self, t: Triple) -> None:
        self._known.discard(t)
        bucket = self._by_subject.get(t[0])
        if bucket is not None:
            bucket.discard(t)
        bucket = self._by_predicate.get(t[1])
        if bucket is not None:
            bucket.discard(t)

    def _match(self, pattern, binding) -> Iterator[Triple]:
        s = pattern[0] if not _is_var(pattern[0]) else binding.get(pattern[0].name)
        p = pattern[1] if not _is_var(pattern[1]) else binding.get(pattern[1].name)
        o = pattern[2] if not _is_var(pattern[2]) else binding.get(pattern[2].name)
        if p is not None:
            candidates = self._by_predicate.get(p, ())
        elif s is not None:
            candidates = self._by_subject.get(s, ())
        else:
            candidates = self._known
        # snapshot: matching runs while propagation mutates the indexes
        for t in list(candidates):
            if (s is None or t[0] == s) and (p is None or t[1] == p) and (
                o is None or t[2] == o
            ):
                yield t

    # ------------------------------------------------------------------
    # justification bookkeeping
    # ------------------------------------------------------------------
    def _add_just(self, consequent: Triple, just: Justification) -> bool:
        if consequent in just.antecedents:
            # A fact can never be part of its own (well-founded) support;
            # such rule instances add nothing to the closure.
            return False
        bucket = self._justs.setdefault(consequent, set())
        if just in bucket:
            return False
        bucket.add(just)
        for antecedent in just.antecedents:
            self._dependents.setdefault(antecedent, set()).add((consequent, just))
        return True

    def _drop_just(self, consequent: Triple, just: Justification) -> None:
        bucket = self._justs.get(consequent)
        if bucket is None or just not in bucket:
            return
        bucket.discard(just)
        for antecedent in just.antecedents:
            deps = self._dependents.get(antecedent)
            if deps is not None:
                deps.discard((consequent, just))

    # ------------------------------------------------------------------
    # public mutation API
    # ------------------------------------------------------------------
    def add(self, triple: Triple) -> bool:
        """Assert an explicit fact. Returns True if the state changed."""
        s, p, o = triple
        t: Triple = (s, p, o)
        if t in self.explicit:
            return False
        self.explicit.add(t)
        if t not in self._known:
            self._register(t)
            self._propagate([t])
        return True

    def retract(self, triple: Triple) -> None:
        """Withdraw an explicit fact and any conclusions that lose support."""
        s, p, o = triple
        t: Triple = (s, p, o)
        if t not in self.explicit:
            raise KeyError(f"not an explicit fact: {t!r}")
        self.explicit.remove(t)
        deleted: Set[Triple] = set()
        touched: Set[Triple] = set()
        self._cascade_delete(t, deleted, touched)
        self._rederive(deleted, touched)

    # ------------------------------------------------------------------
    # forward chaining
    # ------------------------------------------------------------------
    def _propagate(self, seeds) -> None:
        queue = list(seeds)
        while queue:
            fact = queue.pop()
            for rule in RULES:
                for just, consequent in rule.derive_from(fact, self._match):
                    self._record(consequent, just, queue)

    def _record(self, consequent: Triple, just: Justification, queue) -> None:
        if not self._add_just(consequent, just):
            return
        self.derived.add(consequent)
        if consequent not in self._known:
            self._register(consequent)
            queue.append(consequent)

    # ------------------------------------------------------------------
    # retraction (DRed: over-delete, then re-derive)
    # ------------------------------------------------------------------
    def _cascade_delete(self, fact: Triple, deleted: Set[Triple], touched: Set[Triple]) -> None:
        # DRed over-deletion: remove ``fact`` and everything derived from it,
        # even facts that still have other justifications -- those may be
        # circular (only grounded through facts now being deleted).  The
        # re-derivation pass restores whatever is still well-founded.
        for just in list(self._justs.get(fact, ())):
            self._drop_just(fact, just)
        self.derived.remove(fact)
        self._unregister(fact)
        deleted.add(fact)
        for consequent, just in list(self._dependents.get(fact, ())):
            self._drop_just(consequent, just)
            touched.add(consequent)
            if consequent in self.explicit:
                # Explicit facts are never deleted, but this derivation of
                # them is gone; if none remain they leave ``derived``.
                if not self._justs.get(consequent):
                    self.derived.remove(consequent)
            elif consequent not in deleted:
                self._cascade_delete(consequent, deleted, touched)
        # Any leftovers here refer to already-dropped justifications.
        self._dependents.pop(fact, None)

    def _rederive(self, deleted: Set[Triple], touched: Set[Triple]) -> None:
        pending = set(deleted)
        restored: Set[Triple] = set()
        changed = True
        while changed:
            changed = False
            for fact in list(pending):
                found = []
                for rule in RULES:
                    for just, _ in rule.derive_consequent(fact, self._match):
                        found.append(just)
                if found:
                    for just in found:
                        self._add_just(fact, just)
                    self.derived.add(fact)
                    self._register(fact)
                    pending.discard(fact)
                    restored.add(fact)
                    changed = True
        # Repair justification records of facts that survived the
        # over-deletion but lost some of their supports.
        for consequent in touched | restored:
            if consequent not in self._known:
                continue
            for rule in RULES:
                for just, _ in rule.derive_consequent(consequent, self._match):
                    if self._add_just(consequent, just):
                        self.derived.add(consequent)

    # ------------------------------------------------------------------
    # query API
    # ------------------------------------------------------------------
    def __contains__(self, triple: Triple) -> bool:
        return tuple(triple) in self._known

    def is_explicit(self, triple: Triple) -> bool:
        return tuple(triple) in self.explicit

    def is_derived(self, triple: Triple) -> bool:
        return tuple(triple) in self.derived

    def conclusions(self) -> Iterator[Triple]:
        """All facts that currently hold (explicit and derived)."""
        return iter(self._known)

    def explain(self, triple: Triple) -> list:
        """Direct reasons ``triple`` currently holds.

        Returns a list whose entries are either the string ``"explicit"``
        or :class:`Justification` objects. Raises KeyError if the fact
        does not currently hold.
        """
        t = tuple(triple)
        if t not in self._known:
            raise KeyError(f"not a current conclusion: {t!r}")
        reasons = []
        if t in self.explicit:
            reasons.append("explicit")
        reasons.extend(sorted(self._justs.get(t, ()), key=lambda j: (j.rule, j.antecedents)))
        return reasons

    def proof(self, triple: Triple):
        """A nested proof tree for ``triple`` (cycles are marked, not followed)."""
        t = tuple(triple)
        if t not in self._known:
            raise KeyError(f"not a current conclusion: {t!r}")
        return self._proof(t, frozenset())

    def _proof(self, t: Triple, path):
        if t in path:
            return ("cycle", t)
        if t in self.explicit:
            return ("explicit", t)
        path = path | {t}
        branches = [
            (just.rule, [self._proof(a, path) for a in just.antecedents])
            for just in sorted(self._justs.get(t, ()), key=lambda j: (j.rule, j.antecedents))
        ]
        return ("derived", t, branches)
