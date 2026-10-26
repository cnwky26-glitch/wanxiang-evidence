"""通过 DeepSeek 抽取、复核证据并计算同源分组。"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from deepseek_client import MODEL, complete_json
from evidence_extraction import build_batches, build_messages, build_review_messages, resolve_facts
from source_dedup import analyze_sources


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def extract_documents(documents, questions, output):
    evidence = []
    normalization_log = []
    batches = list(build_batches(documents))
    if not batches:
        raise ValueError("原始文档中没有可供抽取的段落")
    for number, batch in enumerate(batches, start=1):
        print(f"抽取批次 {number}/{len(batches)}", flush=True)
        draft = complete_json(build_messages(questions, batch), output / f"extract_{number:03d}.json")
        reviewed = complete_json(build_review_messages(questions, batch, draft), output / f"review_{number:03d}.json")
        facts = resolve_facts(reviewed, batch, normalization_log)
        evidence.extend(facts)
        print(f"复核批次 {number}/{len(batches)}：保留 {len(facts)} 条证据", flush=True)
    sources = analyze_sources(documents, evidence)
    write_json(output / "evidence.json", evidence)
    write_json(output / "source_analysis.json", sources)
    write_json(output / "normalization_log.json", normalization_log)
    return evidence, sources, len(batches)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="运行目录；相同请求可复用已保存响应")
    args = parser.parse_args()
    documents = json.loads(args.documents.read_text(encoding="utf-8-sig"))
    questions = json.loads(args.questions.read_text(encoding="utf-8-sig"))
    if not documents or not questions:
        raise ValueError("原始文档与问题清单不能为空")
    args.output.mkdir(parents=True, exist_ok=True)
    evidence, sources, batch_count = extract_documents(documents, questions, args.output)
    write_json(args.output / "run.json", {
        "status": "completed", "completed_at": datetime.now(timezone.utc).isoformat(),
        "provider": "DeepSeek", "model": MODEL, "thinking": "disabled",
        "documents_path": str(args.documents.resolve()), "questions_path": str(args.questions.resolve()),
        "document_count": len(documents), "question_count": len(questions), "batch_count": batch_count,
        "evidence_count": len(evidence), "quote_granularity": "claim_span",
        "source_group_count_estimate": sources["source_group_count_estimate"],
        "independent_source_count_estimate": sources["independent_source_count_estimate"],
    })
    print(f"证据输出：{args.output / 'evidence.json'}", flush=True)


if __name__ == "__main__":
    main()
