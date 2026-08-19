from __future__ import annotations

import html
import json
from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True, slots=True)
class RenderedDigest:
    text: str
    html: str
    has_content: bool


def render_digest(batches: Sequence[Any], notifications: Sequence[Any]) -> RenderedDigest:
    text_sections: list[str] = []
    html_sections: list[str] = []

    course_qa: list[tuple[str, str]] = []
    academic: list[tuple[str, dict[str, Any]]] = []
    casual: list[tuple[str, dict[str, Any]]] = []
    for row in batches:
        data = json.loads(row["summary_json"])
        if row["group_type"] == "course" and data.get("qa_summary"):
            course_qa.append((row["group_name"], data["qa_summary"]))
        elif row["group_type"] == "academic" and _academic_has_content(data):
            academic.append((row["group_name"], data))
        elif row["group_type"] == "casual" and _casual_has_content(data):
            casual.append((row["group_name"], data))

    if notifications or course_qa:
        lines = ["第一部分：重要通知"]
        html_parts = ["<h2>第一部分：重要通知</h2>"]
        for item in notifications:
            lines.extend([f"\n[{item['group_name']}] {item['title']}", item["original_text"]])
            html_parts.append(f"<h3>[{html.escape(item['group_name'])}] {html.escape(item['title'])}</h3>")
            html_parts.append(f"<p>{html.escape(item['original_text'])}</p>")
            if item["latest_update_text"]:
                lines.append(f"后续更新：{item['latest_update_text']}")
                html_parts.append(f"<p><strong>后续更新：</strong>{html.escape(item['latest_update_text'])}</p>")
        for group_name, qa in course_qa:
            lines.extend([f"\n[{group_name}] 答疑摘要", qa])
            html_parts.extend([f"<h3>[{html.escape(group_name)}] 答疑摘要</h3>", f"<p>{html.escape(qa)}</p>"])
        text_sections.append("\n".join(lines))
        html_sections.append("".join(html_parts))

    if academic:
        lines = ["第二部分：学术群内容"]
        html_parts = ["<h2>第二部分：学术群内容</h2>"]
        for group_name, data in academic:
            lines.extend([f"\n## {group_name}", data.get("overview", "")])
            html_parts.extend([f"<h3>{html.escape(group_name)}</h3>", f"<p>{html.escape(data.get('overview', ''))}</p>"])
            for view in data.get("member_views", []):
                line = f"- {view['member']}：{view['view']}"
                lines.append(line)
                html_parts.append(f"<p>{html.escape(line)}</p>")
            _append_list(lines, html_parts, "分歧", data.get("disagreements", []))
            _append_list(lines, html_parts, "共识", data.get("consensus", []))
            _append_list(lines, html_parts, "未解决问题", data.get("unresolved_questions", []))
            tags = data.get("knowledge_tags", [])
            if tags:
                lines.append("知识 tags：" + " / ".join(tags))
                html_parts.append(f"<p><strong>知识 tags：</strong>{html.escape(' / '.join(tags))}</p>")
        text_sections.append("\n".join(lines))
        html_sections.append("".join(html_parts))

    if casual:
        lines = ["第三部分：闲聊群总结"]
        html_parts = ["<h2>第三部分：闲聊群总结</h2>"]
        for group_name, data in casual:
            lines.extend([f"\n## {group_name}", data.get("overview", "")])
            html_parts.extend([f"<h3>{html.escape(group_name)}</h3>", f"<p>{html.escape(data.get('overview', ''))}</p>"])
            _append_list(lines, html_parts, "值得关注", data.get("noteworthy", []))
            _append_list(lines, html_parts, "计划", data.get("plans", []))
            _append_list(lines, html_parts, "资源", data.get("resources", []))
        text_sections.append("\n".join(lines))
        html_sections.append("".join(html_parts))

    return RenderedDigest("\n\n".join(text_sections).strip(), "".join(html_sections), bool(text_sections))


def _append_list(lines: list[str], html_parts: list[str], title: str, items: list[str]) -> None:
    if not items:
        return
    lines.append(f"{title}：" + "；".join(items))
    html_parts.append(f"<p><strong>{html.escape(title)}：</strong>{html.escape('；'.join(items))}</p>")


def _academic_has_content(data: dict[str, Any]) -> bool:
    return any(data.get(key) for key in (
        "overview", "member_views", "disagreements", "consensus", "unresolved_questions", "knowledge_tags"
    ))


def _casual_has_content(data: dict[str, Any]) -> bool:
    return any(data.get(key) for key in ("overview", "noteworthy", "plans", "resources"))

