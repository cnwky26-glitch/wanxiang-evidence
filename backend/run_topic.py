"""完整专题流程：原文到证据、同源分组、双角色仲裁、DAG及置信度。"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from deepseek_client import MODEL
from extract_evidence import extract_documents, write_json
from judgment import judge_topic
from reasoning import build_graph, calculate_confidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    documents = json.loads(args.documents.read_text(encoding="utf-8-sig"))
    questions = json.loads(args.questions.read_text(encoding="utf-8-sig"))
    if not documents or not questions:
        raise ValueError("原文与问题不能为空")
    args.output.mkdir(parents=True, exist_ok=True)
    # 请求正文会写入运行目录并在复用时逐项比较，禁止混用变化后的输入。
    manifest = {"documents": documents, "questions": questions}
    manifest_path = args.output / "input_manifest.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
        raise ValueError("本次输入与运行目录不一致，请使用新目录")
    write_json(manifest_path, manifest)
    started = datetime.now(timezone.utc)
    evidence, sources, batch_count = extract_documents(documents, questions, args.output)
    print(f"证据复核完成：{len(evidence)} 条；进入双角色研判", flush=True)
    conclusions, arbitration, context = judge_topic(questions, documents, evidence, sources, args.output)
    write_json(args.output / "conclusions.json", conclusions)
    write_json(args.output / "arbitration.json", arbitration)
    write_json(args.output / "evidence_index.json", context)
    graph = build_graph(documents, context, arbitration)
    confidence = calculate_confidence(graph)
    write_json(args.output / "reasoning_graph.json", graph)
    write_json(args.output / "confidence.json", confidence)
    write_json(args.output / "run.json", {
        "status": "completed", "model": MODEL, "document_count": len(documents),
        "question_count": len(questions), "extraction_batches": batch_count,
        "evidence_count": len(evidence), "conclusion_count": len(conclusions),
        "source_group_count_estimate": sources["source_group_count_estimate"],
        "conflict_record_count": sum(len(item["conflicts"]) for item in arbitration["decisions"]),
        "graph_node_count": len(graph["nodes"]), "graph_edge_count": len(graph["edges"]),
        "started_at": started.isoformat(), "completed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "含 DAG 与启发式置信度的完整专题功能运行；未进行官方语义评分，尚未前端联调。",
    })
    lines = ["# 专题研判结果", "", f"原文 {len(documents)} 份，证据 {len(evidence)} 条，问题 {len(questions)} 个。",
             "", "以下结果由双角色独立研判后仲裁生成，仅依据输入材料。来源组数量是规则估计，不证明真正相互独立。", ""]
    for record in arbitration["decisions"]:
        lines.extend([f"## {record['question_id']} {record['question']}", "", record["conclusion"], "",
                      "仲裁理由：" + record["rationale"], ""])
        score = confidence["nodes"]["C:" + record["question_id"]]
        lines.extend([f"启发式置信度：{score['effective_score']:.2f} / 100（不是正确概率）。", ""])
        for agent in arbitration["independent_assessments"]:
            position = next(value for value in agent["assessments"] if value["question_id"] == record["question_id"])
            lines.extend([f"Agent {agent['agent_id']} 独立结论：" + position["conclusion"], ""])
        for conflict in record["conflicts"]:
            lines.extend([f"- 冲突类型：{conflict['conflict_type']}；消解类型：{conflict['resolution_type']}。",
                          "  结果：" + conflict["result"], "  依据：" + conflict["basis"],
                          "  保留意见：" + (conflict["minority_opinion"] or "无额外保留意见"),
                          "  重新评估条件：" + conflict["reopen_condition"]])
        lines.append("")
        for item in record["minority_agent_opinions"]:
            lines.extend([f"Agent {item['agent_id']} 未全采纳意见：{item['original_conclusion']}",
                          "重新评估条件：" + item["reopen_condition"], ""])
        lines.extend(["支撑证据：" + "、".join(record["supporting_evidence_ids"]),
                      "冲突证据：" + "、".join(record["conflicting_evidence_ids"]), ""])
    lines.extend(["## 结果文件", "", "evidence.json 为 A.2 证据；conclusions.json 为 A.3 结论；"
                  "arbitration.json 保存独立意见、冲突处理和重启条件；evidence_index.json 按 evidence_id 提供对应原文；"
                  "reasoning_graph.json 保存含中间判断的依赖图；confidence.json 保存公式、分项和逐组计算依据。",
                  "人工重算可离线使用 build_reasoning.py，不需要重新调用模型。"])
    (args.output / "专题研判结果.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"专题流程完成：{len(conclusions)} 条最终结论，输出目录 {args.output}", flush=True)


if __name__ == "__main__":
    main()
