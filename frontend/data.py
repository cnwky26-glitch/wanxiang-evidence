"""本地数据适配层：只读 JSON，复用后端规则，不启动 HTTP 或调用模型。"""
from html import escape
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
from serve_api import TopicAPI

DEFAULT_RUN = ROOT / "artifacts" / "full_topic_ai171" / "run_01"
REQUIRED_FILES = ("input_manifest.json", "evidence_index.json", "arbitration.json",
                  "source_analysis.json", "run.json")


def fingerprint(directory):
    """将文件时间和大小加入缓存键；原地更新数据也会自动重新载入。"""
    root = Path(directory).expanduser().resolve()
    missing = [name for name in REQUIRED_FILES if not (root / name).is_file()]
    if missing:
        raise ValueError("运行目录缺少文件：" + "、".join(missing))
    paths = [root / name for name in REQUIRED_FILES]
    paths += [BACKEND / "serve_api.py", BACKEND / "reasoning.py"]
    return tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in paths)


def load_topic(directory):
    root = Path(directory).expanduser().resolve()
    fingerprint(root)
    topic = TopicAPI(root, root.parent.name.removeprefix("full_topic_"))
    if not topic.questions or not topic.evidence:
        raise ValueError("运行快照中没有问题或证据，无法展示。")
    # 提前校验每条引用，避免点击后才发现快照缺少原文。
    for eid in topic.evidence:
        detail = topic.evidence_detail(eid)
        if not detail["evidence"]["quote"]:
            raise ValueError(f"{eid} 缺少原文引用。")
        paragraph = detail["paragraph"]
        doc = detail["document"]
        if doc["content"][paragraph["char_start"]:paragraph["char_end"]] != paragraph["text"]:
            raise ValueError(f"{eid} 的段落位置与原文不一致。")
    return topic


def evidence_ids(topic, qid, scope="当前问题全部依据"):
    decision = topic.questions[qid]
    if scope == "直接支持":
        return list(decision["supporting_evidence_ids"])
    if scope == "直接冲突":
        return list(decision["conflicting_evidence_ids"])
    if scope == "专题全部证据":
        return sorted(topic.evidence)
    # 包括只被中间判断引用的证据。
    return sorted(n["id"] for n in topic.graph_view(qid)["nodes"] if n["type"] == "evidence")


def role_for(topic, qid, eid):
    decision = topic.questions[qid]
    if eid in decision["supporting_evidence_ids"]:
        return "直接支持"
    if eid in decision["conflicting_evidence_ids"]:
        return "直接冲突"
    return "中间判断依据" if eid in evidence_ids(topic, qid) else "其他证据"


def highlight(text, start, end):
    """只高亮指定位置，所有原文先转义，绝不执行材料中的 HTML。"""
    if not 0 <= start < end <= len(text):
        raise ValueError("引用区间超出原文范围。")
    return (escape(text[:start]) + '<mark class="quote-mark">' +
            escape(text[start:end]) + '</mark>' + escape(text[end:]))


def quote_html(detail, full_document=False, context_chars=None):
    p = detail["paragraph"]
    start, end = p["quote_start"], p["quote_end"]
    if full_document:
        start += p["char_start"]
        end += p["char_start"]
        text = detail["document"]["content"]
    else:
        text = p["text"]
    prefix = suffix = ""
    if context_chars is not None:
        left, right = max(0, start - context_chars), min(len(text), end + context_chars)
        prefix = '<span class="context-gap" title="前文已省略">…</span>' if left else ""
        suffix = '<span class="context-gap" title="后文已省略">…</span>' if right < len(text) else ""
        text, start, end = text[left:right], start - left, end - left
    return '<div class="original-text">' + prefix + highlight(text, start, end) + suffix + '</div>'


def change_weights(topic, current, node_id, value):
    """先验证完整新覆盖表；相同于证据基线的值移除，避免虚假的变更。"""
    updated = dict(current)
    if value is None or (node_id in topic.evidence and value == topic.evidence[node_id]["base_weight"]):
        updated.pop(node_id, None)
    else:
        updated[node_id] = value
    result = topic.recalculate(updated)
    return updated, result


def select_graph_node(topic, qid, node_id):
    """图中节点定位：证据直接打开；判断/来源显示其关联证据。"""
    node = topic.nodes[node_id]
    ids = evidence_ids(topic, qid)
    if node["type"] == "evidence":
        return [node_id]
    if node["type"] in ("conclusion", "intermediate"):
        return list(dict.fromkeys(node["supporting_evidence_ids"] + node["conflicting_evidence_ids"]))
    return [eid for eid in ids if topic.evidence[eid]["doc_id"] == node["doc_id"]
            and (node["type"] != "paragraph" or topic.evidence[eid]["para_id"] == node["para_id"])]
