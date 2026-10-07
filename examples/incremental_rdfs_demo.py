"""
增量 RDFS 推导器演示（IncrementalRDFSReasoner）。

三张小型知识图谱：
  1. 菱形继承：同一结论有多条独立依据，撤掉一条不消失；
  2. 互相继承（循环）：循环本身不能成为脱离显式前提的永久支持，
     且用户直接声明过的结论不被连带删除；
  3. 属性规则相互触发：subPropertyOf / domain / range 链式触发。

每一步增/删之后，都把增量维护的结论与“逐次全量求闭包”
（rdfs_closure 从头重算）做对比，并在撤回时展示哪些解释随之失效。

运行：python examples/incremental_rdfs_demo.py
"""

from rdflib import Namespace
from rdflib.namespace import RDF, RDFS

from rdflib.extras.incremental_rdfs import IncrementalRDFSReasoner, rdfs_closure

EX = Namespace("http://example.org/")


def qname(term):
    """把长 IRI 缩成易读的形式。"""
    if term == RDF.type:
        return "rdf:type"
    if term == RDFS.subClassOf:
        return "rdfs:subClassOf"
    if term == RDFS.subPropertyOf:
        return "rdfs:subPropertyOf"
    if term == RDFS.domain:
        return "rdfs:domain"
    if term == RDFS.range:
        return "rdfs:range"
    return term.n3().replace("<http://example.org/", "ex:").replace(">", "")


def show_triple(triple):
    s, p, o = triple
    return f"({qname(s)}, {qname(p)}, {qname(o)})"


def check_against_full_closure(reasoner, step):
    """对比增量维护的结论与从头全量求闭包的结果。"""
    expected = rdfs_closure(set(reasoner.graph))
    actual = set(reasoner.conclusions())
    assert actual == expected, (
        f"{step}: 增量结果与全量闭包不一致\n"
        f"  多出: {sorted(map(show_triple, actual - expected))}\n"
        f"  缺少: {sorted(map(show_triple, expected - actual))}"
    )
    print(f"  [对照] {step}: 增量结论 {len(actual)} 条，与全量闭包一致 ✓")


def show_explanations(reasoner, triple, label):
    explanations = reasoner.explain(triple)
    if not explanations:
        print(f"  {label}: 不成立（无有效解释）")
        return set()
    print(f"  {label}: 成立，{len(explanations)} 条解释")
    for i, support in enumerate(sorted(explanations, key=len), 1):
        premises = " ∧ ".join(sorted(show_triple(t) for t in support))
        print(f"    解释{i}: {premises}")
    return set(explanations)


def report_invalidated(before, after):
    lost = before - after
    if not lost:
        print("  没有解释失效。")
    for support in sorted(lost, key=len):
        premises = " ∧ ".join(sorted(show_triple(t) for t in support))
        print(f"  ✗ 失效解释: {premises}")


def scenario_diamond():
    print("=" * 72)
    print("场景 1：菱形继承 —— 同一结论有多个支持，撤掉一个它不消失")
    print("=" * 72)
    reasoner = IncrementalRDFSReasoner()
    facts = [
        (EX.x, RDF.type, EX.A),
        (EX.A, RDFS.subClassOf, EX.B),
        (EX.A, RDFS.subClassOf, EX.C),
        (EX.B, RDFS.subClassOf, EX.D),
        (EX.C, RDFS.subClassOf, EX.D),
    ]
    for triple in facts:
        reasoner.add(triple)
    check_against_full_closure(reasoner, "初始 5 条显式事实")

    target = (EX.x, RDF.type, EX.D)
    print("菱形 A→B→D 与 A→C→D 两条路径都推出 x 的类型 D：")
    before = show_explanations(reasoner, target, "ex:x rdf:type ex:D")

    print("\n撤回 (ex:B, rdfs:subClassOf, ex:D)：")
    reasoner.retract((EX.B, RDFS.subClassOf, EX.D))
    check_against_full_closure(reasoner, "撤回 B subClassOf D 后")
    after = show_explanations(reasoner, target, "ex:x rdf:type ex:D")
    print("随之失效的解释：")
    report_invalidated(before, after)
    print("结论仍在：另一条经过 ex:C 的解释独立成立，结论不消失。")


