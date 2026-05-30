import json
from html import escape
from itertools import zip_longest
import os
from pathlib import Path
from urllib.parse import parse_qs

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ceo_agent_service.codex_history import (
    RenderedCodexEvent,
    render_local_codex_session,
)
from ceo_agent_service.codex_decision import audit_summary_explains_no_documents
from ceo_agent_service.config import (
    assistant_signature,
    broadcast_mention_aliases,
    env_file_path,
    forbidden_path_prefixes,
    group_read_recovery_limit,
    group_read_recovery_window,
    handoff_ack,
    mention_aliases,
    message_recovery_interval,
    principal_display_name,
    principal_handoff_name,
    principal_name,
    single_chat_read_recovery_limit,
    single_chat_read_recovery_window,
    style_speaker_names,
    write_env_values,
    work_profile_path,
)
from ceo_agent_service.developer_prompt import (
    configurable_prompt_variable_pairs,
    DeveloperPromptTemplateError,
    developer_prompt_template_path,
    prompt_variable_env_key,
    read_developer_prompt_template,
    read_user_prompt_template,
    render_developer_prompt_template,
    render_user_prompt_template,
    split_developer_prompt_template,
    user_prompt_template_path,
    write_developer_prompt_template,
    write_configurable_prompt_variables,
    write_user_prompt_template,
)
from ceo_agent_service.dingtalk_models import (
    CodexAction,
    DingTalkConversation,
    DingTalkMessage,
    SensitivityKind,
)
from ceo_agent_service.dws_client import DwsClient
from ceo_agent_service.store import (
    AutoReplyStore,
    ReplyAttempt,
    ReplyError,
    ReplyTask,
    SentReply,
)
from ceo_agent_service.user_prompt_blocks import USER_PROMPT_BLOCKS, UserPromptBlock
from ceo_agent_service.worker import DingTalkAutoReplyWorker


CSS = """
:root{--ink:#0a0a0a;--charcoal:#1c1c1e;--slate:#3a3a3c;--steel:#5a5a5c;--stone:#888888;--muted:#a8a8aa;--canvas:#ffffff;--surface:#f7f7f7;--surface-soft:#fafafa;--surface-code:#1c1c1e;--hairline:#e5e5e5;--hairline-soft:#ededed;--mint:#00d4a4;--mint-deep:#00b48a;--tag:#3772cf;--error:#d45656}
*{box-sizing:border-box}
body{font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;background:var(--canvas);color:var(--ink);font-size:14px;line-height:1.5}
header{position:sticky;top:0;z-index:10;background:rgba(255,255,255,.94);border-bottom:1px solid var(--hairline);backdrop-filter:saturate(180%) blur(12px)}
.shell{width:100%;margin:0 auto;padding:0 24px}
.topbar{display:flex;align-items:center;justify-content:space-between;gap:24px;min-height:72px}
.brand{display:flex;align-items:center;gap:12px;min-width:0}
.brand-home:hover{text-decoration:none}
.brand-mark{width:28px;height:28px;border-radius:8px;background:var(--ink);box-shadow:inset 0 -8px 0 rgba(0,212,164,.26)}
h1{margin:0;color:var(--ink);font-size:18px;font-weight:600;line-height:1.35;letter-spacing:0}
.eyebrow{margin-top:2px;color:var(--steel);font-size:12px;font-weight:500;line-height:1.4}
main{width:100%;margin:0 auto;padding:20px 24px 40px}
a{color:var(--ink);text-decoration:none}
a:hover{text-decoration:underline;text-decoration-color:var(--mint);text-underline-offset:3px}
table{width:100%;border-collapse:separate;border-spacing:0;background:var(--canvas);border:1px solid var(--hairline);border-radius:8px;overflow:hidden}
th,td{border-bottom:1px solid var(--hairline-soft);padding:12px 14px;text-align:left;vertical-align:top;font-size:14px;line-height:1.45}
tr:last-child td{border-bottom:0}
th{background:var(--surface-soft);color:var(--steel);font-size:12px;font-weight:600;line-height:1.4}
.config-variable-table th,.config-variable-table td{padding:5px 8px}
.config-variable-table th:first-child,.config-variable-table td:first-child{width:260px}
.config-variable-table input[type="text"]{height:28px;padding:4px 7px;border-radius:6px;font-size:12px;line-height:1.35}
.config-key-input{font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;color:var(--steel);background:var(--surface-soft)}
.config-value-input{font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.config-value{display:inline-flex;max-width:100%;padding:4px 8px;border-radius:7px;background:var(--surface);border:1px solid var(--hairline-soft);color:var(--charcoal);font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;font-size:12px;line-height:1.45;white-space:pre-wrap;word-break:break-word}
.config-token{display:inline-flex;max-width:100%;padding:3px 7px;border-radius:6px;background:#ddfff6;border:1px solid rgba(0,180,138,.55);color:#005b49;font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;font-size:12px;font-weight:700;line-height:1.4;white-space:pre-wrap;word-break:break-word;box-shadow:0 0 0 2px rgba(0,212,164,.12)}
.system-config-table th:first-child,.system-config-table td:first-child{width:260px}
.system-config-table th:nth-child(2),.system-config-table td:nth-child(2){width:280px}
.config-collapse{border:1px solid var(--hairline);border-radius:8px;background:var(--surface-soft);margin:10px 0;overflow:hidden}
.config-collapse summary{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 12px;cursor:pointer;list-style:none}
.config-collapse summary::-webkit-details-marker{display:none}
.config-collapse summary h3{margin:0;font-size:14px;line-height:1.35}
.config-collapse summary::after{content:"Show";color:var(--steel);font-size:12px;font-weight:600}
.config-collapse[open] summary{border-bottom:1px solid var(--hairline)}
.config-collapse[open] summary::after{content:"Hide"}
.config-collapse table{border:0;border-radius:0}
.config-collapse form{padding:0 0 10px}
.dynamic-preview{max-height:56px;margin:0;padding:7px 9px;font-size:12px;line-height:1.35}
.logic-list{display:grid;gap:14px}
.logic-section{border:1px solid var(--hairline);border-radius:8px;padding:16px;background:var(--surface-soft)}
.logic-section h3{margin:0 0 10px;color:var(--ink);font-size:16px;font-weight:600;line-height:1.4}
.logic-section dl{display:grid;gap:9px;margin:0}
.logic-section dt{color:var(--steel);font-size:12px;font-weight:700;line-height:1.4}
.logic-section dd{margin:2px 0 0;color:var(--charcoal);font-size:14px;line-height:1.5}
.attempt-feed{display:grid;gap:8px}
.attempt-item{background:var(--canvas);border:1px solid var(--hairline);border-radius:8px;padding:10px 12px}
.attempt-head{display:flex;align-items:center;justify-content:space-between;gap:12px;min-width:0}
.attempt-title{display:flex;align-items:center;gap:7px;min-width:0;flex-wrap:nowrap}
.attempt-side{display:flex;align-items:center;gap:10px;flex:0 0 auto}
.attempt-id{font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;font-size:13px;font-weight:700;color:var(--ink)}
.attempt-main{font-size:14px;font-weight:600;color:var(--ink);min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.attempt-meta{color:var(--steel);font-size:13px;line-height:1.4;white-space:nowrap}
.attempt-time{color:var(--stone);font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;font-size:12px;line-height:1.4;text-align:right;white-space:nowrap}
.attempt-lines{display:grid;gap:4px;margin-top:8px}
.attempt-line{display:grid;grid-template-columns:24px minmax(0,1fr);gap:8px;align-items:start;min-width:0}
.attempt-label{color:var(--steel);font-size:12px;font-weight:700;line-height:1.45}
.attempt-copy{color:var(--charcoal);font-size:13px;line-height:1.45;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}
.attempt-foot{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:6px;flex-wrap:wrap}
.attempt-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.attempt-warning{color:#8a2626;font-size:12px;line-height:1.4}
.attempt-info{position:relative;display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border:1px solid #d29a12;border-radius:50%;color:#8a5a08;background:#fff3c4;font-size:11px;font-weight:700;line-height:1;cursor:help;flex:0 0 auto}
.attempt-info:hover,.attempt-info:focus{background:#ffe7a3;border-color:#b77908;outline:0}
.attempt-info::after{content:attr(data-tooltip);display:none;position:absolute;left:0;bottom:calc(100% + 8px);z-index:30;width:max-content;max-width:min(320px,calc(100vw - 48px));padding:7px 9px;border-radius:6px;background:#1f2937;color:#fff;box-shadow:0 8px 24px rgba(15,23,42,.18);font-size:12px;font-weight:500;line-height:1.4;text-align:left;white-space:normal}
.attempt-info::before{content:"";display:none;position:absolute;left:4px;bottom:calc(100% + 3px);z-index:31;border:5px solid transparent;border-top-color:#1f2937}
.attempt-info:hover::after,.attempt-info:focus::after,.attempt-info:hover::before,.attempt-info:focus::before{display:block}
.nav{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.nav-item{display:inline-flex;align-items:center;height:36px;padding:0 14px;border:1px solid var(--hairline);border-radius:999px;background:var(--canvas);color:var(--steel);font-size:14px;font-weight:500}
a.nav-item:hover{color:var(--ink);text-decoration:none;border-color:var(--ink)}
.nav-item.active{background:var(--ink);border-color:var(--ink);color:#fff;cursor:default}
.prompt-tabs{display:inline-flex;align-items:center;gap:6px;padding:4px;border:1px solid var(--hairline);border-radius:999px;background:var(--surface-soft);margin:0 0 12px}
.prompt-tab{display:inline-flex;align-items:center;height:32px;padding:0 13px;border-radius:999px;color:var(--steel);font-size:13px;font-weight:600}
.prompt-tab:hover{text-decoration:none;color:var(--ink)}
.prompt-tab.active{background:var(--ink);color:#fff}
.pill{display:inline-flex;align-items:center;min-height:24px;padding:3px 9px;border-radius:999px;background:var(--surface);color:var(--steel);border:1px solid var(--hairline);font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;font-size:12px;line-height:1.3;white-space:nowrap}
.action-no_reply{background:rgba(55,114,207,.10);color:#245aa5;border-color:rgba(55,114,207,.24)}
.action-send_reply,.action-ask_clarifying_question{background:rgba(0,212,164,.10);color:#006b55;border-color:rgba(0,180,138,.24)}
.action-handoff_to_human{background:rgba(195,125,13,.12);color:#8a5a08;border-color:rgba(195,125,13,.24)}
.action-stop_with_error{background:rgba(212,86,86,.12);color:#9a2f2f;border-color:rgba(212,86,86,.24)}
.status-sent{background:rgba(0,212,164,.12);color:#006b55;border-color:rgba(0,180,138,.28)}
.status-resolved{background:rgba(0,212,164,.12);color:#006b55;border-color:rgba(0,180,138,.28)}
.status-pending,.status-processing{background:rgba(55,114,207,.10);color:#245aa5;border-color:rgba(55,114,207,.24)}
.status-skipped{background:var(--surface);color:var(--stone)}
.status-failed,.status-blocked,.status-active{background:rgba(212,86,86,.12);color:#9a2f2f;border-color:rgba(212,86,86,.24)}
.quality-warning{border-color:rgba(212,86,86,.28);background:rgba(212,86,86,.08)}
.quality-warning ul{margin:8px 0 0;padding-left:20px;color:#8a2626}
.context-only-info{display:inline-flex;align-items:center;gap:8px}
.card{background:var(--canvas);border:1px solid var(--hairline);border-radius:8px;padding:24px;margin:16px 0}
.card h2{margin:0 0 14px;color:var(--ink);font-size:18px;font-weight:600;line-height:1.4;letter-spacing:0}
.card p{margin:8px 0}
.review-grid{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(340px,.75fr);gap:16px;align-items:start;margin:16px 0}
.review-grid .card{margin:0}
.reply-pre{min-height:188px;background:var(--surface-soft);border-color:var(--hairline);font-size:14px;line-height:1.55}
.reply-meta{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
.trigger-pre{min-height:0;margin:0 0 14px;background:var(--surface-soft);border-color:var(--hairline);font-size:14px;line-height:1.55}
.compact-card{padding:16px}
.compact-card h2{font-size:16px;margin-bottom:10px}
.collapsible-card{padding:0;overflow:hidden}
.collapsible-card summary{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:16px 24px;cursor:pointer}
.collapsible-card summary h2{margin:0;font-size:18px}
.collapsible-card summary::after{content:"Show";color:var(--steel);font-size:12px;font-weight:600}
.collapsible-card[open] summary{border-bottom:1px solid var(--hairline)}
.collapsible-card[open] summary::after{content:"Hide"}
.collapsible-card pre{border:0;border-radius:0;margin:0}
.event{background:var(--canvas);border:1px solid var(--hairline);border-radius:8px;margin:16px 0;overflow:hidden}
.event summary{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:14px 16px;cursor:pointer;list-style:none}
.event summary::-webkit-details-marker{display:none}
.event-title{min-width:0;font-size:15px;font-weight:600;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.event-preview{margin-top:3px;color:var(--steel);font-size:12px;font-weight:400;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.event time{flex:0 0 auto;color:var(--stone);font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;font-size:12px}
.event pre{border:0;border-top:1px solid var(--hairline);border-radius:0;margin:0}
.grid{display:grid;grid-template-columns:180px 1fr;gap:10px 18px}
.grid .muted{font-size:12px;font-weight:600}
pre{white-space:pre-wrap;background:var(--surface);border:1px solid var(--hairline);border-radius:8px;padding:16px;overflow:auto;color:var(--charcoal);font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;font-size:13px;line-height:1.55}
.json-pre{background:#fbfbfb;color:var(--charcoal)}
.json-key{color:#7b3fb2}
.json-string{color:#0b6b50}
.json-number{color:#9a5b00}
.json-bool{color:#1f5fbf}
.json-null{color:#8a2626}
textarea,input[type="text"]{width:100%;box-sizing:border-box;background:var(--canvas);color:var(--ink);border:1px solid var(--hairline);border-radius:8px;padding:12px 14px;font:inherit}
textarea{min-height:104px;resize:vertical}
textarea:focus,input[type="text"]:focus{outline:0;border-color:var(--mint);box-shadow:0 0 0 3px rgba(0,212,164,.16)}
button{background:var(--ink);color:#fff;border:0;border-radius:999px;padding:10px 18px;font-size:14px;font-weight:500;line-height:1.3}
label{display:block;margin:14px 0 7px;color:var(--slate);font-size:13px;font-weight:600}
.review-link{display:inline-flex;align-items:center;height:30px;padding:0 12px;border:1px solid var(--hairline);border-radius:999px;background:var(--canvas);color:var(--ink);font-size:13px;font-weight:500;white-space:nowrap}
.review-link:hover{text-decoration:none;border-color:var(--ink);background:var(--surface-soft)}
.danger{background:#9f1d1d}
.muted{color:var(--steel)}
@media (max-width:900px){.attempt-head{align-items:flex-start;flex-direction:column}.attempt-title{flex-wrap:wrap}.attempt-side{align-items:flex-start;flex-direction:column;gap:6px}.attempt-main,.attempt-meta{white-space:normal}.attempt-time{text-align:left}.attempt-copy{-webkit-line-clamp:3}.review-grid{grid-template-columns:1fr}}
@media (max-width:760px){.shell,main{padding-left:12px;padding-right:12px}.topbar{align-items:flex-start;flex-direction:column;padding:14px 0}.grid{grid-template-columns:1fr}th,td{padding:10px 12px}.attempt-foot{align-items:flex-start;flex-direction:column}}
"""

