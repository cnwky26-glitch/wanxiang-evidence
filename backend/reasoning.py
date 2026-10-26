"""由已有仲裁记录构建依赖 DAG，并按公开规则重算置信度。"""
from collections import defaultdict
from graphlib import TopologicalSorter
from math import isfinite


FORMULA = {
    "weights": {"source": 0.5, "consistency": 0.3, "chain": 0.2},
    "source": "支持侧每个来源组取最高有效权重 w；信源分 = sum(w*w)/sum(w)。",
    "consistency": "支持强度 S、冲突强度 R 分别为各侧来源组最高有效权重之和；"
                   "有效来源组并集不足2组时取50，否则取100*S/(S+R)；无支持时取0。",
    "chain": "覆盖分 = 100*仍有效的支持来源组数/原支持来源组数；"
             "推理链分 = min(覆盖分, 各必需中间判断的有效分)，无中间判断时等于覆盖分。",
    "total": "计算分 = 0.5*信源分 + 0.3*一致性分 + 0.2*推理链分；"
             "无有效支持或必需中间判断失效时，有效分为0。内部不提前舍入，展示保留2位小数。",
    "intervention": "证据权重默认取数据集 source_credibility_prior*100；0表示撤销。"
                    "也可指定中间/最终判断的人工有效分，0表示停用；不允许用人工正分恢复已失去支撑的判断。",
    "limitations": [
        "团队启发式公式，未经概率校准；来源先验来自输入数据，不是本程序实测。",
        "一致性按仲裁引用的支持/冲突角色计算。不同时间、口径或对不确定性的讨论可能保留双方引用，"
        "此时低一致性不等于谨慎结论错误，也不证明双方在相同口径下互相否定。",
        "覆盖率和上游分数只是推理链可靠性的代理，不能验证逻辑或证明因果。",
        "来源组是规则估计；同组两侧均出现时保留内部矛盾，不按篇数投票。",
        "中间节点复用已有冲突处理结果，边表示引用或处理依赖，不是新生成的隐含推理。",
        "人工调整只重算数值和有效状态，原结论文字保留；受影响判断必须重新审阅。",
    ],
}


def build_graph(documents, evidence, arbitration):
    nodes, edges = {}, []
    docs = {doc["doc_id"]: doc for doc in documents}
    paragraphs = {(doc["doc_id"], para["para_id"]): para
                  for doc in documents for para in doc["paragraphs"]}
    for item in evidence:
        weight = item["source_credibility_prior"]
        if type(weight) not in (int, float) or not isfinite(weight) or not 0 <= weight <= 1:
            raise ValueError(f"{item['evidence_id']} 缺少有效的0至1来源先验")
        doc_id, para_id = item["doc_id"], item["para_id"]
        doc, para = docs[doc_id], paragraphs[(doc_id, para_id)]
        source_id, paragraph_id = "S:" + doc_id, "P:" + para_id
        if source_id not in nodes:
            nodes[source_id] = {"id": source_id, "type": "source", "doc_id": doc_id,
                                "title": doc["title"], "url": item["source_url"],
                                "origin_id": item["origin_id"], "publish_time": doc["publish_time"]}
        if paragraph_id not in nodes:
            nodes[paragraph_id] = {"id": paragraph_id, "type": "paragraph", "doc_id": doc_id,
                                   **para, "text": doc["content"][para["char_start"]:para["char_end"]]}
            edges.append({"source": source_id, "target": paragraph_id, "relation": "contains"})
        nodes[item["evidence_id"]] = {**item, "id": item["evidence_id"], "type": "evidence",
                                       "base_weight": weight * 100}
        edges.append({"source": paragraph_id, "target": item["evidence_id"], "relation": "extracts"})
    for decision in arbitration["decisions"]:
        qid = decision["question_id"]
        intermediate_ids = []
        for index, conflict in enumerate(decision["conflicts"], 1):
            node_id = f"I:{qid}:{index:02d}"
            intermediate_ids.append(node_id)
            nodes[node_id] = {**conflict, "id": node_id, "type": "intermediate",
                               "question_id": qid, "text": conflict["result"],
                               "premise_ids": []}
        node_id = "C:" + qid
        nodes[node_id] = {"id": node_id, "type": "conclusion", "question_id": qid,
                          "question": decision["question"], "text": decision["conclusion"],
                          "supporting_evidence_ids": decision["supporting_evidence_ids"],
                          "conflicting_evidence_ids": decision["conflicting_evidence_ids"],
                          "premise_ids": intermediate_ids, "rationale": decision["rationale"],
                          "minority_agent_opinions": decision["minority_agent_opinions"]}
        for premise in intermediate_ids:
            edges.append({"source": premise, "target": node_id, "relation": "premise",
                          "basis": "该问题最终仲裁所列的必需处理步骤；不另计为独立信源"})
    for node in list(nodes.values()):
        if node["type"] not in ("intermediate", "conclusion"):
            continue
        for key, relation in (("supporting_evidence_ids", "supports"), ("conflicting_evidence_ids", "opposes")):
            for evidence_id in node[key]:
                if nodes[evidence_id]["type"] != "evidence":
                    raise ValueError(f"{node['id']} 引用非证据节点 {evidence_id}")
                edges.append({"source": evidence_id, "target": node["id"], "relation": relation})
    parents = {node_id: [] for node_id in nodes}
    for edge in edges:
        parents[edge["target"]].append(edge["source"])
    return {"schema_version": "1.0", "meaning": "引用与仲裁处理依赖图，不是因果图",
            "nodes": list(nodes.values()), "edges": edges,
            "topological_order": list(TopologicalSorter(parents).static_order())}


