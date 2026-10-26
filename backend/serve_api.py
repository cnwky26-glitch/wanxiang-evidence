"""供前端读取已完成专题、下钻原文和预览权重重算；不调用模型。"""
import argparse
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from reasoning import build_graph, calculate_confidence


class TopicAPI:
    def __init__(self, run_dir, topic_id):
        inputs = {name: json.loads((run_dir / (name + ".json")).read_text(encoding="utf-8-sig"))
                  for name in ("input_manifest", "evidence_index", "arbitration", "source_analysis", "run")}
        self.topic_id = topic_id
        self.run_id = run_dir.name
        self.run = inputs["run"]
        manifest = inputs["input_manifest"]
        self.documents = {doc["doc_id"]: doc for doc in manifest["documents"]}
        self.questions = {item["question_id"]: item for item in inputs["arbitration"]["decisions"]}
        self.assessments = inputs["arbitration"]["independent_assessments"]
        self.sources = inputs["source_analysis"]
        self.graph = build_graph(manifest["documents"], inputs["evidence_index"], inputs["arbitration"])
        self.baseline = calculate_confidence(self.graph)
        self.nodes = {node["id"]: node for node in self.graph["nodes"]}
        self.evidence = {node["id"]: node for node in self.graph["nodes"] if node["type"] == "evidence"}
        self.parents = defaultdict(list)
        for edge in self.graph["edges"]:
            self.parents[edge["target"]].append(edge["source"])

    def overview(self):
        return {
            "schema_version": "1.0", "topic_id": self.topic_id, "run_id": self.run_id,
            "model": self.run["model"],
            "counts": {"documents": len(self.documents), "evidence": len(self.evidence),
                       "questions": len(self.questions),
                       "intermediate": sum(node["type"] == "intermediate" for node in self.graph["nodes"]),
                       "source_groups_estimate": self.sources["source_group_count_estimate"]},
            "source_count_basis": self.sources["count_basis"],
            "questions": [{"question_id": qid, "question": item["question"], "conclusion": item["conclusion"],
                           "node_id": "C:" + qid, "confidence": self.baseline["nodes"]["C:" + qid]}
                          for qid, item in self.questions.items()],
            "formula": self.baseline["formula"],
            "scope": "已完成专题的本地接口；GET返回基线；POST只预览完整权重覆盖表，不保存或改写模型结论。",
        }

    def question(self, qid):
        node_id = "C:" + qid
        return {
            "topic_id": self.topic_id, **self.questions[qid], "node_id": node_id,
            "confidence": self.baseline["nodes"][node_id],
            "independent_assessments": [
                {"agent_id": agent["agent_id"], "role": agent["role"],
                 **next(item for item in agent["assessments"] if item["question_id"] == qid)}
                for agent in self.assessments],
            "intermediate_nodes": [{**self.nodes[pid], "confidence": self.baseline["nodes"][pid]}
                                   for pid in self.nodes[node_id]["premise_ids"]],
        }

    def graph_view(self, qid=None):
        included = set(self.nodes) if qid is None else {"C:" + qid}
        pending = [] if qid is None else list(included)
        while pending:
            for parent in self.parents[pending.pop()]:
                if parent not in included:
                    included.add(parent)
                    pending.append(parent)
        return {"topic_id": self.topic_id, "schema_version": self.graph["schema_version"],
                "meaning": self.graph["meaning"],
                "nodes": [node for node in self.graph["nodes"] if node["id"] in included],
                "edges": [edge for edge in self.graph["edges"]
                          if edge["source"] in included and edge["target"] in included],
                "topological_order": [nid for nid in self.graph["topological_order"] if nid in included],
                "confidence": {nid: score for nid, score in self.baseline["nodes"].items() if nid in included}}

    def evidence_detail(self, eid):
        fact = self.evidence[eid]
        paragraph = self.nodes["P:" + fact["para_id"]]
        start = paragraph["text"].index(fact["quote"])
        end = start + len(fact["quote"])
        return {
            "topic_id": self.topic_id, "evidence": fact,
            "confidence": self.baseline["nodes"][eid],
            "paragraph": {**paragraph, "quote_start": start, "quote_end": end,
                          "offset_unit": "unicode_code_point", "before_quote": paragraph["text"][:start],
                          "quote": fact["quote"], "after_quote": paragraph["text"][end:]},
            "document": self.documents[fact["doc_id"]],
            "references": [edge for edge in self.graph["edges"]
                           if edge["source"] == eid and edge["relation"] in ("supports", "opposes")],
        }

    def recalculate(self, weights):
        updated = calculate_confidence(self.graph, weights, previous=self.baseline)
        return {
            "topic_id": self.topic_id, "confidence": updated,
            "changes": [{"node_id": nid, "before": self.baseline["nodes"][nid], "after": updated["nodes"][nid]}
                        for nid in updated["recomputed_node_ids"]],
            "conclusions": [{"question_id": qid, "node_id": "C:" + qid, "text": item["conclusion"],
                             "confidence": updated["nodes"]["C:" + qid]} for qid, item in self.questions.items()],
            "text_policy": "保留原结论文字；受影响判断标记needs_reassessment，未调用模型重新研判。",
        }