FAVICON_HREF = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E"
    "%3Crect width='64' height='64' rx='14' fill='%230a0a0a'/%3E"
    "%3Crect x='8' y='42' width='48' height='10' rx='5' fill='%2300d4a4'/%3E"
    "%3C/svg%3E"
)
CONTEXT_ONLY_TOOLTIP = (
    "No tools were used; this answer was generated from conversation context only."
)
NO_AUDIT_DOCUMENTS_TOOLTIP = (
    "No audit documents were attached; this answer was generated without document evidence."
)
NO_AUDIT_CONTEXT_TOOLTIP = (
    "No audit documents or tool events were attached; this answer was generated from conversation context only."
)
NO_CODEX_SESSION_TOOLTIP = (
    "No Codex session is linked; review this attempt using the stored audit fields only."
)


def render_page(
    title: str,
    body: str,
    *,
    auto_refresh: bool = False,
    active_nav: str | None = None,
) -> str:
    refresh_meta = (
        "<meta http-equiv=\"refresh\" content=\"15\">" if auto_refresh else ""
    )
    nav_html = _top_nav(active_nav)
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"{refresh_meta}"
        f"<title>{escape(title)}</title>"
        f"<link rel=\"icon\" href=\"{FAVICON_HREF}\">"
        f"<style>{CSS}</style></head><body>"
        "<header><div class=\"shell topbar\"><a class=\"brand brand-home\" href=\"/\" aria-label=\"History home\">"
        "<div class=\"brand-mark\"></div><div>"
        f"<h1>{escape(title)}</h1><div class=\"eyebrow\">Local audit console</div>"
        "</div></a>"
        f"{nav_html}"
        "</div></header><main>"
        f"{body}</main></body></html>"
    )


def _top_nav(active_nav: str | None) -> str:
    items = [
        ("history", "History", "/"),
        ("codex", "Codex Sessions", "/codex"),
        ("config", "Config", "/config"),
        ("errors", "Errors", "/errors"),
    ]
    item_html = "".join(
        (
            f"<span class=\"nav-item active\" aria-current=\"page\">{escape(label)}</span>"
            if key == active_nav
            else f"<a class=\"nav-item\" href=\"{escape(href)}\">{escape(label)}</a>"
        )
        for key, label, href in items
    )
    return f"<nav class=\"nav\">{item_html}</nav>"


def render_config_page(
    *,
    active_tab: str = "info",
    saved: bool = False,
) -> str:
    if active_tab == "developer":
        content = _render_developer_prompt_editor_content(saved=saved)
    elif active_tab == "user":
        content = _render_user_prompt_editor_content(saved=saved)
    elif active_tab == "system":
        content = _render_system_config()
    else:
        active_tab = "info"
        content = _render_config_info()
    body = f"{_prompt_config_card(active_tab)}{_config_tabs(active_tab)}{content}"
    return render_page("Config", body, active_nav="config")