def score_judgment(node, nodes, scores):
    sides = {}
    for side in ("supporting", "conflicting"):
        groups = defaultdict(list)
        for eid in node[side + "_evidence_ids"]:
            groups[nodes[eid]["origin_id"]].append(eid)
        sides[side] = [{"origin_id": origin, "evidence_ids": ids,
                        "representative_id": max(ids, key=lambda eid: scores[eid]["effective_weight"]),
                        "weight": max(scores[eid]["effective_weight"] for eid in ids)}
                       for origin, ids in sorted(groups.items())]
    support = sides["supporting"]
    conflict = sides["conflicting"]
    support_mass = sum(group["weight"] for group in support)
    conflict_mass = sum(group["weight"] for group in conflict)
    active_support = [group for group in support if group["weight"] > 0]
    active_origins = {group["origin_id"] for group in support + conflict if group["weight"] > 0}
    source_numerator = sum(group["weight"] ** 2 for group in support)
    source = source_numerator / support_mass if support_mass else 0
    consistency = (50 if len(active_origins) < 2 else 100 * support_mass / (support_mass + conflict_mass)) if support_mass else 0
    coverage = 100 * len(active_support) / len(support) if support else 0
    premises = [{"id": pid, "effective_score": scores[pid]["effective_score"],
                 "status": scores[pid]["status"]} for pid in node["premise_ids"]]
    chain = min([coverage] + [item["effective_score"] for item in premises])
    computed = source * 0.5 + consistency * 0.3 + chain * 0.2
    status = "active" if support_mass else "unsupported"
    if support_mass and any(item["effective_score"] == 0 for item in premises):
        status = "upstream_blocked"
    return {"status": status, "computed_score": computed,
            "effective_score": computed if status == "active" else 0,
            "components": {"source": source, "consistency": consistency, "chain": chain},
            "calculation": {"source_numerator": source_numerator, "support_mass": support_mass,
                            "conflict_mass": conflict_mass, "active_source_group_count_estimate": len(active_origins),
                            "active_support_group_count": len(active_support), "support_group_count": len(support),
                            "support_coverage": coverage, "premises": premises, "source_groups": sides}}


def calculate_confidence(graph, weights=None, previous=None):
    """previous 必须来自同一 DAG；有前次结果时只计算权重变化节点及其后代。"""
    weights = {} if weights is None else weights
    if not isinstance(weights, dict):
        raise ValueError("weights 必须是节点ID到0至100数字的对象")
    nodes = {node["id"]: node for node in graph["nodes"]}
    for node_id, weight in weights.items():
        if node_id not in nodes or nodes[node_id]["type"] not in ("evidence", "intermediate", "conclusion"):
            raise ValueError(f"不可调整的节点ID：{node_id}")
        if type(weight) not in (int, float) or not isfinite(weight) or not 0 <= weight <= 100:
            raise ValueError(f"{node_id} 权重必须是0至100的有限数字")
    old_weights = previous["weight_overrides"] if previous else {}
    changed = {key for key in weights.keys() | old_weights.keys() if weights.get(key) != old_weights.get(key)}
    children = defaultdict(list)
    for edge in graph["edges"]:
        children[edge["source"]].append(edge["target"])
    affected = set(changed)
    pending = list(changed)
    while pending:
        for child in children[pending.pop()]:
            if child not in affected:
                affected.add(child)
                pending.append(child)
    scores = dict(previous["nodes"]) if previous else {}
    recomputed = []
    for node_id in graph["topological_order"]:
        node = nodes[node_id]
        if node["type"] in ("source", "paragraph") or (previous and node_id not in affected):
            continue
        if node["type"] == "evidence":
            effective = weights.get(node_id, node["base_weight"])
            scores[node_id] = {"base_weight": node["base_weight"], "effective_weight": effective,
                               "status": "active" if effective > 0 else "withdrawn"}
        else:
            result = score_judgment(node, nodes, scores)
            result["manual_score"] = weights.get(node_id)
            if node_id in weights and result["status"] == "active":
                result["effective_score"] = weights[node_id]
                if weights[node_id] == 0:
                    result["status"] = "disabled"
            result["needs_reassessment"] = node_id in affected
            scores[node_id] = result
        recomputed.append(node_id)
    return {"formula": FORMULA, "weight_overrides": weights, "nodes": scores,
            "changed_node_ids": sorted(changed), "recomputed_node_ids": recomputed,
            "affected_judgment_ids": [nid for nid in graph["topological_order"]
                                      if nid in affected and nodes[nid]["type"] in ("intermediate", "conclusion")]}
