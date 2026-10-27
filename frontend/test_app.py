"""验证数据契约和关键交互；不调用模型、不修改原始快照。"""
from hashlib import sha256
from html import escape
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from data import (DEFAULT_RUN, REQUIRED_FILES, load_topic, fingerprint, highlight,
                  quote_html, change_weights, evidence_ids, select_graph_node)
from streamlit.testing.v1 import AppTest


class DataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 若数据加载意外调用外部 API，立刻失败。
        with patch("urllib.request.urlopen", side_effect=AssertionError("禁止网络调用")):
            cls.topic = load_topic(DEFAULT_RUN)

    def test_all_208_quotes_and_full_document_offsets(self):
        self.assertEqual(len(self.topic.evidence), 208)
        for eid in self.topic.evidence:
            detail = self.topic.evidence_detail(eid)
            p = detail["paragraph"]
            self.assertEqual(p["before_quote"] + p["quote"] + p["after_quote"], p["text"])
            start, end = p["char_start"] + p["quote_start"], p["char_start"] + p["quote_end"]
            self.assertEqual(detail["document"]["content"][start:end], p["quote"])
            self.assertIn('<mark class="quote-mark">' + escape(p["quote"]) + '</mark>', quote_html(detail, True))
            snippet = quote_html(detail, context_chars=120)
            self.assertIn('<mark class="quote-mark">' + escape(p["quote"]) + '</mark>', snippet)
            self.assertEqual(snippet.count('<mark '), 1)

    def test_html_is_escaped_and_unicode_offsets_preserved(self):
        text = '中文🙂 <script>alert(1)</script> "原句" & 结尾'
        start = text.index('"原句"')
        rendered = highlight(text, start, start + len('"原句"'))
        self.assertNotIn('<script>', rendered)
        self.assertIn('&lt;script&gt;', rendered)
        self.assertIn('&amp;', rendered)
        self.assertIn('<mark class="quote-mark">&quot;原句&quot;</mark>', rendered)

    def test_withdraw_restore_and_files_unchanged(self):
        hashes = {p.name: sha256(p.read_bytes()).hexdigest() for p in DEFAULT_RUN.glob('*.json')}
        before = self.topic.baseline["nodes"]["C:q_001"]["effective_score"]
        weights, result = change_weights(self.topic, {}, "F0002", 0)
        self.assertAlmostEqual(before, 65.17, places=2)
        self.assertAlmostEqual(result["confidence"]["nodes"]["C:q_001"]["effective_score"], 63.69, places=2)
        self.assertEqual(set(result["confidence"]["recomputed_node_ids"]), {"F0002", "I:q_001:01", "C:q_001"})
        self.assertTrue(result["confidence"]["nodes"]["C:q_001"]["needs_reassessment"])
        weights, _ = change_weights(self.topic, weights, "F0033", 0)
        self.assertEqual(set(weights), {"F0002", "F0033"})
        weights, _ = change_weights(self.topic, weights, "F0002", None)
        self.assertEqual(set(weights), {"F0033"})
        weights, restored = change_weights(self.topic, weights, "F0033", None)
        self.assertEqual(restored["confidence"]["nodes"], self.topic.baseline["nodes"])
        self.assertEqual(hashes, {p.name: sha256(p.read_bytes()).hexdigest() for p in DEFAULT_RUN.glob('*.json')})

    def test_invalid_weights_and_missing_snapshot(self):
        for node, value in [("F0002", 101), ("F0002", -1), ("F0002", True), ("F0002", float('nan')), ("missing", 0)]:
            with self.assertRaises(ValueError):
                change_weights(self.topic, {}, node, value)
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "缺少文件"):
                fingerprint(temp)

    def test_graph_drilldown_and_question_scopes(self):
        for qid in self.topic.questions:
            ids = evidence_ids(self.topic, qid)
            for n in self.topic.graph_view(qid)["nodes"]:
                self.assertTrue(set(select_graph_node(self.topic, qid, n["id"])) <= set(ids))
            self.assertTrue(set(evidence_ids(self.topic, qid, "直接支持")) <= set(ids))
            self.assertTrue(set(evidence_ids(self.topic, qid, "直接冲突")) <= set(ids))