def _prompt_config_card(active_tab: str) -> str:
    return (
        "<section class=\"card\">"
        "<h2>Prompt config</h2>"
        "<p class=\"muted\">Shared configuration used while rendering Developer Prompt "
        "and User Prompt.</p>"
        "<details class=\"config-collapse\">"
        "<summary><h3>Config variables</h3></summary>"
        f"{_config_variable_form(active_tab)}"
        "</details>"
        "<details class=\"config-collapse\">"
        "<summary><h3>Dynamic functions</h3></summary>"
        f"{_user_prompt_dynamic_function_table()}"
        "</details>"
        "</section>"
    )


def _config_variable_form(active_tab: str) -> str:
    try:
        variable_inputs = _config_variable_inputs()
        error_html = ""
    except (OSError, DeveloperPromptTemplateError) as exc:
        variable_inputs = ""
        error_html = (
            "<p class=\"attempt-warning\">"
            f"Cannot load variables: {escape(str(exc))}"
            "</p>"
        )
    return (
        f"{error_html}"
        "<form method=\"post\" action=\"/config/variables\">"
        f"<input type=\"hidden\" name=\"active_tab\" value=\"{escape(active_tab)}\">"
        f"{variable_inputs}"
        "<p><button type=\"submit\">Save variables</button></p>"
        "</form>"
    )


def _render_config_info() -> str:
    logic_sections = _config_logic_sections()
    logic_html = "".join(
        "<section class=\"logic-section\">"
        f"<h3>{escape(title)}</h3>"
        "<dl>"
        + "".join(
            f"<div><dt>{escape(label)}</dt><dd>{_highlight_logic_text(description)}</dd></div>"
            for label, description in rows
        )
        + "</dl>"
        "</section>"
        for title, rows in logic_sections
    )
    return (
        "<section class=\"card\">"
        "<h2>Producer 路由配置</h2>"
        "<p class=\"muted\">这里展示 producer 如何把钉钉消息变成 reply task。</p>"
        f"<div class=\"logic-list\">{logic_html}</div>"
        "</section>"
    )


def _system_config_rows() -> list[tuple[str, str, str]]:
    mention_text = _csv_label(mention_aliases())
    broadcast_text = _csv_label(broadcast_mention_aliases())
    style_speaker_text = _csv_label(style_speaker_names())
    forbidden_path_text = _csv_label(forbidden_path_prefixes())
    return [
        (
            "CEO_PRINCIPAL_NAME",
            principal_name(),
            "代理对象账号名称；用于系统识别和派生 Prompt 里的 principal/handoff_name。",
        ),
        (
            "CEO_PRINCIPAL_DISPLAY_NAME",
            principal_display_name(),
            "系统展示名；用于运行时生成日历、profile 等系统文案。",
        ),
        (
            "CEO_PRINCIPAL_HANDOFF_NAME",
            principal_handoff_name(),
            "需要真人接管时使用的称呼。",
        ),
        (
            "CEO_MENTION_ALIASES",
            mention_text,
            "群聊/消息触发时识别点名 principal 的别名；影响 producer 候选生成。",
        ),
        (
            "CEO_BROADCAST_MENTION_ALIASES",
            broadcast_text,
            "识别 @所有人、@all 等广播消息；群聊广播也会进入候选判断。",
        ),
        (
            "CEO_STYLE_SPEAKER_NAMES",
            style_speaker_text,
            "用于从会议纪要和语料中抽取风格语料；可以和当前账号显示名不同。",
        ),
        (
            "CEO_ASSISTANT_SIGNATURE",
            assistant_signature(),
            "服务发送回复时追加的分身签名。",
        ),
        (
            "CEO_HANDOFF_ACK",
            handoff_ack(),
            "系统需要交给真人处理时的默认提示文案。",
        ),
        (
            "CEO_WORK_PROFILE_PATH",
            str(work_profile_path()),
            "work_profile_instruction() 读取这个文件并注入 Developer Prompt。",
        ),
        (
            "CEO_FORBIDDEN_PATH_PREFIXES",
            forbidden_path_text,
            "系统安全检查使用：按路径前缀识别本机路径泄漏。",
        ),
        (
            "MESSAGE_RECOVERY_INTERVAL",
            _duration_label(message_recovery_interval()),
            "每次慢路径兜底扫描之间至少间隔多久。",
        ),
        (
            "SINGLE_CHAT_READ_RECOVERY_WINDOW",
            _duration_label(single_chat_read_recovery_window()),
            "慢路径私聊恢复扫描回看多长时间内的会话。",
        ),
        (
            "SINGLE_CHAT_READ_RECOVERY_LIMIT",
            str(single_chat_read_recovery_limit()),
            "慢路径私聊恢复扫描最多读取多少个会话。",
        ),
        (
            "GROUP_READ_RECOVERY_WINDOW",
            _duration_label(group_read_recovery_window()),
            "慢路径群聊恢复扫描回看多长时间内的会话。",
        ),
        (
            "GROUP_READ_RECOVERY_LIMIT",
            str(group_read_recovery_limit()),
            "慢路径群聊恢复扫描最多读取多少个会话。",
        ),
    ]


def _config_variable_inputs() -> str:
    rows: list[str] = ["<tr><th>Key</th><th>Value</th><th>.env key</th></tr>"]
    for key, value in configurable_prompt_variable_pairs():
        rows.append(_variable_input_row(key, value))
    return "<table class=\"config-variable-table\">" + "".join(rows) + "</table>"


def _variable_input_row(key: str, value: str) -> str:
    return (
        "<tr>"
        f"<td><code class=\"config-value\">{escape(key)}</code>"
        f"<input type=\"hidden\" name=\"variable_key\" value=\"{escape(key)}\"></td>"
        f"<td><input class=\"config-value-input\" type=\"text\" name=\"variable_value\" value=\"{escape(value)}\"></td>"
        f"<td><code class=\"config-value\">{escape(prompt_variable_env_key(key))}</code></td>"
        "</tr>"
    )


def _developer_prompt_variable_map() -> dict[str, str]:
    return dict(configurable_prompt_variable_pairs())


def _render_system_config() -> str:
    editable_keys = _editable_system_config_keys()
    rows = [
        "<tr><th>Key</th><th>Current value</th><th>说明</th></tr>",
        *[
            "<tr>"
            f"<td>{_system_config_key_cell(key, key in editable_keys)}</td>"
            f"<td>{_system_config_value_cell(key, value, key in editable_keys)}</td>"
            f"<td>{escape(description)}</td>"
            "</tr>"
            for key, value, description in _system_config_rows()
        ],
    ]
    return (
        "<section class=\"card\">"
        "<h2>系统运行参数</h2>"
        "<p class=\"muted\">这些值来自环境变量或代码常量，用于服务运行；"
        "不写入 Prompt，也不会保存到 Developer Prompt 的 &lt;vars&gt;。"
        f"保存位置：<code>{escape(str(env_file_path()))}</code></p>"
        "<form method=\"post\" action=\"/config/system\">"
        "<table class=\"system-config-table\">"
        + "".join(rows)
        + "</table>"
        "<p><button type=\"submit\">Save system config</button></p>"
        "</form>"
        "</section>"
    )


def _editable_system_config_keys() -> set[str]:
    return {
        "CEO_PRINCIPAL_NAME",
        "CEO_PRINCIPAL_DISPLAY_NAME",
        "CEO_PRINCIPAL_HANDOFF_NAME",
        "CEO_MENTION_ALIASES",
        "CEO_BROADCAST_MENTION_ALIASES",
        "CEO_STYLE_SPEAKER_NAMES",
        "CEO_ASSISTANT_SIGNATURE",
        "CEO_HANDOFF_ACK",
        "CEO_WORK_PROFILE_PATH",
        "CEO_FORBIDDEN_PATH_PREFIXES",
        "MESSAGE_RECOVERY_INTERVAL",
        "SINGLE_CHAT_READ_RECOVERY_WINDOW",
        "SINGLE_CHAT_READ_RECOVERY_LIMIT",
        "GROUP_READ_RECOVERY_WINDOW",
        "GROUP_READ_RECOVERY_LIMIT",
    }


def _system_config_key_cell(key: str, editable: bool) -> str:
    if not editable:
        return f"<code class=\"config-value\">{escape(key)}</code>"
    return (
        f"<code class=\"config-value\">{escape(key)}</code>"
        f"<input type=\"hidden\" name=\"system_key\" value=\"{escape(key)}\">"
    )


def _system_config_value_cell(key: str, value: str, editable: bool) -> str:
    if not editable:
        return f"<code class=\"config-value\">{escape(value)}</code>"
    return (
        "<input class=\"config-value-input\" type=\"text\" "
        f"name=\"system_value\" value=\"{escape(value)}\" "
        f"aria-label=\"{escape(key)}\">"
    )


