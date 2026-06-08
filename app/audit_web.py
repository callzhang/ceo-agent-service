import json
import asyncio
from collections.abc import Iterable
from collections import deque
from datetime import datetime, timedelta, timezone, tzinfo
from html import escape
from itertools import count, zip_longest
import os
from pathlib import Path
import subprocess
import urllib.error
import urllib.request
from urllib.parse import parse_qs, quote, urlparse

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)

from app.codex_history import (
    RenderedCodexEvent,
    extract_codex_audit_events_from_session,
    render_local_codex_session,
)
from app.codex_decision import audit_summary_explains_no_documents
from app.config import (
    assistant_signature,
    batch_seconds,
    broadcast_mention_aliases,
    consumer_poll_interval_seconds,
    corpus_dir,
    document_extraction_ids,
    env_file_path,
    fast_path_unread_backoff_duration,
    feedback_spike_vercel_base_url,
    forbidden_path_prefixes,
    handoff_ack,
    memory_connector_user_id,
    mention_aliases,
    message_recovery_interval,
    poll_interval_seconds,
    principal_name,
    producer_interval_seconds,
    read_env_file,
    single_chat_read_recovery_limit,
    single_chat_read_recovery_window,
    user_alias,
    worker_db_path,
    write_env_values,
    work_profile_path,
    workspace_path,
)
from app.developer_prompt import (
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
from app.dingtalk_models import (
    CodexAction,
    DingTalkConversation,
    DingTalkMessage,
    SensitivityKind,
)
from app.dws_client import DwsClient
from app.feedback_spike import (
    FeedbackLinkContext,
    extract_feedback_link_context,
)
from app.store import (
    FAST_PATH_UNREAD_BACKOFF_TASK_ERROR,
    AutoReplyStore,
    FeedbackEvent,
    ReplyAttempt,
    ReplyError,
    ReplyTask,
    SentReply,
    UserFeedbackItem,
)
from app.user_prompt_blocks import USER_PROMPT_BLOCKS, UserPromptBlock

DISPLAY_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
from app.worker import DingTalkAutoReplyWorker


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
.config-variable-table th:first-child,.config-variable-table td:first-child{width:360px}
.config-variable-table td:first-child .config-value{white-space:nowrap;word-break:normal}
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
.notification-panel{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:12px 0}
.notification-log{max-height:260px}
.card-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px;flex-wrap:wrap}
.card-head h2{margin:0}
.compact-button{display:inline-flex;align-items:center;height:30px;padding:0 12px;border:1px solid var(--hairline);border-radius:999px;background:var(--canvas);color:var(--ink);font-size:13px;font-weight:500;line-height:1;white-space:nowrap}
.compact-button:hover{border-color:var(--ink);background:var(--surface-soft)}
.agent-log-button{display:inline-flex;align-items:center;height:34px;padding:0 14px;border:1px solid rgba(55,114,207,.38);border-radius:999px;background:#3772cf;color:#fff;font-size:13px;font-weight:700;line-height:1;white-space:nowrap;box-shadow:0 6px 18px rgba(55,114,207,.18)}
.agent-log-button:hover{background:#245aa5;color:#fff;text-decoration:none}
.pagination{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:0 0 12px;padding:8px 10px;border:1px solid var(--hairline);border-radius:8px;background:var(--surface-soft);flex-wrap:wrap}
.pagination.bottom{margin:12px 0 0}
.pagination-status{display:flex;align-items:center;gap:8px;min-width:0;flex-wrap:wrap}
.pagination-range{color:var(--ink);font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;font-size:13px;font-weight:800;line-height:1.3}
.pagination-page{display:inline-flex;align-items:center;height:24px;padding:0 8px;border:1px solid rgba(0,180,138,.28);border-radius:999px;background:#ddfff6;color:#005b49;font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;font-size:12px;font-weight:800;line-height:1}
.pagination-total{color:var(--steel);font-size:12px;font-weight:600;line-height:1.35}
.pagination-actions{display:flex;align-items:center;gap:4px;padding:3px;border:1px solid var(--hairline);border-radius:999px;background:var(--canvas);flex-wrap:nowrap}
.pagination-button{display:inline-flex;align-items:center;justify-content:center;height:28px;min-width:34px;padding:0 10px;border:1px solid transparent;border-radius:999px;background:transparent;color:var(--steel);font-size:12px;font-weight:700;line-height:1;white-space:nowrap}
.pagination-button:hover{border-color:var(--hairline);background:var(--surface-soft);color:var(--ink);text-decoration:none}
.pagination-arrow{min-width:28px;padding:0 8px;font-size:16px}
.pagination-button.is-disabled{color:var(--muted);background:var(--surface-soft);cursor:default}
.pagination-button.is-disabled:hover{border-color:transparent;color:var(--muted);background:var(--surface-soft)}
.history-chart-card{padding:16px 18px;margin:0 0 12px}
.history-chart-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:10px;flex-wrap:wrap}
.history-chart-title{margin:0;color:var(--ink);font-size:16px;font-weight:700;line-height:1.35}
.history-chart-subtitle{color:var(--steel);font-size:12px;font-weight:600;line-height:1.4}
.history-chart{width:100%;height:260px}
.history-chart-empty{display:flex;align-items:center;justify-content:center;height:180px;border:1px dashed var(--hairline);border-radius:8px;color:var(--steel);background:var(--surface-soft);font-size:13px}
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
.attempt-conversation-banner{display:flex;align-items:center;justify-content:space-between;gap:14px;border:1px solid rgba(0,180,138,.34);background:#f3fffb}
.attempt-conversation-left{display:flex;align-items:center;gap:14px;min-width:0}
.attempt-conversation-label{display:inline-flex;align-items:center;height:28px;padding:0 10px;border-radius:999px;background:#ddfff6;border:1px solid rgba(0,180,138,.42);color:#005b49;font-size:12px;font-weight:800;white-space:nowrap}
.attempt-conversation-main{min-width:0}
.attempt-conversation-title{color:var(--ink);font-size:20px;font-weight:750;line-height:1.3;word-break:break-word}
.attempt-conversation-sub{margin-top:2px;color:var(--steel);font-size:12px;font-weight:600;line-height:1.4}
.attempt-detail-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
.attempt-detail-cell{min-width:0;padding:10px 12px;border:1px solid var(--hairline);border-radius:8px;background:var(--surface-soft)}
.attempt-detail-label{margin-bottom:4px;color:var(--steel);font-size:12px;font-weight:700;line-height:1.35}
.attempt-detail-value{color:var(--ink);font-size:13px;font-weight:600;line-height:1.45;word-break:break-word}
.feedback-chip{display:inline-flex;align-items:center;max-width:100%;min-height:24px;padding:3px 9px;border-radius:999px;background:#ddfff6;border:1px solid rgba(0,180,138,.42);color:#005b49;font-size:12px;font-weight:700;line-height:1.35;white-space:nowrap}
.feedback-card{border-color:rgba(0,180,138,.28);background:linear-gradient(180deg,#ffffff 0%,#f6fffc 100%)}
.feedback-event{border:1px solid var(--hairline);border-radius:8px;background:var(--canvas);padding:12px;margin-top:10px}
.feedback-event-head{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:8px}
.feedback-rating{display:inline-flex;align-items:center;min-height:26px;padding:4px 10px;border-radius:999px;background:rgba(0,212,164,.12);border:1px solid rgba(0,180,138,.28);color:#005b49;font-size:13px;font-weight:700}
.feedback-comment{font-size:14px;color:var(--charcoal);white-space:pre-wrap}
.feedback-token{font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;color:var(--steel);font-size:12px;word-break:break-all}
.user-feedback-table th:nth-child(1),.user-feedback-table td:nth-child(1){width:112px}
.user-feedback-table th:nth-child(2),.user-feedback-table td:nth-child(2){width:100px}
.user-feedback-table th:nth-child(4),.user-feedback-table td:nth-child(4){width:150px}
.user-feedback-table th:nth-child(5),.user-feedback-table td:nth-child(5){width:190px}
.user-feedback-comment{font-weight:600;color:var(--ink)}
.user-feedback-context{margin-top:4px;color:var(--steel);font-size:12px;line-height:1.4}
.user-feedback-actions{display:flex;align-items:center;gap:8px;flex-wrap:nowrap;white-space:nowrap}
.user-feedback-actions form{display:inline-flex;margin:0}
.user-feedback-actions button{display:inline-flex;align-items:center;height:30px;padding:0 12px;border:1px solid var(--hairline);border-radius:999px;background:var(--canvas);color:var(--ink);font-size:13px;font-weight:500;line-height:1;white-space:nowrap}
.user-feedback-actions button:hover{border-color:var(--ink);background:var(--surface-soft)}
.audit-tool-list{display:grid;gap:12px;margin-top:8px}
.audit-tool-event{border:1px solid var(--hairline);border-radius:8px;background:var(--canvas);padding:12px}
.audit-tool-head{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:10px}
.audit-tool-title{display:flex;align-items:center;gap:8px;min-width:0;color:var(--ink);font-size:14px;font-weight:750}
.audit-tool-index{font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;color:var(--steel);font-size:12px;font-weight:700}
.audit-tool-command{max-width:100%;font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;color:var(--steel);font-size:12px;line-height:1.4;word-break:break-word}
.audit-tool-io{display:grid;gap:8px}
.audit-tool-section{display:grid;gap:4px}
.audit-tool-label{color:var(--steel);font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.03em}
.audit-tool-pre{margin:0;max-height:420px;overflow:auto;border:1px solid var(--hairline);border-radius:7px;background:var(--surface-soft);padding:9px 10px;color:var(--charcoal);font-size:12px;line-height:1.45;white-space:pre-wrap;word-break:break-word}
.attempt-info{position:relative;display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border:1px solid #d29a12;border-radius:50%;color:#8a5a08;background:#fff3c4;font-size:11px;font-weight:700;line-height:1;cursor:help;flex:0 0 auto}
.attempt-info:hover,.attempt-info:focus{background:#ffe7a3;border-color:#b77908;outline:0}
.attempt-info::after{content:attr(data-tooltip);display:none;position:absolute;left:0;bottom:calc(100% + 8px);z-index:30;width:max-content;max-width:min(320px,calc(100vw - 48px));padding:7px 9px;border-radius:6px;background:#1f2937;color:#fff;box-shadow:0 8px 24px rgba(15,23,42,.18);font-size:12px;font-weight:500;line-height:1.4;text-align:left;white-space:normal}
.attempt-info::before{content:"";display:none;position:absolute;left:4px;bottom:calc(100% + 3px);z-index:31;border:5px solid transparent;border-top-color:#1f2937}
.attempt-info:hover::after,.attempt-info:focus::after,.attempt-info:hover::before,.attempt-info:focus::before{display:block}
.nav{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.nav-item{display:inline-flex;align-items:center;height:36px;padding:0 14px;border:1px solid var(--hairline);border-radius:999px;background:var(--canvas);color:var(--steel);font-size:14px;font-weight:500}
a.nav-item:hover{color:var(--ink);text-decoration:none;border-color:var(--ink)}
.nav-item.active{background:var(--ink);border-color:var(--ink);color:#fff;cursor:default}
.nav-badge{display:inline-flex;align-items:center;justify-content:center;min-width:18px;height:18px;margin-left:7px;padding:0 5px;border-radius:999px;background:#d45656;color:#fff;font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;font-size:11px;font-weight:800;line-height:1}
.prompt-tabs{display:inline-flex;align-items:center;gap:6px;padding:4px;border:1px solid var(--hairline);border-radius:999px;background:var(--surface-soft);margin:0 0 12px}
.prompt-tab{display:inline-flex;align-items:center;height:32px;padding:0 13px;border-radius:999px;color:var(--steel);font-size:13px;font-weight:600}
.prompt-tab:hover{text-decoration:none;color:var(--ink)}
.prompt-tab.active{background:var(--ink);color:#fff}
.pill{display:inline-flex;align-items:center;min-height:24px;padding:3px 9px;border-radius:999px;background:var(--surface);color:var(--steel);border:1px solid var(--hairline);font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;font-size:12px;line-height:1.3;white-space:nowrap}
.status-sent{background:rgba(0,212,164,.12);color:#006b55;border-color:rgba(0,180,138,.28)}
.status-resolved{background:rgba(0,212,164,.12);color:#006b55;border-color:rgba(0,180,138,.28)}
.status-pending,.status-processing,.status-commented{background:rgba(55,114,207,.10);color:#245aa5;border-color:rgba(55,114,207,.24)}
.status-skipped{background:var(--surface);color:var(--stone)}
.status-failed,.status-blocked,.status-active{background:rgba(212,86,86,.12);color:#9a2f2f;border-color:rgba(212,86,86,.24)}
.status-action{background:var(--surface);color:var(--steel);border-color:var(--hairline)}
.action-state-sent,.action-state-accepted,.action-state-approved,.action-state-resolved{background:rgba(0,212,164,.12);color:#006b55;border-color:rgba(0,180,138,.28)}
.action-state-skipped{background:var(--surface);color:var(--stone);border-color:var(--hairline)}
.action-state-pending,.action-state-processing,.action-state-dry-run,.action-state-commented{background:rgba(55,114,207,.10);color:#245aa5;border-color:rgba(55,114,207,.24)}
.action-state-tentative,.action-state-returned{background:rgba(195,125,13,.12);color:#8a5a08;border-color:rgba(195,125,13,.24)}
.action-state-failed,.action-state-blocked,.action-state-declined,.action-state-rejected{background:rgba(212,86,86,.12);color:#9a2f2f;border-color:rgba(212,86,86,.24)}
.quality-warning{border-color:rgba(212,86,86,.28);background:rgba(212,86,86,.08)}
.quality-warning ul{margin:8px 0 0;padding-left:20px;color:#8a2626}
.context-only-info{display:inline-flex;align-items:center;gap:8px}
.card{background:var(--canvas);border:1px solid var(--hairline);border-radius:8px;padding:24px;margin:16px 0}
.card h2{margin:0 0 14px;color:var(--ink);font-size:18px;font-weight:600;line-height:1.4;letter-spacing:0}
.card p{margin:8px 0}
.review-grid{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(340px,.75fr);gap:16px;align-items:start;margin:16px 0}
.review-grid .card{margin:0}
.review-side{display:grid;gap:16px}
.reply-pre{min-height:188px;background:var(--surface-soft);border-color:var(--hairline);font-size:14px;line-height:1.55}
.reply-meta{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
.trigger-pre{min-height:0;margin:0 0 14px;background:var(--surface-soft);border-color:var(--hairline);font-size:14px;line-height:1.55}
.codex-reason{margin:0 0 14px;padding:12px 14px;border:1px solid rgba(55,114,207,.22);border-radius:8px;background:rgba(55,114,207,.08);color:var(--charcoal);font-size:14px;line-height:1.5;white-space:pre-wrap}
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
@media (max-width:900px){.attempt-head{align-items:flex-start;flex-direction:column}.attempt-title{flex-wrap:wrap}.attempt-side{align-items:flex-start;flex-direction:column;gap:6px}.attempt-main,.attempt-meta{white-space:normal}.attempt-time{text-align:left}.attempt-copy{-webkit-line-clamp:3}.review-grid{grid-template-columns:1fr}.attempt-detail-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:760px){.shell,main{padding-left:12px;padding-right:12px}.topbar{align-items:flex-start;flex-direction:column;padding:14px 0}.grid{grid-template-columns:1fr}th,td{padding:10px 12px}.attempt-foot{align-items:flex-start;flex-direction:column}.attempt-conversation-banner{align-items:flex-start;flex-direction:column}.attempt-detail-grid{grid-template-columns:1fr}.history-chart{height:220px}}
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
_BROWSER_NOTIFICATION_SUBSCRIBERS: set[asyncio.Queue[dict[str, str]]] = set()
_BROWSER_NOTIFICATION_HISTORY: deque[dict[str, str]] = deque(maxlen=20)
_BROWSER_NOTIFICATION_SEQUENCE = count(1)
_DINGTALK_BRIDGE_STATUS: deque[dict[str, str]] = deque(maxlen=20)
DEFAULT_ATTEMPT_LIST_LIMIT = 50
DEFAULT_ERROR_LIST_LIMIT = 50
HISTORY_CHART_HOURS = 24
HISTORY_CHART_COLORS = {
    "💬 Sent": "#00b48a",
    "💬 Skipped": "#a8a8aa",
    "💬 Processing": "#3772cf",
    "💬 Failed": "#d45656",
    "💬 Dry run": "#c37d0d",
    "📆 Accepted": "#00b48a",
    "📆 Tentative": "#c37d0d",
    "📆 Declined": "#d45656",
    "🧾 Approved": "#00b48a",
    "🧾 Commented": "#3772cf",
    "🧾 Returned": "#c37d0d",
    "🧾 Rejected": "#d45656",
}


def render_page(
    title: str,
    body: str,
    *,
    auto_refresh: bool = False,
    active_nav: str | None = None,
    user_feedback_pending_count: int | None = None,
) -> str:
    refresh_meta = (
        "<meta http-equiv=\"refresh\" content=\"15\">" if auto_refresh else ""
    )
    nav_html = _top_nav(active_nav, user_feedback_pending_count)
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
        f"{body}</main>{_browser_notification_client_script()}</body></html>"
    )


def render_browser_notifications_page() -> str:
    body = """
<section class="card">
<h2>Chrome 通知</h2>
<p class="muted">打开这个页面并允许通知后，CEO 服务会优先通过 Chrome 弹出通知。点击通知会打开对应的钉钉会话。</p>
<div class="notification-panel">
  <button type="button" id="enable-notifications">允许 Chrome 通知</button>
  <span class="pill" id="notification-state">checking</span>
</div>
<pre class="notification-log" id="notification-log">等待连接...</pre>
</section>
"""
    return render_page("Notifications", body, active_nav="notifications")


def _browser_notification_client_script() -> str:
    return """
<script>
(() => {
  const lockKey = "ceo-agent-service-notification-leader";
  const lockTtlMs = 5000;
  const heartbeatMs = 2000;
  const tabId = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
  const stateEl = document.getElementById("notification-state");
  const logEl = document.getElementById("notification-log");
  const enableButton = document.getElementById("enable-notifications");
  let events = null;
  let serviceWorkerReady = null;

  function logLine(text) {
    if (!logEl) {
      return;
    }
    const timestamp = new Date().toLocaleTimeString();
    logEl.textContent = `[${timestamp}] ${text}\n` + logEl.textContent;
  }

  function setState(text) {
    if (stateEl) {
      stateEl.textContent = text;
    }
  }

  function canNotify() {
    return "Notification" in window && Notification.permission === "granted";
  }

  function ensureServiceWorker() {
    if (!("serviceWorker" in navigator)) {
      return Promise.resolve(null);
    }
    if (!serviceWorkerReady) {
      serviceWorkerReady = navigator.serviceWorker
        .register("/notification-service-worker.js")
        .then(() => navigator.serviceWorker.ready)
        .catch((error) => {
          logLine(`service worker failed: ${error}`);
          return null;
        });
    }
    return serviceWorkerReady;
  }

  function readLock() {
    try {
      return JSON.parse(localStorage.getItem(lockKey) || "null");
    } catch (error) {
      return null;
    }
  }

  function writeLock() {
    localStorage.setItem(lockKey, JSON.stringify({ id: tabId, ts: Date.now() }));
  }

  function ownsFreshLock() {
    const lock = readLock();
    return lock && lock.id === tabId && Date.now() - Number(lock.ts || 0) < lockTtlMs;
  }

  function releaseLock() {
    if (ownsFreshLock()) {
      localStorage.removeItem(lockKey);
    }
  }

  async function showBrowserNotification(payload) {
    logLine(`${payload.title}: ${payload.message}`);
    if (!canNotify()) {
      return;
    }
    const options = {
      body: payload.message,
      tag: payload.id,
      renotify: true,
      data: { url: payload.url || "", detailUrl: payload.detail_url || "" },
    };
    const registration = await ensureServiceWorker();
    if (!registration) {
      logLine("notification skipped: service worker unavailable");
      return;
    }
    await registration.showNotification(payload.title, options);
  }

  function stopEvents() {
    if (events) {
      events.close();
      events = null;
    }
  }

  function startEvents() {
    if (events) {
      return;
    }
    events = new EventSource("/notifications/events");
    events.onopen = () => logLine("connected to 8765 notification stream");
    events.onerror = () => logLine("notification stream reconnecting");
    events.onmessage = (event) => {
      showBrowserNotification(JSON.parse(event.data));
    };
  }

  function refreshPermission() {
    if (!("Notification" in window)) {
      setState("not supported");
      if (enableButton) {
        enableButton.disabled = true;
      }
      return;
    }
    setState(Notification.permission);
  }

  function electLeader() {
    refreshPermission();
    if (!canNotify()) {
      releaseLock();
      stopEvents();
      return;
    }
    const lock = readLock();
    const lockIsStale = !lock || Date.now() - Number(lock.ts || 0) > lockTtlMs;
    if (lockIsStale || lock.id === tabId) {
      writeLock();
      ensureServiceWorker();
      startEvents();
      setState("granted connected");
      return;
    }
    stopEvents();
    setState("granted standby");
  }

  async function requestNotificationPermission() {
    if (!("Notification" in window)) {
      refreshPermission();
      return;
    }
    const permission = await Notification.requestPermission();
    logLine(`permission: ${permission}`);
    if (permission === "granted") {
      await ensureServiceWorker();
    }
    electLeader();
  }

  if (enableButton) {
    enableButton.addEventListener("click", requestNotificationPermission);
  }
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.addEventListener("message", (event) => {
      const payload = event.data || {};
      if (payload.type !== "ceo-agent-service:navigate" || !payload.url) {
        return;
      }
      const target = new URL(payload.url, window.location.origin);
      if (target.origin !== window.location.origin) {
        return;
      }
      const targetPath = `${target.pathname}${target.search}${target.hash}`;
      const currentPath = `${window.location.pathname}${window.location.search}${window.location.hash}`;
      if (targetPath !== currentPath) {
        window.location.assign(targetPath);
      }
    });
  }
  window.addEventListener("storage", (event) => {
    if (event.key === lockKey) {
      electLeader();
    }
  });
  window.addEventListener("beforeunload", () => {
    releaseLock();
    stopEvents();
  });
  setInterval(electLeader, heartbeatMs);
  electLeader();
})();
</script>
"""


def _notification_service_worker_script() -> str:
    return """
self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(handleNotificationClick(event.notification.data || {}));
});

async function handleNotificationClick(data) {
  if (data.url) {
    try {
      await fetch(data.url, {
        method: "GET",
        headers: { "Accept": "application/json" },
      });
    } catch (error) {
      // The backend bridge is best-effort; do not open a fallback browser tab.
    }
  }
  const windows = await self.clients.matchAll({
    type: "window",
    includeUncontrolled: true,
  });
  for (const client of windows) {
    try {
      if (new URL(client.url).origin === self.location.origin && client.focus) {
        await client.focus();
        if (data.detailUrl && client.postMessage) {
          client.postMessage({
            type: "ceo-agent-service:navigate",
            url: data.detailUrl,
          });
        }
        return;
      }
    } catch (error) {
      // Ignore malformed client URLs.
    }
  }
}
"""


def _browser_notification_event(
    *,
    title: str,
    message: str,
    url: str,
) -> dict[str, str]:
    return {
        "id": f"ceo-agent-service-{next(_BROWSER_NOTIFICATION_SEQUENCE)}",
        "title": title,
        "message": message,
        "url": url,
        "detail_url": _notification_detail_url(url),
    }


def _notification_detail_url(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    attempt_ids = query.get("attempt_id", [])
    if not attempt_ids:
        return ""
    try:
        attempt_id = int(attempt_ids[0])
    except ValueError:
        return ""
    if attempt_id <= 0:
        return ""
    return f"/attempts/{attempt_id}"


def _dingtalk_conversation_url(cid: str) -> str:
    return (
        "dingtalk://dingtalkclient/page/conversation"
        f"?cid={quote(cid.strip(), safe='')}"
    )


def _dingtalk_pc_slide_link_url(link: str) -> str:
    return (
        "dingtalk://dingtalkclient/page/link"
        f"?url={quote(link, safe='')}&pc_slide=true"
    )


def _dingtalk_url_from_bridge_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.path != "/open-dingtalk":
        return ""
    query = parse_qs(parsed.query)
    conversation_id = (query.get("conversation_id") or [""])[0].strip()
    if conversation_id:
        return _dingtalk_pc_slide_link_url(
            f"{parsed.scheme}://{parsed.netloc}/dingtalk/open-chat-bridge"
            f"?conversation_id={quote(conversation_id, safe='')}"
        )
    cid = (query.get("cid") or [""])[0].strip()
    if not cid:
        return ""
    return _dingtalk_conversation_url(cid)


def render_dingtalk_open_chat_bridge(open_conversation_id: str) -> str:
    escaped_conversation_id = json.dumps(open_conversation_id)
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>打开钉钉会话</title>
  <style>
    body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;padding:28px;background:#fff;color:#111;line-height:1.5}}
    .card{{max-width:520px;margin:12vh auto 0;border:1px solid #e5e5e5;border-radius:12px;padding:22px;background:#fafafa}}
    h1{{margin:0 0 10px;font-size:18px}}
    p{{margin:8px 0;color:#555}}
    code{{word-break:break-all;background:#eee;border-radius:6px;padding:2px 5px}}
  </style>
  <script src="https://g.alicdn.com/dingding/dingtalk-jsapi/3.0.25/dingtalk.open.js"></script>
</head>
<body>
  <section class="card">
    <h1>正在打开钉钉会话</h1>
    <p id="status">等待钉钉 JSAPI...</p>
    <p><code>{escape(open_conversation_id)}</code></p>
  </section>
  <script>
    const openConversationId = {escaped_conversation_id};
    const statusEl = document.getElementById("status");
    function report(stage, detail) {{
      const body = JSON.stringify({{
        conversation_id: openConversationId,
        stage,
        detail: detail || "",
      }});
      if (navigator.sendBeacon) {{
        navigator.sendBeacon("/dingtalk/bridge-status", body);
        return;
      }}
      fetch("/dingtalk/bridge-status", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body,
      }}).catch(() => {{}});
    }}
    function setStatus(text) {{
      statusEl.textContent = text;
      report("status", text);
    }}
    function apiNames(dd) {{
      const root = Object.keys(dd || {{}}).sort().slice(0, 80);
      const chat = Object.keys((dd && dd.biz && dd.biz.chat) || {{}}).sort();
      return JSON.stringify({{ root, chat }});
    }}
    function invokeWithCallbackTimeout(label, invoke) {{
      report("invoke", label);
      return new Promise((resolve) => {{
        let callbackSeen = false;
        const done = (ok, text) => {{
        callbackSeen = true;
        setStatus(text);
          resolve(ok);
        }};
        invoke(done);
        setTimeout(() => {{
          if (!callbackSeen) {{
            report("callback-timeout", label);
            resolve(false);
          }}
        }}, 1200);
      }});
    }}
    function closeBridgePageSoon() {{
      setTimeout(() => {{
        const dd = window.dd;
        const closeNavigation = dd && dd.biz && dd.biz.navigation && dd.biz.navigation.close;
        if (typeof closeNavigation === "function") {{
          report("close-navigation", "");
          closeNavigation({{}});
          return;
        }}
        if (dd && typeof dd.closePage === "function") {{
          report("close-page", "");
          dd.closePage({{}});
        }}
        window.close();
      }}, 600);
    }}
    async function openChat() {{
      const dd = window.dd;
      if (!dd) {{
        setStatus("钉钉 JSAPI 未加载。请确认本页是在钉钉客户端内打开。");
        return;
      }}
      report("dd-api-names", apiNames(dd));
      const attempts = [];
      const legacyApi = dd.biz && dd.biz.chat && dd.biz.chat.toConversationByOpenConversationId;
      if (typeof legacyApi === "function") {{
        attempts.push(["biz.chat.toConversationByOpenConversationId", (done) => {{
          legacyApi({{
            openConversationId,
            onSuccess: () => done(true, "已通过旧版桌面会话 API 发起跳转。"),
            onFail: (error) => done(false, `旧版桌面会话 API 跳转失败: ${{JSON.stringify(error)}}`),
          }});
        }}]);
      }}
      if (typeof dd.openChatByConversationId === "function") {{
        attempts.push(["openChatByConversationId", (done) => {{
          dd.openChatByConversationId({{
            openConversationId,
            success: () => done(true, "已通过新版会话 API 发起跳转。"),
            fail: (error) => done(false, `新版会话 API 跳转失败: ${{JSON.stringify(error)}}`),
            complete: () => {{}},
          }});
        }}]);
      }}
      const toConversationApi = dd.biz && dd.biz.chat && dd.biz.chat.toConversation;
      if (typeof toConversationApi === "function") {{
        attempts.push(["biz.chat.toConversation", (done) => {{
          toConversationApi({{
            cid: openConversationId,
            openConversationId,
            onSuccess: () => done(true, "已通过桌面会话 cid API 发起跳转。"),
            onFail: (error) => done(false, `桌面会话 cid API 跳转失败: ${{JSON.stringify(error)}}`),
          }});
        }}]);
      }}
      for (const [label, invoke] of attempts) {{
        const ok = await invokeWithCallbackTimeout(label, invoke);
        if (ok) {{
          closeBridgePageSoon();
          return;
        }}
      }}
      setStatus("当前钉钉客户端没有可用的桌面会话跳转能力，或全部跳转失败。");
    }}
    function openWhenReady() {{
      report("loaded", navigator.userAgent);
      if (window.dd && typeof window.dd.ready === "function") {{
        let opened = false;
        const openOnce = () => {{
          if (opened) {{
            return;
          }}
          opened = true;
          openChat();
        }};
        window.dd.ready(() => {{
          report("dd-ready", "");
          openOnce();
        }});
        window.dd.error((error) => setStatus(`JSAPI 初始化失败: ${{JSON.stringify(error)}}`));
        setTimeout(() => {{
          report("dd-ready-timeout", "");
          openOnce();
        }}, 1000);
        return;
      }}
      setTimeout(openChat, 350);
    }}
    window.addEventListener("load", openWhenReady);
  </script>
</body>
</html>"""


def _publish_browser_notification(event: dict[str, str]) -> bool:
    _BROWSER_NOTIFICATION_HISTORY.append(event)
    subscribers = list(_BROWSER_NOTIFICATION_SUBSCRIBERS)
    for queue in subscribers:
        queue.put_nowait(event)
    return bool(subscribers)


def _browser_notification_event_stream() -> StreamingResponse:
    async def event_stream():
        queue: asyncio.Queue[dict[str, str]] = asyncio.Queue()
        _BROWSER_NOTIFICATION_SUBSCRIBERS.add(queue)
        try:
            yield ": connected\n\n"
            while True:
                event = await queue.get()
                data = json.dumps(event, ensure_ascii=False)
                yield f"data: {data}\n\n"
        finally:
            _BROWSER_NOTIFICATION_SUBSCRIBERS.discard(queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _top_nav(
    active_nav: str | None,
    user_feedback_pending_count: int | None = None,
) -> str:
    items = [
        ("history", "History", "/"),
        ("tasks", "Tasks", "/tasks"),
        ("user-feedback", "用户反馈", "/user-feedback"),
        ("codex", "Codex Sessions", "/codex"),
        ("config", "Config", "/config"),
        ("errors", "Errors", "/errors"),
    ]
    item_html = "".join(
        _top_nav_item(
            key=key,
            label=label,
            href=href,
            active=key == active_nav,
            user_feedback_pending_count=user_feedback_pending_count,
        )
        for key, label, href in items
    )
    return f"<nav class=\"nav\">{item_html}</nav>"


def _top_nav_item(
    *,
    key: str,
    label: str,
    href: str,
    active: bool,
    user_feedback_pending_count: int | None,
) -> str:
    label_html = escape(label)
    if key == "user-feedback" and user_feedback_pending_count:
        badge_text = "99+" if user_feedback_pending_count > 99 else str(user_feedback_pending_count)
        label_html += f"<span class=\"nav-badge\">{escape(badge_text)}</span>"
    if active:
        return f"<span class=\"nav-item active\" aria-current=\"page\">{label_html}</span>"
    return f"<a class=\"nav-item\" href=\"{escape(href)}\">{label_html}</a>"


def render_config_page(
    *,
    active_tab: str = "info",
    saved: bool = False,
    db_path: Path | None = None,
) -> str:
    if active_tab == "developer":
        content = _render_developer_prompt_editor_content(saved=saved)
    elif active_tab == "user":
        content = _render_user_prompt_editor_content(saved=saved)
    elif active_tab == "system":
        content = _render_system_config(db_path=db_path)
    else:
        active_tab = "info"
        content = _render_config_info()
    body = f"{_prompt_config_card(active_tab)}{_config_tabs(active_tab)}{content}"
    pending_count = (
        AutoReplyStore(db_path).count_pending_user_feedback_items()
        if db_path is not None
        else None
    )
    return render_page(
        "Config",
        body,
        active_nav="config",
        user_feedback_pending_count=pending_count,
    )


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
    env_values = read_env_file()
    mention_text = _csv_label(mention_aliases())
    broadcast_text = _csv_label(broadcast_mention_aliases())
    document_extraction_text = _csv_label(document_extraction_ids())
    forbidden_path_text = _csv_label(forbidden_path_prefixes())
    known_rows = [
        (
            "CEO_PRINCIPAL_NAME",
            principal_name(),
            "代理对象账号名称；用于系统内部识别 principal。",
        ),
        (
            "USER_ALIAS",
            user_alias(),
            "用户别名；用于展示、handoff 文案、日历/profile 等运行时文案。",
        ),
        (
            "MEMORY_CONNECTOR_USER_ID",
            memory_connector_user_id(),
            "Memory Connector 的用户空间；用于 MCP header 和 prompt 中的 memory user_id。",
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
            "DOCUMENT_EXTRACTION_IDS",
            document_extraction_text,
            "用于从会议纪要和文档语料中抽取该身份的发言或材料。",
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
            "CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL",
            feedback_spike_vercel_base_url(),
            "对话方反馈页根地址；配置后发出的回复会自动追加赞踩链接并记录 feedback token。",
        ),
        (
            "CEO_WORKSPACE",
            str(workspace_path()),
            "本地知识库路径；Codex agent 和 graphify 从这里读取业务材料。",
        ),
        (
            "CEO_WORKER_DB",
            str(worker_db_path()),
            "本地 SQLite 运行状态和审计数据库路径。",
        ),
        (
            "CEO_CORPUS_DIR",
            str(corpus_dir()),
            "回复风格语料和检索语料的本地目录。",
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
            "CEO_PRODUCER_INTERVAL_SECONDS",
            str(producer_interval_seconds()),
            "主服务内 producer loop 的运行间隔。",
        ),
        (
            "CEO_CONSUMER_POLL_INTERVAL_SECONDS",
            str(consumer_poll_interval_seconds()),
            "consumer 检查 pending reply task 的间隔秒数。",
        ),
        (
            "CEO_POLL_INTERVAL_SECONDS",
            str(poll_interval_seconds()),
            "本地 run 模式下，快路径轮询未读会话的间隔秒数。",
        ),
        (
            "CEO_BATCH_SECONDS",
            str(batch_seconds()),
            "本地 run 模式下，每个消息发现批次覆盖的时间窗口秒数。",
        ),
        (
            "FAST_PATH_UNREAD_BACKOFF",
            _duration_label(fast_path_unread_backoff_duration()),
            "快路径扫描到未读会话后等待多久再读取，给真人先回复或清未读的时间。",
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
    ]
    descriptions = {key: description for key, _, description in known_rows}
    values = {key: value for key, value, _ in known_rows}
    ordered_keys = [key for key, _, _ in known_rows]
    for key in env_values:
        if key not in values:
            ordered_keys.append(key)
    return [
        (
            key,
            env_values.get(key, values.get(key, "")),
            descriptions.get(key, "来自 .env；服务启动或 prompt/config 渲染时读取。"),
        )
        for key in ordered_keys
    ]


def _config_variable_inputs() -> str:
    rows: list[str] = ["<tr><th>Key</th><th>Value</th></tr>"]
    for key, value in configurable_prompt_variable_pairs():
        rows.append(_variable_input_row(key, value))
    return "<table class=\"config-variable-table\">" + "".join(rows) + "</table>"


def _variable_input_row(key: str, value: str) -> str:
    env_key = prompt_variable_env_key(key)
    return (
        "<tr>"
        f"<td><code class=\"config-value\">{escape(env_key)}</code>"
        f"<input type=\"hidden\" name=\"variable_key\" value=\"{escape(env_key)}\"></td>"
        f"<td><input class=\"config-value-input\" type=\"text\" name=\"variable_value\" value=\"{escape(value)}\"></td>"
        "</tr>"
    )


def _developer_prompt_variable_map() -> dict[str, str]:
    return dict(configurable_prompt_variable_pairs())


def _render_system_config(*, db_path: Path | None = None) -> str:
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
        f"{_runtime_identity_cache_html(db_path)}"
        "</section>"
    )


def _runtime_identity_cache_html(db_path: Path | None) -> str:
    configured_db_path = os.environ.get("CEO_WORKER_DB")
    store_path = db_path or (Path(configured_db_path) if configured_db_path else None)
    current_user_id = ""
    if store_path is not None and store_path.exists():
        current_user_id = AutoReplyStore(store_path).get_current_user_id() or ""
    table = "".join(
        "<tr>"
        f"<td><code class=\"config-value\">{escape(key)}</code></td>"
        f"<td><code class=\"config-value\">{escape(value)}</code></td>"
        f"<td>{escape(description)}</td>"
        "</tr>"
        for key, value, description in [
            (
                "current_user_id",
                current_user_id or "not cached",
                "DWS 当前登录账号写入 DB 的只读缓存；用于识别本人消息，不从 .env 手填。",
            )
        ]
    )
    return (
        "<h3>运行时身份缓存</h3>"
        "<p class=\"muted\">只展示本人身份真值；消息字段和组织字段不在这里配置。</p>"
        "<table class=\"system-config-table\">"
        "<tr><th>Key</th><th>Current value</th><th>说明</th></tr>"
        f"{table}</table>"
    )


def _editable_system_config_keys() -> set[str]:
    return {
        "CEO_PRINCIPAL_NAME",
        "USER_ALIAS",
        "MEMORY_CONNECTOR_USER_ID",
        "CEO_MENTION_ALIASES",
        "CEO_BROADCAST_MENTION_ALIASES",
        "DOCUMENT_EXTRACTION_IDS",
        "CEO_ASSISTANT_SIGNATURE",
        "CEO_HANDOFF_ACK",
        "CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL",
        "CEO_WORKSPACE",
        "CEO_WORKER_DB",
        "CEO_CORPUS_DIR",
        "CEO_WORK_PROFILE_PATH",
        "CEO_FORBIDDEN_PATH_PREFIXES",
        "CEO_PRODUCER_INTERVAL_SECONDS",
        "CEO_CONSUMER_POLL_INTERVAL_SECONDS",
        "CEO_POLL_INTERVAL_SECONDS",
        "CEO_BATCH_SECONDS",
        "FAST_PATH_UNREAD_BACKOFF",
        "MESSAGE_RECOVERY_INTERVAL",
        "SINGLE_CHAT_READ_RECOVERY_WINDOW",
        "SINGLE_CHAT_READ_RECOVERY_LIMIT",
        *read_env_file().keys(),
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
        _duration_label(fast_path_unread_backoff_duration()),
        "read_unread_messages",
        "read_mentioned_messages",
        "addresses_principal",
        "seen_messages",
        "reply_tasks",
        _duration_label(message_recovery_interval()),
        _duration_label(single_chat_read_recovery_window()),
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
            "快路径首次扫描到未读会话后，会读取未读消息并写入 reply_tasks/pending，"
            f"但延迟 {_duration_label(fast_path_unread_backoff_duration())} 后才允许 consumer 领取；"
            "慢路径未到点时，会过滤早于 message_fast_path_checked_at 的会话。",
        ),
        (
            "读取",
            "快路径首次触发时使用 read_unread_messages 取得可审计的 trigger。producer 也会调用 "
            f"read_mentioned_messages 和广播 mention 查询，所以即使未读状态不完整，"
            f"也能找到 {mention_example}、{broadcast_example} 这类点名或广播消息。",
        ),
        (
            "输出",
            "候选消息会经过过滤、按 seen_messages 去重、检查过期窗口；"
            "之后要么作为通知/系统消息跳过，要么进入 reply_tasks。"
            "等待窗口结束时如果会话已不再未读，会记录 skipped；仍未读则进入 processing。",
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
            "慢路径不从本地 seen_messages 主动恢复群聊。群聊只通过 "
            "read_mentioned_messages、广播 mention 查询，或当前未读会话中的明确点名进入候选。",
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


def _page_offset(page: int, limit: int | None) -> int:
    if limit is None:
        return 0
    return max(0, page - 1) * limit


def _page_count(total_count: int, limit: int | None) -> int:
    if limit is None or limit <= 0:
        return 1
    return max(1, (max(0, total_count) + limit - 1) // limit)


def _bounded_page(page: int, limit: int | None, total_count: int) -> int:
    return min(max(1, page), _page_count(total_count, limit))


def _page_href(base_path: str, page: int) -> str:
    if page <= 1:
        return base_path
    return f"{base_path}?page={page}"


def _format_local_time(value: str, *, local_tz: tzinfo | None = None) -> str:
    raw = value.strip()
    if not raw:
        return ""
    local_timezone = local_tz or datetime.now().astimezone().tzinfo
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            parsed = datetime.strptime(raw, DISPLAY_TIME_FORMAT)
        except ValueError:
            return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(local_timezone).strftime(DISPLAY_TIME_FORMAT)


def _parse_utc_timestamp(value: str) -> datetime | None:
    raw = value.strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            parsed = datetime.strptime(raw, DISPLAY_TIME_FORMAT)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _history_event_label(attempt: ReplyAttempt) -> str:
    calendar_status = attempt.calendar_response_status.strip().lower()
    if calendar_status == "accepted":
        return "📆 Accepted"
    if calendar_status == "tentative":
        return "📆 Tentative"
    if calendar_status == "declined":
        return "📆 Declined"

    oa_action = attempt.oa_action.strip().lower()
    if oa_action in {"agree", "approve", "approved"}:
        return "🧾 Approved"
    if oa_action in {"comment", "commented"}:
        return "🧾 Commented"
    if oa_action in {"return", "returned"}:
        return "🧾 Returned"
    if oa_action in {"refuse", "reject", "rejected"}:
        return "🧾 Rejected"

    status = attempt.send_status.strip().lower()
    if status == "sent":
        return "💬 Sent"
    if status == "skipped":
        return "💬 Skipped"
    if status in {"failed", "blocked"}:
        return "💬 Failed"
    if status == "dry_run":
        return "💬 Dry run"
    return "💬 Processing"


def _history_chart_payload(
    store: AutoReplyStore,
    *,
    hours: int = HISTORY_CHART_HOURS,
    now: datetime | None = None,
) -> dict[str, object]:
    local_tz = datetime.now().astimezone().tzinfo
    local_now = now.astimezone(local_tz) if now else datetime.now(local_tz)
    bucket_count = max(1, hours)
    first_bucket = local_now.replace(minute=0, second=0, microsecond=0) - timedelta(
        hours=bucket_count - 1
    )
    labels = [
        (first_bucket + timedelta(hours=index)).strftime("%m-%d %H:%M")
        for index in range(bucket_count)
    ]
    since_utc = first_bucket.astimezone(timezone.utc).strftime(DISPLAY_TIME_FORMAT)
    attempts = store.list_reply_attempts_since(since_utc)
    bucket_values: dict[str, list[int]] = {}
    label_indexes = {label: index for index, label in enumerate(labels)}
    for attempt in attempts:
        created_at = _parse_utc_timestamp(attempt.created_at)
        if created_at is None:
            continue
        local_bucket = created_at.astimezone(local_tz).replace(
            minute=0,
            second=0,
            microsecond=0,
        )
        label = local_bucket.strftime("%m-%d %H:%M")
        bucket_index = label_indexes.get(label)
        if bucket_index is None:
            continue
        event_label = _history_event_label(attempt)
        bucket_values.setdefault(event_label, [0] * bucket_count)[bucket_index] += 1
    series = [
        {
            "name": name,
            "type": "bar",
            "stack": "events",
            "data": bucket_values[name],
            "itemStyle": {"color": HISTORY_CHART_COLORS.get(name, "#5a5a5c")},
        }
        for name in HISTORY_CHART_COLORS
        if name in bucket_values
    ]
    return {
        "labels": labels,
        "series": series,
        "total": sum(sum(item["data"]) for item in series),
        "range": f"{labels[0]} - {labels[-1]}",
    }


def _render_history_chart(store: AutoReplyStore) -> str:
    payload = _history_chart_payload(store)
    if int(payload["total"]) <= 0:
        return (
            "<section class=\"card history-chart-card\">"
            "<div class=\"history-chart-head\">"
            "<div><h2 class=\"history-chart-title\">最近 24 小时事件</h2>"
            f"<div class=\"history-chart-subtitle\">{escape(str(payload['range']))}</div></div>"
            "<span class=\"pill\">0 events</span>"
            "</div><div class=\"history-chart-empty\">暂无事件</div></section>"
        )
    payload_json = json.dumps(payload, ensure_ascii=False)
    return (
        "<section class=\"card history-chart-card\">"
        "<div class=\"history-chart-head\">"
        "<div><h2 class=\"history-chart-title\">最近 24 小时事件</h2>"
        f"<div class=\"history-chart-subtitle\">{escape(str(payload['range']))}</div></div>"
        f"<span class=\"pill\">{int(payload['total'])} events</span>"
        "</div>"
        "<div id=\"history-event-chart\" class=\"history-chart\" role=\"img\" "
        "aria-label=\"最近 24 小时事件数量堆叠柱状图\"></div>"
        "<script src=\"https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js\"></script>"
        "<script>"
        f"window.historyEventChartData = {payload_json};"
        """
(() => {
  const el = document.getElementById("history-event-chart");
  if (!el || !window.echarts) {
    return;
  }
  const historyEventChartData = window.historyEventChartData;
  const chart = echarts.init(el, null, {renderer: "canvas"});
  chart.setOption({
    animation: false,
    tooltip: {trigger: "axis", axisPointer: {type: "shadow"}},
    legend: {top: 0, left: 0, itemWidth: 10, itemHeight: 10, textStyle: {color: "#5a5a5c"}},
    grid: {left: 34, right: 12, top: 54, bottom: 32},
    xAxis: {
      type: "category",
      data: historyEventChartData.labels,
      axisTick: {show: false},
      axisLabel: {color: "#888888", fontSize: 11, hideOverlap: true}
    },
    yAxis: {
      type: "value",
      minInterval: 1,
      splitLine: {lineStyle: {color: "#ededed"}},
      axisLabel: {color: "#888888", fontSize: 11}
    },
    series: historyEventChartData.series
  });
  window.addEventListener("resize", () => chart.resize());
})();
"""
        "</script></section>"
    )


def _pagination_range(page: int, limit: int | None, total_count: int) -> str:
    if total_count <= 0:
        return "0-0"
    if limit is None or limit <= 0:
        return f"1-{total_count}"
    start = _page_offset(page, limit) + 1
    end = min(start + limit - 1, total_count)
    return f"{start}-{end}"


def _pagination_button(
    *,
    label_html: str,
    aria_label: str,
    href: str | None,
    arrow: bool = False,
) -> str:
    classes = "pagination-button"
    if href is None:
        classes += " is-disabled"
    if arrow:
        classes += " pagination-arrow"
    label = escape(aria_label)
    if href is None:
        return (
            f"<span class=\"{classes}\" aria-label=\"{label}\" title=\"{label}\">"
            f"{label_html}</span>"
        )
    return (
        f"<a class=\"{classes}\" href=\"{escape(href)}\" "
        f"aria-label=\"{label}\" title=\"{label}\">{label_html}</a>"
    )


def _pagination_controls(
    *,
    base_path: str,
    page: int,
    limit: int | None,
    total_count: int,
    bottom: bool = False,
) -> str:
    page_count = _page_count(total_count, limit)
    if page_count <= 1:
        return ""
    page = min(max(1, page), page_count)
    is_first = page <= 1
    is_last = page >= page_count
    first_html = _pagination_button(
        label_html="首页",
        aria_label="第一页",
        href=None if is_first else _page_href(base_path, 1),
    )
    prev_html = _pagination_button(
        label_html="&lsaquo;",
        aria_label="上一页",
        href=None if is_first else _page_href(base_path, page - 1),
        arrow=True,
    )
    next_html = _pagination_button(
        label_html="&rsaquo;",
        aria_label="下一页",
        href=None if is_last else _page_href(base_path, page + 1),
        arrow=True,
    )
    last_html = _pagination_button(
        label_html="末页",
        aria_label="最后一页",
        href=None if is_last else _page_href(base_path, page_count),
    )
    bottom_class = " bottom" if bottom else ""
    return (
        f"<div class=\"pagination{bottom_class}\">"
        "<div class=\"pagination-status\">"
        f"<span class=\"pagination-range\">{_pagination_range(page, limit, total_count)}</span>"
        f"<span class=\"pagination-page\">{page} / {page_count}</span>"
        f"<span class=\"pagination-total\">共 {total_count} 条</span>"
        "</div>"
        f"<nav class=\"pagination-actions\" aria-label=\"分页导航\">"
        f"{first_html}{prev_html}{next_html}{last_html}</nav>"
        "</div>"
    )


def render_attempt_list(
    store: AutoReplyStore,
    limit: int | None = DEFAULT_ATTEMPT_LIST_LIMIT,
    page: int = 1,
) -> str:
    total_count = store.count_reply_attempts()
    page = _bounded_page(page, limit, total_count)
    offset = _page_offset(page, limit)
    items = []
    if page == 1:
        for task in store.list_reply_tasks(
            statuses=("pending", "processing"),
            limit=limit,
        ):
            items.append(_reply_task_item(task))
    attempts = store.list_reply_attempts(limit=limit, offset=offset)
    sent_replies_by_attempt = store.list_sent_replies_for_attempts(attempts)
    feedback_events_by_token = _feedback_events_by_sent_reply(
        store,
        sent_replies_by_attempt.values(),
    )
    for attempt in attempts:
        sent_reply = sent_replies_by_attempt.get(
            (attempt.conversation_id, attempt.trigger_message_id)
        )
        feedback_events = _feedback_events_for_sent_reply(
            sent_reply, feedback_events_by_token
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
            f"{_attempt_action_pills(attempt)}"
            f"<div class=\"attempt-main\">{escape(attempt.conversation_title)}</div>"
            f"<div class=\"attempt-meta\">{escape(attempt.trigger_sender)}</div>"
            "</div>"
            "<div class=\"attempt-side\">"
            f"<time class=\"attempt-time\">{escape(_format_local_time(attempt.created_at))}</time>"
            "<div class=\"attempt-actions\">"
            f"{_review_link(attempt)}"
            "</div>"
            "</div>"
            "</div>"
            "<div class=\"attempt-lines\">"
            f"{_attempt_text_line('问', attempt.trigger_text, 260)}"
            f"{_attempt_text_line('答', _reply_preview_text(attempt), 320)}"
            "</div>"
            f"{_attempt_feedback_summary(feedback_events, sent_reply)}"
            f"{foot_section}"
            "</article>"
        )
    if not items:
        body = (
            f"{_render_history_chart(store)}"
            "<section class=\"card\"><p class=\"muted\">No reply attempts recorded.</p>"
            f"<p class=\"muted\">DB: {escape(str(store.path))}</p></section>"
        )
    else:
        pagination = _pagination_controls(
            base_path="/",
            page=page,
            limit=limit,
            total_count=total_count,
        )
        body = (
            f"{_render_history_chart(store)}"
            f"{pagination}"
            "<section class=\"attempt-feed\">"
            + "".join(items)
            + "</section>"
            f"{_pagination_controls(base_path='/', page=page, limit=limit, total_count=total_count, bottom=True)}"
        )
    return render_page(
        "CEO Agent Audit",
        body,
        auto_refresh=True,
        active_nav="history",
        user_feedback_pending_count=store.count_pending_user_feedback_items(),
    )


def render_tasks_page(store: AutoReplyStore) -> str:
    projects = store.list_work_projects(limit=100)
    draft_count = len(store.list_follow_up_drafts(statuses=("draft",), limit=200))
    rows = []
    for project in projects:
        todos = store.list_work_todos(
            project_id=project.id,
            statuses=("open", "waiting_owner"),
        )
        todo_text = ", ".join(todo.title for todo in todos)
        rows.append(
            "<tr>"
            f"<td><a href=\"/tasks/{project.id}\">{escape(project.title)}</a></td>"
            f"<td><span class=\"pill\">{escape(project.category)}</span></td>"
            f"<td><span class=\"pill\">{escape(project.priority)}</span></td>"
            f"<td><span class=\"pill\">{escape(project.risk_level)}</span></td>"
            f"<td>{escape(project.owner_name)}</td>"
            f"<td>{escape(_excerpt(project.current_state, 90))}</td>"
            f"<td>{escape(_excerpt(project.next_step, 110))}</td>"
            f"<td>{len(todos)}</td>"
            f"<td>{escape(_excerpt(todo_text, 140))}</td>"
            "</tr>"
        )
    table = (
        "<table><thead><tr>"
        "<th>Project</th><th>Category</th><th>Priority</th><th>Risk</th>"
        "<th>Owner</th><th>State</th><th>Next</th><th>Open</th><th>TODOs</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )
    drafts = store.list_follow_up_drafts(statuses=("draft",), limit=50)
    draft_items = "".join(
        "<div class=\"attempt-item\">"
        "<div class=\"attempt-head\">"
        "<div class=\"attempt-title\">"
        f"<div class=\"attempt-main\">{escape(draft.owner_name or 'Owner')}</div>"
        f"<div class=\"attempt-meta\">{escape(draft.target_kind)}</div>"
        "</div>"
        f"<time class=\"attempt-time\">{escape(_format_local_time(draft.scheduled_at))}</time>"
        "</div>"
        "<div class=\"attempt-lines\">"
        f"{_attempt_text_line('问', draft.question_text, 240)}"
        "</div>"
        "</div>"
        for draft in drafts
    )
    body = (
        "<section class=\"card\"><div class=\"card-head\">"
        "<h2>Tasks</h2>"
        f"<span class=\"pill\">Draft follow-ups {draft_count}</span>"
        "</div>"
        f"{table if rows else '<p class=\"muted\">No work projects recorded.</p>'}"
        "</section>"
        "<section class=\"card\"><h2>Pending follow-ups</h2>"
        f"{draft_items or '<p class=\"muted\">No pending follow-ups.</p>'}"
        "</section>"
    )
    return render_page(
        "Tasks",
        body,
        active_nav="tasks",
        user_feedback_pending_count=store.count_pending_user_feedback_items(),
    )


def render_task_project_detail(store: AutoReplyStore, project_id: int) -> tuple[int, str]:
    project = store.get_work_project(project_id)
    if project is None:
        body = (
            "<section class=\"card\"><div class=\"card-head\">"
            "<h2>Project not found</h2>"
            "<a class=\"compact-button\" href=\"/tasks\">Back</a>"
            "</div>"
            f"<p class=\"muted\">No work project exists for id {project_id}.</p>"
            "</section>"
        )
        return (
            404,
            render_page(
                "Task project",
                body,
                active_nav="tasks",
                user_feedback_pending_count=store.count_pending_user_feedback_items(),
            ),
        )

    todos = store.list_work_todos(project_id=project.id)
    updates = store.list_work_updates(project.id, limit=50)
    drafts = store.list_follow_up_drafts(project_id=project.id, limit=100)

    detail_rows = _task_project_detail_rows(project)
    facts = _task_facts_rows(project.facts_json)
    todo_rows = _task_todo_rows(todos)
    update_rows = _task_update_rows(updates)
    draft_rows = _task_follow_up_rows(drafts)

    body = (
        "<section class=\"card\"><div class=\"card-head\">"
        "<div>"
        f"<h2>{escape(project.title)}</h2>"
        "<div class=\"reply-meta\">"
        f"<span class=\"pill\">{escape(project.status)}</span>"
        f"<span class=\"pill\">{escape(project.category)}</span>"
        f"<span class=\"pill\">{escape(project.priority)}</span>"
        f"<span class=\"pill\">risk {escape(project.risk_level)}</span>"
        "</div>"
        "</div>"
        "<a class=\"compact-button\" href=\"/tasks\">Back</a>"
        "</div>"
        "<div class=\"attempt-detail-grid\">"
        f"{_task_detail_cell('Owner', project.owner_name or project.owner_user_id or '-')}"
        f"{_task_detail_cell('Next follow-up', _format_local_time(project.next_follow_up_at) or '-')}"
        f"{_task_detail_cell('Updated', _format_local_time(project.updated_at))}"
        f"{_task_detail_cell('Derek attention', 'yes' if project.needs_derek_attention else 'no')}"
        "</div>"
        "</section>"
        "<section class=\"card\"><h2>Project details</h2>"
        f"{_simple_table(('Field', 'Value'), detail_rows)}"
        "</section>"
        "<section class=\"card\"><h2>TODOs</h2>"
        f"{_simple_table(('ID', 'TODO', 'Owner', 'Status', 'Priority', 'DDL', 'Next follow-up', 'Question', 'Blocker', 'Evidence'), todo_rows) if todo_rows else '<p class=\"muted\">No TODOs recorded.</p>'}"
        "</section>"
        "<section class=\"card\"><h2>Facts</h2>"
        f"{_simple_table(('Description', 'Source', 'Created', 'Updated'), facts) if facts else '<p class=\"muted\">No facts recorded.</p>'}"
        "</section>"
        "<section class=\"card\"><h2>Updates</h2>"
        f"{_simple_table(('Time', 'Source', 'Summary', 'Changes', 'Reason', 'Confidence'), update_rows) if update_rows else '<p class=\"muted\">No updates recorded.</p>'}"
        "</section>"
        "<section class=\"card\"><h2>Follow-ups</h2>"
        f"{_simple_table(('Time', 'Owner', 'Target', 'Status', 'Question', 'Risk', 'Result'), draft_rows) if draft_rows else '<p class=\"muted\">No follow-ups recorded.</p>'}"
        "</section>"
        f"{_collapsible_json_card('Memory context', project.memory_context_json)}"
    )
    return (
        200,
        render_page(
            project.title,
            body,
            active_nav="tasks",
            user_feedback_pending_count=store.count_pending_user_feedback_items(),
        ),
    )


def _task_project_detail_rows(project) -> list[tuple[str, str]]:
    tags = _task_json_compact(project.tags_json, "[]")
    related_people = _task_json_compact(project.related_people_json, "[]")
    source_conversations = _task_json_compact(project.source_conversations_json, "[]")
    return [
        ("Goal", project.goal),
        ("Background", project.background),
        ("Current state", project.current_state),
        ("Blocker", project.blocker),
        ("Next step", project.next_step),
        ("Follow-up mode", str(project.follow_up_mode)),
        ("Tags", tags),
        ("Related people", related_people),
        ("Source conversations", source_conversations),
        ("Created", _format_local_time(project.created_at)),
        ("Last activity", _format_local_time(project.last_activity_at)),
    ]


def _task_facts_rows(facts_json: str) -> list[tuple[str, str, str, str]]:
    rows = []
    for fact in _json_list(facts_json):
        if not isinstance(fact, dict):
            continue
        rows.append(
            (
                str(fact.get("description") or ""),
                str(fact.get("source") or ""),
                str(fact.get("created") or ""),
                str(fact.get("updated") or ""),
            )
        )
    return rows


def _task_todo_rows(todos) -> list[tuple[str, str, str, str, str, str, str, str, str, str]]:
    rows = []
    for todo in todos:
        owner = todo.owner_name or todo.owner_user_id
        rows.append(
            (
                str(todo.id),
                todo.title,
                owner,
                str(todo.status),
                str(todo.priority),
                _format_local_time(todo.deadline_at) or todo.deadline_at,
                _format_local_time(todo.next_follow_up_at) or todo.next_follow_up_at,
                todo.follow_up_question,
                todo.blocker,
                _task_json_compact(todo.completion_evidence_json, "{}"),
            )
        )
    return rows


def _task_update_rows(updates) -> list[tuple[str, str, str, str, str, str]]:
    rows = []
    for update in updates:
        source = f"{update.source_type}:{update.source_ref}".strip(":")
        rows.append(
            (
                _format_local_time(update.created_at),
                source,
                update.summary,
                _task_json_compact(update.changes_json, "{}"),
                update.merge_reason,
                f"{update.confidence:.2f}",
            )
        )
    return rows


def _task_follow_up_rows(drafts) -> list[tuple[str, str, str, str, str, str, str]]:
    rows = []
    for draft in drafts:
        target = (
            f"{draft.target_kind}:{draft.target_conversation_id}"
            if draft.target_conversation_id
            else draft.target_kind
        )
        rows.append(
            (
                _format_local_time(draft.scheduled_at) or draft.scheduled_at,
                draft.owner_name or draft.owner_user_id,
                target,
                str(draft.status),
                draft.question_text,
                _task_json_compact(draft.risk_check_json, "{}"),
                _task_json_compact(draft.send_result_json, "{}"),
            )
        )
    return rows


def _task_detail_cell(label: str, value: str) -> str:
    return (
        "<div class=\"attempt-detail-cell\">"
        f"<div class=\"attempt-detail-label\">{escape(label)}</div>"
        f"<div class=\"attempt-detail-value\">{escape(value)}</div>"
        "</div>"
    )


def _simple_table(headers: Iterable[str], rows: Iterable[Iterable[str]]) -> str:
    header_html = "".join(f"<th>{escape(header)}</th>" for header in headers)
    row_html = "".join(
        "<tr>" + "".join(f"<td>{escape(value)}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return (
        "<table><thead><tr>"
        f"{header_html}"
        "</tr></thead><tbody>"
        f"{row_html}"
        "</tbody></table>"
    )


def _json_list(text: str) -> list:
    try:
        payload = json.loads(text or "[]")
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def _task_json_compact(text: str, default: str) -> str:
    try:
        payload = json.loads(text or default)
    except json.JSONDecodeError:
        return text
    if payload in ({}, []):
        return ""
    return _excerpt(json.dumps(payload, ensure_ascii=False), 260)


def render_user_feedback_list(
    store: AutoReplyStore, limit: int = 50, page: int = 1
) -> str:
    total_count = store.count_user_feedback_items()
    page = _bounded_page(page, limit, total_count)
    offset = _page_offset(page, limit)
    rows = []
    for item in store.list_user_feedback_items(limit=limit, offset=offset):
        status = _user_feedback_status(item)
        attempt_link = (
            f"<a class=\"review-link\" href=\"/attempts/{item.attempt_id}\">处理</a>"
            if item.attempt_id
            else "<span class=\"muted\">未关联</span>"
        )
        resolve_action = _user_feedback_resolve_action(item, status)
        context_lines = [
            value
            for value in (
                item.conversation_title,
                item.trigger_sender,
                _excerpt(item.trigger_text, 140),
            )
            if value
        ]
        context_html = (
            f"<div class=\"user-feedback-context\">{escape(' · '.join(context_lines))}</div>"
            if context_lines
            else ""
        )
        comment = item.comment.strip() or "未填写评语"
        rows.append(
            "<tr>"
            f"<td><span class=\"pill status-{escape(status)}\">{escape(status)}</span></td>"
            f"<td>{escape(_feedback_rating_stars_for_rating(item.rating) or item.rating_label or item.rating)}</td>"
            "<td>"
            f"<div class=\"user-feedback-comment\">{escape(comment)}</div>"
            f"{context_html}"
            "</td>"
            f"<td>{escape(_format_local_time(item.received_at or item.updated_at))}</td>"
            f"<td><div class=\"user-feedback-actions\">{attempt_link}{resolve_action}</div></td>"
            "</tr>"
        )
    if rows:
        pagination = _pagination_controls(
            base_path="/user-feedback",
            page=page,
            limit=limit,
            total_count=total_count,
        )
        body = (
            "<section class=\"card\">"
            f"{_user_feedback_page_head()}"
            f"{pagination}"
            "<table class=\"user-feedback-table\"><thead><tr>"
            "<th>状态</th><th>评分</th><th>用户反馈</th><th>时间</th><th>操作</th>"
            "</tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
            f"{_pagination_controls(base_path='/user-feedback', page=page, limit=limit, total_count=total_count, bottom=True)}"
            "</section>"
        )
    else:
        body = (
            "<section class=\"card\">"
            f"{_user_feedback_page_head()}"
            "<p class=\"muted\">暂无用户反馈。</p></section>"
        )
    return render_page(
        "用户反馈",
        body,
        active_nav="user-feedback",
        user_feedback_pending_count=store.count_pending_user_feedback_items(),
    )


def _user_feedback_page_head() -> str:
    return (
        "<div class=\"card-head\"><h2>用户反馈</h2>"
        "<form method=\"post\" action=\"/user-feedback/sync\">"
        "<button class=\"compact-button\" type=\"submit\">同步最新反馈</button>"
        "</form></div>"
    )


def _user_feedback_status(item: UserFeedbackItem) -> str:
    if (
        item.resolved_at.strip()
        or item.reviewer_feedback.strip()
        or item.corrected_reply_text.strip()
    ):
        return "resolved"
    return "pending"


def _user_feedback_resolve_action(item: UserFeedbackItem, status: str) -> str:
    if status == "resolved":
        return "<span class=\"muted\">已处理</span>"
    return (
        "<form method=\"post\" action=\"/user-feedback/resolve\">"
        f"<input type=\"hidden\" name=\"key\" value=\"{escape(item.key)}\">"
        "<button type=\"submit\">标记 resolved</button>"
        "</form>"
    )


def _reply_task_item(task: ReplyTask) -> str:
    error_html = (
        f"<div class=\"attempt-foot\"><span class=\"attempt-warning\">{escape(task.error)}</span></div>"
        if task.error and task.error != FAST_PATH_UNREAD_BACKOFF_TASK_ERROR
        else ""
    )
    return (
        "<article class=\"attempt-item\">"
        "<div class=\"attempt-head\">"
        "<div class=\"attempt-title\">"
        f"<span class=\"attempt-id\">#task-{task.id}</span>"
        f"<span class=\"pill status-action {_action_state_class(task.status)}\">"
        f"💬 {_display_action_state(task.status)}</span>"
        f"<div class=\"attempt-main\">{escape(task.conversation_title)}</div>"
        f"<div class=\"attempt-meta\">{escape(task.trigger_sender)}</div>"
        "</div>"
        "<div class=\"attempt-side\">"
        f"<time class=\"attempt-time\">{escape(_format_local_time(task.updated_at))}</time>"
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
        if task.error == FAST_PATH_UNREAD_BACKOFF_TASK_ERROR:
            available_at = _format_local_time(task.available_at)
            return f"快路径已触发，等待到 {available_at} 后确认是否仍需处理"
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
    feedback_events = _feedback_events_for_sent_reply(
        sent_reply,
        _feedback_events_by_sent_reply(store, [sent_reply] if sent_reply else []),
    )
    codex_session_id = attempt.codex_session_id or store.get_codex_session_id(
        attempt.conversation_id
    )
    return 200, render_page(
        f"Attempt #{attempt.id}",
        _attempt_detail_body(attempt, sent_reply, codex_session_id, feedback_events),
        active_nav="history",
        user_feedback_pending_count=store.count_pending_user_feedback_items(),
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
    return render_page(
        "Codex Sessions",
        table,
        active_nav="codex",
        user_feedback_pending_count=store.count_pending_user_feedback_items(),
    )


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
                user_feedback_pending_count=(
                    store.count_pending_user_feedback_items() if store else None
                ),
            )
        return 404, render_page(
            "Codex session not found",
            f"<p>Codex session not found: {escape(session_id)}</p>",
            active_nav="codex",
            user_feedback_pending_count=(
                store.count_pending_user_feedback_items() if store else None
            ),
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
    return 200, render_page(
        f"Codex Session {session_id}",
        body,
        active_nav="codex",
        user_feedback_pending_count=(
            store.count_pending_user_feedback_items() if store else None
        ),
    )


def render_error_list(
    store: AutoReplyStore,
    limit: int | None = DEFAULT_ERROR_LIST_LIMIT,
    page: int = 1,
) -> str:
    total_count = store.count_errors()
    page = _bounded_page(page, limit, total_count)
    offset = _page_offset(page, limit)
    rows = []
    for error in store.list_errors(limit=limit, offset=offset):
        resolution = _error_resolution_label(store, error)
        status_class = (
            "status-resolved"
            if resolution.startswith("resolved")
            else "status-active"
        )
        rows.append(
            "<tr>"
            f"<td>#{error.id}</td>"
            f"<td>{escape(_format_local_time(error.created_at))}</td>"
            f"<td>{escape(error.conversation_id or '')}</td>"
            f"<td>{escape(error.message_id or '')}</td>"
            f"<td><span class=\"pill status-failed\">{escape(error.kind)}</span></td>"
            f"<td><span class=\"pill {status_class}\">{escape(resolution)}</span></td>"
            f"<td>{escape(error.detail)}</td>"
            "</tr>"
        )
    pagination = _pagination_controls(
        base_path="/errors",
        page=page,
        limit=limit,
        total_count=total_count,
    )
    table = (
        f"{pagination}"
        "<table><thead><tr><th>ID</th><th>Time</th><th>Conversation</th>"
        "<th>Message</th><th>Kind</th><th>Status</th><th>Detail</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
        f"{_pagination_controls(base_path='/errors', page=page, limit=limit, total_count=total_count, bottom=True)}"
    )
    return render_page(
        "Errors",
        table,
        active_nav="errors",
        user_feedback_pending_count=store.count_pending_user_feedback_items(),
    )


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
            expression="app.prompt:work_profile_instruction()",
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


def handle_user_feedback_resolve_post(
    store: AutoReplyStore, body: bytes
) -> tuple[int, dict[str, str], str]:
    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    key = parsed.get("key", [""])[0]
    if not store.resolve_feedback_event(key):
        return 404, {}, render_page("Feedback not found", "Feedback not found")
    return 303, {"Location": "/user-feedback"}, ""


def handle_user_feedback_sync_post(
    store: AutoReplyStore,
) -> tuple[int, dict[str, str], str]:
    _sync_feedback_events_for_sent_replies(
        store,
        store.list_sent_replies_with_feedback_tokens(),
    )
    return 303, {"Location": "/user-feedback"}, ""


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
    def attempt_list(request: Request) -> str:
        return render_attempt_list(
            AutoReplyStore(db_path),
            page=_positive_int_query(request, "page", default=1),
        )

    @app.get("/user-feedback", response_class=HTMLResponse)
    def user_feedback_list(request: Request) -> str:
        return render_user_feedback_list(
            AutoReplyStore(db_path),
            page=_positive_int_query(request, "page", default=1),
        )

    @app.get("/tasks", response_class=HTMLResponse)
    def tasks_page() -> str:
        return render_tasks_page(AutoReplyStore(db_path))

    @app.get("/tasks/{project_id}", response_class=HTMLResponse)
    def task_project_detail(project_id: int) -> HTMLResponse:
        status, html = render_task_project_detail(AutoReplyStore(db_path), project_id)
        return HTMLResponse(html, status_code=status)

    @app.get("/errors", response_class=HTMLResponse)
    def error_list(request: Request) -> str:
        return render_error_list(
            AutoReplyStore(db_path),
            page=_positive_int_query(request, "page", default=1),
        )

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
            db_path=db_path,
        )

    @app.get("/notifications", response_class=HTMLResponse)
    def browser_notifications() -> str:
        return render_browser_notifications_page()

    @app.get("/notification-service-worker.js")
    def notification_service_worker() -> Response:
        return Response(
            _notification_service_worker_script(),
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/notifications/events")
    def browser_notification_events() -> StreamingResponse:
        return _browser_notification_event_stream()

    @app.post("/browser-notifications")
    async def browser_notification_post(request: Request) -> JSONResponse:
        payload = await request.json()
        event = _browser_notification_event(
            title=str(payload.get("title") or "CEO Agent"),
            message=str(payload.get("message") or ""),
            url=str(payload.get("url") or ""),
        )
        delivered = _publish_browser_notification(event)
        return JSONResponse(
            {
                "ok": True,
                "delivered": delivered,
                "subscribers": len(_BROWSER_NOTIFICATION_SUBSCRIBERS),
                "dingtalk_url": _dingtalk_url_from_bridge_url(event["url"]),
            }
        )

    @app.get("/dingtalk/open-chat-bridge", response_class=HTMLResponse)
    def dingtalk_open_chat_bridge(conversation_id: str) -> HTMLResponse:
        cleaned_conversation_id = conversation_id.strip()
        if not cleaned_conversation_id:
            return HTMLResponse("missing conversation_id", status_code=400)
        return HTMLResponse(render_dingtalk_open_chat_bridge(cleaned_conversation_id))

    @app.post("/dingtalk/bridge-status")
    async def dingtalk_bridge_status(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        _DINGTALK_BRIDGE_STATUS.append(
            {
                "conversation_id": str(payload.get("conversation_id") or ""),
                "stage": str(payload.get("stage") or ""),
                "detail": str(payload.get("detail") or ""),
            }
        )
        return JSONResponse({"ok": True})

    @app.get("/dingtalk/bridge-status")
    def dingtalk_bridge_status_list() -> JSONResponse:
        return JSONResponse({"events": list(_DINGTALK_BRIDGE_STATUS)})

    @app.get("/open-dingtalk")
    def open_dingtalk(request: Request, cid: str = "", conversation_id: str = "") -> JSONResponse:
        cleaned_conversation_id = conversation_id.strip()
        if cleaned_conversation_id:
            bridge_url = (
                f"{request.url.scheme}://{request.url.netloc}"
                "/dingtalk/open-chat-bridge"
                f"?conversation_id={quote(cleaned_conversation_id, safe='')}"
            )
            dingtalk_url = _dingtalk_pc_slide_link_url(bridge_url)
            completed = subprocess.run(["/usr/bin/open", dingtalk_url], check=False)
            return JSONResponse(
                {
                    "ok": completed.returncode == 0,
                    "dingtalk_url": dingtalk_url,
                    "bridge_url": bridge_url,
                    "open_returncode": completed.returncode,
                }
            )
        cleaned_cid = cid.strip()
        if not cleaned_cid:
            return JSONResponse(
                {"ok": False, "error": "missing_cid"},
                status_code=400,
            )
        dingtalk_url = _dingtalk_conversation_url(cleaned_cid)
        completed = subprocess.run(["/usr/bin/open", dingtalk_url], check=False)
        return JSONResponse(
            {
                "ok": completed.returncode == 0,
                "dingtalk_url": dingtalk_url,
                "open_returncode": completed.returncode,
            }
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

    @app.post("/user-feedback/resolve")
    async def user_feedback_resolve(request: Request):
        status, headers, html = handle_user_feedback_resolve_post(
            AutoReplyStore(db_path),
            await request.body(),
        )
        return _fastapi_post_response(status, headers, html)

    @app.post("/user-feedback/sync")
    def user_feedback_sync():
        status, headers, html = handle_user_feedback_sync_post(AutoReplyStore(db_path))
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
            "app.audit_web:create_default_audit_app",
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


def _positive_int_query(request: Request, name: str, *, default: int) -> int:
    raw_value = request.query_params.get(name, "")
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return value if value > 0 else default


def _attempt_detail_body(
    attempt: ReplyAttempt,
    sent_reply: SentReply | None,
    codex_session_id: str | None,
    feedback_events: list[FeedbackEvent],
) -> str:
    fields = [
        ("trigger message id", attempt.trigger_message_id),
        ("action", attempt.action),
        ("sensitivity", attempt.sensitivity_kind),
        ("permission", _permission_display(attempt)),
        ("send status", attempt.send_status),
        ("send error", attempt.send_error),
        ("retry count", str(attempt.retry_count)),
        ("created", _format_local_time(attempt.created_at)),
        ("updated", _format_local_time(attempt.updated_at)),
        ("reviewed", _format_local_time(attempt.reviewed_at or "")),
    ]
    return (
        f"{_attempt_conversation_banner(attempt, codex_session_id)}"
        f"{_attempt_detail_grid(fields)}"
        f"{_review_panel(attempt, sent_reply, feedback_events)}"
        f"{_quality_warning_card(attempt)}"
        f"{_context_only_info_card(attempt)}"
        f"{_oa_metadata_card(attempt)}"
        f"{_calendar_metadata_card(attempt)}"
        f"{_text_card('Audit summary', attempt.audit_summary)}"
        f"{_collapsible_json_card('Audit documents', attempt.audit_documents_json)}"
        f"{_audit_tool_events_card(attempt)}"
        f"{_text_card('Draft reply (raw Codex reply)', attempt.draft_reply_text)}"
    )


def _permission_display(attempt: ReplyAttempt) -> str:
    action = attempt.permission_action.strip()
    reason = attempt.permission_reason.strip()
    if action and reason:
        return f"{action} · {reason}"
    return action or reason


def _attempt_conversation_banner(
    attempt: ReplyAttempt, codex_session_id: str | None
) -> str:
    subtitle = (
        f"<div class=\"attempt-conversation-sub\">触发人：{escape(attempt.trigger_sender)}</div>"
        if attempt.trigger_sender.strip()
        else ""
    )
    agent_log = (
        f"<a class=\"agent-log-button\" href=\"/codex/{escape(codex_session_id)}\">"
        "agent 执行记录</a>"
        if codex_session_id
        else "<span class=\"muted\">No agent execution record</span>"
    )
    return (
        "<section class=\"card compact-card attempt-conversation-banner\">"
        "<div class=\"attempt-conversation-left\">"
        "<div class=\"attempt-conversation-label\">群名</div>"
        "<div class=\"attempt-conversation-main\">"
        f"<div class=\"attempt-conversation-title\">{escape(attempt.conversation_title)}</div>"
        f"{subtitle}"
        "</div>"
        "</div>"
        f"{agent_log}"
        "</section>"
    )


def _attempt_detail_grid(fields: list[tuple[str, str]]) -> str:
    cells = "".join(
        "<div class=\"attempt-detail-cell\">"
        f"<div class=\"attempt-detail-label\">{escape(label)}</div>"
        f"<div class=\"attempt-detail-value\">{escape(value)}</div>"
        "</div>"
        for label, value in fields
    )
    return (
        "<section class=\"card compact-card\">"
        f"<div class=\"attempt-detail-grid\">{cells}</div>"
        "</section>"
    )


def _sync_feedback_events_for_sent_replies(
    store: AutoReplyStore,
    sent_replies: Iterable[SentReply],
) -> None:
    contexts = {
        context.feedback_token: context
        for sent_reply in sent_replies
        if (context := _feedback_context_for_sent_reply(sent_reply)) is not None
    }
    existing_events = store.list_feedback_events_for_tokens(list(contexts))
    for context in contexts.values():
        if existing_events.get(context.feedback_token):
            continue
        _sync_feedback_events_for_context(store, context)


def _sync_feedback_events_for_context(
    store: AutoReplyStore,
    context: FeedbackLinkContext,
) -> None:
    url = (
        f"{context.vercel_base_url}/api/dingtalk-feedback-spike-events"
        f"?feedback_token={quote(context.feedback_token)}&limit=20"
    )
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (
        OSError,
        TimeoutError,
        urllib.error.URLError,
        urllib.error.HTTPError,
        json.JSONDecodeError,
    ):
        return
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        return
    for event in events:
        if not isinstance(event, dict):
            continue
        token = str(event.get("feedback_token") or "").strip()
        if token != context.feedback_token:
            continue
        key = str(event.get("key") or "").strip()
        if not key:
            key = f"{token}:{event.get('received_at') or ''}:{event.get('rating') or ''}"
        store.upsert_feedback_event(
            key=key,
            feedback_token=token,
            rating=str(event.get("rating") or ""),
            rating_label=str(event.get("rating_label") or ""),
            comment=str(event.get("comment") or ""),
            original_text=str(event.get("original_text") or ""),
            reply_text=str(event.get("reply_text") or ""),
            source=str(event.get("source") or ""),
            received_at=str(event.get("received_at") or ""),
            raw_json=json.dumps(event, ensure_ascii=False),
        )


def _feedback_context_for_sent_reply(
    sent_reply: SentReply,
) -> FeedbackLinkContext | None:
    context = extract_feedback_link_context(sent_reply.reply_text)
    if context is not None:
        return context
    token = sent_reply.feedback_token.strip()
    base_url = os.getenv("CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL", "").strip().rstrip("/")
    if token and (
        base_url.startswith("https://") or base_url.startswith("http://")
    ):
        return FeedbackLinkContext(feedback_token=token, vercel_base_url=base_url)
    return None


def _feedback_token_for_sent_reply(sent_reply: SentReply | None) -> str:
    if sent_reply is None:
        return ""
    if sent_reply.feedback_token.strip():
        return sent_reply.feedback_token.strip()
    context = extract_feedback_link_context(sent_reply.reply_text)
    return context.feedback_token if context else ""


def _feedback_events_by_sent_reply(
    store: AutoReplyStore,
    sent_replies: Iterable[SentReply],
) -> dict[str, list[FeedbackEvent]]:
    tokens = [_feedback_token_for_sent_reply(sent_reply) for sent_reply in sent_replies]
    return store.list_feedback_events_for_tokens(tokens)


def _feedback_events_for_sent_reply(
    sent_reply: SentReply | None,
    feedback_events_by_token: dict[str, list[FeedbackEvent]],
) -> list[FeedbackEvent]:
    token = _feedback_token_for_sent_reply(sent_reply)
    if not token:
        return []
    return feedback_events_by_token.get(token, [])


def _attempt_feedback_summary(
    feedback_events: list[FeedbackEvent],
    sent_reply: SentReply | None,
) -> str:
    if feedback_events:
        latest = feedback_events[0]
        label = _feedback_rating_stars(latest) or latest.rating_label or latest.rating
        comment = f" | {_excerpt(latest.comment, 90)}" if latest.comment.strip() else ""
        return (
            "<div class=\"attempt-foot\">"
            f"<span class=\"feedback-chip\">反馈：{escape(label)}{escape(comment)}</span>"
            "</div>"
        )
    return ""


def _feedback_rating_stars(event: FeedbackEvent) -> str:
    return _feedback_rating_stars_for_rating(event.rating)


def _feedback_rating_stars_for_rating(rating: str) -> str:
    star_counts = {
        "very_unhelpful": 1,
        "not_useful": 2,
        "neutral": 3,
        "useful": 4,
        "very_useful": 5,
    }
    count = star_counts.get(rating)
    return "☆" * count if count else ""


def _counterparty_feedback_card(
    sent_reply: SentReply | None,
    feedback_events: list[FeedbackEvent],
) -> str:
    token = _feedback_token_for_sent_reply(sent_reply)
    if not token and not feedback_events:
        return ""
    if not feedback_events:
        return (
            "<section class=\"card feedback-card\"><h2>对方反馈</h2>"
            "<p class=\"muted\">还没有收到对方反馈。</p>"
            f"<p class=\"feedback-token\">token: {escape(token)}</p></section>"
        )
    events_html = "".join(_feedback_event_html(event) for event in feedback_events)
    return (
        "<section class=\"card feedback-card\"><h2>对方反馈</h2>"
        f"<p class=\"feedback-token\">token: {escape(token)}</p>"
        f"{events_html}</section>"
    )


def _feedback_event_html(event: FeedbackEvent) -> str:
    rating = event.rating_label or event.rating or "feedback"
    comment = event.comment.strip() or "未填写评语"
    return (
        "<article class=\"feedback-event\">"
        "<div class=\"feedback-event-head\">"
        f"<span class=\"feedback-rating\">{escape(rating)}</span>"
        f"<time class=\"attempt-time\">{escape(_format_local_time(event.received_at or event.updated_at))}</time>"
        "</div>"
        f"<div class=\"feedback-comment\">{escape(comment)}</div>"
        f"<p class=\"muted\">source: {escape(event.source)}</p>"
        "</article>"
    )


def _review_panel(
    attempt: ReplyAttempt,
    sent_reply: SentReply | None,
    feedback_events: list[FeedbackEvent],
) -> str:
    reply_text = attempt.final_reply_text or attempt.draft_reply_text
    if not reply_text.strip():
        reply_text = "No generated reply recorded."
    return (
        "<section class=\"review-grid\">"
        "<div class=\"card\">"
        "<div class=\"reply-meta\">"
        f"{_attempt_action_pills(attempt)}"
        "</div>"
        "<h2>Trigger</h2>"
        f"<pre class=\"trigger-pre\">{escape(_trigger_text(attempt))}</pre>"
        "<h2>Codex reason</h2>"
        f"<div class=\"codex-reason\">{escape(attempt.codex_reason)}</div>"
        "<h2>生成回复</h2>"
        f"<pre class=\"reply-pre\">{escape(reply_text)}</pre>"
        "</div>"
        "<div class=\"review-side\">"
        f"{_feedback_form(attempt)}"
        f"{_counterparty_feedback_card(sent_reply, feedback_events)}"
        "</div>"
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


def _calendar_metadata_card(attempt: ReplyAttempt) -> str:
    if not any(
        value.strip()
        for value in (
            attempt.calendar_event_id,
            attempt.calendar_response_status,
            attempt.calendar_response_result_json,
        )
    ):
        return ""
    rows = "".join(
        f"<div class=\"muted\">{escape(label)}</div><div>{escape(value)}</div>"
        for label, value in (
            ("event id", attempt.calendar_event_id),
            ("response", attempt.calendar_response_status),
        )
    )
    return (
        "<section class=\"card compact-card\"><h2>Calendar response</h2>"
        f"<div class=\"grid\">{rows}</div></section>"
        + (
            _json_card(
                "Calendar response result",
                attempt.calendar_response_result_json,
            )
            if attempt.calendar_response_result_json.strip()
            else ""
        )
    )


def _attempt_action_pills(attempt: ReplyAttempt) -> str:
    calendar_only = (
        attempt.send_status.strip().lower() == "calendar"
        and attempt.calendar_response_status.strip()
    )
    actions = (
        []
        if calendar_only
        else [(f"💬 {_display_action_state(attempt.send_status)}", attempt.send_status)]
    )
    if attempt.oa_action.strip():
        actions.append((f"🧾 {attempt.oa_action.strip()}", attempt.oa_action))
    if attempt.calendar_response_status.strip():
        actions.append(
            (
                f"📆 {_display_action_state(attempt.calendar_response_status)}",
                attempt.calendar_response_status,
            )
        )
    return "".join(
        f"<span class=\"pill status-action {_action_state_class(state)}\">"
        f"{escape(label)}</span>"
        for label, state in actions
    )


def _attempt_action_label_text(attempt: ReplyAttempt) -> str:
    calendar_only = (
        attempt.send_status.strip().lower() == "calendar"
        and attempt.calendar_response_status.strip()
    )
    return " · ".join(
        label
        for label in (
            (
                ""
                if calendar_only
                else f"💬 {_display_action_state(attempt.send_status)}"
            ),
            (
                f"🧾 {attempt.oa_action.strip()}"
                if attempt.oa_action.strip()
                else ""
            ),
            (
                f"📆 {_display_action_state(attempt.calendar_response_status)}"
                if attempt.calendar_response_status.strip()
                else ""
            ),
        )
        if label
    )


def _display_action_state(value: str) -> str:
    return " ".join(
        part.capitalize()
        for part in value.replace("-", "_").split("_")
        if part
    )


def _action_state_class(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    mapped = {
        "通过": "approved",
        "同意": "approved",
        "拒绝": "rejected",
        "退回": "returned",
        "评论": "commented",
        "留言": "commented",
    }.get(value.strip(), normalized)
    if mapped in {"approve", "approved", "pass"}:
        mapped = "approved"
    elif mapped in {"accept", "accepted"}:
        mapped = "accepted"
    elif mapped in {"decline", "declined"}:
        mapped = "declined"
    elif mapped in {"reject", "rejected"}:
        mapped = "rejected"
    elif mapped in {"return", "returned"}:
        mapped = "returned"
    elif mapped in {"dry-run", "dryrun"}:
        mapped = "dry-run"
    safe = "".join(
        char if (char.isascii() and char.isalnum()) or char == "-" else "-"
        for char in mapped
    )
    safe = "-".join(part for part in safe.split("-") if part)
    return f"action-state-{safe or 'unknown'}"


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
            f"<td>{escape(_format_local_time(attempt.created_at))}</td>"
            f"<td>{escape(attempt.trigger_sender)}</td>"
            f"<td>{_attempt_action_pills(attempt)}</td>"
            f"<td>{escape(_excerpt(attempt.trigger_text, 120))}</td>"
            "</tr>"
        )
    return (
        "<section class=\"card\"><h2>Related history</h2>"
        "<table><thead><tr><th>Attempt</th><th>Time</th><th>Sender</th>"
        "<th>Actions</th><th>Trigger</th></tr></thead><tbody>"
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


def _feedback_form(attempt: ReplyAttempt) -> str:
    return (
        f"<section class=\"card\" id=\"feedback\"><h2>内部反馈/建议修改</h2>"
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
        f"#{attempt.id} · {escape(_attempt_action_label_text(attempt))}</a>"
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


def _audit_tool_events_card(attempt: ReplyAttempt) -> str:
    events = _audit_tool_events_for_attempt(attempt)
    if not events:
        return _collapsible_json_card("Audit tool events", attempt.audit_tool_events_json)
    return (
        "<details class=\"card collapsible-card\">"
        "<summary><h2>Audit tool events</h2></summary>"
        f"<div class=\"audit-tool-list\">{_audit_tool_events_html(events)}</div>"
        "</details>"
    )


def _audit_tool_events_for_attempt(attempt: ReplyAttempt) -> list[dict[str, str]]:
    if attempt.codex_session_id.strip():
        session_events = extract_codex_audit_events_from_session(
            attempt.codex_session_id.strip(),
            start_line=attempt.codex_transcript_start_line,
            end_line=(
                attempt.codex_transcript_end_line
                if attempt.codex_transcript_end_line > 0
                else None
            ),
        )
        if session_events:
            return session_events
    try:
        payload = json.loads(attempt.audit_tool_events_json or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [event for event in payload if isinstance(event, dict)]


def _audit_tool_events_html(events: list[dict[str, str]]) -> str:
    calls: list[dict[str, object]] = []
    by_call_id: dict[str, dict[str, object]] = {}
    for event in events:
        tool = str(event.get("tool") or "tool").strip() or "tool"
        call_id = str(event.get("call_id") or "").strip()
        if tool == "tool_output":
            target = by_call_id.get(call_id) if call_id else None
            if target is not None:
                target["output"] = str(event.get("output") or "")
                target["output_event"] = event
                continue
            calls.append(
                {
                    "tool": "tool_output",
                    "call_id": call_id,
                    "input": "",
                    "output": str(event.get("output") or ""),
                    "command": str(event.get("command") or ""),
                    "event": event,
                }
            )
            continue
        call = {
            "tool": tool,
            "call_id": call_id,
            "input": _audit_tool_input_text(event),
            "output": "",
            "command": str(event.get("command") or ""),
            "event": event,
        }
        calls.append(call)
        if call_id:
            by_call_id[call_id] = call
    return "".join(_audit_tool_call_html(index, call) for index, call in enumerate(calls, 1))


def _audit_tool_input_text(event: dict[str, str]) -> str:
    value = str(event.get("input") or "").strip()
    if value:
        return value
    command = str(event.get("command") or "").strip()
    path = str(event.get("path") or "").strip()
    fallback = {key: val for key, val in {"command": command, "path": path}.items() if val}
    if fallback:
        return json.dumps(fallback, ensure_ascii=False, indent=2)
    return json.dumps(event, ensure_ascii=False, indent=2)


def _audit_tool_call_html(index: int, call: dict[str, object]) -> str:
    tool = str(call.get("tool") or "tool")
    command = str(call.get("command") or "").strip()
    call_id = str(call.get("call_id") or "").strip()
    input_text = str(call.get("input") or "").strip()
    output_text = str(call.get("output") or "").strip()
    command_line = (
        f"<div class=\"audit-tool-command\">{escape(command)}</div>"
        if command and command != call_id
        else ""
    )
    call_id_line = (
        f"<span class=\"pill\">{escape(call_id)}</span>" if call_id else ""
    )
    return (
        "<div class=\"audit-tool-event\">"
        "<div class=\"audit-tool-head\">"
        "<div class=\"audit-tool-title\">"
        f"<span class=\"audit-tool-index\">#{index}</span>"
        f"<span>to {escape(tool)}</span>"
        f"{call_id_line}"
        "</div>"
        f"{command_line}"
        "</div>"
        "<div class=\"audit-tool-io\">"
        f"{_audit_tool_section_html('input / command args', input_text)}"
        f"{_audit_tool_section_html('output', output_text)}"
        "</div>"
        "</div>"
    )


def _audit_tool_section_html(label: str, text: str) -> str:
    if not text.strip():
        return ""
    return (
        "<div class=\"audit-tool-section\">"
        f"<div class=\"audit-tool-label\">{escape(label)}</div>"
        f"<pre class=\"audit-tool-pre\">{escape(text)}</pre>"
        "</div>"
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
