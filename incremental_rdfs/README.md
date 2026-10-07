# incremental_rdfs

A small-scope **incremental RDFS reasoner** on top of rdflib, with
justification tracking. It maintains the closure of a set of explicit
triples under exactly six rules (this is *not* full RDFS):

| rule | meaning |
| --- | --- |
| `sc-trans` | `a subClassOf b`, `b subClassOf c` → `a subClassOf c` |
| `type-inherit` | `x type a`, `a subClassOf b` → `x type b` |
| `sp-trans` | `p subPropertyOf q`, `q subPropertyOf r` → `p subPropertyOf r` |
| `prop-inherit` | `s p o`, `p subPropertyOf q` → `s q o` |
| `domain` | `p domain c`, `s p o` → `s type c` |
| `range` | `p range c`, `s p o` → `o type c` |

## Design

- **Separate stores.** Explicit facts live in `reasoner.explicit` (an
  rdflib `Graph`), derived facts in `reasoner.derived` (another `Graph`).
- **Justifications.** Every derived fact records each independent
  support (`Justification(rule, antecedents)`). A conclusion with
  several supports survives the retraction of any one of them.
- **Incremental retraction (DRed).** Retracting a fact over-deletes
  everything derived from it, then re-derives whatever is still
  grounded in the remaining facts. Cyclic support therefore collapses
  when its explicit premises go away, and explicitly asserted
  conclusions are never deleted as collateral damage.
- **Queries.** `fact in reasoner`, `reasoner.conclusions()`,
  `reasoner.explain(fact)` (direct reasons), `reasoner.proof(fact)`
  (nested proof tree, cycles marked).

## Usage

```python
from rdflib import Namespace
from incremental_rdfs import IncrementalRDFSReasoner
from incremental_rdfs.reasoner import SC, TYPE

EX = Namespace("http://example.org/")
r = IncrementalRDFSReasoner()
r.add((EX.A, SC, EX.B))
r.add((EX.x, TYPE, EX.A))
assert (EX.x, TYPE, EX.B) in r
print(r.explain((EX.x, TYPE, EX.B)))
r.retract((EX.A, SC, EX.B))
assert (EX.x, TYPE, EX.B) not in r
```

## Demo and tests

```
python -m incremental_rdfs.demo            # diamond / cycle / property-rule scenarios
python -m pytest incremental_rdfs/tests    # unit tests + closure equivalence
```

Every mutation in the tests and the demo is cross-checked against a
from-scratch brute-force closure (`incremental_rdfs/tests/closure.py`).
