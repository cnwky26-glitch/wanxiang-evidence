"""按来源、段落、证据、判断分层绘图；不把依赖边当作因果关系。"""
from html import escape
import plotly.graph_objects as go

LAYERS = ["source", "paragraph", "evidence", "intermediate", "conclusion"]
NAMES = {"source": "来源", "paragraph": "段落", "evidence": "证据", "intermediate": "中间判断", "conclusion": "结论"}
COLORS = {"source": "#74879d", "paragraph": "#b3c5d4", "evidence": "#12877e", "intermediate": "#bb8a32", "conclusion": "#24467a"}
RELATIONS = {"contains": "包含", "extracts": "抽取", "supports": "支持", "opposes": "冲突", "premise": "判断依赖"}


def make_figure(graph, scores):
    columns = {kind: sorted([n for n in graph["nodes"] if n["type"] == kind], key=lambda n: n["id"]) for kind in LAYERS}
    positions = {}
    for x, kind in enumerate(LAYERS):
        count = len(columns[kind])
        for i, node in enumerate(columns[kind]):
            positions[node["id"]] = (x, 0.5 if count == 1 else 1 - i / max(1, count - 1))
    fig = go.Figure()
    for relation, label in RELATIONS.items():
        xs, ys = [], []
        for edge in graph["edges"]:
            if edge["relation"] != relation:
                continue
            a, b = positions[edge["source"]], positions[edge["target"]]
            xs.extend([a[0], b[0], None]); ys.extend([a[1], b[1], None])
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name=label,
                                line=dict(color="#c46b64" if relation == "opposes" else "#a4b6c5",
                                          width=1.4, dash="dot" if relation == "opposes" else "solid"),
                                hoverinfo="skip", showlegend=bool(xs)))
    for kind in LAYERS:
        group = columns[kind]
        hover, colors = [], []
        for n in group:
            score = scores.get(n["id"], {})
            disabled = score.get("status") in ("withdrawn", "disabled", "unsupported", "upstream_blocked")
            colors.append("#c8cdd2" if disabled else COLORS[kind])
            title = str(n.get("fact_des", n.get("text", n.get("title", n["id"]))))
            value = score.get("effective_score", score.get("effective_weight"))
            hover.append(escape(n["id"]) + "<br>" + escape(title[:65]) +
                         (f"<br>有效分 / 权重：{value:.2f}" if value is not None else ""))
        fig.add_trace(go.Scatter(
            x=[positions[n["id"]][0] for n in group], y=[positions[n["id"]][1] for n in group],
            customdata=[n["id"] for n in group], text=[n["id"] for n in group],
            mode="markers+text" if kind in ("intermediate", "conclusion") else "markers",
            textposition="top center", textfont=dict(size=11),
            marker=dict(size=14 if kind in ("intermediate", "conclusion") else 10,
                        color=colors, line=dict(width=1, color="white")),
            hovertext=hover, hovertemplate="%{hovertext}<extra></extra>",
            name=NAMES[kind], showlegend=False,
        ))
    fig.update_layout(
        height=530, margin=dict(l=25,r=25,t=45,b=35), paper_bgcolor="white", plot_bgcolor="white",
        font=dict(family="Microsoft YaHei, sans-serif", color="#243750"),
        xaxis=dict(tickvals=list(range(5)),ticktext=[NAMES[k] for k in LAYERS],side="top",range=[-0.25,4.4],
                   showgrid=False,zeroline=False,fixedrange=True),
        yaxis=dict(visible=False,range=[-0.08,1.1],fixedrange=True),
        legend=dict(orientation="h",y=-0.04), clickmode="event+select", dragmode="pan",
        uirevision="question-graph",
    )
    return fig
