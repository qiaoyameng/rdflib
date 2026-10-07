"""Demo: three small graphs, incremental updates, and one retraction.

Run with:  python -m incremental_rdfs.demo

Every mutation is checked against a from-scratch full closure of the
remaining explicit facts, and one retraction is inspected to show which
explanations it invalidates.
"""

from rdflib import Namespace

from incremental_rdfs.reasoner import IncrementalRDFSReasoner, SC, SP, DOM, RNG, TYPE
from incremental_rdfs.tests.closure import full_closure

EX = Namespace("http://example.org/")


def fmt(t):
    def short(u):
        s = str(u)
        if s.startswith(str(EX)):
            return ":" + s[len(str(EX)):]
        if "#" in s:
            return s.split("#")[-1]
        return s.rsplit("/", 1)[-1]

    return f"({short(t[0])}, {short(t[1])}, {short(t[2])})"


def show_explanations(reasoner, fact, indent="  "):
    for reason in reasoner.explain(fact):
        if reason == "explicit":
            print(f"{indent}- asserted explicitly")
        else:
            ants = ", ".join(fmt(a) for a in reason.antecedents)
            print(f"{indent}- {reason.rule}: {ants}")


def check(reasoner, explicit, label):
    got = set(reasoner.conclusions())
    want = full_closure(explicit)
    status = "OK" if got == want else "MISMATCH"
    print(f"  [closure check: {status}] {label} "
          f"({len(explicit)} explicit -> {len(got)} known)")
    assert got == want


def show_invalidated(before, reasoner):
    """Diff a pre-retraction explanation snapshot against the current state."""
    print("Conclusions that disappeared entirely:")
    gone = sorted(set(before) - set(reasoner.conclusions()))
    for t in gone:
        print(f"  {fmt(t)}")
    if not gone:
        print("  (none)")
    print("Explanations invalidated on surviving conclusions:")
    any_invalid = False
    for t in sorted(set(before) & set(reasoner.conclusions())):
        lost = [x for x in before[t] if x != "explicit" and x not in reasoner.explain(t)]
        if lost:
            any_invalid = True
            print(f"  {fmt(t)}")
            for j in lost:
                ants = ", ".join(fmt(a) for a in j.antecedents)
                print(f"    - no longer supported via {j.rule}: {ants}")
    if not any_invalid:
        print("  (none)")


def scenario_diamond():
    print("=" * 72)
    print("1) Diamond inheritance: x -type-> A -sc-> B -\\")
    print("                       \\-sc-> C -/  both -> D")
    print("=" * 72)
    explicit = [
        (EX.x, TYPE, EX.A),
        (EX.A, SC, EX.B),
        (EX.A, SC, EX.C),
        (EX.B, SC, EX.D),
        (EX.C, SC, EX.D),
    ]
    r = IncrementalRDFSReasoner()
    for i, f in enumerate(explicit, 1):
        r.add(f)
        check(r, explicit[:i], "add " + fmt(f))

    fact = (EX.x, TYPE, EX.D)
    print(f"\n{fmt(fact)} holds with two independent supports:")
    show_explanations(r, fact)

    before = {t: r.explain(t) for t in list(r.conclusions())}
    print(f"\n-- retract {fmt((EX.B, SC, EX.D))} --")
    r.retract((EX.B, SC, EX.D))
    explicit.remove((EX.B, SC, EX.D))
    check(r, explicit, "after retracting (B, subClassOf, D)")
    print(f"{fmt(fact)} still holds (one support is enough):")
    show_explanations(r, fact)
    print()
    show_invalidated(before, r)
    return r


def scenario_cycle():
    print()
    print("=" * 72)
    print("2) Mutual inheritance: A <-> B (a cycle must not self-sustain)")
    print("=" * 72)
    explicit = [(EX.A, SC, EX.B), (EX.B, SC, EX.A)]
    r = IncrementalRDFSReasoner()
    for f in explicit:
        r.add(f)
    check(r, explicit, "both edges present")
    print("While the cycle is intact it derives the reflexive facts:")
    for f in [(EX.A, SC, EX.A), (EX.B, SC, EX.B)]:
        print(f"  {fmt(f)}")
        show_explanations(r, f, indent="    ")

    print(f"\n-- retract {fmt((EX.A, SC, EX.B))} --")
    r.retract((EX.A, SC, EX.B))
    explicit.remove((EX.A, SC, EX.B))
    check(r, explicit, "after breaking the cycle")
    gone = [f for f in [(EX.A, SC, EX.A), (EX.B, SC, EX.B)] if f not in r]
    print("No longer concluded (the cycle was their only ground):")
    for f in gone:
        print(f"  {fmt(f)}")
    print("Still concluded:")
    for t in sorted(r.conclusions()):
        print(f"  {fmt(t)}")


def scenario_property_rules():
    print()
    print("=" * 72)
    print("3) Property rules triggering each other")
    print("   worksWith -sp-> knows -sp-> acquaintedWith, knows dom/rng Person")
    print("=" * 72)
    explicit = [
        (EX.alice, EX.worksWith, EX.bob),
        (EX.worksWith, SP, EX.knows),
        (EX.knows, SP, EX.acquaintedWith),
        (EX.knows, DOM, EX.Person),
        (EX.knows, RNG, EX.Person),
    ]
    r = IncrementalRDFSReasoner()
    for i, f in enumerate(explicit, 1):
        r.add(f)
        check(r, explicit[:i], "add " + fmt(f))

    interesting = [
        (EX.alice, EX.knows, EX.bob),
        (EX.alice, EX.acquaintedWith, EX.bob),
        (EX.alice, TYPE, EX.Person),
        (EX.bob, TYPE, EX.Person),
    ]
    print("\nDerived (note how derived facts trigger further rules):")
    for f in interesting:
        print(f"  {fmt(f)}")
        show_explanations(r, f, indent="    ")

    before = {t: r.explain(t) for t in list(r.conclusions())}
    print(f"\n-- retract {fmt((EX.worksWith, SP, EX.knows))} --")
    r.retract((EX.worksWith, SP, EX.knows))
    explicit.remove((EX.worksWith, SP, EX.knows))
    check(r, explicit, "after retracting (worksWith, subPropertyOf, knows)")
    print()
    show_invalidated(before, r)


if __name__ == "__main__":
    scenario_diamond()
    scenario_cycle()
    scenario_property_rules()
    print()
    print("All incremental states matched the from-scratch full closure.")
