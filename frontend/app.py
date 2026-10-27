"""启动：在项目根目录执行 python -m streamlit run frontend/app.py。"""
from html import escape
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

import streamlit as st

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from data import (DEFAULT_RUN, ROOT, fingerprint, load_topic, evidence_ids, role_for,
                  quote_html, change_weights, select_graph_node)
from graph_view import make_figure

STATUS_LABELS = {"active": "有效", "unsupported": "无有效支持", "upstream_blocked": "上游判断阻断",
                 "disabled": "已停用", "withdrawn": "已撤销"}

st.set_page_config(page_title="万象奔策 · 证据研判", page_icon="📑", layout="wide")
st.markdown('<style>' + (HERE / 'styles.css').read_text(encoding='utf-8') + '</style>', unsafe_allow_html=True)


@st.cache_data(show_spinner=False, max_entries=3)
def cached_topic(directory, signature):
    # signature 随快照和后端代码变动；返回副本，避免不同用户共享可变状态。
    return load_topic(directory)


def plain(text, css="plain-copy"):
    """材料按纯文本渲染，不把原文当 Markdown 或 HTML 执行。"""
    st.markdown(f'<div class="{css}">{escape(str(text))}</div>', unsafe_allow_html=True)


def jump_to_evidence(eid):
    st.session_state.selected_evidence = eid
    st.session_state.scope = "专题全部证据"
    st.session_state.search = ""
    st.session_state.section = "证据溯源"


def reset_question():
    st.session_state.selected_evidence = None
    st.session_state.scope = "当前问题全部依据"
    st.session_state.search = ""


def reset_weights():
    st.session_state.weights = {}
    # 清除编辑器草稿，保证恢复后输入框也回到原始权重。
    for key in list(st.session_state):
        if key.startswith("weight_input_"):
            del st.session_state[key]


def restore_sample():
    st.session_state.run_dir = str(DEFAULT_RUN)
    st.session_state.directory_input = str(DEFAULT_RUN)


def refs(topic, ids, prefix):
    if not ids:
        st.caption("未列出证据。")
    for eid in ids:
        fact = topic.evidence[eid]
        st.button(f"{eid}  {fact['fact_des'][:48]}", key=f"{prefix}_{eid}", width="stretch",
                  on_click=jump_to_evidence, args=(eid,))


def detail_panel(topic, qid, eid, scores):
    detail = topic.evidence_detail(eid)
    fact, paragraph, document = detail["evidence"], detail["paragraph"], detail["document"]
    plain(f"{eid}  /  {role_for(topic, qid, eid)}", "evidence-tag")
    plain(fact["fact_des"], "source-title")
    plain(document["title"])
    st.caption(f"来源发布时间：{document.get('publish_time', '未提供')}  ·  段落：{fact['para_id']}")
    st.caption(f"来源组：{fact['origin_id']}（规则估计）  ·  当前证据权重：{scores[eid]['effective_weight']:.2f} / 100")
    if scores[eid]["status"] == "withdrawn":
        st.warning("此证据已在当前预览中撤销；原文仍保留供核查。")
    st.markdown("#### 原文定位")
    st.markdown(quote_html(detail, context_chars=120), unsafe_allow_html=True)
    st.caption("黄色为逐字引用；同时显示前后各最多 120 字上下文，省略处以 … 标记。")
    if paragraph["text"].count(fact["quote"]) > 1:
        st.caption("该引用在本段重复出现；按后端约定定位第一个匹配。")
    url = str(document.get("content_url", ""))
    if urlsplit(url).scheme in ("http", "https"):
        st.link_button("打开来源网页（需联网）", url)
    with st.expander("展开完整段落"):
        st.markdown(quote_html(detail), unsafe_allow_html=True)
    with st.expander("展开完整原文"):
        st.markdown(quote_html(detail, full_document=True), unsafe_allow_html=True)
        st.caption("黄色为引用原句；可在原文区域内滚动。")
    with st.expander("结构化字段与引用关系"):
        st.json(fact.get("argument", {}))
        related = detail["references"]
        if related:
            st.dataframe([{"引用判断": e["target"], "关系": "支持" if e["relation"] == "supports" else "冲突"}
                          for e in related], hide_index=True, width="stretch")
        else:
            st.caption("此证据未被当前图谱的判断节点引用。")
    export = {"topic_id": topic.topic_id, "run_id": topic.run_id, "evidence": fact,
              "paragraph": paragraph, "source_title": document["title"], "source_url": url,
              "preview_weight": scores[eid]["effective_weight"]}
    st.download_button("下载这条证据与定位信息", json.dumps(export, ensure_ascii=False, indent=2),
                       file_name=f"{topic.topic_id}_{eid}.json", mime="application/json", key=f"export_{eid}")


