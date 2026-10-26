"""对已有证据运行同源分组，不调用模型。"""
import argparse
import json
from pathlib import Path

from source_dedup import analyze_sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="新建的来源统计 JSON 文件")
    args = parser.parse_args()
    documents = json.loads(args.documents.read_text(encoding="utf-8-sig"))
    evidence = json.loads(args.evidence.read_text(encoding="utf-8-sig"))
    result = analyze_sources(documents, evidence)
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(result, target, ensure_ascii=False, indent=2)
        target.write("\n")
    print(f"{result['document_count']} 份文档，{result['source_group_count_estimate']} 个来源组；"
          f"证据涉及的独立来源组估计为 {result['independent_source_count_estimate']} 个")


if __name__ == "__main__":
    main()