def make_handler(topic):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, body):
            data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_OPTIONS(self):
            self.respond(200, {"methods": ["GET", "POST", "OPTIONS"]})

        def do_GET(self):
            url = urlsplit(self.path)
            parts = [unquote(part) for part in url.path.strip("/").split("/")]
            if parts == ["api", "topic"]:
                self.respond(200, topic.overview())
            elif len(parts) == 3 and parts[:2] == ["api", "questions"] and parts[2] in topic.questions:
                self.respond(200, topic.question(parts[2]))
            elif len(parts) == 3 and parts[:2] == ["api", "evidence"] and parts[2] in topic.evidence:
                self.respond(200, topic.evidence_detail(parts[2]))
            elif parts == ["api", "graph"]:
                query = parse_qs(url.query, keep_blank_values=True)
                if query and (set(query) != {"question_id"} or len(query["question_id"]) != 1):
                    self.respond(400, {"error": "图谱仅接受一个question_id参数"})
                    return
                qid = query["question_id"][0] if query else None
                if qid is not None and qid not in topic.questions:
                    self.respond(404, {"error": "问题不存在"})
                    return
                self.respond(200, topic.graph_view(qid))
            else:
                self.respond(404, {"error": "接口或标识不存在"})

        def do_POST(self):
            if urlsplit(self.path).path != "/api/recalculate":
                self.respond(404, {"error": "接口不存在"})
                return
            if self.headers.get_content_type() != "application/json":
                self.respond(415, {"error": "Content-Type必须为application/json"})
                return
            length_text = self.headers.get("Content-Length")
            if length_text is None:
                self.respond(411, {"error": "缺少Content-Length"})
                return
            try:
                length = int(length_text)
            except ValueError:
                self.respond(400, {"error": "Content-Length不是整数"})
                return
            if not 0 < length <= 1024 * 1024:
                self.respond(413, {"error": "请求体须为1字节至1MiB"})
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                self.respond(400, {"error": str(error)})
                return
            if not isinstance(payload, dict) or set(payload) != {"weights"} or not isinstance(payload["weights"], dict):
                self.respond(400, {"error": "请求须为仅含weights对象的JSON，例如{\"weights\":{\"F0002\":0}}"})
                return
            try:
                result = topic.recalculate(payload["weights"])
            except ValueError as error:
                self.respond(400, {"error": str(error)})
                return
            self.respond(200, result)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--topic-id", help="本服务的专题标识；默认使用运行目录的父目录名")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8502)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    topic = TopicAPI(run_dir, args.topic_id or run_dir.parent.name)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(topic))
    print(f"专题：{topic.topic_id}；运行：{topic.run_id}", flush=True)
    print(f"监听：{args.host}:{server.server_port}；专题入口：/api/topic", flush=True)
    print("仅服务已有结果和重算预览，不调用模型、不写回文件；Ctrl+C停止。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("接口服务已停止", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
