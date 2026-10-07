"""Independent brute-force closure, used as the oracle in tests/demo.

This deliberately shares nothing with the incremental reasoner except
the rule table, so comparing against it is a meaningful check.
"""

from typing import Iterable, Set, Tuple

from ..reasoner import DOM, RNG, SC, SP, TYPE

Triple = Tuple


def full_closure(explicit: Iterable[Triple]) -> Set[Triple]:
    """Least fixpoint of the six supported rules over ``explicit``."""
    known: Set[Triple] = set(explicit)
    changed = True
    while changed:
        changed = False
        for s, p, o in list(known):
            if p == SC:
                for s2, p2, o2 in list(known):
                    if p2 == SC and s2 == o and (s, SC, o2) not in known:
                        known.add((s, SC, o2))
                        changed = True
                    if p2 == TYPE and o2 == s and (s2, TYPE, o) not in known:
                        known.add((s2, TYPE, o))
                        changed = True
            elif p == SP:
                for s2, p2, o2 in list(known):
                    if p2 == SP and s2 == o and (s, SP, o2) not in known:
                        known.add((s, SP, o2))
                        changed = True
                for s2, p2, o2 in list(known):
                    if p2 == s and (s2, o, o2) not in known:
                        known.add((s2, o, o2))
                        changed = True
            elif p == DOM:
                for s2, p2, o2 in list(known):
                    if p2 == s and (s2, TYPE, o) not in known:
                        known.add((s2, TYPE, o))
                        changed = True
            elif p == RNG:
                for s2, p2, o2 in list(known):
                    if p2 == s and (o2, TYPE, o) not in known:
                        known.add((o2, TYPE, o))
                        changed = True
    return known