def _highlight_logic_text(text: str) -> str:
    highlighted = escape(text)
    terms = [
        _slash_label(mention_aliases()),
        _slash_label(broadcast_mention_aliases()),
        "list_unread_conversations(count=50)",
        "message_fast_path_checked_at",
        "read_unread_messages",
        "read_mentioned_messages",
        "addresses_principal",
        "seen_messages",
        "reply_tasks",
        _duration_label(message_recovery_interval()),
        _duration_label(single_chat_read_recovery_window()),
        _duration_label(group_read_recovery_window()),
    ]
    for term in sorted({item for item in terms if item}, key=len, reverse=True):
        escaped_term = escape(term)
        highlighted = highlighted.replace(
            escaped_term,
            f"<code class=\"config-token\">{escaped_term}</code>",
        )
    return highlighted


def _config_logic_sections() -> list[tuple[str, list[tuple[str, str]]]]:
    mention_example = _slash_label(mention_aliases())
    broadcast_example = _slash_label(broadcast_mention_aliases())
    fast_path_rows = [
        (
            "入口",
            "每次 producer 运行都会调用 list_unread_conversations(count=50)。"
            "慢路径未到点时，会过滤早于 message_fast_path_checked_at 的会话。",
        ),
        (
            "读取",
            "未读会话使用 read_unread_messages。producer 也会调用 "
            f"read_mentioned_messages 和广播 mention 查询，所以即使未读状态不完整，"
            f"也能找到 {mention_example}、{broadcast_example} 这类点名或广播消息。",
        ),
        (
            "输出",
            "候选消息会经过过滤、按 seen_messages 去重、检查过期窗口；"
            "之后要么作为通知/系统消息跳过，要么进入 reply_tasks。",
        ),
    ]
    slow_path_rows = [
        (
            "周期",
            f"每 {_duration_label(message_recovery_interval())} 运行一次。",
        ),
        (
            "私聊恢复",
            "从本地 DB 加入最近 "
            f"{_duration_label(single_chat_read_recovery_window())} 内的私聊会话，最多 "
            f"{single_chat_read_recovery_limit()} 个。它会读取最近消息和未读消息，"
            "再处理 latest seen message 之后的新消息。",
        ),
        (
            "群聊恢复",
            "从本地 DB 加入最近 "
            f"{_duration_label(group_read_recovery_window())} 内的群聊会话，最多 "
            f"{group_read_recovery_limit()} 个。群聊候选仍然必须点名 "
            f"{principal_display_name()} 或配置的广播别名，才会进入队列。",
        ),
    ]
    group_rows = [
        (
            "触发",
            "群聊候选必须通过 addresses_principal："
            f"包含 {mention_example}，或包含 {broadcast_example} 这类广播别名。"
            "没有这些点名信息的群聊消息，快路径和慢路径都不会处理。",
        ),
        (
            "文档",
            "群聊文档卡片只有先满足上面的群聊触发规则，才会进入 agent 判断。"
            f"没有 {mention_example} 的普通群聊文档分享不会创建 reply task。",
        ),
        (
            "合并",
            "同一发送人的连续候选消息会先合并再入队，所以一个 reply_task "
            "可以代表一小段相关群聊消息。",
        ),
    ]
    direct_rows = [
        (
            "触发",
            f"私聊不要求 {mention_example}。经过未读/恢复选择和系统通知过滤后，"
            "最新一条剩余私聊消息会进入 agent 判断。",
        ),
        (
            "文档",
            "私聊文档会进入 agent 判断；不能因为文档卡片渲染成图片/链接卡片，"
            "就直接当作 no_reply。",
        ),
        (
            "系统过滤",
            "预过滤仍会跳过明确的系统/状态通知、本人消息、过期且已 seen 的消息，"
            "以及不可处理的渲染媒体。日历、OA 审批、会议纪要权限消息会绕过通用通知跳过逻辑，进入各自的专门处理器。",
        ),
    ]
    return [
        ("快路径", fast_path_rows),
        ("慢路径", slow_path_rows),
        ("群聊", group_rows),
        ("私聊", direct_rows),
    ]


def _csv_label(values: tuple[str, ...]) -> str:
    return ", ".join(values)


def _slash_label(values: tuple[str, ...]) -> str:
    return "/".join(values)


def _duration_label(value) -> str:
    total_seconds = int(value.total_seconds())
    if total_seconds % 3600 == 0:
        hours = total_seconds // 3600
        return f"{hours}h"
    if total_seconds % 60 == 0:
        minutes = total_seconds // 60
        return f"{minutes}m"
    return f"{total_seconds}s"


def render_attempt_list(store: AutoReplyStore, limit: int | None = None) -> str:
    items = []
    for task in store.list_reply_tasks(
        statuses=("pending", "processing"),
        limit=limit,
    ):
        items.append(_reply_task_item(task))
    for attempt in store.list_reply_attempts(limit=limit):
        codex_session_id = attempt.codex_session_id or store.get_codex_session_id(
            attempt.conversation_id
        )
        warning_text = _attempt_warning_summary(attempt)
        warning_html = (
            f"<span class=\"attempt-warning\">{escape(warning_text)}</span>"
            if warning_text
            else ""
        )
        info_html = _attempt_info_icon(attempt)
        foot_section = (
            f'<div class="attempt-foot">{warning_html}</div>' if warning_html else ""
        )
        items.append(
            "<article class=\"attempt-item\">"
            "<div class=\"attempt-head\">"
            "<div class=\"attempt-title\">"
            f"<a class=\"attempt-id\" href=\"/attempts/{attempt.id}\">#{attempt.id}</a>"
            f"{info_html}"
            f"<span class=\"pill action-{escape(attempt.action)}\">{escape(attempt.action)}</span>"
            f"<span class=\"pill status-{escape(attempt.send_status)}\">{escape(attempt.send_status)}</span>"
            f"<div class=\"attempt-main\">{escape(attempt.conversation_title)}</div>"
            f"<div class=\"attempt-meta\">{escape(attempt.trigger_sender)}</div>"
            "</div>"
            "<div class=\"attempt-side\">"
            f"<time class=\"attempt-time\">{escape(attempt.created_at)}</time>"
            "<div class=\"attempt-actions\">"
            f"{_review_link(attempt)}"
            f"{_codex_link(codex_session_id)}"
            "</div>"
            "</div>"
            "</div>"
            "<div class=\"attempt-lines\">"
            f"{_attempt_text_line('问', attempt.trigger_text, 260)}"
            f"{_attempt_text_line('答', _reply_preview_text(attempt), 320)}"
            "</div>"
            f"{foot_section}"
            "</article>"
        )
    if not items:
        body = (
            "<section class=\"card\"><p class=\"muted\">No reply attempts recorded.</p>"
            f"<p class=\"muted\">DB: {escape(str(store.path))}</p></section>"
        )
    else:
        body = "<section class=\"attempt-feed\">" + "".join(items) + "</section>"
    return render_page(
        "CEO Agent Audit",
        body,
        auto_refresh=True,
        active_nav="history",
    )


def _reply_task_item(task: ReplyTask) -> str:
    error_html = (
        f"<div class=\"attempt-foot\"><span class=\"attempt-warning\">{escape(task.error)}</span></div>"
        if task.error
        else ""
    )
    return (
        "<article class=\"attempt-item\">"
        "<div class=\"attempt-head\">"
        "<div class=\"attempt-title\">"
        f"<span class=\"attempt-id\">#task-{task.id}</span>"
        "<span class=\"pill action-send_reply\">Queued / processing</span>"
        f"<span class=\"pill status-{escape(task.status)}\">{escape(task.status)}</span>"
        f"<div class=\"attempt-main\">{escape(task.conversation_title)}</div>"
        f"<div class=\"attempt-meta\">{escape(task.trigger_sender)}</div>"
        "</div>"
        "<div class=\"attempt-side\">"
        f"<time class=\"attempt-time\">{escape(task.updated_at)}</time>"
        "</div>"
        "</div>"
        "<div class=\"attempt-lines\">"
        f"{_attempt_text_line('问', task.trigger_text, 260)}"
        f"{_attempt_text_line('进', _reply_task_progress_text(task), 320)}"
        "</div>"
        f"{error_html}"
        "</article>"
    )


def _reply_task_progress_text(task: ReplyTask) -> str:
    if task.status == "pending":
        return "已进入处理队列，等待分身生成回复"
    if task.status == "processing":
        return "分身正在处理"
    if task.error:
        return task.error
    return "任务尚未完成"


def render_attempt_detail(store: AutoReplyStore, attempt_id: int) -> tuple[int, str]:
    attempt = store.get_reply_attempt(attempt_id)
    if attempt is None:
        return 404, render_page(
            "Attempt not found",
            f"<p>Attempt #{attempt_id} does not exist.</p>",
        )
    sent_reply = store.get_sent_reply(
        attempt.conversation_id,
        attempt.trigger_message_id,
    )
    codex_session_id = attempt.codex_session_id or store.get_codex_session_id(
        attempt.conversation_id
    )
    return 200, render_page(
        f"Attempt #{attempt.id}",
        _attempt_detail_body(attempt, sent_reply, codex_session_id),
        active_nav="history",
    )


