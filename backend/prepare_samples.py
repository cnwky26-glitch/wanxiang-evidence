"""对齐赛方参考样例的字段并记录源数据问题，不生成研判答案。"""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "多智能体可信研判与证据链溯源" / "样例集-拓尔思-20261012"
OUTPUT = ROOT / "artifacts" / "sample_alignment"
ARGUMENT_FIELDS = ("时间", "主体", "地点", "内容", "影响", "数值口径")


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare_topic(folder):
    documents, evidence, conclusions = [
        json.loads((folder / name).read_text(encoding="utf-8-sig"))
        for name in ("raw_documents.json", "evidence_samples.json", "conclusions.json")
    ]
    documents_by_id = {doc["doc_id"]: doc for doc in documents}
    evidence_by_id = {fact["fact_id"]: fact for fact in evidence}
    if len(documents_by_id) != len(documents) or len(evidence_by_id) != len(evidence):
        raise ValueError(f"{folder.name}：文档或证据 ID 重复，不能建立唯一引用")

    issues = []
    pending_fields = []
    content_aliases = []
    aligned_evidence = []
    for fact in evidence:
        source_argument = fact["argument"].copy()
        if "content" in source_argument:
            if "内容" in source_argument:
                raise ValueError(f"{folder.name}/{fact['fact_id']}：内容字段与 content 同时存在")
            source_argument["内容"] = source_argument.pop("content")
            content_aliases.append(fact["fact_id"])
        missing = [key for key in ARGUMENT_FIELDS if key not in source_argument]
        if missing:
            pending_fields.append({"fact_id": fact["fact_id"], "fields": missing})
        aligned_evidence.append({
            "doc_id": fact["doc_id"], "para_id": fact["para_id"],
            "quote": fact["quote"], "fact_des": fact["fact_des"],
            "argument": {key: source_argument.get(key, "") for key in ARGUMENT_FIELDS},
            "entities": fact["entities"],
        })

        location = {key: fact[key] for key in ("fact_id", "doc_id", "para_id")}
        doc = documents_by_id.get(fact["doc_id"])
        if doc is None:
            issues.append({**location, "severity": "blocking", "code": "missing_document"})
            continue
        paragraphs = {para["para_id"]: para for para in doc["paragraphs"]}
        para = paragraphs.get(fact["para_id"])
        if para is None:
            issues.append({**location, "severity": "blocking", "code": "missing_paragraph"})
            continue
        start, end = para["char_start"], para["char_end"]
        if not 0 <= start < end <= len(doc["content"]):
            issues.append({**location, "severity": "blocking", "code": "invalid_paragraph_range"})
            continue
        if not fact["quote"] or fact["quote"] not in doc["content"][start:end]:
            issues.append({
                **location, "severity": "blocking", "code": "quote_not_in_paragraph",
                "quote_in_document": bool(fact["quote"]) and fact["quote"] in doc["content"],
            })

    questions = []
    aligned_conclusions = []
    reference_count = 0
    for conclusion in conclusions:
        questions.append({key: conclusion[key] for key in ("question_id", "question")})
        aligned = {key: conclusion[key] for key in ("question_id", "question", "conclusion")}
        for side in ("supporting_facts", "conflicting_facts"):
            aligned[side] = []
            for ref in conclusion[side]:
                fact = evidence_by_id[ref["fact_id"]]
                if ref["doc_id"] != fact["doc_id"]:
                    raise ValueError(f"{folder.name}/{conclusion['question_id']}：引用的文档与证据不一致")
                aligned[side].append({key: fact[key] for key in ("doc_id", "para_id")})
                reference_count += 1
                if ref["quote"] != fact["quote"]:
                    issues.append({
                        "severity": "review", "code": "reference_quote_differs",
                        "question_id": conclusion["question_id"], "side": side,
                        "fact_id": ref["fact_id"], "doc_id": fact["doc_id"],
                        "para_id": fact["para_id"],
                    })
        aligned_conclusions.append(aligned)

    target = OUTPUT / folder.name
    target.mkdir(parents=True, exist_ok=True)
    write_json(target / "questions.json", questions)
    write_json(target / "reference_evidence_draft.json", aligned_evidence)
    write_json(target / "reference_conclusions_draft.json", aligned_conclusions)
    return {
        "topic": folder.name, "documents": len(documents), "evidence": len(evidence),
        "questions": len(questions), "reference_status": "draft",
        "blocking_count": sum(issue["severity"] == "blocking" for issue in issues),
        "reference_quote_review_count": sum(issue["severity"] == "review" for issue in issues),
        "adaptations": {"reference_para_ids_added": reference_count,
                        "content_alias_fact_ids": content_aliases},
        "argument_fields_to_review": pending_fields, "issues": issues,
    }


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    topics = [prepare_topic(folder) for folder in sorted(DATASET.iterdir()) if folder.is_dir()]
    summary = {
        "topics": len(topics),
        **{key: sum(topic[key] for topic in topics) for key in (
            "documents", "evidence", "questions", "blocking_count", "reference_quote_review_count")},
    }
    report = {
        "scope": "参考样例字段适配与定位核对；未评估语义正确性，不代表算法预测或可提交结果。",
        "empty_field_policy": "缺失 argument 字段暂填空字符串并逐条记录待核对；不代表原文确无该信息。",
        "summary": summary, "topics": topics,
    }
    write_json(OUTPUT / "sample_issues.json", report)
    lines = [
        "# 样例字段适配与问题清单", "", report["scope"], "", report["empty_field_policy"], "",
        "| 专题 | 文档 | 证据 | 问题 | 定位阻塞 | 引用文本待核对 | 缺失字段待核对条目 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for topic in topics:
        lines.append(f"| {topic['topic']} | {topic['documents']} | {topic['evidence']} | "
                     f"{topic['questions']} | {topic['blocking_count']} | "
                     f"{topic['reference_quote_review_count']} | {len(topic['argument_fields_to_review'])} |")
    lines.extend(["", "问题代码：missing_document 为缺少文档；missing_paragraph 为缺少段落；"
                  "invalid_paragraph_range 为段落偏移越界；quote_not_in_paragraph 为引用未在指定段落逐字命中；"
                  "reference_quote_differs 为结论内嵌引用与证据表原文不一致。", ""])
    for topic in topics:
        lines.extend([f"## {topic['topic']}", "",
                      f"按 fact_id 补出 {topic['adaptations']['reference_para_ids_added']} 条段落引用。"])
        if topic["adaptations"]["content_alias_fact_ids"]:
            lines.append("content 对齐为“内容”：" + "、".join(topic["adaptations"]["content_alias_fact_ids"]) + "。")
        lines.append("")
        for issue in topic["issues"]:
            label = "阻塞" if issue["severity"] == "blocking" else "待核对"
            question = issue.get("question_id", "")
            lines.append(f"- {label}：{question} {issue['fact_id']}，{issue['doc_id']} / "
                         f"{issue['para_id']}，`{issue['code']}`。")
        if not topic["issues"]:
            lines.append("原文定位与引用文本核对通过；字段语义准确性仍待核对。")
        lines.extend(["", "缺失字段明细见 sample_issues.json 的 argument_fields_to_review。", ""])
    (OUTPUT / "样例问题清单.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    print(f"结果目录：{OUTPUT}")


if __name__ == "__main__":
    main()