def evidence_page(topic, qid, scores):
    left, right = st.columns([0.9, 1.5], gap="large")
    with left:
        st.subheader("查找依据")
        scope = st.selectbox("证据范围", ["当前问题全部依据", "直接支持", "直接冲突", "专题全部证据"], key="scope")
        search = st.text_input("搜索证据", placeholder="输入证据 ID、描述、原文或来源", key="search").strip().casefold()
        ids = evidence_ids(topic, qid, scope)
        ids = [eid for eid in ids if not search or search in " ".join(str(topic.evidence[eid].get(k, ""))
               for k in ("evidence_id", "fact_des", "quote", "source_title")).casefold()]
        st.caption(f"找到 {len(ids)} 条证据。点击一条，在右侧核对原文。")
        if not ids:
            st.session_state.selected_evidence = None
            st.info("没有匹配的证据。清空搜索词或切换证据范围。")
        elif st.session_state.get("selected_evidence") not in ids:
            st.session_state.selected_evidence = ids[0]
        with st.container(height=470, border=True):
            for eid in ids:
                fact = topic.evidence[eid]
                selected = st.session_state.selected_evidence == eid
                label = f"{eid} · {role_for(topic, qid, eid)}\n\n{fact['fact_des'][:80]}"
                if st.button(label, key=f"evidence_{eid}", width="stretch", type="primary" if selected else "secondary"):
                    st.session_state.selected_evidence = eid
                    st.rerun()
    with right:
        st.subheader("核对原文")
        eid = st.session_state.get("selected_evidence")
        if eid:
            detail_panel(topic, qid, eid, scores)
        else:
            st.info("选择证据后，这里展示原文引用及其上下文。")


def arbitration_page(topic, qid):
    decision = topic.question(qid)
    st.subheader("双角色独立意见")
    st.caption("两角色使用同一基础模型的独立会话，不能视为统计上独立的专家验证。以下为已保存的运行结果。")
    dispositions = {x["agent_id"]: x for x in decision["agent_dispositions"]}
    status_names = {"adopted": "已采用", "partially_adopted": "部分采用", "not_adopted": "未采用"}
    for col, agent in zip(st.columns(2, gap="large"), decision["independent_assessments"]):
        with col, st.container(border=True):
            aid = agent["agent_id"]
            st.subheader("A · 事实研判员" if aid == "A" else "B · 反证核查员")
            plain(agent["conclusion"])
            disposition = dispositions[aid]
            st.caption("仲裁处理：" + status_names[disposition["status"]])
            plain(disposition["reason"])
            with st.expander("局限、引用与重新评估条件"):
                for item in agent.get("limitations", []):
                    plain("• " + item)
                if disposition.get("reopen_condition"):
                    st.markdown("**重新评估条件**")
                    plain(disposition["reopen_condition"])
                st.markdown("**支持引用**")
                refs(topic, agent["supporting_evidence_ids"], f"agent_{aid}_support")
                st.markdown("**冲突引用**")
                refs(topic, agent["conflicting_evidence_ids"], f"agent_{aid}_conflict")
    st.subheader("仲裁理由")
    plain(decision["rationale"])
    st.subheader(f"冲突处理记录 · {len(decision['conflicts'])} 项")
    kind_names = {"direct": "直接矛盾", "time_update": "时间更新", "scope_difference": "统计口径", "hypothesis": "假设区分", "insufficient_evidence": "证据不足", "other": "其他"}
    if not decision["conflicts"]:
        st.info("本问题未记录单独的冲突处理事项。")
    for i, item in enumerate(decision["conflicts"], 1):
        with st.expander(f"{i}. {kind_names.get(item['conflict_type'], item['conflict_type'])}：{item['result'][:64]}", expanded=i == 1):
            for label, key in [("处理结果", "result"), ("处理依据", "basis"), ("保留意见", "minority_opinion"), ("重新评估条件", "reopen_condition")]:
                st.markdown(f"**{label}**")
                plain(item.get(key) or "未记录额外内容")
            a, b = st.columns(2)
            with a:
                st.markdown("**支持依据**")
                refs(topic, item["supporting_evidence_ids"], f"conflict_{i}_support")
            with b:
                st.markdown("**冲突依据**")
                refs(topic, item["conflicting_evidence_ids"], f"conflict_{i}_oppose")