def render_codex_session_list(store: AutoReplyStore) -> str:
    rows = []
    for conversation in store.list_codex_conversations():
        session_id = conversation.codex_session_id or ""
        latest_attempts = store.list_reply_attempts_for_conversation(
            conversation.conversation_id,
            limit=1,
        )
        history_cell = _attempt_link(latest_attempts[0]) if latest_attempts else ""
        rows.append(
            "<tr>"
            f"<td>{escape(conversation.title)}</td>"
            f"<td>{escape(conversation.conversation_id)}</td>"
            f"<td>{escape('single' if conversation.single_chat else 'group')}</td>"
            f"<td><a href=\"/codex/{escape(session_id)}\">{escape(session_id)}</a></td>"
            f"<td>{history_cell}</td>"
            "</tr>"
        )
    table = (
        "<table><thead><tr><th>Conversation</th><th>ID</th><th>Type</th>"
        "<th>Codex session</th><th>History</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )
    return render_page("Codex Sessions", table, active_nav="codex")


def render_codex_session_detail(
    session_id: str,
    codex_home: Path | None = None,
    store: AutoReplyStore | None = None,
) -> tuple[int, str]:
    rendered = render_local_codex_session(session_id, codex_home=codex_home)
    if rendered.missing:
        related_attempts = (
            store.list_reply_attempts_for_codex_session(session_id) if store else []
        )
        if related_attempts:
            body = (
                "<section class=\"card\"><h2>Codex session unavailable</h2>"
                "<p class=\"muted\">The local Codex transcript file for this session "
                "is no longer available on this machine.</p>"
                f"<p class=\"muted\">{escape(session_id)}</p></section>"
                f"{_related_history_card(related_attempts)}"
            )
            return 200, render_page(
                "Codex session unavailable",
                body,
                active_nav="codex",
            )
        return 404, render_page(
            "Codex session not found",
            f"<p>Codex session not found: {escape(session_id)}</p>",
            active_nav="codex",
        )
    events = "".join(_codex_event_card(event) for event in rendered.events)
    related_history = _related_history_card(
        store.list_reply_attempts_for_codex_session(session_id) if store else []
    )
    body = (
        "<section class=\"card\"><div class=\"grid\">"
        f"<div class=\"muted\">session id</div><div>{escape(rendered.session_id)}</div>"
        f"<div class=\"muted\">local file</div><div>{escape(str(rendered.path or ''))}</div>"
        f"<div class=\"muted\">rendered events</div><div>{len(rendered.events)}</div>"
        "</div></section>"
        f"{related_history}"
        f"{events}"
    )
    return 200, render_page(f"Codex Session {session_id}", body, active_nav="codex")


def render_error_list(store: AutoReplyStore, limit: int | None = None) -> str:
    rows = []
    for error in store.list_errors(limit=limit):
        resolution = _error_resolution_label(store, error)
        status_class = (
            "status-resolved"
            if resolution.startswith("resolved")
            else "status-active"
        )
        rows.append(
            "<tr>"
            f"<td>#{error.id}</td>"
            f"<td>{escape(error.created_at)}</td>"
            f"<td>{escape(error.conversation_id or '')}</td>"
            f"<td>{escape(error.message_id or '')}</td>"
            f"<td><span class=\"pill status-failed\">{escape(error.kind)}</span></td>"
            f"<td><span class=\"pill {status_class}\">{escape(resolution)}</span></td>"
            f"<td>{escape(error.detail)}</td>"
            "</tr>"
        )
    table = (
        "<table><thead><tr><th>ID</th><th>Time</th><th>Conversation</th>"
        "<th>Message</th><th>Kind</th><th>Status</th><th>Detail</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )
    return render_page("Errors", table, active_nav="errors")


def _config_tabs(active_tab: str) -> str:
    info_class = "prompt-tab active" if active_tab == "info" else "prompt-tab"
    system_class = "prompt-tab active" if active_tab == "system" else "prompt-tab"
    developer_class = (
        "prompt-tab active" if active_tab == "developer" else "prompt-tab"
    )
    user_class = "prompt-tab active" if active_tab == "user" else "prompt-tab"
    return (
        "<nav class=\"prompt-tabs\" aria-label=\"Config sections\">"
        f"<a class=\"{info_class}\" href=\"/config?tab=info\">Info</a>"
        f"<a class=\"{system_class}\" href=\"/config?tab=system\">"
        "System Config</a>"
        f"<a class=\"{developer_class}\" href=\"/config?tab=developer\">"
        "Developer Prompt</a>"
        f"<a class=\"{user_class}\" href=\"/config?tab=user\">"
        "User Prompt</a>"
        "</nav>"
    )


def render_developer_prompt_editor(
    *,
    active_tab: str = "developer",
    saved: bool = False,
) -> str:
    if active_tab not in {"developer", "user"}:
        active_tab = "developer"
    return render_config_page(active_tab=active_tab, saved=saved)


def _render_developer_prompt_editor_content(*, saved: bool = False) -> str:
    template_path = developer_prompt_template_path()
    error_html = ""
    try:
        template = read_developer_prompt_template()
    except OSError as exc:
        template = ""
        error_html = (
            "<p class=\"attempt-warning\">"
            f"Cannot read template: {escape(str(exc))}"
            "</p>"
        )
    _, body_template = split_developer_prompt_template(template)
    try:
        preview = render_developer_prompt_template(template) if template else ""
    except DeveloperPromptTemplateError as exc:
        preview = ""
        error_html = (
            "<p class=\"attempt-warning\">"
            f"Template render error: {escape(str(exc))}"
            "</p>"
        )
    saved_html = "<p class=\"muted\">Saved.</p>" if saved else ""
    return (
        "<section class=\"card\">"
        "<div class=\"grid\">"
        "<div class=\"muted\">template path</div>"
        f"<div>{escape(str(template_path))}</div>"
        "</div>"
        f"{saved_html}{error_html}"
        "<form method=\"post\" action=\"/config?tab=developer\">"
        "<label for=\"template\">Template</label>"
        f"<textarea id=\"template\" name=\"template\" style=\"min-height:520px\">{escape(body_template)}</textarea>"
        "<p><button type=\"submit\">Save template</button></p>"
        "</form>"
        "</section>"
        "<section class=\"card\">"
        "<h2>Rendered preview</h2>"
        f"<pre>{escape(preview)}</pre>"
        "</section>"
    )


def _render_user_prompt_editor_content(*, saved: bool = False) -> str:
    template_path = user_prompt_template_path()
    error_html = ""
    try:
        template = read_user_prompt_template()
    except OSError as exc:
        template = ""
        error_html = (
            "<p class=\"attempt-warning\">"
            f"Cannot read template: {escape(str(exc))}"
            "</p>"
        )
    try:
        preview = render_user_prompt_template(template, {}) if template else ""
    except DeveloperPromptTemplateError as exc:
        preview = ""
        error_html = (
            "<p class=\"attempt-warning\">"
            f"Template render error: {escape(str(exc))}"
            "</p>"
        )
    saved_html = "<p class=\"muted\">Saved.</p>" if saved else ""
    return (
        "<section class=\"card\">"
        "<div class=\"grid\">"
        "<div class=\"muted\">template path</div>"
        f"<div>{escape(str(template_path))}</div>"
        "</div>"
        f"{saved_html}{error_html}"
        "<form method=\"post\" action=\"/config?tab=user\">"
        "<label for=\"template\">Template</label>"
        f"<textarea id=\"template\" name=\"template\" style=\"min-height:520px\">{escape(template)}</textarea>"
        "<p><button type=\"submit\">Save template</button></p>"
        "</form>"
        "</section>"
        "<section class=\"card\">"
        "<h2>Rendered preview</h2>"
        f"<pre>{escape(preview)}</pre>"
        "</section>"
    )


def _user_prompt_dynamic_function_table() -> str:
    blocks = [
        UserPromptBlock(
            name="work_profile_instruction",
            expression="ceo_agent_service.prompt:work_profile_instruction()",
            description="读取并注入工作人格 Profile；通常用于 Developer Prompt。",
            default=(
                "工作人格 Profile:\n"
                "- 由服务端注入；不要再尝试读取 profile 文件路径。\n"
                "- 用于学习判断顺序、追问方式和回复边界。"
            ),
        ),
        *USER_PROMPT_BLOCKS,
    ]
    rows = [
        "<tr><th>Function</th><th>Description</th><th>Default preview</th></tr>",
        *[
            "<tr>"
            f"<td><code>{escape(block.name)}()</code><br>"
            f"<code>&lt;code: {escape(block.expression)}&gt;</code></td>"
            f"<td>{escape(block.description)}</td>"
            f"<td><pre class=\"dynamic-preview\">{escape(block.default)}</pre></td>"
            "</tr>"
            for block in blocks
        ],
    ]
    return "<table>" + "".join(rows) + "</table>"


def _error_resolution_label(store: AutoReplyStore, error: ReplyError) -> str:
    if not error.conversation_id or not error.message_id:
        return "active"
    if store.get_sent_reply(error.conversation_id, error.message_id):
        return "resolved: sent"
    attempt = store.get_latest_reply_attempt_for_trigger(
        error.conversation_id,
        error.message_id,
    )
    if attempt and attempt.send_status == "sent":
        return "resolved: sent"
    return "active"


def handle_feedback_post(
    store: AutoReplyStore, attempt_id: int, body: bytes
) -> tuple[int, dict[str, str], str]:
    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    feedback = parsed.get("feedback", [""])[0]
    corrected_reply = parsed.get("corrected_reply", [""])[0]
    if not store.record_reply_feedback(
        attempt_id,
        feedback=feedback,
        corrected_reply_text=corrected_reply,
    ):
        return 404, {}, render_page("Attempt not found", "Attempt not found")
    return 303, {"Location": f"/attempts/{attempt_id}"}, ""


def handle_developer_prompt_post(body: bytes) -> tuple[int, dict[str, str], str]:
    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    template = parsed.get("template", [""])[0]
    write_developer_prompt_template(template.strip())
    return 303, {"Location": "/config?tab=developer&saved=1"}, ""


def handle_prompt_variables_post(body: bytes) -> tuple[int, dict[str, str], str]:
    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    active_tab = parsed.get("active_tab", ["info"])[0]
    if active_tab not in {"info", "system", "developer", "user"}:
        active_tab = "info"
    write_configurable_prompt_variables(
        [
            (key, value)
            for key, value in zip_longest(
                parsed.get("variable_key", []),
                parsed.get("variable_value", []),
                fillvalue="",
            )
        ]
    )
    return 303, {"Location": f"/config?tab={active_tab}&saved=1"}, ""


def handle_system_config_post(body: bytes) -> tuple[int, dict[str, str], str]:
    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    editable_keys = _editable_system_config_keys()
    updates = {
        key: value
        for key, value in zip_longest(
            parsed.get("system_key", []),
            parsed.get("system_value", []),
            fillvalue="",
        )
        if key in editable_keys
    }
    write_env_values(updates)
    return 303, {"Location": "/config?tab=system&saved=1"}, ""


def handle_user_prompt_post(body: bytes) -> tuple[int, dict[str, str], str]:
    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    template = parsed.get("template", [""])[0]
    write_user_prompt_template(template)
    return 303, {"Location": "/config?tab=user&saved=1"}, ""


def handle_recall_post(
    store: AutoReplyStore, dws, attempt_id: int
) -> tuple[int, dict[str, str], str]:
    attempt = store.get_reply_attempt(attempt_id)
    if attempt is None:
        return 404, {}, render_page("Attempt not found", "Attempt not found")
    sent_reply = store.get_sent_reply(
        attempt.conversation_id,
        attempt.trigger_message_id,
    )
    if sent_reply is None or not sent_reply.recall_key:
        return (
            400,
            {},
            render_page(
                "撤销不可用",
                "<p>撤销不可用：没有可撤销 key，当前发送方式不支持自动撤销。</p>",
            ),
        )
    try:
        dws.recall_bot_message(attempt.conversation_id, sent_reply.recall_key)
    except Exception as exc:
        store.update_sent_reply_recall(
            sent_reply.id,
            recall_status="failed",
            recall_error=str(exc),
        )
        return (
            500,
            {},
            render_page("撤销失败", f"<p>{escape(str(exc))}</p>"),
        )
    store.update_sent_reply_recall(
        sent_reply.id,
        recall_status="recalled",
        recall_error="",
    )
    return 303, {"Location": f"/attempts/{attempt_id}"}, ""


def handle_reviewed_message_reply(
    store: AutoReplyStore,
    dws: DwsClient,
    *,
    user_name: str,
    group_name: str,
    message_str: str,
    reply_text: str,
    reviewer_feedback: str = "",
) -> dict[str, object]:
    conversations = dws.search_conversations(group_name)
    exact_conversations = [
        conversation for conversation in conversations if conversation.title == group_name
    ]
    stored_conversation = None
    if len(exact_conversations) != 1:
        stored_conversation = store.find_conversation_by_title(group_name)
    if len(exact_conversations) != 1 and stored_conversation is not None:
        exact_conversations = [
            DingTalkConversation(
                open_conversation_id=stored_conversation.conversation_id,
                title=stored_conversation.title,
                single_chat=stored_conversation.single_chat,
                unread_point=1,
            )
        ]
    if len(exact_conversations) != 1:
        raise ValueError(
            f"expected one conversation named {group_name!r}, got {len(exact_conversations)}"
        )
    conversation = exact_conversations[0]
    messages = _reviewed_reply_lookup_messages(dws, conversation)
    matches = [
        message
        for message in messages
        if message.sender_name == user_name and message.content == message_str
    ]
    if not matches:
        raise ValueError("message not found for user_name/group_name/message_str")
    trigger = matches[0]
    store.upsert_conversation(
        conversation_id=conversation.open_conversation_id,
        title=conversation.title,
        single_chat=conversation.single_chat,
        codex_session_id=None,
    )
    attempt_id = store.record_reply_attempt(
        conversation_id=conversation.open_conversation_id,
        conversation_title=conversation.title,
        trigger_message_id=trigger.open_message_id,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        action=CodexAction.SEND_REPLY.value,
        sensitivity_kind=SensitivityKind.GENERAL.value,
        codex_reason="reviewed_message_reply",
        draft_reply_text=reply_text,
        audit_tool_events_json=json.dumps(
            [
                {
                    "tool": "audit_web.handle_reviewed_message_reply",
                    "result": "matched user_name/group_name/message_str",
                }
            ],
            ensure_ascii=False,
        ),
        audit_summary="已按发送人、群名、消息原文定位并处理。",
    )
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=None, dry_run=False)
    worker._send_reply(
        conversation=conversation,
        trigger=trigger,
        new_messages=[trigger],
        reply_text=reply_text,
        reason="reviewed_message_reply",
        attempt_id=attempt_id,
    )
    if reviewer_feedback.strip():
        store.record_reply_feedback(
            attempt_id,
            feedback=reviewer_feedback,
            corrected_reply_text=reply_text,
        )
    attempt = store.get_reply_attempt(attempt_id)
    if attempt is None:
        raise ValueError(f"reply attempt disappeared: {attempt_id}")
    return {
        "attempt_id": attempt_id,
        "conversation_title": conversation.title,
        "trigger_sender": trigger.sender_name,
        "trigger_text": trigger.content,
        "send_status": attempt.send_status,
        "final_reply_text": attempt.final_reply_text,
        "reviewer_feedback": attempt.reviewer_feedback,
    }


def _reviewed_reply_lookup_messages(
    dws: DwsClient,
    conversation: DingTalkConversation,
) -> list[DingTalkMessage]:
    seen_message_ids: set[str] = set()
    result: list[DingTalkMessage] = []
    lookup_batches = []
    if not conversation.single_chat:
        lookup_batches.append(dws.read_mentioned_messages(conversation, limit=100))
    lookup_batches.extend(
        [
            dws.read_recent_messages(conversation),
            dws.read_unread_messages(conversation),
        ]
    )
    for message in [message for batch in lookup_batches for message in batch]:
        if message.open_message_id in seen_message_ids:
            continue
        seen_message_ids.add(message.open_message_id)
        result.append(message)
    return result


def create_audit_app(
    db_path: Path,
    ding_robot_code: str | None = None,
    ding_robot_name: str | None = None,
) -> FastAPI:
    app = FastAPI(title="CEO Agent Audit")

    @app.get("/", response_class=HTMLResponse)
    def attempt_list() -> str:
        return render_attempt_list(AutoReplyStore(db_path))

    @app.get("/errors", response_class=HTMLResponse)
    def error_list() -> str:
        return render_error_list(AutoReplyStore(db_path))

    @app.get("/codex", response_class=HTMLResponse)
    def codex_session_list() -> str:
        return render_codex_session_list(AutoReplyStore(db_path))

    @app.get("/codex/{session_id}", response_class=HTMLResponse)
    def codex_session_detail(session_id: str) -> HTMLResponse:
        status, html = render_codex_session_detail(
            session_id,
            store=AutoReplyStore(db_path),
        )
        return HTMLResponse(html, status_code=status)

    @app.get("/developer-prompt", response_class=HTMLResponse)
    def developer_prompt_editor(request: Request) -> str:
        tab = request.query_params.get("tab", "developer")
        saved_suffix = "&saved=1" if request.query_params.get("saved") == "1" else ""
        return RedirectResponse(f"/config?tab={tab}{saved_suffix}", status_code=303)

    @app.get("/config", response_class=HTMLResponse)
    def config_page(request: Request) -> str:
        return render_config_page(
            active_tab=request.query_params.get("tab", "info"),
            saved=request.query_params.get("saved") == "1",
        )

    @app.get("/attempts/{attempt_id}", response_class=HTMLResponse)
    def attempt_detail(attempt_id: int) -> HTMLResponse:
        status, html = render_attempt_detail(AutoReplyStore(db_path), attempt_id)
        return HTMLResponse(html, status_code=status)

    @app.post("/attempts/{attempt_id}/feedback")
    async def feedback(attempt_id: int, request: Request):
        status, headers, html = handle_feedback_post(
            AutoReplyStore(db_path),
            attempt_id,
            await request.body(),
        )
        return _fastapi_post_response(status, headers, html)

    @app.post("/developer-prompt")
    async def developer_prompt_save(request: Request):
        if request.query_params.get("tab") == "user":
            status, headers, html = handle_user_prompt_post(await request.body())
        else:
            status, headers, html = handle_developer_prompt_post(await request.body())
        return _fastapi_post_response(status, headers, html)

    @app.post("/config")
    async def config_save(request: Request):
        if request.query_params.get("tab") == "user":
            status, headers, html = handle_user_prompt_post(await request.body())
        else:
            status, headers, html = handle_developer_prompt_post(await request.body())
        return _fastapi_post_response(status, headers, html)

    @app.post("/config/variables")
    async def config_variables_save(request: Request):
        status, headers, html = handle_prompt_variables_post(await request.body())
        return _fastapi_post_response(status, headers, html)

    @app.post("/config/system")
    async def config_system_save(request: Request):
        status, headers, html = handle_system_config_post(await request.body())
        return _fastapi_post_response(status, headers, html)

    @app.post("/attempts/{attempt_id}/recall")
    def recall(attempt_id: int):
        status, headers, html = handle_recall_post(
            AutoReplyStore(db_path),
            DwsClient(ding_robot_code=ding_robot_code, ding_robot_name=ding_robot_name),
            attempt_id,
        )
        return _fastapi_post_response(status, headers, html)

    @app.post("/messages/reviewed-reply")
    async def reviewed_reply(request: Request):
        payload = json.loads((await request.body()).decode("utf-8"))
        result = handle_reviewed_message_reply(
            AutoReplyStore(db_path),
            DwsClient(ding_robot_code=ding_robot_code, ding_robot_name=ding_robot_name),
            user_name=str(payload["user_name"]),
            group_name=str(payload["group_name"]),
            message_str=str(payload["message_str"]),
            reply_text=str(payload["reply_text"]),
            reviewer_feedback=str(
                payload.get("reviewer_feedback") or payload.get("feedback") or ""
            ),
        )
        return JSONResponse(result)

    return app


def create_default_audit_app() -> FastAPI:
    return create_audit_app(
        Path(os.environ["CEO_WORKER_DB"]),
        ding_robot_code=os.getenv("CEO_DING_ROBOT_CODE")
        or os.getenv("DINGTALK_DING_ROBOT_CODE"),
        ding_robot_name=os.getenv("CEO_DING_ROBOT_NAME"),
    )


def run_audit_web(
    db_path: Path,
    host: str,
    port: int,
    ding_robot_code: str | None = None,
    ding_robot_name: str | None = None,
    reload: bool = False,
    reload_delay_seconds: int = 1,
    reload_dirs: list[Path] | None = None,
) -> None:
    print(f"audit-web listening on http://{host}:{port}", flush=True)
    if reload:
        os.environ["CEO_WORKER_DB"] = str(db_path)
        if ding_robot_code:
            os.environ["CEO_DING_ROBOT_CODE"] = ding_robot_code
        if ding_robot_name:
            os.environ["CEO_DING_ROBOT_NAME"] = ding_robot_name
        uvicorn.run(
            "ceo_agent_service.audit_web:create_default_audit_app",
            factory=True,
            host=host,
            port=port,
            loop="asyncio",
            http="h11",
            reload=True,
            reload_delay=reload_delay_seconds,
            reload_dirs=[str(path) for path in reload_dirs] if reload_dirs else None,
        )
        return

    uvicorn.run(
        create_audit_app(
            db_path,
            ding_robot_code=ding_robot_code,
            ding_robot_name=ding_robot_name,
        ),
        host=host,
        port=port,
        loop="asyncio",
        http="h11",
    )


def _fastapi_post_response(status: int, headers: dict[str, str], html: str):
    if status == 303:
        return RedirectResponse(headers["Location"], status_code=303)
    return HTMLResponse(html, status_code=status)


def _attempt_detail_body(
    attempt: ReplyAttempt,
    sent_reply: SentReply | None,
    codex_session_id: str | None,
) -> str:
    fields = [
        ("conversation", attempt.conversation_title),
        ("trigger sender", attempt.trigger_sender),
        ("trigger message id", attempt.trigger_message_id),
        ("action", attempt.action),
        ("sensitivity", attempt.sensitivity_kind),
        ("permission", attempt.permission_action),
        ("permission reason", attempt.permission_reason),
        ("send status", attempt.send_status),
        ("send error", attempt.send_error),
        ("retry count", str(attempt.retry_count)),
        ("created", attempt.created_at),
        ("updated", attempt.updated_at),
        ("reviewed", attempt.reviewed_at or ""),
    ]
    rows = "".join(
        f"<div class=\"muted\">{escape(label)}</div><div>{escape(value)}</div>"
        for label, value in fields
    )
    return (
        f"{_review_panel(attempt)}"
        f"<section class=\"card compact-card\"><div class=\"grid\">{rows}</div></section>"
        f"{_quality_warning_card(attempt)}"
        f"{_context_only_info_card(attempt)}"
        f"{_oa_metadata_card(attempt)}"
        f"{_recall_card(attempt, sent_reply)}"
        f"{_codex_session_card(codex_session_id, attempt)}"
        f"{_text_card('Trigger', attempt.trigger_text)}"
        f"{_text_card('Codex reason', attempt.codex_reason)}"
        f"{_text_card('Audit summary', attempt.audit_summary)}"
        f"{_collapsible_json_card('Audit documents', attempt.audit_documents_json)}"
        f"{_collapsible_json_card('Audit tool events', attempt.audit_tool_events_json)}"
        f"{_text_card('Draft reply (raw Codex reply)', attempt.draft_reply_text)}"
        f"{_text_card('Final reply (send-ready text)', attempt.final_reply_text)}"
    )


def _review_panel(attempt: ReplyAttempt) -> str:
    reply_text = attempt.final_reply_text or attempt.draft_reply_text
    if not reply_text.strip():
        reply_text = "No generated reply recorded."
    return (
        "<section class=\"review-grid\">"
        "<div class=\"card\">"
        "<div class=\"reply-meta\">"
        f"<span class=\"pill action-{escape(attempt.action)}\">{escape(attempt.action)}</span>"
        f"<span class=\"pill status-{escape(attempt.send_status)}\">{escape(attempt.send_status)}</span>"
        "</div>"
        "<h2>Trigger</h2>"
        f"<pre class=\"trigger-pre\">{escape(_trigger_text(attempt))}</pre>"
        "<h2>生成回复</h2>"
        f"<pre class=\"reply-pre\">{escape(reply_text)}</pre>"
        "</div>"
        f"{_feedback_form(attempt)}"
        "</section>"
    )


def _codex_session_card(
    codex_session_id: str | None, attempt: ReplyAttempt | None = None
) -> str:
    if not codex_session_id:
        return (
            "<section class=\"card\"><h2>Codex local history</h2>"
            "<p class=\"muted\">No Codex session recorded for this conversation.</p>"
            "</section>"
        )
    line_range = ""
    if attempt and attempt.codex_transcript_end_line > attempt.codex_transcript_start_line:
        line_range = (
            f"<p class=\"muted\">lines {attempt.codex_transcript_start_line}-"
            f"{attempt.codex_transcript_end_line}</p>"
        )
    return (
        "<section class=\"card\"><h2>Codex local history</h2>"
        f"<p><a href=\"/codex/{escape(codex_session_id)}\">"
        "View rendered Codex session</a></p>"
        f"<p class=\"muted\">{escape(codex_session_id)}</p>"
        f"{line_range}"
        "</section>"
    )


def _quality_warning_card(attempt: ReplyAttempt) -> str:
    warnings = _quality_warnings(attempt)
    if not warnings:
        return ""
    items = "".join(f"<li>{escape(warning)}</li>" for warning in warnings)
    return (
        "<section class=\"card quality-warning\"><h2>Audit quality warnings</h2>"
        f"<ul>{items}</ul></section>"
    )


def _context_only_info_card(attempt: ReplyAttempt) -> str:
    info_icon = _attempt_info_icon(attempt)
    if not info_icon:
        return ""
    return (
        "<section class=\"card compact-card\">"
        f"<h2 class=\"context-only-info\">Audit context {info_icon}</h2>"
        "</section>"
    )


def _oa_metadata_card(attempt: ReplyAttempt) -> str:
    if not any(
        value.strip()
        for value in (
            attempt.oa_process_instance_id,
            attempt.oa_task_id,
            attempt.oa_url,
            attempt.oa_action,
            attempt.oa_remark,
            attempt.oa_action_result_json,
        )
    ):
        return ""
    rows = "".join(
        f"<div class=\"muted\">{escape(label)}</div><div>{escape(value)}</div>"
        for label, value in (
            ("process instance", attempt.oa_process_instance_id),
            ("task id", attempt.oa_task_id),
            ("url", attempt.oa_url),
            ("action", attempt.oa_action),
            ("remark", attempt.oa_remark),
        )
    )
    return (
        "<section class=\"card compact-card\"><h2>OA approval</h2>"
        f"<div class=\"grid\">{rows}</div></section>"
        f"{_json_card('OA action result', attempt.oa_action_result_json)}"
    )


def _quality_warnings(attempt: ReplyAttempt) -> list[str]:
    if attempt.send_status == "skipped":
        return []
    warnings: list[str] = []
    if not attempt.audit_summary.strip():
        warnings.append("missing audit_summary")
    return warnings


def _attempt_warning_summary(attempt: ReplyAttempt) -> str:
    warnings = _quality_warnings(attempt)
    if not warnings:
        return ""
    if len(warnings) == 1:
        return f"Quality warning: {warnings[0]}"
    return f"Quality warnings: {len(warnings)}"


def _attempt_info_icon(attempt: ReplyAttempt) -> str:
    tooltip = _attempt_info_tooltip(attempt)
    if not tooltip:
        return ""
    escaped_tooltip = escape(tooltip)
    return (
        f"<span class=\"attempt-info\" data-tooltip=\"{escaped_tooltip}\" "
        f"aria-label=\"{escaped_tooltip}\" tabindex=\"0\">i</span>"
    )


def _attempt_info_tooltip(attempt: ReplyAttempt) -> str:
    if attempt.send_status == "skipped" or attempt.action not in {
        "send_reply",
        "ask_clarifying_question",
    }:
        return ""
    notes: list[str] = []
    if not attempt.codex_session_id.strip():
        notes.append(NO_CODEX_SESSION_TOOLTIP)
    has_documents = _json_array_has_items(
        attempt.audit_documents_json
    ) or audit_summary_explains_no_documents(attempt.audit_summary)
    has_tool_events = _json_array_has_items(attempt.audit_tool_events_json)
    if not has_documents and not has_tool_events:
        notes.append(NO_AUDIT_CONTEXT_TOOLTIP)
    elif not has_documents:
        notes.append(NO_AUDIT_DOCUMENTS_TOOLTIP)
    elif not has_tool_events:
        notes.append(CONTEXT_ONLY_TOOLTIP)
    return " ".join(notes)


def _json_array_has_items(text: str) -> bool:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(payload, list) and len(payload) > 0


def _related_history_card(attempts: list[ReplyAttempt]) -> str:
    if not attempts:
        return (
            "<section class=\"card\"><h2>Related history</h2>"
            "<p class=\"muted\">No reply attempts recorded for this Codex session.</p>"
            "</section>"
        )
    rows = []
    for attempt in attempts:
        rows.append(
            "<tr>"
            f"<td>{_attempt_link(attempt)}</td>"
            f"<td>{escape(attempt.created_at)}</td>"
            f"<td>{escape(attempt.trigger_sender)}</td>"
            f"<td><span class=\"pill action-{escape(attempt.action)}\">{escape(attempt.action)}</span></td>"
            f"<td><span class=\"pill status-{escape(attempt.send_status)}\">{escape(attempt.send_status)}</span></td>"
            f"<td>{escape(_excerpt(attempt.trigger_text, 120))}</td>"
            "</tr>"
        )
    return (
        "<section class=\"card\"><h2>Related history</h2>"
        "<table><thead><tr><th>Attempt</th><th>Time</th><th>Sender</th>"
        "<th>Action</th><th>Status</th><th>Trigger</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></section>"
    )


def _codex_event_card(event: RenderedCodexEvent) -> str:
    open_attr = " open" if event.expanded else ""
    preview = _excerpt(event.body, 140)
    return (
        f"<details class=\"event event-{escape(event.kind)}\"{open_attr}>"
        "<summary>"
        "<div>"
        f"<div class=\"event-title\">{escape(event.title)}</div>"
        f"<div class=\"event-preview\">{escape(preview)}</div>"
        "</div>"
        f"<time>{escape(event.timestamp)}</time>"
        "</summary>"
        f"<pre>{escape(event.body)}</pre>"
        "</details>"
    )


def _recall_card(attempt: ReplyAttempt, sent_reply: SentReply | None) -> str:
    if attempt.send_status != "sent":
        return ""
    if sent_reply is None:
        return (
            "<section class=\"card\"><h2>撤销发送</h2>"
            "<p class=\"muted\">撤销不可用：没有找到对应的发送记录。</p></section>"
        )
    if sent_reply.recall_status == "recalled":
        return (
            "<section class=\"card\"><h2>撤销发送</h2>"
            f"<p>已撤销：{escape(sent_reply.recalled_at or '')}</p></section>"
        )
    if not sent_reply.recall_key:
        return (
            "<section class=\"card\"><h2>撤销发送</h2>"
            "<p class=\"muted\">撤销不可用：当前发送方式不支持。dws 目前只支持机器人消息通过 processQueryKey 撤回；这条消息是当前用户身份发送，未记录可撤销 key。</p>"
            "</section>"
        )
    return (
        "<section class=\"card\"><h2>撤销发送</h2>"
        f"<form method=\"post\" action=\"/attempts/{attempt.id}/recall\" "
        "onsubmit=\"return confirm('确认撤销这条已发送消息？');\">"
        "<button class=\"danger\" type=\"submit\">撤销这条消息</button>"
        "</form></section>"
    )


def _feedback_form(attempt: ReplyAttempt) -> str:
    return (
        f"<section class=\"card\" id=\"feedback\"><h2>记录反馈 / 修改意见</h2>"
        f"<form method=\"post\" action=\"/attempts/{attempt.id}/feedback\">"
        "<label>反馈意见</label><textarea name=\"feedback\" "
        "placeholder=\"这条判断哪里不对、为什么不满意、以后应该遵守什么规则\">"
        f"{escape(attempt.reviewer_feedback)}</textarea>"
        "<label>建议回复</label><textarea name=\"corrected_reply\" "
        "placeholder=\"如果重写，这条消息应该怎么回复\">"
        f"{escape(attempt.corrected_reply_text)}</textarea>"
        "<p><button type=\"submit\">保存反馈</button></p></form></section>"
    )


def _review_link(attempt: ReplyAttempt) -> str:
    label = "查看/反馈" if not (attempt.reviewer_feedback or attempt.corrected_reply_text) else "查看/修改"
    return f"<a class=\"review-link\" href=\"/attempts/{attempt.id}\">{label}</a>"


def _attempt_link(attempt: ReplyAttempt) -> str:
    return (
        f"<a href=\"/attempts/{attempt.id}\">"
        f"#{attempt.id} · {escape(attempt.action)} · {escape(attempt.send_status)}</a>"
    )


def _codex_link(codex_session_id: str | None) -> str:
    if not codex_session_id:
        return "<span class=\"muted\">-</span>"
    return (
        f"<a class=\"review-link\" href=\"/codex/{escape(codex_session_id)}\">"
        "Codex</a>"
    )


def _attempt_text_line(label: str, text: str, length: int) -> str:
    return (
        "<div class=\"attempt-line\">"
        f"<span class=\"attempt-label\">{escape(label)}</span>"
        f"<span class=\"attempt-copy\">{escape(_excerpt(text, length))}</span>"
        "</div>"
    )


def _reply_preview_text(attempt: ReplyAttempt) -> str:
    text = attempt.final_reply_text or attempt.draft_reply_text
    lines = text.splitlines()
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith(">")):
        lines.pop(0)
    preview = "\n".join(lines).strip()
    return preview or text