class InteractionTests(unittest.TestCase):
    def app(self):
        at = AppTest.from_file(str(HERE / "app.py"), default_timeout=30).run()
        self.assertFalse(at.exception)
        return at

    def assert_ok(self, at):
        self.assertFalse(at.exception, [e.message for e in at.exception])

    def test_all_questions_and_sections(self):
        at = self.app()
        for qid in ['q_001', 'q_002', 'q_003']:
            at.selectbox(key="question_id").select(qid).run()
            for section in ['证据溯源', '双角色与仲裁', '依赖图谱', '权重预览']:
                at.radio(key="section").set_value(section).run()
                self.assert_ok(at)

    def test_evidence_selection_filter_and_empty_search(self):
        at = self.app()
        at.button(key="evidence_F0013").click().run()
        self.assertEqual(at.session_state['selected_evidence'], 'F0013')
        self.assert_ok(at)
        at.selectbox(key="scope").select("直接冲突").run()
        topic = load_topic(DEFAULT_RUN)
        self.assertIn(at.session_state['selected_evidence'], topic.questions['q_001']['conflicting_evidence_ids'])
        at.text_input(key="search").set_value("zzzz_no_such_fact_123").run()
        self.assertIsNone(at.session_state['selected_evidence'])
        self.assertTrue(any("没有匹配" in x.value for x in at.info))
        at.selectbox(key="question_id").select("q_002").run()
        self.assertEqual(at.text_input(key="search").value, '')
        self.assertIsNotNone(at.session_state['selected_evidence'])
        self.assert_ok(at)

    def test_weight_preview_persists_and_resets(self):
        at = self.app()
        at.radio(key="section").set_value("权重预览").run()
        at.selectbox(key="weight_node_q_001").select("F0002").run()
        at.number_input(key="weight_input_F0002").set_value(0.0)
        next(b for b in at.button if b.label == '应用预览').click().run()
        self.assert_ok(at)
        self.assertEqual(at.metric[0].value, '63.69')
        self.assertEqual(at.session_state['weights'], {'F0002': 0.0})
        at.radio(key="section").set_value("证据溯源").run()
        self.assertEqual(at.metric[0].value, '63.69')
        self.assertTrue(any("需要重新审阅" in x.value for x in at.warning))
        at.selectbox(key="question_id").select("q_002").run()
        self.assertEqual(at.metric[0].value, '69.06')
        self.assertEqual(at.session_state['weights'], {'F0002': 0.0})
        at.button(key="sidebar_reset").click().run()
        self.assertEqual(at.session_state['weights'], {})
        self.assert_ok(at)

    def test_arbitration_reference_navigation(self):
        at = self.app()
        at.radio(key="section").set_value("双角色与仲裁").run()
        button = next(b for b in at.button if b.key and b.key.startswith("agent_A_support_"))
        eid = button.key.rsplit('_', 1)[-1]
        button.click().run()
        self.assertEqual(at.radio(key="section").value, '证据溯源')
        self.assertEqual(at.session_state['selected_evidence'], eid)
        self.assert_ok(at)

    def test_invalid_directory_can_restore_sample(self):
        at = self.app()
        at.text_input(key="directory_input").set_value(str(DEFAULT_RUN / 'missing'))
        next(b for b in at.button if b.label == '载入本地快照').click().run()
        self.assertTrue(any('无法载入' in e.value for e in at.error))
        self.assert_ok(at)
        next(b for b in at.button if b.label == '恢复内置样例').click().run()
        self.assertFalse(at.error)
        self.assert_ok(at)


if __name__ == '__main__':
    unittest.main()