def graph_page(topic, qid, scores):
    graph = topic.graph_view(qid)
    st.subheader("当前问题的证据依赖图")
    st.caption(f"{len(graph['nodes'])} 个节点 · {len(graph['edges'])} 条边。由左向右表示来源到判断的依赖，不代表因果。红色虚线表示冲突引用。")
    event = st.plotly_chart(make_figure(graph, scores), key=f"graph_{qid}", on_select="rerun", selection_mode="points",
                            width="stretch", config={"displaylogo": False, "scrollZoom": False})
    options = [n["id"] for n in graph["nodes"]]
    points = event.selection.points if event else []
    selected = points[-1].get("customdata") if points else None
    if isinstance(selected, list):
        selected = selected[0] if selected else None
    if selected in options and selected != st.session_state.get(f"last_graph_event_{qid}"):
        st.session_state[f"graph_node_{qid}"] = selected
        st.session_state[f"last_graph_event_{qid}"] = selected
    node_id = st.selectbox("选中节点（也可直接点击图中圆点）", options, key=f"graph_node_{qid}",
                           format_func=lambda nid: f"{nid}  {topic.nodes[nid].get('fact_des', topic.nodes[nid].get('title', topic.nodes[nid].get('text', '')))[:55]}")
    plain(topic.nodes[node_id].get("fact_des", topic.nodes[node_id].get("text", topic.nodes[node_id].get("title", node_id))))
    st.markdown("**这个节点关联的证据**")
    refs(topic, select_graph_node(topic, qid, node_id), "graph_ref")
    st.download_button("下载当前问题图谱", json.dumps({**graph, "confidence": {nid: s for nid, s in scores.items() if nid in options}}, ensure_ascii=False, indent=2),
                       file_name=f"{qid}_graph.json", mime="application/json")


def weights_page(topic, qid, preview):
    st.subheader("人工权重预览")
    st.info("仅重算分数和有效状态，不调用模型、不改写原结论。调整保存在当前会话，刷新浏览器可能清除；切换问题会保留。")
    ids = evidence_ids(topic, qid)
    node_ids = ids + list(topic.nodes["C:" + qid]["premise_ids"]) + ["C:" + qid]
    node_id = st.selectbox("选择要调整的证据或判断", node_ids, key=f"weight_node_{qid}",
                          format_func=lambda nid: nid + "  " + topic.nodes[nid].get("fact_des", topic.nodes[nid].get("text", ""))[:60])
    base = topic.baseline["nodes"][node_id]
    base_value = base.get("effective_weight", base.get("effective_score"))
    current = st.session_state.weights.get(node_id, base_value)
    st.caption(f"基线值 {base_value:.2f} / 100。0 表示撤销证据或停用判断。")
    with st.form("weight_form"):
        value = st.number_input("新的权重 / 人工有效分", min_value=0.0, max_value=100.0, value=float(current), step=5.0,
                                key=f"weight_input_{node_id}")
        apply = st.form_submit_button("应用预览", type="primary")
    if apply:
        new_weights, _ = change_weights(topic, st.session_state.weights, node_id, value)
        st.session_state.weights = new_weights
        st.rerun()
    left, right = st.columns(2)
    with left:
        if st.button("恢复当前节点", width="stretch"):
            new_weights, _ = change_weights(topic, st.session_state.weights, node_id, None)
            st.session_state.weights = new_weights
            del st.session_state[f"weight_input_{node_id}"]
            st.rerun()
    with right:
        st.button("恢复全部基线", on_click=reset_weights, width="stretch")
    rows = []
    for item in preview["conclusions"]:
        score = item["confidence"]
        baseline = topic.baseline["nodes"][item["node_id"]]["effective_score"]
        rows.append({"问题": item["question_id"], "原始分": round(baseline, 2), "预览分": round(score["effective_score"], 2),
                     "变化": round(score["effective_score"] - baseline, 2), "状态": STATUS_LABELS.get(score["status"], score["status"]),
                     "需重新审阅": "是" if score["needs_reassessment"] else "否"})
    st.dataframe(rows, hide_index=True, width="stretch")
    affected = preview["confidence"]["affected_judgment_ids"]
    st.caption("受影响判断：" + ("、".join(affected) if affected else "无"))
    with st.expander(f"查看完整权重覆盖表（{len(st.session_state.weights)} 项）"):
        st.json(st.session_state.weights)
    st.download_button("下载本次权重预览", json.dumps({"topic_id": topic.topic_id, "run_id": topic.run_id,
                       "weights": st.session_state.weights, "conclusions": preview["conclusions"], "text_policy": preview["text_policy"]}, ensure_ascii=False, indent=2),
                       file_name=f"{topic.topic_id}_weight_preview.json", mime="application/json")