def _text_card(title: str, text: str) -> str:
    return f"<section class=\"card\"><h2>{escape(title)}</h2><pre>{escape(text)}</pre></section>"


def _json_card(title: str, text: str) -> str:
    return (
        f"<section class=\"card\"><h2>{escape(title)}</h2>"
        f"<pre class=\"json-pre\">{_json_html(text)}</pre></section>"
    )


def _collapsible_json_card(title: str, text: str) -> str:
    return (
        "<details class=\"card collapsible-card\">"
        f"<summary><h2>{escape(title)}</h2></summary>"
        f"<pre class=\"json-pre\">{_json_html(text)}</pre></details>"
    )


def _trigger_text(attempt: ReplyAttempt) -> str:
    if attempt.trigger_sender.strip():
        return f"{attempt.trigger_sender}: {attempt.trigger_text}"
    return attempt.trigger_text


def _json_html(text: str) -> str:
    try:
        payload = json.loads(text or "[]")
    except Exception:
        return escape(text)
    return _json_value_html(payload, 0)


def _json_value_html(value, level: int) -> str:
    indent = " " * (level * 2)
    child_indent = " " * ((level + 1) * 2)
    if isinstance(value, dict):
        if not value:
            return "{}"
        lines = ["{"]
        items = list(value.items())
        for index, (key, item_value) in enumerate(items):
            comma = "," if index < len(items) - 1 else ""
            key_html = (
                f"<span class=\"json-key\">"
                f"{escape(json.dumps(str(key), ensure_ascii=False))}</span>"
            )
            lines.append(
                f"{child_indent}{key_html}: "
                f"{_json_value_html(item_value, level + 1)}{comma}"
            )
        lines.append(f"{indent}" + "}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return "[]"
        lines = ["["]
        for index, item_value in enumerate(value):
            comma = "," if index < len(value) - 1 else ""
            lines.append(
                f"{child_indent}{_json_value_html(item_value, level + 1)}{comma}"
            )
        lines.append(f"{indent}]")
        return "\n".join(lines)
    if isinstance(value, str):
        return (
            f"<span class=\"json-string\">"
            f"{escape(json.dumps(value, ensure_ascii=False))}</span>"
        )
    if isinstance(value, bool):
        return f"<span class=\"json-bool\">{str(value).lower()}</span>"
    if value is None:
        return "<span class=\"json-null\">null</span>"
    return f"<span class=\"json-number\">{escape(str(value))}</span>"


def _attempt_id_from_path(path: str) -> int | None:
    parts = path.strip("/").split("/")
    if len(parts) != 2 or parts[0] != "attempts":
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def _excerpt(text: str, length: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= length:
        return normalized
    return f"{normalized[:length]}..."