def scenario_mutual_inheritance():
    print()
    print("=" * 72)
    print("场景 2：互相继承（循环）—— 循环不是脱离显式前提的永久支持")
    print("=" * 72)
    reasoner = IncrementalRDFSReasoner()
    facts = [
        (EX.A, RDFS.subClassOf, EX.B),
        (EX.B, RDFS.subClassOf, EX.A),
        (EX.y, RDF.type, EX.A),
    ]
    for triple in facts:
        reasoner.add(triple)
    check_against_full_closure(reasoner, "循环 A↔B 加实例 y")

    target = (EX.y, RDF.type, EX.B)
    print("循环中推出的结论：")
    before = show_explanations(reasoner, target, "ex:y rdf:type ex:B")
    show_explanations(reasoner, (EX.A, RDFS.subClassOf, EX.A),
                      "ex:A rdfs:subClassOf ex:A（循环自反结论）")

    print("\n撤回 (ex:A, rdfs:subClassOf, ex:B)，循环被拆断：")
    reasoner.retract((EX.A, RDFS.subClassOf, EX.B))
    check_against_full_closure(reasoner, "撤回 A subClassOf B 后")
    after = show_explanations(reasoner, target, "ex:y rdf:type ex:B")
    show_explanations(reasoner, (EX.A, RDFS.subClassOf, EX.A),
                      "ex:A rdfs:subClassOf ex:A")
    print("随之失效的解释：")
    report_invalidated(before, after)
    print("循环没有留下任何“自我维持”的结论：所有解释都锚定在显式前提上。")

    print("\n现在恢复循环，并由用户直接声明 ex:y rdf:type ex:B：")
    reasoner.add((EX.A, RDFS.subClassOf, EX.B))
    reasoner.add(target)
    check_against_full_closure(reasoner, "用户显式声明 y type B 后")
    show_explanations(reasoner, target, "ex:y rdf:type ex:B")

    print("\n再次撤回 (ex:A, rdfs:subClassOf, ex:B)：")
    reasoner.retract((EX.A, RDFS.subClassOf, EX.B))
    check_against_full_closure(reasoner, "再次撤回 A subClassOf B 后")
    show_explanations(reasoner, target, "ex:y rdf:type ex:B")
    assert reasoner.is_entailed(target) and reasoner.is_explicit(target)
    print("用户直接声明过的结论不被推导的连带删除影响，仍然成立。")


def scenario_property_rules():
    print()
    print("=" * 72)
    print("场景 3：属性规则相互触发 —— subPropertyOf / domain / range 链")
    print("=" * 72)
    reasoner = IncrementalRDFSReasoner()
    facts = [
        (EX.p, RDFS.subPropertyOf, EX.q),
        (EX.q, RDFS.subPropertyOf, EX.r),
        (EX.p, RDFS.domain, EX.C1),
        (EX.q, RDFS.range, EX.C2),
        (EX.C2, RDFS.subClassOf, EX.C3),
        (EX.x, EX.p, EX.y),
    ]
    for triple in facts:
        reasoner.add(triple)
    check_against_full_closure(reasoner, "属性链 + domain/range + 一条断言")

    watched = [
        (EX.p, RDFS.subPropertyOf, EX.r),
        (EX.x, EX.q, EX.y),
        (EX.x, EX.r, EX.y),
        (EX.x, RDF.type, EX.C1),
        (EX.y, RDF.type, EX.C2),
        (EX.y, RDF.type, EX.C3),
    ]
    print("由 (ex:x, ex:p, ex:y) 链式触发出的结论：")
    before = {}
    for triple in watched:
        before[triple] = show_explanations(reasoner, triple, show_triple(triple))

    print("\n撤回 (ex:p, rdfs:subPropertyOf, ex:q)，整条触发链回退：")
    reasoner.retract((EX.p, RDFS.subPropertyOf, EX.q))
    check_against_full_closure(reasoner, "撤回 p subPropertyOf q 后")
    for triple in watched:
        after = show_explanations(reasoner, triple, show_triple(triple))
        lost = before[triple] - after
        if lost:
            print("  随之失效的解释：")
            report_invalidated(before[triple], after)


def main():
    scenario_diamond()
    scenario_mutual_inheritance()
    scenario_property_rules()
    print()
    print("全部场景结束：每一步增量维护的结论都与逐次全量求闭包一致。")


if __name__ == "__main__":
    main()
