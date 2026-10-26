"""离线生成已有专题的 DAG、置信度及人工权重重算结果；不调用模型。"""
import argparse
import json
from pathlib import Path

from extract_evidence import write_json
from reasoning import build_graph, calculate_confidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--weights", type=Path, help="节点ID到0至100权重的JSON对象；0表示停用")
    args = parser.parse_args()
    manifest = json.loads((args.run_dir / "input_manifest.json").read_text(encoding="utf-8-sig"))
    evidence = json.loads((args.run_dir / "evidence_index.json").read_text(encoding="utf-8-sig"))
    arbitration = json.loads((args.run_dir / "arbitration.json").read_text(encoding="utf-8-sig"))
    graph = build_graph(manifest["documents"], evidence, arbitration)
    baseline = calculate_confidence(graph)
    args.output.mkdir(parents=True, exist_ok=True)
    write_json(args.output / "reasoning_graph.json", graph)
    write_json(args.output / "confidence.json", baseline)
    updated = None
    if args.weights:
        weights = json.loads(args.weights.read_text(encoding="utf-8-sig"))
        updated = calculate_confidence(graph, weights, previous=baseline)
        write_json(args.output / "confidence_after.json", updated)
        write_json(args.output / "intervention.json", {
            "input_run_dir": str(args.run_dir.resolve()), "weights": weights,
            "changed_node_ids": updated["changed_node_ids"],
            "recomputed_node_ids": updated["recomputed_node_ids"],
            "affected_judgment_ids": updated["affected_judgment_ids"],
            "changes": [{"node_id": nid, "before": baseline["nodes"][nid], "after": updated["nodes"][nid]}
                        for nid in updated["recomputed_node_ids"]],
            "text_policy": "保留原结论文字；受影响判断标记 needs_reassessment，未调用模型重新判断。",
        })
    counts = {kind: sum(node["type"] == kind for node in graph["nodes"])
              for kind in ("source", "paragraph", "evidence", "intermediate", "conclusion")}
    lines = ["# DAG 与置信度重算结果", "", "本轮离线复用已有专题，不调用模型。图谱表示引用和仲裁处理依赖。",
             "", f"节点 {len(graph['nodes'])} 个，边 {len(graph['edges'])} 条；节点分类："
             + json.dumps(counts, ensure_ascii=False) + "。"]
    lines.extend(["", "## 计算结果", "", "| 判断ID | 信源分 | 一致性分 | 链路分 | 基线分 | 调整后有效分 | 需重新研判 |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | --- |"])
    judgments = [node for node in graph["nodes"] if node["type"] in ("intermediate", "conclusion")]
    for node in judgments:
        nid = node["id"]
        before = baseline["nodes"][nid]
        after = updated["nodes"][nid] if updated else before
        comp = before["components"]
        lines.append(f"| {nid} | {comp['source']:.2f} | {comp['consistency']:.2f} | {comp['chain']:.2f} | "
                     f"{before['effective_score']:.2f} | {after['effective_score']:.2f} | {'是' if after['needs_reassessment'] else '否'} |")
    if updated:
        lines.extend(["", "人工覆盖值：`" + json.dumps(updated["weight_overrides"], ensure_ascii=False) + "`。",
                      f"仅重算 {len(updated['recomputed_node_ids'])} 个节点，其中受影响判断 "
                      + "、".join(updated["affected_judgment_ids"]) + "。其余节点复用基线值。",
                      "结论文字没有被改写；数值变化不能替代语义重审。"])
    lines.extend(["", "## 图谱概览", "", "支持引用为实线，冲突引用为虚线；这里只折叠展示证据集合。"
                  "reasoning_graph.json 保留逐条证据、原文段落和来源节点。", "", "```mermaid", "flowchart LR"])
    aliases = {node["id"]: f"n{index}" for index, node in enumerate(judgments)}
    for node in judgments:
        alias = aliases[node["id"]]
        lines.extend([f'  {alias}["{node["id"]}"]',
                      f'  s{alias}["支持证据 {len(node["supporting_evidence_ids"])} 条"] --> {alias}',
                      f'  r{alias}["冲突证据 {len(node["conflicting_evidence_ids"])} 条"] -.-> {alias}'])
        for premise in node["premise_ids"]:
            lines.append(f"  {aliases[premise]} --> {alias}")
    lines.extend(["```", "", "## 分项复算", "", "总分 = 0.50×信源分 + 0.30×一致性分 + 0.20×推理链分。"])
    for node in judgments:
        score = baseline["nodes"][node["id"]]
        comp, calc = score["components"], score["calculation"]
        lines.extend(["", f"### {node['id']}", "", node["text"], "",
                      f"信源分：{calc['source_numerator']:.6f} / {calc['support_mass']:.6f} = {comp['source']:.6f}。",
                      f"一致性：支持强度 {calc['support_mass']:.6f}，冲突强度 {calc['conflict_mass']:.6f}，"
                      f"有效来源组 {calc['active_source_group_count_estimate']}，结果 {comp['consistency']:.6f}。",
                      f"支持组覆盖 {calc['active_support_group_count']}/{calc['support_group_count']}；"
                      f"必需上游分：{json.dumps(calc['premises'], ensure_ascii=False)}；链路分 {comp['chain']:.6f}。",
                      f"0.50×{comp['source']:.6f} + 0.30×{comp['consistency']:.6f} + 0.20×{comp['chain']:.6f}"
                      f" = {score['computed_score']:.6f}；有效分 {score['effective_score']:.6f}。",
                      "逐组代表证据及全部引用见 confidence.json 的 calculation.source_groups。"])
    lines.extend(["", "## 规则和边界", ""])
    for key, text in baseline["formula"].items():
        if isinstance(text, str):
            lines.append(f"- {key}：{text}")
    lines.extend("- " + text for text in baseline["formula"]["limitations"])
    (args.output / "DAG与置信度重算.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"DAG完成：{counts}，{len(graph['edges'])}条边", flush=True)
    if updated:
        print("受影响判断：" + "、".join(updated["affected_judgment_ids"]), flush=True)
    print(f"输出目录：{args.output}", flush=True)


if __name__ == "__main__":
    main()