def main():
    with st.sidebar:
        st.markdown('<div class="brand">万象奔策</div><div class="brand-note">研判有依据 · 原文可追溯</div>', unsafe_allow_html=True)
        with st.expander("本地数据设置"):
            if "directory_input" not in st.session_state:
                st.session_state.directory_input = st.session_state.get("run_dir", str(DEFAULT_RUN))
            with st.form("data_path"):
                directory = st.text_input("运行快照目录", key="directory_input")
                submitted = st.form_submit_button("载入本地快照")
            if submitted:
                path = Path(directory).expanduser()
                st.session_state.run_dir = str((ROOT / path if not path.is_absolute() else path).resolve())
            st.caption("目录需包含 input_manifest、evidence_index、arbitration、source_analysis 和 run 五个 JSON。")
        directory = st.session_state.get("run_dir", str(DEFAULT_RUN))
    try:
        signature = fingerprint(directory)
        with st.spinner("正在读取本地证据快照…"):
            topic = cached_topic(directory, signature)
    except (ValueError, KeyError, IndexError, TypeError, OSError, StopIteration) as error:
        st.error("无法载入运行快照：" + str(error))
        st.info("在左侧“本地数据设置”选择完整的运行目录，或恢复随项目附带的样例。")
        st.button("恢复内置样例", on_click=restore_sample)
        st.stop()
    if st.session_state.get("snapshot_signature") != signature:
        st.session_state.snapshot_signature = signature
        reset_weights()
        reset_question()
        st.session_state.question_id = next(iter(topic.questions))
        st.session_state.section = "证据溯源"
        for key in list(st.session_state):
            if key.startswith(("weight_node_", "graph_node_")):
                del st.session_state[key]
    with st.sidebar:
        st.caption(f"专题 {topic.topic_id} / {topic.run_id}")
        qid = st.selectbox("选择研判问题", list(topic.questions), key="question_id",
                           format_func=lambda q: f"{q}｜{topic.questions[q]['question']}", on_change=reset_question)
        st.caption("先选问题，再点击证据核对原文。")
        st.divider()
        st.markdown("**阅读提示**")
        st.caption("结论来自已保存的模型结果。分数是启发式指标，不是正确概率；来源分组不是独立性证明。")
        st.caption("所有页面操作均使用本地数据，不需要模型密钥。")
        if st.session_state.weights:
            st.warning(f"当前有 {len(st.session_state.weights)} 项权重覆盖。")
            st.button("清除预览，恢复基线", on_click=reset_weights, key="sidebar_reset")
        with st.expander("运行与分组信息"):
            st.caption("保存的模型标识：" + str(topic.run.get("model", "未知")))
            st.caption(topic.sources["count_basis"])
            st.json(topic.sources.get("rules", {}))
    preview = topic.recalculate(st.session_state.weights)
    scores = preview["confidence"]["nodes"]
    current_score = scores["C:" + qid]
    overview = topic.overview()
    counts = overview["counts"]
    st.markdown('<div class="masthead"><span class="title">证据研判工作台</span><span class="local-status">本地快照 · 无模型调用</span></div>', unsafe_allow_html=True)
    st.markdown('<div class="stat-strip">' + ''.join(f'<span><b>{counts[key]}</b>{label}</span>' for key, label in
                [("documents", "份原文"), ("evidence", "条证据"), ("questions", "个问题"), ("intermediate", "个中间判断")]) + '</div>', unsafe_allow_html=True)
    st.subheader(topic.questions[qid]["question"])
    text_col, score_col = st.columns([4, 1], gap="large")
    with text_col:
        plain(topic.questions[qid]["conclusion"], "conclusion")
    with score_col:
        baseline = topic.baseline["nodes"]["C:" + qid]["effective_score"]
        delta = current_score["effective_score"] - baseline
        st.metric("启发式有效分", f"{current_score['effective_score']:.2f}", delta=f"{delta:+.2f} 相对基线" if st.session_state.weights else None)
        st.caption("满分 100；不是正确概率。")
        if current_score["status"] != "active":
            st.error("当前判断状态：" + STATUS_LABELS.get(current_score["status"], current_score["status"]))
        with st.expander("查看分项"):
            for key, label in [("source", "信源"), ("consistency", "一致性"), ("chain", "推理链")]:
                st.caption(f"{label}：{current_score['components'][key]:.2f}")
            st.caption("总分权重：0.50 / 0.30 / 0.20。人工判断分与公式分分别保留。")
    if current_score.get("needs_reassessment"):
        st.warning("此判断受权重调整影响，需要重新审阅。以上结论文字保持原样，尚未重新调用模型研判。")
    section = st.radio("工作区", ["证据溯源", "双角色与仲裁", "依赖图谱", "权重预览"], horizontal=True, key="section", label_visibility="collapsed")
    st.divider()
    if section == "证据溯源":
        evidence_page(topic, qid, scores)
    elif section == "双角色与仲裁":
        arbitration_page(topic, qid)
    elif section == "依赖图谱":
        graph_page(topic, qid, scores)
    else:
        weights_page(topic, qid, preview)
    st.divider()
    st.caption("演示边界：仅依据本地材料与已有模型结果。引用定位正确不代表结论语义准确；依赖图不证明因果。")


if __name__ == "__main__":
    main()
