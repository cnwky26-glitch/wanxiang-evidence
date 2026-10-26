"""按文章地址、正文和明确通稿来源分组，保留所有文档与证据。"""
from collections import defaultdict
from hashlib import sha256
from itertools import combinations
import re
from urllib.parse import parse_qsl, urlencode, urlsplit


WIRE_PATTERN = re.compile(
    r"(?:^|\n)\s*(新华社|中新社|中新网)[^\n。]{0,50}电"
    r"|(?:来源|转自)\s*[:：]\s*(新华社|中新社|中新网|路透社|美联社|央视新闻)"
)


def article_url(value):
    if not value:
        return ""
    parts = urlsplit(value)
    host = parts.netloc.casefold().removeprefix("www.")
    query = urlencode(sorted((key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
                             if not key.casefold().startswith("utm_")))
    return host + parts.path.rstrip("/") + ("?" + query if query else "")


def analyze_sources(documents, evidence):
    parents = {doc["doc_id"]: doc["doc_id"] for doc in documents}

    def find(doc_id):
        while parents[doc_id] != doc_id:
            parents[doc_id] = parents[parents[doc_id]]
            doc_id = parents[doc_id]
        return doc_id

    features = {}
    for doc in documents:
        text = re.sub(r"\s+", "", doc["content"]).casefold()
        marker = WIRE_PATTERN.search(doc["content"])
        wire = next(group for group in marker.groups() if group) if marker else ""
        features[doc["doc_id"]] = {
            "url": article_url(doc.get("content_url", "")), "text": text,
            "digest": sha256(text.encode("utf-8")).hexdigest(), "wire": wire,
            "shingles": {text[i:i + 12] for i in range(len(text) - 11)} if wire else set(),
        }

    links = []
    for left, right in combinations(features, 2):
        a, b = features[left], features[right]
        rules = []
        if a["url"] and a["url"] == b["url"]:
            rules.append("same_article_url")
        if min(len(a["text"]), len(b["text"])) >= 80 and a["digest"] == b["digest"]:
            rules.append("same_normalized_text")
        similarity = None
        if (not rules and a["wire"] and a["wire"] == b["wire"]
                and min(len(a["text"]), len(b["text"])) >= 200):
            similarity = len(a["shingles"] & b["shingles"]) / len(a["shingles"] | b["shingles"])
            if similarity >= 0.85:
                rules.append("wire_reprint")
        if rules:
            root_a, root_b = find(left), find(right)
            parents[max(root_a, root_b)] = min(root_a, root_b)
            link = {"left_doc_id": left, "right_doc_id": right, "rules": rules}
            if "wire_reprint" in rules:
                link.update(wire=a["wire"], text_similarity=round(similarity, 4))
            links.append(link)

    members = defaultdict(list)
    for doc in documents:
        members[find(doc["doc_id"])].append(doc)
    groups = []
    origins = {}
    for root, docs in sorted(members.items()):
        origin_id = "origin:" + root
        doc_ids = [doc["doc_id"] for doc in docs]
        origins.update((doc_id, origin_id) for doc_id in doc_ids)
        groups.append({
            "origin_id": origin_id, "doc_ids": doc_ids,
            "documents": [{key: doc[key] for key in ("doc_id", "title", "content_url") if key in doc}
                          for doc in docs],
            "matching_records": [link for link in links if link["left_doc_id"] in doc_ids],
        })
    assignments = [{"evidence_index": index, "doc_id": fact["doc_id"], "para_id": fact["para_id"],
                    "origin_id": origins[fact["doc_id"]]} for index, fact in enumerate(evidence)]
    return {
        "document_count": len(documents), "source_group_count_estimate": len(groups),
        "evidence_count": len(evidence),
        "evidence_document_count": len({fact["doc_id"] for fact in evidence}),
        "independent_source_count_estimate": len({item["origin_id"] for item in assignments}),
        "count_basis": "证据所在文章的来源组数估计；未识别的共同消息源可能使计数偏高，不证明真正相互独立。",
        "rules": {
            "same_article_url": "忽略协议、www、片段和 utm_ 跟踪参数后的文章地址相同；保留不同正文版本。",
            "same_normalized_text": "仅去空白并统一英文大小写后，至少80字符的正文相同；不改动原文。",
            "wire_reprint": "有相同的明确通稿来源，正文至少200字符，12字符片段集合的 Jaccard 相似度不低于0.85。",
        },
        "groups": groups, "evidence_sources": assignments,
    }
