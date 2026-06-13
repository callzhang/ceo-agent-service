import json
import asyncio
from collections.abc import Callable, Iterable, Mapping
from collections import deque
from datetime import datetime, timedelta, timezone, tzinfo
from html import escape
from itertools import count, zip_longest
import os
from pathlib import Path
import subprocess
from typing import TypedDict
import urllib.error
import urllib.request
from urllib.parse import parse_qs, quote, urlencode, urlparse

import uvicorn
from fastapi import FastAPI, HTTPException, Request
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
    OperationLog,
    ReplyAttempt,
    ReplyError,
    ReplyTask,
    SentReply,
    UserFeedbackItem,
)
from app.setup_wizard import (
    build_wizard_status,
    check_setup_step,
    get_action_definition,
    get_step_definition,
    run_setup_action,
)
from app.setup_wizard_models import SetupStepStatus, SetupWizardEvent
from app.task_models import ProjectPriority, ProjectStatus, RiskLevel, TodoStatus
from app.user_prompt_blocks import USER_PROMPT_BLOCKS, UserPromptBlock

DISPLAY_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
from app.worker import DingTalkAutoReplyWorker


CSS = """
:root{--ink:#0a0a0a;--charcoal:#1c1c1e;--slate:#3a3a3c;--steel:#5a5a5c;--stone:#888888;--muted:#a8a8aa;--canvas:#ffffff;--surface:#f7f7f7;--surface-soft:#fafafa;--surface-code:#1c1c1e;--hairline:#e5e5e5;--hairline-soft:#ededed;--mint:#00d4a4;--mint-deep:#00b48a;--tag:#3772cf;--error:#d45656}
*{box-sizing:border-box}
body{font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;background:var(--canvas);color:var(--ink);font-size:14px;line-height:1.5}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
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
.column-sized-table{table-layout:fixed}
.column-sized-table th,.column-sized-table td{overflow-wrap:anywhere;word-break:break-word}
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
.tutorial-intro{display:grid;gap:12px;margin:0 0 14px}
.tutorial-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:0}
.tutorial-summary-item{border:1px solid var(--hairline);border-radius:8px;background:var(--surface-soft);padding:12px}
.tutorial-summary-label{display:block;margin-bottom:4px;color:var(--steel);font-size:11px;font-weight:800;line-height:1.35;text-transform:uppercase;letter-spacing:.03em}
.tutorial-summary-value{color:var(--ink);font-size:14px;font-weight:750;line-height:1.35}
.tutorial-steps{display:grid;gap:12px;margin:0;padding:0;list-style:none;counter-reset:tutorial-step}
.tutorial-step{display:grid;grid-template-columns:42px minmax(0,1fr);gap:14px;border:1px solid var(--hairline);border-radius:8px;background:var(--canvas);padding:14px;counter-increment:tutorial-step}
.tutorial-step-number{display:inline-flex;align-items:center;justify-content:center;width:32px;height:32px;border:1px solid rgba(0,180,138,.28);border-radius:8px;background:#ddfff6;color:#005b49;font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;font-size:13px;font-weight:900;line-height:1}
.tutorial-step-number::before{content:counter(tutorial-step)}
.tutorial-step-body{display:grid;gap:8px;min-width:0}
.tutorial-step-head{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;flex-wrap:wrap}
.tutorial-step h3{margin:0;color:var(--ink);font-size:16px;font-weight:750;line-height:1.35}
.tutorial-phase{display:inline-flex;align-items:center;height:24px;padding:0 8px;border:1px solid var(--hairline);border-radius:999px;background:var(--surface-soft);color:var(--steel);font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;font-size:11px;font-weight:800;line-height:1;white-space:nowrap}
.tutorial-step p{margin:0;color:var(--charcoal);font-size:14px;line-height:1.5}
.tutorial-lists{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:10px}
.tutorial-list{margin:0;padding:10px 12px 10px 28px;border:1px solid var(--hairline-soft);border-radius:8px;background:var(--surface-soft);color:var(--charcoal)}
.tutorial-list li{margin:3px 0;font-size:13px;line-height:1.45}
.tutorial-command-list{display:grid;gap:6px;margin:0}
.tutorial-command-list code{display:block;padding:8px 10px;border:1px solid var(--hairline-soft);border-radius:7px;background:var(--surface-code);color:#f7f7f7;font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;font-size:12px;line-height:1.45;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word}
.tutorial-links{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.tutorial-link{display:inline-flex;align-items:center;height:28px;padding:0 10px;border:1px solid var(--hairline);border-radius:999px;background:var(--canvas);color:var(--ink);font-size:12px;font-weight:700;line-height:1;white-space:nowrap}
.tutorial-link:hover{border-color:var(--ink);background:var(--surface-soft);text-decoration:none}
.setup-step-status{display:inline-flex;align-items:center;height:24px;padding:0 8px;border:1px solid var(--hairline);border-radius:999px;background:var(--surface-soft);font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;font-size:11px;font-weight:800;line-height:1;white-space:nowrap}
.setup-status-done{background:#ddfff6;border-color:rgba(0,180,138,.46);color:#005b49}
.setup-status-running,.setup-status-checking{background:rgba(55,114,207,.10);border-color:rgba(55,114,207,.24);color:#245aa5}
.setup-status-needs_action{background:rgba(195,125,13,.12);border-color:rgba(195,125,13,.24);color:#8a5a08}
.setup-status-failed,.setup-status-blocked{background:rgba(212,86,86,.12);border-color:rgba(212,86,86,.24);color:#9a2f2f}
.setup-wizard-step form{margin:0}
@media (max-width:900px){.tutorial-summary,.tutorial-lists{grid-template-columns:1fr}.tutorial-step{grid-template-columns:1fr}.tutorial-step-number{width:30px;height:30px}}
.notification-panel{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:12px 0}
.notification-log{max-height:260px}
.card-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px;flex-wrap:wrap}
.card-head h2{margin:0}
.tasks-page{margin:16px 0}
.tasks-toolbar{display:flex;align-items:center;justify-content:space-between;gap:18px;margin:0 0 12px;flex-wrap:wrap}
.tasks-toolbar-left,.tasks-toolbar-right{display:flex;align-items:center;gap:10px;min-width:0;flex-wrap:wrap}
.tasks-count{color:var(--ink);font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;font-size:14px;font-weight:800;line-height:1.3;white-space:nowrap}
.tasks-search{position:relative;display:flex;align-items:center;width:min(360px,calc(100vw - 48px))}
.tasks-search input[type="text"]{height:34px;padding:7px 34px 7px 12px;border-radius:999px;font-size:13px;line-height:1.3}
.tasks-search-clear{position:absolute;right:8px;display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:999px;color:var(--steel);font-size:16px;font-weight:700;line-height:1}
.tasks-search-clear:hover{background:var(--surface-soft);color:var(--ink);text-decoration:none}
.tasks-pages{display:flex;align-items:center;gap:4px;flex-wrap:nowrap}
.tasks-page-link{display:inline-flex;align-items:center;justify-content:center;height:28px;min-width:28px;padding:0 8px;border:1px solid transparent;border-radius:999px;color:var(--steel);font-size:12px;font-weight:700;line-height:1;white-space:nowrap}
.tasks-page-link:hover{border-color:var(--hairline);background:var(--surface-soft);color:var(--ink);text-decoration:none}
.tasks-page-link.active{border-color:rgba(0,180,138,.28);background:#ddfff6;color:#005b49}
.tasks-page-link.disabled{color:var(--muted);background:transparent;cursor:default}
.tasks-page-size{height:30px;border:1px solid var(--hairline);border-radius:999px;background:var(--canvas);color:var(--ink);padding:0 10px;font-size:12px;font-weight:700}
.todo-checklist{display:grid;gap:4px;margin:0;padding:0;list-style:none}
.todo-checklist li{display:flex;align-items:flex-start;gap:7px;min-width:0;color:var(--charcoal);font-size:13px;line-height:1.35}
.todo-check{display:inline-flex;align-items:center;justify-content:center;flex:0 0 auto;width:15px;height:15px;margin-top:1px;border:1px solid var(--hairline);border-radius:4px;color:transparent;font-size:11px;font-weight:900;line-height:1}
.todo-check.done{border-color:rgba(0,180,138,.46);background:#ddfff6;color:#005b49}
.todo-copy{display:grid;gap:2px;min-width:0}
.todo-due{color:var(--steel);font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;font-size:11px;line-height:1.3}
.todo-total{color:var(--steel);font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;font-size:11px;font-weight:700;line-height:1.3}
.todo-detail-list{display:grid;gap:0}
.todo-detail-item{display:grid;gap:10px;padding:12px 0;border-bottom:1px solid var(--hairline-soft)}
.todo-detail-item:first-child{padding-top:0}
.todo-detail-item:last-child{border-bottom:0;padding-bottom:0}
.todo-detail-main{display:grid;grid-template-columns:18px minmax(0,1fr);gap:10px;align-items:start}
.todo-detail-check{display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;margin-top:3px;border:1px solid var(--hairline);border-radius:4px;background:var(--canvas);color:transparent;font-size:11px;font-weight:900;line-height:1}
.todo-detail-check.done{border-color:rgba(0,180,138,.46);background:#ddfff6;color:#005b49}
.todo-detail-body{display:grid;gap:7px;min-width:0}
.todo-detail-title-row{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;min-width:0}
.todo-detail-title{min-width:0;margin:0;color:var(--ink);font-size:15px;font-weight:760;line-height:1.4;overflow-wrap:anywhere;word-break:break-word}
.todo-detail-meta{display:flex;flex-wrap:wrap;gap:5px 10px;color:var(--steel);font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;font-size:11px;font-weight:700;line-height:1.35}
.todo-detail-fields{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-top:2px}
.todo-detail-field{min-width:0}
.todo-detail-label{margin-bottom:2px;color:var(--steel);font-size:11px;font-weight:800;line-height:1.25;text-transform:uppercase}
.todo-detail-value{color:var(--charcoal);font-size:13px;line-height:1.45;overflow-wrap:anywhere;word-break:break-word}
.detail-pill-list{display:flex;align-items:flex-start;gap:6px;flex-wrap:wrap;min-width:0}
.detail-pill{display:inline-flex;align-items:center;min-height:24px;padding:3px 9px;border:1px solid var(--hairline);border-radius:999px;background:var(--surface-soft);color:var(--charcoal);font-size:12px;font-weight:800;line-height:1.25;overflow-wrap:anywhere;word-break:break-word}
.todo-detail-followups{display:grid;gap:8px;margin-left:28px;padding:10px 0 0 12px;border-left:2px solid rgba(55,114,207,.24);color:var(--charcoal)}
.todo-followup-heading{color:var(--steel);font-size:12px;font-weight:800;line-height:1.25}
.todo-followup-list{display:grid;gap:8px;margin:0;padding:0;list-style:none}
.todo-followup-item{display:flex;min-width:0}
.todo-followup-bubble{display:grid;gap:7px;width:min(760px,100%);padding:10px 12px;border:1px solid rgba(55,114,207,.16);border-radius:12px 12px 12px 4px;background:#f5faff;color:var(--charcoal);box-shadow:0 1px 0 rgba(17,24,39,.03)}
.todo-followup-head{display:flex;align-items:center;gap:7px;min-width:0;flex-wrap:wrap}
.todo-followup-recipient{min-width:0;color:var(--ink);font-size:12px;font-weight:800;line-height:1.25;overflow-wrap:anywhere;word-break:break-word}
.todo-followup-status{display:inline-flex;align-items:center;height:20px;padding:0 7px;border:1px solid rgba(55,114,207,.18);border-radius:999px;background:var(--canvas);color:#245aa5;font-size:11px;font-weight:800;line-height:1;white-space:nowrap}
.todo-followup-time{margin-left:auto;color:var(--steel);font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;font-size:11px;font-weight:700;line-height:1.3;white-space:nowrap}
.todo-followup-message{color:var(--ink);font-size:13px;line-height:1.5;overflow-wrap:anywhere;word-break:break-word}
.todo-followup-target{color:var(--steel);font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;font-size:11px;font-weight:700;line-height:1.35;overflow-wrap:anywhere;word-break:break-word}
.progress-cell{display:grid;gap:5px;min-width:0}
.progress-meter{height:6px;border-radius:999px;background:var(--surface-soft);overflow:hidden}
.progress-bar{height:100%;border-radius:999px;background:#3772cf}
.progress-label{color:var(--steel);font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;font-size:11px;font-weight:800;line-height:1.25;white-space:nowrap}
.task-state{display:inline-flex;align-items:center;height:24px;padding:0 8px;border:1px solid var(--hairline);border-radius:999px;background:var(--surface-soft);font-size:12px;font-weight:800;line-height:1;white-space:nowrap}
.task-state.completed{background:#ddfff6;border-color:rgba(0,180,138,.46);color:#005b49}
.task-state.over-due{background:rgba(212,86,86,.12);border-color:rgba(212,86,86,.24);color:#9a2f2f}
.task-state.in-progress{background:rgba(55,114,207,.10);border-color:rgba(55,114,207,.24);color:#245aa5}
.task-state.not-started{background:var(--surface-soft);color:var(--steel)}
.tasks-tabulator{width:100%;border:1px solid var(--hairline);border-radius:8px;background:var(--canvas);overflow:hidden}
.tasks-tabulator.tabulator{font-size:13px;color:var(--charcoal)}
.tasks-tabulator .tabulator-header{border-bottom:1px solid var(--hairline);background:var(--surface-soft);color:var(--steel);font-size:12px;font-weight:800}
.tasks-tabulator .tabulator-col{background:var(--surface-soft);border-right:1px solid var(--hairline)}
.tasks-tabulator .tabulator-tableholder{overflow-x:hidden}
.tasks-tabulator .tabulator-table{width:100%!important;min-width:0!important}
.tasks-tabulator .tabulator-col-title{white-space:normal!important;overflow-wrap:anywhere;word-break:break-word}
.tasks-tabulator .tabulator-header-filter input,.tasks-tabulator .tabulator-header-filter select{height:28px;border:1px solid var(--hairline);border-radius:7px;background:var(--canvas);color:var(--ink);font-size:12px}
.tasks-tabulator .tabulator-row{border-bottom:1px solid var(--hairline)}
.tasks-tabulator .tabulator-row.tabulator-row-even{background:#fbfcfd}
.tasks-tabulator .tabulator-row.tabulator-selectable{cursor:pointer}
@media (hover:hover) and (pointer:fine){.tasks-tabulator .tabulator-row.tabulator-selectable:hover{background-color:#f5faff}}
.tasks-tabulator .tabulator-row .tabulator-cell{height:auto!important;border-right:1px solid var(--hairline);padding:9px 10px;white-space:normal!important;overflow:visible;text-overflow:clip;overflow-wrap:anywhere;word-break:break-word}
.tasks-tabulator .tabulator-footer{display:none}
.task-project-title{font-weight:700;overflow-wrap:anywhere;word-break:break-word}
.task-cell-text{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:4;overflow:hidden;white-space:normal;overflow-wrap:anywhere;word-break:break-word}
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
.history-table-header{display:grid;grid-template-columns:minmax(0,1fr) auto minmax(0,1fr);align-items:center;gap:12px;margin:0 0 12px;padding:8px 10px;border:1px solid var(--hairline);border-radius:8px;background:var(--surface-soft)}
.history-type-filter{position:relative;justify-self:start;min-width:0}
.history-type-summary{display:inline-flex;align-items:center;height:30px;padding:0 11px;border:1px solid var(--hairline);border-radius:999px;background:var(--canvas);color:var(--ink);font-size:12px;font-weight:800;line-height:1;white-space:nowrap;cursor:pointer;list-style:none}
.history-type-summary::-webkit-details-marker{display:none}
.history-type-summary::after{content:"⌄";margin-left:8px;color:var(--steel);font-size:12px;line-height:1}
.history-type-filter[open] .history-type-summary{border-color:rgba(55,114,207,.36);background:#eaf1ff;color:#245aa5}
.history-type-menu{position:absolute;top:calc(100% + 6px);left:0;z-index:20;display:grid;gap:8px;min-width:190px;padding:10px;border:1px solid var(--hairline);border-radius:8px;background:var(--canvas);box-shadow:0 14px 34px rgba(17,24,39,.13)}
.history-type-option{display:flex;align-items:center;gap:8px;min-height:26px;color:var(--charcoal);font-size:13px;font-weight:700;line-height:1.35;white-space:nowrap}
.history-type-option input{margin:0}
.history-type-actions{display:flex;align-items:center;justify-content:space-between;gap:8px;padding-top:4px;border-top:1px solid var(--hairline-soft)}
.history-type-apply{height:28px;border:1px solid rgba(0,180,138,.32);border-radius:999px;background:#ddfff6;color:#005b49;padding:0 10px;font-size:12px;font-weight:800;line-height:1;cursor:pointer}
.history-type-clear{display:inline-flex;align-items:center;height:28px;color:var(--steel);font-size:12px;font-weight:800;line-height:1}
.history-type-clear:hover{color:var(--ink)}
.history-page-links{display:flex;align-items:center;justify-content:center;gap:4px;min-width:0;white-space:nowrap}
.history-page-link,.history-page-arrow,.history-page-ellipsis{display:inline-flex;align-items:center;justify-content:center;height:28px;min-width:28px;padding:0 8px;border:1px solid transparent;border-radius:999px;color:var(--steel);font-size:12px;font-weight:800;line-height:1}
.history-page-arrow{font-size:16px}
.history-page-link:hover,.history-page-arrow:hover{border-color:var(--hairline);background:var(--canvas);color:var(--ink);text-decoration:none}
.history-page-link.active{border-color:rgba(55,114,207,.26);background:#eaf1ff;color:#245aa5}
.history-page-arrow.disabled,.history-page-ellipsis{color:var(--muted);cursor:default}
.history-limit-form{display:flex;align-items:center;justify-content:flex-end;gap:8px;min-width:0}
.history-limit-select{height:30px;border:1px solid var(--hairline);border-radius:999px;background:var(--canvas);color:var(--ink);padding:0 10px;font-size:12px;font-weight:800}
.history-total{color:var(--steel);font-size:12px;font-weight:700;line-height:1.35;white-space:nowrap}
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
.attempt-reaction-copy{display:inline-flex;align-items:center;width:max-content;max-width:100%;padding:4px 9px;border-radius:999px;background:#fff4d6;border:1px solid #f4d06f;color:#5f4200;font-size:13px;line-height:1.2;-webkit-line-clamp:1;box-shadow:inset 0 -1px 0 rgba(95,66,0,.08)}
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
.log-feed{display:grid;gap:8px}
.log-item{display:grid;gap:8px;padding:11px 12px;border:1px solid var(--hairline);border-radius:8px;background:var(--canvas)}
.log-main{display:grid;gap:8px;min-width:0}
.log-head{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;align-items:start}
.log-title{display:flex;align-items:center;gap:8px;min-width:0;flex-wrap:wrap}
.log-action{min-width:0;color:var(--ink);font-size:14px;font-weight:760;line-height:1.35;overflow-wrap:anywhere;word-break:break-word}
.log-time{color:var(--steel);font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;font-size:11px;font-weight:700;line-height:1.35;text-align:right;white-space:nowrap}
.log-meta{display:flex;gap:6px 10px;flex-wrap:wrap;min-width:0;color:var(--steel);font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;font-size:11px;font-weight:700;line-height:1.35}
.log-context{min-width:0;overflow-wrap:anywhere;word-break:break-word}
.log-body{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:8px}
.log-body.single{grid-template-columns:1fr}
.log-field{min-width:0;padding:8px 9px;border:1px solid var(--hairline-soft);border-radius:7px;background:var(--surface-soft)}
.log-label{margin-bottom:3px;color:var(--steel);font-size:11px;font-weight:800;line-height:1.25;text-transform:uppercase}
.log-value{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:3;overflow:hidden;color:var(--charcoal);font-size:12px;line-height:1.45;overflow-wrap:anywhere;word-break:break-word}
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
@media (max-width:760px){.shell,main{padding-left:12px;padding-right:12px}.topbar{align-items:flex-start;flex-direction:column;padding:14px 0}.grid{grid-template-columns:1fr}th,td{padding:10px 12px}.attempt-foot{align-items:flex-start;flex-direction:column}.attempt-conversation-banner{align-items:flex-start;flex-direction:column}.attempt-detail-grid{grid-template-columns:1fr}.todo-detail-fields{grid-template-columns:1fr}.todo-followup-time{margin-left:0}.log-head{grid-template-columns:1fr}.log-time{text-align:left}.log-body{grid-template-columns:1fr}.history-chart{height:220px}.history-table-header{grid-template-columns:1fr}.history-page-links{justify-content:flex-start}.history-limit-form{justify-content:flex-start}}
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
DEFAULT_ATTEMPT_LIST_LIMIT = 20
ATTEMPT_LIST_LIMIT_OPTIONS = (20, 50, 100)
HISTORY_TYPE_FILTERS = ("sent", "reacted", "skipped", "failed")
TASK_PAGE_SIZE_OPTIONS = (20, 50, 100)
DEFAULT_TASK_PAGE_SIZE = 20
TABULATOR_CSS_URL = "https://cdn.jsdelivr.net/npm/tabulator-tables@6.4.0/dist/css/tabulator.min.css"
TABULATOR_JS_URL = "https://cdn.jsdelivr.net/npm/tabulator-tables@6.4.0/dist/js/tabulator.min.js"
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


class _TutorialStep(TypedDict):
    phase: str
    title: str
    description: str
    checks: list[str]
    commands: list[str]
    links: list[tuple[str, str]]


def render_page(
    title: str,
    body: str,
    *,
    auto_refresh: bool = False,
    active_nav: str | None = None,
    user_feedback_pending_count: int | None = None,
    head_extra: str = "",
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
        f"<style>{CSS}</style>{head_extra}</head><body>"
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


def render_tutorial_page(*, store: AutoReplyStore | None = None) -> str:
    if store is None:
        configured_db_path = os.environ.get("CEO_WORKER_DB")
        store = AutoReplyStore(Path(configured_db_path or "data/auto-reply.sqlite3"))
    status = build_wizard_status(store)
    steps_html = "".join(_setup_wizard_step_html(step) for step in status.steps)
    body = (
        "<section class=\"card tutorial-intro\">"
        "<h2>Initialization Wizard</h2>"
        "<p class=\"muted\">"
        "This wizard checks and configures the local CEO Agent Service setup. "
        "A step is checked only after the system verifies it."
        "</p>"
        "</section>"
        "<section class=\"card\">"
        "<div class=\"card-head\">"
        "<h2>Setup steps</h2>"
        "<div class=\"tutorial-links\">"
        "<a class=\"tutorial-link\" href=\"/config?tab=system\">系统参数</a>"
        "<a class=\"tutorial-link\" href=\"/tasks\">Tasks</a>"
        "<a class=\"tutorial-link\" href=\"/logs\">Logs</a>"
        "</div>"
        "</div>"
        f"<ol class=\"tutorial-steps setup-wizard-steps\">{steps_html}</ol>"
        "</section>"
    )
    return render_page("Tutorial", body, active_nav="tutorial")


def _setup_wizard_step_html(step: SetupStepStatus) -> str:
    action_html = "".join(
        "<form method=\"post\" action=\"/tutorial/"
        f"{'check' if action.kind == 'check' else 'run' if action.kind == 'run' else 'confirm'}"
        f"/{escape(action.id if action.kind == 'run' else step.step_id)}\">"
        f"<button type=\"submit\" data-action-id=\"{escape(action.id)}\">"
        f"{escape(action.label)}</button>"
        "</form>"
        for action in step.available_actions
        if action.kind != "confirm" or step.manual_confirmation_allowed
    )
    evidence_html = "".join(
        "<li>"
        f"<code>{escape(str(key))}</code>: {escape(str(value))}"
        "</li>"
        for key, value in step.evidence.items()
    )
    evidence_list = (
        f"<ul class=\"tutorial-list\">{evidence_html}</ul>"
        if evidence_html
        else ""
    )
    return (
        "<li class=\"tutorial-step setup-wizard-step\">"
        "<div class=\"tutorial-step-number\" aria-hidden=\"true\"></div>"
        "<div class=\"tutorial-step-body\">"
        "<div class=\"tutorial-step-head\">"
        f"<h3>{escape(step.title)}</h3>"
        f"<span class=\"setup-step-status setup-status-{escape(step.status)}\">"
        f"{escape(step.status)}</span>"
        "</div>"
        f"<p>{escape(step.summary or 'Not checked yet.')}</p>"
        f"{evidence_list}"
        f"<div class=\"tutorial-links\">{action_html}</div>"
        "</div>"
        "</li>"
    )


def _tutorial_step_html(step: _TutorialStep) -> str:
    checks_html = _tutorial_list_html(step["checks"], class_name="tutorial-list")
    commands_html = _tutorial_command_list_html(step["commands"])
    links_html = "".join(
        f"<a class=\"tutorial-link\" href=\"{escape(href)}\">{escape(label)}</a>"
        for label, href in step["links"]
    )
    return (
        "<li class=\"tutorial-step\">"
        "<div class=\"tutorial-step-number\" aria-hidden=\"true\"></div>"
        "<div class=\"tutorial-step-body\">"
        "<div class=\"tutorial-step-head\">"
        f"<h3>{escape(str(step['title']))}</h3>"
        f"<span class=\"tutorial-phase\">{escape(str(step['phase']))}</span>"
        "</div>"
        f"<p>{escape(str(step['description']))}</p>"
        "<div class=\"tutorial-lists\">"
        f"{checks_html}"
        f"{commands_html}"
        "</div>"
        f"<div class=\"tutorial-links\">{links_html}</div>"
        "</div>"
        "</li>"
    )


def _tutorial_list_html(items: list[str], *, class_name: str) -> str:
    return (
        f"<ul class=\"{escape(class_name)}\">"
        + "".join(f"<li>{escape(str(item))}</li>" for item in items)
        + "</ul>"
    )


def _tutorial_command_list_html(commands: list[str]) -> str:
    return (
        "<div class=\"tutorial-command-list\">"
        + "".join(f"<code>{escape(str(command))}</code>" for command in commands)
        + "</div>"
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def confirm_setup_step(
    step_id: str,
    *,
    store: AutoReplyStore,
    confirmed_by: str,
    evidence: dict[str, str],
) -> SetupWizardEvent:
    try:
        definition = get_step_definition(step_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown setup step") from exc
    if not any(action.kind == "confirm" for action in definition.actions):
        raise HTTPException(
            status_code=409,
            detail=f"{definition.title} does not allow manual confirmation.",
        )
    if not confirmed_by.strip():
        raise HTTPException(status_code=400, detail="confirmed_by is required.")
    summary = f"Manually confirmed {definition.title}."
    store.upsert_setup_wizard_step(
        step_id=definition.id,
        status="done",
        summary=summary,
        manual_confirmed_by=confirmed_by,
    )
    return SetupWizardEvent(
        step_id=definition.id,
        action_id=f"confirm_{definition.id}",
        status="done",
        summary=summary,
        evidence=evidence,
    )


def _setup_status_map(store: AutoReplyStore) -> dict[str, SetupStepStatus]:
    return {step.step_id: step for step in build_wizard_status(store).steps}


def _require_available_setup_action(
    store: AutoReplyStore,
    action_id: str,
    *,
    kind: str,
):
    try:
        definition = get_action_definition(action_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown setup action") from exc
    if definition.kind != kind:
        raise HTTPException(status_code=400, detail="Wrong setup action type.")
    step_status = _setup_status_map(store).get(definition.step_id)
    if step_status is None:
        raise HTTPException(status_code=404, detail="Unknown setup step")
    if not any(action.id == action_id for action in step_status.available_actions):
        raise HTTPException(
            status_code=409,
            detail=f"{step_status.title} is not ready for this action.",
        )
    return definition


def _wants_setup_redirect(request: Request) -> bool:
    content_type = request.headers.get("content-type", "")
    return (
        "application/x-www-form-urlencoded" in content_type
        or "multipart/form-data" in content_type
    )


def _setup_action_response(request: Request, payload) -> Response:
    if _wants_setup_redirect(request):
        return RedirectResponse("/tutorial", status_code=303)
    return JSONResponse(payload.model_dump())


def _tutorial_steps() -> list[_TutorialStep]:
    return [
        {
            "phase": "Phase 0",
            "title": "收集交互参数",
            "description": "先确认本机路径和身份参数，再改配置；不知道的值先检查机器，只有授权、扫码、策略选择才打断用户。",
            "checks": [
                "Repository path: ~/Documents/Projects/ceo-agent-service",
                "Workspace path: ~/Documents/memory",
                "Principal display name, mention aliases, signature, handoff acknowledgement",
                "Memory Connector MCP URL and DingTalk KB workspace are optional",
            ],
            "commands": [
                "sed -n '1,240p' ~/.agents/AGENT.md",
                "git status --short --branch",
            ],
            "links": [("Runbook", "/config"), ("System config", "/config?tab=system")],
        },
        {
            "phase": "Phase 1",
            "title": "准备本地依赖和 CLI",
            "description": "确认 Python 环境、dws CLI、Codex CLI 和仓库依赖可用；HOME 必须是真实用户目录，不能指向项目目录。",
            "checks": [
                "Python 3.11+ and editable package install",
                "dws auth status and dws doctor pass under the real user account",
                "Codex CLI can run codex exec through the local runtime",
                "Start in dry-run mode until the audit UI is reviewed",
            ],
            "commands": [
                "python3 -m venv .venv",
                ".venv/bin/pip install -e '.[dev]'",
                "dws auth status",
                "codex --version",
            ],
            "links": [("Config", "/config"), ("Logs", "/logs")],
        },
        {
            "phase": "Phase 2",
            "title": "配置 MCP 和基础环境",
            "description": "按 README 配置 Memory Connector MCP、.env、workspace、SQLite 和 corpus 目录；MCP 身份使用已安装 Authorization header，不单独填写 user_id。",
            "checks": [
                ".env comes from .env.example and stays uncommitted",
                "CEO_WORKSPACE, CEO_WORKER_DB, CEO_CORPUS_DIR point at local paths",
                "Memory Connector MCP is optional but must use the authenticated OAuth identity",
                "CEO_NOT_SEND_MESSAGE=1 or CEO_DRY_RUN=1 remains enabled",
            ],
            "commands": [
                "cp .env.example .env",
                ".venv/bin/ceo-agent setup-memory-connector --memory-url '<memory-mcp-url>'",
                "mkdir -p data corpus \"$HOME/Documents/memory\"",
            ],
            "links": [("System config", "/config?tab=system")],
        },
        {
            "phase": "Phase 4",
            "title": "准备本地数据和风格语料",
            "description": "把 AI 听记、SOP、招聘、战略和 Thinking 材料放在 CEO_WORKSPACE 或其他忽略路径，不把私有数据放进 Git。",
            "checks": [
                "Workspace contains AI听记, management/OA, management/strategy, recruiting, Thinking",
                "build-corpus reads local AI minutes and writes style outputs",
                "collect-corpus appends recent DingTalk sent-message samples through current dws identity",
                "corpus/style_corpus.csv is local runtime data, not source code",
            ],
            "commands": [
                ".venv/bin/ceo-agent build-corpus --workspace \"$HOME/Documents/memory\" --corpus-dir ./corpus",
                ".venv/bin/ceo-agent collect-corpus --workspace \"$HOME/Documents/memory\" --corpus-dir ./corpus",
            ],
            "links": [("Tasks", "/tasks")],
        },
        {
            "phase": "Phase 5",
            "title": "生成并复核工作画像蒸馏",
            "description": "build-work-profile 生成证据索引和初版 profile；Nvwa 只在准备/复核阶段使用，运行时只读取 profiles/work_profile.md。",
            "checks": [
                "Expected outputs: profiles/work_profile.md, data/profile-evidence/evidence_index.jsonl, corpus/style_corpus.csv",
                "Nvwa review rewrites only profiles/work_profile.md",
                "Profile must not contain raw private excerpts, absolute paths, tokens, session ids, or DingTalk cache content",
                "Runtime consumes the profile through work_profile_instruction()",
            ],
            "commands": [
                ".venv/bin/ceo-agent build-work-profile --workspace \"$HOME/Documents/memory\" --corpus-dir ./corpus",
                ".venv/bin/pytest tests/test_work_profile.py tests/test_prompt.py tests/test_worker.py::test_consumer_codex_command_embeds_work_profile_content -q",
            ],
            "links": [("Config", "/config"), ("Logs", "/logs")],
        },
        {
            "phase": "Phase 6",
            "title": "验证权限和 dry-run 审计",
            "description": "先做只读权限探测，再运行一次 dry-run；审计页必须能解释路由、证据、错误和未发送状态。",
            "checks": [
                "dws can read unread conversations, docs, AI tables, contacts, calendar, OA, and AI minutes needed by the deployment",
                "Audit UI loads on 127.0.0.1:8765",
                "Dry-run has no unexpected live send",
                "No unresolved failed or processing backlog remains",
            ],
            "commands": [
                ".venv/bin/ceo-agent probe-dws",
                ".venv/bin/python -m app.cli audit-web --reload --host 127.0.0.1 --port 8765",
                "CEO_NOT_SEND_MESSAGE=1 .venv/bin/ceo-agent run-once --not-send-message",
            ],
            "links": [("History", "/"), ("Logs", "/logs"), ("Tasks", "/tasks")],
        },
        {
            "phase": "Phase 8",
            "title": "安装 launchd，最后再决定 live send",
            "description": "launchd 只在 dry-run 行为被审阅后安装；真实发送需要明确设置 CEO_NOT_SEND_MESSAGE=0 和 CEO_LIVE_SEND_BLOCKERS_ACCEPTED=1。",
            "checks": [
                "Inspect launchd/com.ceo-agent-service.main.plist before installation",
                "launchctl print confirms com.ceo-agent-service.main is running",
                "Live send scope is reviewed per chat, alias, action, OA/calendar/follow-up boundary",
                "CEO_LIVE_SEND_BLOCKERS_ACCEPTED=1 is never implied by installation success",
            ],
            "commands": [
                "scripts/install-auto-reply-agents.sh",
                "launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'",
                "CEO_NOT_SEND_MESSAGE=0 CEO_LIVE_SEND_BLOCKERS_ACCEPTED=1 .venv/bin/ceo-agent send-attempt --attempt-id <reviewed-attempt-id>",
            ],
            "links": [("History", "/"), ("Logs", "/logs")],
        },
    ]


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
      if (typeof dd.openChatByConversationId === "function") {{
        report("invoke", "openChatByConversationId");
        const ok = await new Promise((resolve) => {{
          let callbackSeen = false;
          const done = (result, text) => {{
            callbackSeen = true;
            setStatus(text);
            resolve(result);
          }};
          dd.openChatByConversationId({{
            openConversationId,
            success: () => done(true, "已通过当前会话 API 发起跳转。"),
            fail: (error) => done(false, `当前会话 API 跳转失败: ${{JSON.stringify(error)}}`),
            complete: () => {{}},
          }});
          setTimeout(() => {{
            if (!callbackSeen) {{
              report("callback-timeout", "openChatByConversationId");
              resolve(false);
            }}
          }}, 1200);
        }});
        if (ok) {{
          closeBridgePageSoon();
          return;
        }}
        return;
      }}
      setStatus("当前钉钉客户端没有可用的 openChatByConversationId 会话跳转能力。");
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
        ("tutorial", "Tutorial", "/tutorial"),
        ("tasks", "Tasks", "/tasks"),
        ("user-feedback", "用户反馈", "/user-feedback"),
        ("codex", "Codex Sessions", "/codex"),
        ("config", "Config", "/config"),
        ("logs", "Logs", "/logs"),
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


def _history_type_filters(values: str | Iterable[str]) -> tuple[str, ...]:
    raw_values = [values] if isinstance(values, str) else list(values)
    selected: list[str] = []
    for raw_value in raw_values:
        for part in str(raw_value).split(","):
            cleaned = part.strip().lower()
            if cleaned in HISTORY_TYPE_FILTERS and cleaned not in selected:
                selected.append(cleaned)
    return tuple(selected)


def _history_type_filter_label(type_filters: tuple[str, ...]) -> str:
    if not type_filters:
        return "type: all"
    return "type: " + ", ".join(type_filters)


def _attempt_list_limit(value: int) -> int:
    return value if value in ATTEMPT_LIST_LIMIT_OPTIONS else DEFAULT_ATTEMPT_LIST_LIMIT


def _page_href(
    base_path: str,
    page: int,
    *,
    limit: int | None = None,
    type_filters: tuple[str, ...] = (),
    include_limit: bool = False,
) -> str:
    query: dict[str, str | list[str]] = {}
    if page > 1:
        query["page"] = str(page)
    if include_limit and limit is not None and limit != DEFAULT_ATTEMPT_LIST_LIMIT:
        query["limit"] = str(limit)
    if type_filters:
        query["type"] = list(type_filters)
    if not query:
        return base_path
    return f"{base_path}?{urlencode(query, doseq=True)}"


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


def _history_page_window(page: int, page_count: int) -> list[int | None]:
    if page_count <= 7:
        return list(range(1, page_count + 1))
    pages: list[int | None] = [1]
    start = max(2, page - 1)
    end = min(page_count - 1, page + 1)
    if start > 2:
        pages.append(None)
    pages.extend(range(start, end + 1))
    if end < page_count - 1:
        pages.append(None)
    pages.append(page_count)
    return pages


def _history_page_button(
    *,
    base_path: str,
    page: int,
    current_page: int,
    limit: int | None,
    type_filters: tuple[str, ...],
) -> str:
    if page == current_page:
        return (
            f"<span class=\"history-page-link active\" aria-current=\"page\">"
            f"{page}</span>"
        )
    return (
        f"<a class=\"history-page-link\" href=\""
        f"{escape(_page_href(base_path, page, limit=limit, type_filters=type_filters, include_limit=True))}"
        f"\">{page}</a>"
    )


def _history_table_header(
    *,
    base_path: str,
    page: int,
    limit: int | None,
    total_count: int,
    type_filters: tuple[str, ...],
) -> str:
    page_count = _page_count(total_count, limit)
    page = min(max(1, page), page_count)
    type_options = []
    for value in HISTORY_TYPE_FILTERS:
        checked = " checked" if value in type_filters else ""
        type_options.append(
            "<label class=\"history-type-option\">"
            f"<input type=\"checkbox\" name=\"type\" value=\"{escape(value)}\"{checked}>"
            f"{escape(value)}"
            "</label>"
        )
    type_limit_hidden = (
        ""
        if limit is None or limit == DEFAULT_ATTEMPT_LIST_LIMIT
        else f"<input type=\"hidden\" name=\"limit\" value=\"{limit}\">"
    )
    type_clear_href = _page_href(base_path, 1, limit=limit, include_limit=True)
    prev_href = None if page <= 1 else _page_href(
        base_path,
        page - 1,
        limit=limit,
        type_filters=type_filters,
        include_limit=True,
    )
    next_href = None if page >= page_count else _page_href(
        base_path,
        page + 1,
        limit=limit,
        type_filters=type_filters,
        include_limit=True,
    )
    prev_html = (
        "<span class=\"history-page-arrow disabled\" aria-label=\"上一页\">&lsaquo;</span>"
        if prev_href is None
        else f"<a class=\"history-page-arrow\" href=\"{escape(prev_href)}\" aria-label=\"上一页\">&lsaquo;</a>"
    )
    next_html = (
        "<span class=\"history-page-arrow disabled\" aria-label=\"下一页\">&rsaquo;</span>"
        if next_href is None
        else f"<a class=\"history-page-arrow\" href=\"{escape(next_href)}\" aria-label=\"下一页\">&rsaquo;</a>"
    )
    page_links = []
    for item in _history_page_window(page, page_count):
        if item is None:
            page_links.append("<span class=\"history-page-ellipsis\">...</span>")
        else:
            page_links.append(
                _history_page_button(
                    base_path=base_path,
                    page=item,
                    current_page=page,
                    limit=limit,
                    type_filters=type_filters,
                )
            )
    limit_options = "".join(
        f"<option value=\"{value}\"{' selected' if value == limit else ''}>{value}/页</option>"
        for value in ATTEMPT_LIST_LIMIT_OPTIONS
    )
    hidden_types = "".join(
        f"<input type=\"hidden\" name=\"type\" value=\"{escape(value)}\">"
        for value in type_filters
    )
    return (
        "<div class=\"history-table-header\">"
        "<details class=\"history-type-filter\">"
        f"<summary class=\"history-type-summary\">{escape(_history_type_filter_label(type_filters))}</summary>"
        "<form class=\"history-type-menu\" method=\"get\" action=\"/\">"
        f"{type_limit_hidden}"
        + "".join(type_options)
        + "<div class=\"history-type-actions\">"
        "<button class=\"history-type-apply\" type=\"submit\">Apply</button>"
        f"<a class=\"history-type-clear\" href=\"{escape(type_clear_href)}\">Clear</a>"
        "</div></form></details>"
        "<nav class=\"history-page-links\" aria-label=\"分页导航\">"
        f"{prev_html}{''.join(page_links)}{next_html}"
        "</nav>"
        "<form class=\"history-limit-form\" method=\"get\" action=\"/\">"
        f"{hidden_types}"
        f"<select class=\"history-limit-select\" name=\"limit\" onchange=\"this.form.submit()\">{limit_options}</select>"
        f"<span class=\"history-total\">共 {total_count} 条</span>"
        "</form>"
        "</div>"
    )


def _pagination_controls(
    *,
    base_path: str,
    page: int,
    limit: int | None,
    total_count: int,
    bottom: bool = False,
    type_filters: tuple[str, ...] = (),
    include_limit: bool = False,
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
        href=None
        if is_first
        else _page_href(
            base_path,
            1,
            limit=limit,
            type_filters=type_filters,
            include_limit=include_limit,
        ),
    )
    prev_html = _pagination_button(
        label_html="&lsaquo;",
        aria_label="上一页",
        href=None
        if is_first
        else _page_href(
            base_path,
            page - 1,
            limit=limit,
            type_filters=type_filters,
            include_limit=include_limit,
        ),
        arrow=True,
    )
    next_html = _pagination_button(
        label_html="&rsaquo;",
        aria_label="下一页",
        href=None
        if is_last
        else _page_href(
            base_path,
            page + 1,
            limit=limit,
            type_filters=type_filters,
            include_limit=include_limit,
        ),
        arrow=True,
    )
    last_html = _pagination_button(
        label_html="末页",
        aria_label="最后一页",
        href=None
        if is_last
        else _page_href(
            base_path,
            page_count,
            limit=limit,
            type_filters=type_filters,
            include_limit=include_limit,
        ),
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
    type_filter: str | Iterable[str] = (),
) -> str:
    type_filters = _history_type_filters(type_filter)
    send_status_filters = type_filters or None
    total_count = store.count_reply_attempts(send_statuses=send_status_filters)
    page = _bounded_page(page, limit, total_count)
    offset = _page_offset(page, limit)
    items = []
    if page == 1 and not type_filters:
        for task in store.list_reply_tasks(
            statuses=("pending", "processing"),
            limit=limit,
        ):
            items.append(_reply_task_item(task))
    attempts = store.list_reply_attempts(
        limit=limit,
        offset=offset,
        send_statuses=send_status_filters,
    )
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
            f"{_attempt_reply_line(attempt)}"
            "</div>"
            f"{_attempt_feedback_summary(feedback_events, sent_reply)}"
            f"{foot_section}"
            "</article>"
        )
    if not items:
        body = (
            f"{_render_history_chart(store)}"
            f"{_history_table_header(base_path='/', page=page, limit=limit, total_count=total_count, type_filters=type_filters)}"
            "<section class=\"card\"><p class=\"muted\">No reply attempts recorded.</p>"
            f"<p class=\"muted\">DB: {escape(str(store.path))}</p></section>"
        )
    else:
        header = _history_table_header(
            base_path="/",
            page=page,
            limit=limit,
            total_count=total_count,
            type_filters=type_filters,
        )
        body = (
            f"{_render_history_chart(store)}"
            f"{header}"
            "<section class=\"attempt-feed\">"
            + "".join(items)
            + "</section>"
            f"{_pagination_controls(base_path='/', page=page, limit=limit, total_count=total_count, bottom=True, type_filters=type_filters, include_limit=True)}"
        )
    return render_page(
        "CEO Agent Audit",
        body,
        auto_refresh=True,
        active_nav="history",
        user_feedback_pending_count=store.count_pending_user_feedback_items(),
    )


def render_tasks_page(
    store: AutoReplyStore,
    query: str = "",
    category: str = "",
    task_state: str = "",
    sort: str = "",
    page: int = 1,
    page_size: int = DEFAULT_TASK_PAGE_SIZE,
) -> str:
    projects = store.list_work_projects(limit=500)
    items = [
        (project, store.list_work_todos(project_id=project.id))
        for project in projects
    ]
    categories = _task_categories(items)
    task_states = _task_states(items)
    rows = [_task_row_payload(project, todos) for project, todos in items]
    initial_state = {
        "query": query.strip(),
        "category": category.strip(),
        "taskState": task_state.strip(),
        "sort": _bounded_task_sort(sort),
        "page": max(page, 1),
        "pageSize": _bounded_task_page_size(page_size),
    }
    toolbar = _task_toolbar(
        total_count=len(rows),
        query=query,
        page_size=initial_state["pageSize"],
    )
    body = (
        "<section class=\"tasks-page\">"
        f"{toolbar}"
        "<div id=\"tasks-table\" class=\"tasks-tabulator\"></div>"
        f"<script id=\"tasks-data\" type=\"application/json\">{_json_script_payload(rows)}</script>"
        f"<script id=\"tasks-initial-state\" type=\"application/json\">{_json_script_payload(initial_state)}</script>"
        f"<script id=\"tasks-categories\" type=\"application/json\">{_json_script_payload(categories)}</script>"
        f"<script id=\"tasks-states\" type=\"application/json\">{_json_script_payload(task_states)}</script>"
        f"{_task_tabulator_script()}"
        "</section>"
    )
    head_extra = (
        f"<link rel=\"stylesheet\" href=\"{TABULATOR_CSS_URL}\">"
        f"<script src=\"{TABULATOR_JS_URL}\"></script>"
    )
    return render_page(
        "Tasks",
        body,
        active_nav="tasks",
        user_feedback_pending_count=store.count_pending_user_feedback_items(),
        head_extra=head_extra,
    )


def _json_script_payload(value) -> str:
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def _task_row_payload(project, todos) -> dict:
    open_count, open_ratio = _task_open_summary(todos)
    progress_count, progress_ratio = _task_progress_summary(todos)
    state = _task_table_state(project, todos)
    todo_payloads = []
    for todo in todos:
        due = _format_local_time(todo.deadline_at) or todo.deadline_at
        todo_payloads.append(
            {
                "title": todo.title,
                "owner": todo.owner_name,
                "status": str(todo.status),
                "done": _task_todo_done(todo),
                "due": due,
            }
        )
    return {
        "id": project.id,
        "title": project.title,
        "detailUrl": f"/tasks/{project.id}",
        "status": state,
        "statusRank": _task_state_sort_rank().get(state, 99),
        "category": str(project.category),
        "priority": str(project.priority),
        "priorityRank": _task_priority_sort_rank().get(str(project.priority), 99),
        "riskLevel": str(project.risk_level),
        "riskRank": _task_risk_sort_rank().get(str(project.risk_level), 99),
        "owner": project.owner_name,
        "currentState": _excerpt(project.current_state, 120),
        "nextStep": _excerpt(project.next_step, 140),
        "openCount": open_count,
        "openRatio": open_ratio,
        "openSummary": f"{open_count} ({open_ratio}%)",
        "progressCount": progress_count,
        "progressTotal": len(todos),
        "progressRatio": progress_ratio,
        "progressSummary": f"{progress_count}/{len(todos)} ({progress_ratio}%)",
        "todoCount": len(todos),
        "todos": todo_payloads,
        "search": "\n".join(_task_project_search_values(project, todos)).casefold(),
    }


def _task_priority_sort_rank() -> dict[str, int]:
    return {
        ProjectPriority.P0.value: 0,
        ProjectPriority.P1.value: 1,
        ProjectPriority.P2.value: 2,
        ProjectPriority.NONE.value: 3,
    }


def _task_risk_sort_rank() -> dict[str, int]:
    return {
        RiskLevel.HIGH.value: 0,
        RiskLevel.MEDIUM.value: 1,
        RiskLevel.LOW.value: 2,
        RiskLevel.NONE.value: 3,
    }


def _bounded_task_page_size(page_size: int) -> int:
    return page_size if page_size in TASK_PAGE_SIZE_OPTIONS else DEFAULT_TASK_PAGE_SIZE


def _task_categories(items) -> list[str]:
    return sorted({str(project.category) for project, _todos in items if str(project.category)})


def _task_states(items) -> list[str]:
    return sorted(
        {_task_table_state(project, todos) for project, todos in items},
        key=lambda value: _task_state_sort_rank().get(value, 99),
    )


def _bounded_task_sort(sort: str) -> str:
    return sort if sort in _task_sort_options() else ""


def _task_sort_options() -> dict[str, tuple[str, str]]:
    return {
        "": ("", ""),
        "project_desc": ("title", "desc"),
        "project_asc": ("title", "asc"),
        "priority_desc": ("priorityRank", "asc"),
        "priority_asc": ("priorityRank", "desc"),
        "risk_desc": ("riskRank", "asc"),
        "risk_asc": ("riskRank", "desc"),
        "owner_desc": ("owner", "desc"),
        "owner_asc": ("owner", "asc"),
        "state_desc": ("currentState", "desc"),
        "state_asc": ("currentState", "asc"),
        "next_desc": ("nextStep", "desc"),
        "next_asc": ("nextStep", "asc"),
        "open_desc": ("openCount", "desc"),
        "open_asc": ("openCount", "asc"),
        "progress_desc": ("progressRatio", "desc"),
        "progress_asc": ("progressRatio", "asc"),
        "todos_desc": ("todoCount", "desc"),
        "todos_asc": ("todoCount", "asc"),
    }


def _task_state_sort_rank() -> dict[str, int]:
    return {
        "over due": 0,
        "in progress": 1,
        "not started": 2,
        "completed": 3,
    }


def _task_open_summary(todos) -> tuple[int, int]:
    total = len(todos)
    open_count = sum(1 for todo in todos if _task_todo_incomplete(todo))
    if total <= 0:
        return open_count, 0
    return open_count, round(open_count * 100 / total)


def _task_progress_summary(todos) -> tuple[int, int]:
    total = len(todos)
    done_count = sum(1 for todo in todos if _task_todo_done(todo))
    if total <= 0:
        return done_count, 0
    return done_count, round(done_count * 100 / total)


def _task_todo_incomplete(todo) -> bool:
    return str(todo.status) not in {TodoStatus.DONE.value, TodoStatus.CANCELLED.value}


def _task_todo_done(todo) -> bool:
    return str(todo.status) == TodoStatus.DONE.value


def _task_table_state(project, todos) -> str:
    if str(project.status) == ProjectStatus.DONE.value:
        return "completed"
    if todos and not any(_task_todo_incomplete(todo) for todo in todos):
        return "completed"
    if any(_task_todo_overdue(todo) for todo in todos if _task_todo_incomplete(todo)):
        return "over due"
    if any(_task_todo_incomplete(todo) for todo in todos):
        return "in progress"
    return "not started"


def _task_todo_overdue(todo) -> bool:
    deadline = _parse_utc_timestamp(todo.deadline_at)
    return bool(deadline and deadline < datetime.now(timezone.utc))


def _task_toolbar(
    *,
    total_count: int,
    query: str,
    page_size: int,
) -> str:
    query = query.strip()
    return (
        "<div class=\"tasks-toolbar\">"
        "<div class=\"tasks-toolbar-left\">"
        f"<span id=\"tasks-count\" class=\"tasks-count\">{total_count} tasks</span>"
        "<label class=\"tasks-search\">"
        "<span class=\"sr-only\">Search tasks</span>"
        f"<input id=\"task-search-input\" type=\"text\" value=\"{escape(query)}\" "
        "placeholder=\"搜索\" autocomplete=\"off\">"
        "<button id=\"task-search-clear\" class=\"tasks-search-clear\" type=\"button\" "
        "aria-label=\"Clear search\" title=\"Clear search\">×</button>"
        "</label>"
        "</div>"
        "<div class=\"tasks-toolbar-right\">"
        "<nav id=\"tasks-pages\" class=\"tasks-pages\" aria-label=\"Task pages\"></nav>"
        f"{_task_page_size_select(page_size=page_size)}"
        "</div>"
        "</div>"
    )


def _task_page_size_select(
    *,
    page_size: int,
) -> str:
    options = "".join(
        f"<option value=\"{size}\"{' selected' if size == page_size else ''}>{size}/page</option>"
        for size in TASK_PAGE_SIZE_OPTIONS
    )
    return (
        "<select id=\"task-page-size\" class=\"tasks-page-size\" "
        "aria-label=\"Tasks per page\">"
        f"{options}</select>"
    )


def _task_tabulator_script() -> str:
    sort_options_json = _json_script_payload(_task_sort_options())
    return f"""
<script>
(() => {{
  const rows = JSON.parse(document.getElementById("tasks-data").textContent || "[]");
  const initial = JSON.parse(document.getElementById("tasks-initial-state").textContent || "{{}}");
  const categories = JSON.parse(document.getElementById("tasks-categories").textContent || "[]");
  const states = JSON.parse(document.getElementById("tasks-states").textContent || "[]");
  const sortOptions = {sort_options_json};
  const countEl = document.getElementById("tasks-count");
  const searchInput = document.getElementById("task-search-input");
  const clearButton = document.getElementById("task-search-clear");
  const pageSizeSelect = document.getElementById("task-page-size");
  const pagesEl = document.getElementById("tasks-pages");

  const escapeHtml = (value) => String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
  const filterValues = (items, label) => {{
    const values = {{"": label}};
    items.forEach((item) => {{ values[item] = item; }});
    return values;
  }};
  const pill = (value) => `<span class="pill">${{escapeHtml(value || "-")}}</span>`;
  const badge = (value) => {{
    const cssClass = String(value || "").replace(/\\s+/g, "-");
    return `<span class="task-state ${{escapeHtml(cssClass)}}">${{escapeHtml(value || "-")}}</span>`;
  }};
  const textCell = (value) => `<div class="task-cell-text">${{escapeHtml(value)}}</div>`;
  const projectCell = (cell) => {{
    const row = cell.getRow().getData();
    return `<div class="task-project-title">${{escapeHtml(row.title)}}</div>`;
  }};
  const progressCell = (cell) => {{
    const row = cell.getRow().getData();
    const ratio = Math.max(0, Math.min(100, Number(row.progressRatio) || 0));
    return `<div class="progress-cell"><div class="progress-meter"><div class="progress-bar" style="width:${{ratio}}%"></div></div><div class="progress-label">${{escapeHtml(row.progressSummary)}}</div></div>`;
  }};
  const todoCell = (cell) => {{
    const todos = cell.getRow().getData().todos || [];
    if (!todos.length) {{
      return `<span class="muted">-</span>`;
    }}
    const visibleTodos = todos.slice(0, 3);
    const items = visibleTodos.map((todo) => {{
      const checkClass = todo.done ? "todo-check done" : "todo-check";
      const check = todo.done ? "✓" : "";
      const due = todo.due ? `<span class="todo-due">DDL ${{escapeHtml(todo.due)}}</span>` : "";
      return `<li><span class="${{checkClass}}" aria-hidden="true">${{check}}</span><span class="todo-copy"><span>${{escapeHtml(todo.title)}}</span>${{due}}</span></li>`;
    }});
    if (todos.length > visibleTodos.length) {{
      items.push(`<li class="todo-total">总共 ${{todos.length}} 条</li>`);
    }}
    return `<ul class="todo-checklist">${{items.join("")}}</ul>`;
  }};
  const sortConfig = sortOptions[initial.sort] || ["", ""];
  const initialSort = sortConfig[0] ? [{{column: sortConfig[0], dir: sortConfig[1]}}] : [];
  const pageSize = Number(initial.pageSize) || {DEFAULT_TASK_PAGE_SIZE};
  const table = new Tabulator("#tasks-table", {{
    data: rows,
    layout: "fitColumns",
    maxHeight: "calc(100vh - 210px)",
    pagination: "local",
    paginationSize: pageSize,
    paginationInitialPage: Number(initial.page) || 1,
    paginationSizeSelector: [{", ".join(str(size) for size in TASK_PAGE_SIZE_OPTIONS)}],
    placeholder: "No matching tasks.",
    initialSort,
    columns: [
      {{title: "Project", field: "title", minWidth: 180, widthGrow: 1.1, sorter: "string", variableHeight: true, formatter: projectCell}},
      {{title: "Status", field: "status", width: 126, sorter: "string", headerFilter: "select", headerFilterParams: {{values: filterValues(states, "All status")}}, headerFilterValue: initial.taskState || "", formatter: (cell) => badge(cell.getValue())}},
      {{title: "Category", field: "category", width: 136, sorter: "string", headerFilter: "select", headerFilterParams: {{values: filterValues(categories, "All categories")}}, headerFilterValue: initial.category || "", formatter: (cell) => pill(cell.getValue())}},
      {{title: "Priority", field: "priorityRank", width: 96, sorter: "number", formatter: (cell) => pill(cell.getRow().getData().priority)}},
      {{title: "Risk", field: "riskRank", width: 88, sorter: "number", formatter: (cell) => pill(cell.getRow().getData().riskLevel)}},
      {{title: "Owner", field: "owner", width: 124, sorter: "string", variableHeight: true, formatter: (cell) => escapeHtml(cell.getValue())}},
      {{title: "State", field: "currentState", minWidth: 140, widthGrow: .8, sorter: "string", variableHeight: true, formatter: (cell) => textCell(cell.getValue())}},
      {{title: "Next", field: "nextStep", minWidth: 150, widthGrow: .9, sorter: "string", variableHeight: true, formatter: (cell) => textCell(cell.getValue())}},
      {{title: "Progress", field: "progressRatio", width: 136, sorter: "number", hozAlign: "left", formatter: progressCell}},
      {{title: "ToDos", field: "todoCount", minWidth: 320, widthGrow: 2, sorter: "number", variableHeight: true, formatter: todoCell}},
    ],
  }});
  table.on("rowClick", (event, row) => {{
    if (event.target.closest("a,button,input,select,textarea,label")) {{
      return;
    }}
    window.location.href = row.getData().detailUrl;
  }});

  const activeRows = () => table.getRows("active");
  const applySearch = () => {{
    const terms = String(searchInput.value || "").trim().toLowerCase().split(/\\s+/).filter(Boolean);
    table.setFilter((data) => !terms.length || terms.every((term) => String(data.search || "").includes(term)));
    clearButton.hidden = !terms.length;
  }};
  const updateCount = (_filters, filteredRows) => {{
    const count = filteredRows ? filteredRows.length : activeRows().length;
    countEl.textContent = `${{count}} tasks`;
  }};
  const updatePages = () => {{
    const current = table.getPage();
    const max = table.getPageMax();
    if (!max || max <= 1) {{
      pagesEl.innerHTML = "";
      return;
    }}
    const visible = new Set([1, max, current - 1, current, current + 1].filter((page) => page >= 1 && page <= max));
    const pieces = [];
    let previous = 0;
    [...visible].sort((a, b) => a - b).forEach((page) => {{
      if (previous && page - previous > 1) {{
        pieces.push(`<span class="tasks-page-link disabled">...</span>`);
      }}
      if (page === current) {{
        pieces.push(`<span class="tasks-page-link active" aria-label="Page ${{page}}">${{page}}</span>`);
      }} else {{
        pieces.push(`<button class="tasks-page-link" type="button" data-page="${{page}}" aria-label="Page ${{page}}">${{page}}</button>`);
      }}
      previous = page;
    }});
    pagesEl.innerHTML = `<button class="tasks-page-link" type="button" data-page="${{Math.max(current - 1, 1)}}" aria-label="Previous page">&lt;</button>${{pieces.join("")}}<button class="tasks-page-link" type="button" data-page="${{Math.min(current + 1, max)}}" aria-label="Next page">&gt;</button>`;
  }};

  table.on("dataFiltered", updateCount);
  table.on("dataFiltered", updatePages);
  table.on("pageLoaded", updatePages);
  table.on("tableBuilt", () => {{
    if (initial.query) {{
      searchInput.value = initial.query;
      applySearch();
    }} else {{
      clearButton.hidden = true;
      updateCount(null, activeRows());
    }}
    updatePages();
  }});
  searchInput.addEventListener("input", applySearch);
  clearButton.addEventListener("click", () => {{
    searchInput.value = "";
    applySearch();
    searchInput.focus();
  }});
  pageSizeSelect.addEventListener("change", () => table.setPageSize(Number(pageSizeSelect.value)));
  pagesEl.addEventListener("click", (event) => {{
    const button = event.target.closest("button[data-page]");
    if (button) {{
      table.setPage(Number(button.dataset.page));
    }}
  }});
}})();
</script>
"""

def _task_project_search_values(project, todos) -> list[str]:
    values = [
        project.title,
        str(project.category),
        project.tags_json,
        str(project.status),
        str(project.priority),
        str(project.risk_level),
        project.owner_user_id,
        project.owner_name,
        project.related_people_json,
        project.goal,
        project.background,
        project.facts_json,
        project.current_state,
        project.blocker,
        project.next_step,
        project.source_conversations_json,
        project.memory_context_json,
    ]
    for todo in todos:
        values.extend(
            [
                todo.title,
                todo.owner_user_id,
                todo.owner_name,
                str(todo.status),
                str(todo.priority),
                todo.deadline_at,
                todo.next_follow_up_at,
                todo.follow_up_question,
                todo.blocker,
                todo.completion_evidence_json,
            ]
        )
    return [value for value in values if value]


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
    conversation_titles = _task_conversation_title_map(project.source_conversations_json)
    todo_panel = _task_todos_panel(todos, drafts, conversation_titles)
    update_rows = _task_update_rows(updates)
    draft_rows = _task_follow_up_rows(
        _unlinked_follow_up_drafts(todos, drafts),
        conversation_titles,
    )

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
        f"{_task_project_detail_table(detail_rows)}"
        "</section>"
        "<section class=\"card\"><h2>TODOs</h2>"
        f"{todo_panel if todos else '<p class=\"muted\">No TODOs recorded.</p>'}"
        "</section>"
        "<section class=\"card\"><h2>Facts</h2>"
        f"{_simple_table(('Description', 'Source', 'Created', 'Updated'), facts, column_widths={'Source': '118px', 'Created': '132px', 'Updated': '132px'}) if facts else '<p class=\"muted\">No facts recorded.</p>'}"
        "</section>"
        "<section class=\"card\"><h2>Updates</h2>"
        f"{_simple_table(('Time', 'Source', 'Summary', 'Changes', 'Reason', 'Confidence'), update_rows, column_widths={'Time': '148px', 'Source': '118px', 'Summary': '240px', 'Changes': '220px', 'Reason': '180px', 'Confidence': '96px'}) if update_rows else '<p class=\"muted\">No updates recorded.</p>'}"
        "</section>"
        + (
            "<section class=\"card\"><h2>Unlinked follow-ups</h2>"
            f"{_simple_table(('Time', 'Owner', 'TODO', 'Target', 'Status', 'Question', 'Risk', 'Result'), draft_rows, column_widths={'Time': '148px', 'Owner': '110px', 'TODO': '88px', 'Target': '112px', 'Status': '104px', 'Question': '240px', 'Risk': '170px', 'Result': '180px'}, html_columns={'TODO'})}"
            "</section>"
            if draft_rows
            else ""
        )
        + f"{_collapsible_json_card('Memory context', project.memory_context_json)}"
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


def _task_project_detail_rows(project) -> list[tuple[str, str, bool]]:
    tags = _task_detail_pills(_task_simple_labels(project.tags_json))
    related_people = _task_detail_pills(_task_people_labels(project.related_people_json))
    source_conversations = _task_detail_pills(
        _task_conversation_labels(project.source_conversations_json)
    )
    return [
        ("Goal", project.goal, False),
        ("Background", project.background, False),
        ("Current state", project.current_state, False),
        ("Blocker", project.blocker, False),
        ("Next step", project.next_step, False),
        ("Follow-up mode", str(project.follow_up_mode), False),
        ("Tags", tags, True),
        ("Related people", related_people, True),
        ("Source conversations", source_conversations, True),
        ("Created", _format_local_time(project.created_at), False),
        ("Last activity", _format_local_time(project.last_activity_at), False),
    ]


def _task_project_detail_table(rows: Iterable[tuple[str, str, bool]]) -> str:
    row_html = "".join(
        "<tr>"
        f"<td>{escape(field)}</td>"
        f"<td>{value if is_html else escape(value)}</td>"
        "</tr>"
        for field, value, is_html in rows
    )
    return (
        "<table><thead><tr><th>Field</th><th>Value</th></tr></thead><tbody>"
        f"{row_html}"
        "</tbody></table>"
    )


def _task_detail_pills(labels: Iterable[str]) -> str:
    pills = "".join(
        f'<span class="detail-pill">{escape(label)}</span>'
        for label in labels
        if label
    )
    return f'<div class="detail-pill-list">{pills}</div>' if pills else "-"


def _task_simple_labels(text: str) -> list[str]:
    labels = []
    for item in _json_list(text):
        label = str(item).strip()
        if label:
            labels.append(label)
    return labels


def _task_people_labels(text: str) -> list[str]:
    labels = []
    for item in _json_list(text):
        if isinstance(item, dict):
            label = str(item.get("name") or item.get("user_id") or "").strip()
        else:
            label = str(item).strip()
        if label:
            labels.append(label)
    return labels


def _task_conversation_labels(text: str) -> list[str]:
    labels = []
    for item in _json_list(text):
        if isinstance(item, dict):
            label = str(item.get("title") or item.get("name") or "").strip()
            if not label:
                label = str(
                    item.get("conversation_id")
                    or item.get("id")
                    or item.get("open_conversation_id")
                    or ""
                ).strip()
        else:
            label = str(item).strip()
        if label:
            labels.append(label)
    return labels


def _task_conversation_title_map(text: str) -> dict[str, str]:
    titles = {}
    for item in _json_list(text):
        if not isinstance(item, dict):
            continue
        conversation_id = str(
            item.get("conversation_id")
            or item.get("id")
            or item.get("open_conversation_id")
            or ""
        ).strip()
        title = str(item.get("title") or item.get("name") or "").strip()
        if conversation_id and title:
            titles[conversation_id] = title
    return titles


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


def _task_todos_panel(todos, drafts, conversation_titles: Mapping[str, str]) -> str:
    follow_ups_by_todo = _follow_up_drafts_by_todo(todos, drafts)
    items = "".join(
        _task_todo_detail_item(
            todo,
            follow_ups_by_todo.get(todo.id, []),
            conversation_titles,
        )
        for todo in todos
    )
    return f'<div class="todo-detail-list">{items}</div>'


def _follow_up_drafts_by_todo(todos, drafts) -> dict[int, list]:
    todo_ids = {todo.id for todo in todos}
    grouped = {todo.id: [] for todo in todos}
    for draft in drafts:
        if draft.todo_id in todo_ids:
            grouped[draft.todo_id].append(draft)
    return grouped


def _unlinked_follow_up_drafts(todos, drafts) -> list:
    todo_ids = {todo.id for todo in todos}
    return [draft for draft in drafts if draft.todo_id not in todo_ids]


def _task_todo_detail_item(todo, follow_ups, conversation_titles: Mapping[str, str]) -> str:
    owner = todo.owner_name or todo.owner_user_id or "-"
    status = str(todo.status)
    priority = str(todo.priority)
    deadline = _format_local_time(todo.deadline_at) or todo.deadline_at or "-"
    next_follow_up = (
        _format_local_time(todo.next_follow_up_at) or todo.next_follow_up_at or "-"
    )
    evidence = _task_json_compact(todo.completion_evidence_json, "{}") or "-"
    check_class = "todo-detail-check done" if _task_todo_done(todo) else "todo-detail-check"
    status_class = _task_status_class(status)
    follow_up_panel = (
        _task_follow_up_child_panel(todo.id, follow_ups, conversation_titles)
        if follow_ups
        else ""
    )
    return (
        f'<article class="todo-detail-item" id="todo-{todo.id}">'
        '<div class="todo-detail-main">'
        f'<span class="{check_class}">✓</span>'
        '<div class="todo-detail-body">'
        '<div class="todo-detail-title-row">'
        f'<h3 class="todo-detail-title">{escape(todo.title or "-")}</h3>'
        f'<span class="task-state {escape(status_class)}">{escape(status)}</span>'
        "</div>"
        '<div class="todo-detail-meta">'
        f"<span>#{todo.id}</span>"
        f"<span>{escape(owner)}</span>"
        f"<span>{escape(priority)}</span>"
        f"<span>DDL {escape(deadline)}</span>"
        f"<span>Next {escape(next_follow_up)}</span>"
        "</div>"
        '<div class="todo-detail-fields">'
        f"{_task_todo_detail_field('Question', todo.follow_up_question or '-')}"
        f"{_task_todo_detail_field('Blocker', todo.blocker or '-')}"
        f"{_task_todo_detail_field('Evidence', evidence)}"
        "</div>"
        "</div>"
        "</div>"
        f"{follow_up_panel}"
        "</article>"
    )


def _task_status_class(status: str) -> str:
    return status.strip().lower().replace("_", "-").replace(" ", "-") or "unknown"


def _task_todo_detail_field(label: str, value: str) -> str:
    return (
        '<div class="todo-detail-field">'
        f'<div class="todo-detail-label">{escape(label)}</div>'
        f'<div class="todo-detail-value">{escape(value)}</div>'
        "</div>"
    )


def _task_follow_up_child_panel(
    todo_id: int,
    drafts,
    conversation_titles: Mapping[str, str],
) -> str:
    items = "".join(
        _task_follow_up_child_item(draft, conversation_titles) for draft in drafts
    )
    label = f"Follow-ups ({len(drafts)})"
    return (
        f'<div class="todo-detail-followups" data-parent-todo="{todo_id}">'
        f"<div class=\"todo-followup-heading\">{escape(label)}</div>"
        f"<ul class=\"todo-followup-list\">{items}</ul>"
        "</div>"
    )


def _task_follow_up_child_item(draft, conversation_titles: Mapping[str, str]) -> str:
    scheduled = _format_local_time(draft.scheduled_at) or draft.scheduled_at or "-"
    owner = draft.owner_name or draft.owner_user_id or "-"
    target = _task_follow_up_target(draft, conversation_titles)
    return (
        "<li class=\"todo-followup-item\">"
        "<div class=\"todo-followup-bubble\">"
        "<div class=\"todo-followup-head\">"
        f"<span class=\"todo-followup-recipient\">{escape(owner)}</span>"
        f"<span class=\"todo-followup-status\">{escape(draft.status)}</span>"
        f"<span class=\"todo-followup-time\">{escape(scheduled)}</span>"
        "</div>"
        f"<div class=\"todo-followup-message\">{escape(draft.question_text)}</div>"
        f"<div class=\"todo-followup-target\">{escape(target)}</div>"
        "</div>"
        "</li>"
    )


def _task_follow_up_target(
    draft,
    conversation_titles: Mapping[str, str] | None = None,
) -> str:
    conversation_titles = conversation_titles or {}
    if draft.target_conversation_id and draft.target_conversation_id in conversation_titles:
        return conversation_titles[draft.target_conversation_id]
    return (
        f"{draft.target_kind}:{draft.target_conversation_id}"
        if draft.target_conversation_id
        else draft.target_kind or "-"
    )


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


def _task_follow_up_rows(
    drafts,
    conversation_titles: Mapping[str, str],
) -> list[tuple[str, str, str, str, str, str, str, str]]:
    rows = []
    for draft in drafts:
        target = _task_follow_up_target(draft, conversation_titles)
        todo_link = "-"
        if draft.todo_id:
            todo_link = f"<a href=\"#todo-{draft.todo_id}\">#{draft.todo_id}</a>"
        rows.append(
            (
                _format_local_time(draft.scheduled_at) or draft.scheduled_at,
                draft.owner_name or draft.owner_user_id,
                todo_link,
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


def _simple_table(
    headers: Iterable[str],
    rows: Iterable[Iterable[str]],
    *,
    column_widths: Mapping[str, str] | None = None,
    html_columns: set[str] | None = None,
) -> str:
    header_values = tuple(headers)
    column_widths = column_widths or {}
    html_columns = html_columns or set()
    colgroup_html = "".join(
        f"<col style=\"width:{escape(column_widths.get(header, 'auto'))}\">"
        for header in header_values
    )
    header_html = "".join(f"<th>{escape(header)}</th>" for header in header_values)
    row_html = "".join(_simple_table_row(header_values, row, html_columns) for row in rows)
    table_class = ' class="column-sized-table"' if column_widths else ""
    colgroup = f"<colgroup>{colgroup_html}</colgroup>" if column_widths else ""
    return (
        f"<table{table_class}>"
        f"{colgroup}"
        "<thead><tr>"
        f"{header_html}"
        "</tr></thead><tbody>"
        f"{row_html}"
        "</tbody></table>"
    )


def _simple_table_row(
    header_values: tuple[str, ...],
    row: Iterable[str],
    html_columns: set[str],
) -> str:
    return (
        "<tr>"
        + "".join(
            f"<td>{value if header in html_columns else escape(value)}</td>"
            for header, value in zip(header_values, row)
        )
        + "</tr>"
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
    return render_log_list(store, limit=limit, page=page)


def render_log_list(
    store: AutoReplyStore,
    limit: int | None = DEFAULT_ERROR_LIST_LIMIT,
    page: int = 1,
) -> str:
    total_count = store.count_operation_logs()
    page = _bounded_page(page, limit, total_count)
    offset = _page_offset(page, limit)
    items = []
    for log in store.list_operation_logs(limit=limit, offset=offset):
        status = _operation_log_status(store, log)
        status_class = _operation_status_class(status)
        items.append(_operation_log_item(log, status, status_class))
    pagination = _pagination_controls(
        base_path="/logs",
        page=page,
        limit=limit,
        total_count=total_count,
    )
    body = (
        f"{pagination}"
        f"<section class=\"log-feed\">{''.join(items)}</section>"
        f"{_pagination_controls(base_path='/logs', page=page, limit=limit, total_count=total_count, bottom=True)}"
    )
    return render_page(
        "Logs",
        body,
        active_nav="logs",
        user_feedback_pending_count=store.count_pending_user_feedback_items(),
    )


def _operation_log_item(log: OperationLog, status: str, status_class: str) -> str:
    summary = _excerpt(log.summary, 420) if log.summary else ""
    detail = _excerpt(log.detail, 420) if log.detail else ""
    if not detail or detail == summary:
        body = (
            "<div class=\"log-body single\">"
            f"{_operation_log_field('Summary', summary or '-')}"
            "</div>"
        )
    else:
        body = (
            "<div class=\"log-body\">"
            f"{_operation_log_field('Summary', summary or '-')}"
            f"{_operation_log_field('Detail', detail)}"
            "</div>"
        )
    return (
        "<article class=\"log-item\">"
        "<div class=\"log-main\">"
        "<div class=\"log-head\">"
        "<div class=\"log-title\">"
        f"<span class=\"pill\">{escape(log.category)}</span>"
        f"<span class=\"log-action\">{escape(log.action or '-')}</span>"
        f"<span class=\"pill {status_class}\">{escape(status or '-')}</span>"
        "</div>"
        f"<time class=\"log-time\">{escape(_format_local_time(log.occurred_at))}</time>"
        "</div>"
        "<div class=\"log-meta\">"
        f"<span>{escape(log.id)}</span>"
        f"<span class=\"log-context\">{escape(log.context or '-')}</span>"
        "</div>"
        f"{body}"
        "</div>"
        "</article>"
    )


def _operation_log_field(label: str, value: str) -> str:
    return (
        "<div class=\"log-field\">"
        f"<div class=\"log-label\">{escape(label)}</div>"
        f"<div class=\"log-value\">{escape(value)}</div>"
        "</div>"
    )


def _operation_log_status(store: AutoReplyStore, log: OperationLog) -> str:
    if log.source_table != "errors":
        return log.status
    error = ReplyError(
        id=log.source_id,
        conversation_id=log.conversation_id or None,
        message_id=log.message_id or None,
        kind=log.action,
        detail=log.detail,
        created_at=log.occurred_at,
    )
    return _error_resolution_label(store, error)


def _operation_status_class(status: str) -> str:
    normalized = status.strip().lower()
    if normalized.startswith("resolved") or normalized in {"sent", "done", "completed"}:
        return "status-resolved"
    if normalized in {"failed", "blocked"}:
        return "status-failed"
    return "status-active"


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
    if sent_reply is None:
        return (
            400,
            {},
            render_page(
                "撤销不可用",
                "<p>撤销不可用：没有找到这条 attempt 对应的已发送回复。</p>",
            ),
        )
    message_id = _sent_reply_recall_message_id(sent_reply)
    if not message_id:
        open_task_id = _sent_reply_open_task_id(sent_reply)
        if open_task_id and hasattr(dws, "query_message_send_status"):
            try:
                message_id = _find_string_value(
                    dws.query_message_send_status(open_task_id),
                    _DWS_MESSAGE_ID_KEYS,
                )
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
    if not message_id and not sent_reply.recall_key:
        return (
            400,
            {},
            render_page(
                "撤销不可用",
                "<p>撤销不可用：没有可撤销消息 ID 或 key，当前发送方式不支持自动撤销。</p>",
            ),
        )
    try:
        if message_id:
            dws.recall_message(attempt.conversation_id, message_id)
        else:
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


def _audit_worker_settings(db_path: Path):
    from app.cli import DEFAULT_DING_ROBOT_NAME, WorkerSettings

    return WorkerSettings(
        workspace=workspace_path(),
        db_path=db_path,
        corpus_dir=corpus_dir(),
        dry_run=False,
        ding_robot_code=os.getenv("CEO_DING_ROBOT_CODE")
        or os.getenv("DINGTALK_DING_ROBOT_CODE"),
        ding_robot_name=os.getenv("CEO_DING_ROBOT_NAME", DEFAULT_DING_ROBOT_NAME),
        ding_receiver_user_id=os.getenv("CEO_DING_RECEIVER_USER_ID"),
    )


def _create_audit_worker(settings):
    from app.cli import create_worker

    return create_worker(settings)


def handle_rerun_attempt_post(
    store: AutoReplyStore,
    attempt_id: int,
    *,
    worker_factory: Callable[[object], object] | None = None,
) -> tuple[int, dict[str, str], str]:
    attempt = store.get_reply_attempt(attempt_id)
    if attempt is None:
        return 404, {}, render_page("Attempt not found", "Attempt not found")
    conversation_record = store.get_conversation(attempt.conversation_id)
    if conversation_record is None:
        return (
            404,
            {},
            render_page(
                "Conversation not found",
                f"<p>Conversation not found: {escape(attempt.conversation_id)}</p>",
            ),
        )
    settings = _audit_worker_settings(store.path)
    worker = (worker_factory or _create_audit_worker)(settings)
    conversation = DingTalkConversation(
        open_conversation_id=conversation_record.conversation_id,
        title=conversation_record.title,
        single_chat=conversation_record.single_chat,
        unread_point=1,
    )
    try:
        processed_message_id = worker.rerun_message(
            conversation,
            attempt.trigger_message_id,
            force_new_decision=True,
            oa_url=attempt.oa_url,
        )
    except (SystemExit, ValueError) as exc:
        return 400, {}, render_page("重跑失败", f"<p>{escape(str(exc))}</p>")
    store.complete_reply_task_for_message(
        attempt.conversation_id,
        processed_message_id,
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
            limit=_attempt_list_limit(
                _positive_int_query(
                    request,
                    "limit",
                    default=DEFAULT_ATTEMPT_LIST_LIMIT,
                )
            ),
            page=_positive_int_query(request, "page", default=1),
            type_filter=request.query_params.getlist("type"),
        )

    @app.get("/user-feedback", response_class=HTMLResponse)
    def user_feedback_list(request: Request) -> str:
        return render_user_feedback_list(
            AutoReplyStore(db_path),
            page=_positive_int_query(request, "page", default=1),
        )

    @app.get("/tutorial", response_class=HTMLResponse)
    def tutorial_page() -> str:
        return render_tutorial_page(store=AutoReplyStore(db_path))

    @app.get("/tutorial/status")
    def tutorial_status() -> JSONResponse:
        return JSONResponse(build_wizard_status(AutoReplyStore(db_path)).model_dump())

    @app.post("/tutorial/check/{step_id}", response_model=None)
    def tutorial_check(step_id: str, request: Request) -> Response:
        store = AutoReplyStore(db_path)
        try:
            step = get_step_definition(step_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown setup step") from exc
        _require_available_setup_action(store, f"check_{step.id}", kind="check")
        status = check_setup_step(step_id, repo_root=_repo_root(), store=store)
        store.upsert_setup_wizard_step(
            step_id=status.step_id,
            status=status.status,
            summary=status.summary,
        )
        return _setup_action_response(request, status)

    @app.post("/tutorial/run/{action_id}", response_model=None)
    def tutorial_run(action_id: str, request: Request) -> Response:
        store = AutoReplyStore(db_path)
        _require_available_setup_action(store, action_id, kind="run")
        event = run_setup_action(action_id, repo_root=_repo_root(), env=dict(os.environ))
        store.record_setup_wizard_event(
            step_id=event.step_id,
            action_id=event.action_id,
            status=event.status,
            summary=event.summary,
            evidence_json=json.dumps(event.evidence, ensure_ascii=False),
            stdout_excerpt=event.stdout_excerpt,
            stderr_excerpt=event.stderr_excerpt,
        )
        if event.step_id != "unknown":
            store.upsert_setup_wizard_step(
                step_id=event.step_id,
                status="done" if event.status == "done" else "failed",
                summary=event.summary,
            )
        return _setup_action_response(request, event)

    @app.post("/tutorial/confirm/{step_id}", response_model=None)
    async def tutorial_confirm(
        step_id: str,
        request: Request,
    ) -> Response:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            payload = await request.json()
        else:
            form_values = parse_qs(
                (await request.body()).decode(),
                keep_blank_values=True,
            )
            payload = {key: values[-1] for key, values in form_values.items()}
        evidence = payload.get("evidence")
        evidence_payload = evidence if isinstance(evidence, Mapping) else {}
        store = AutoReplyStore(db_path)
        _require_available_setup_action(store, f"confirm_{step_id}", kind="confirm")
        event = confirm_setup_step(
            step_id,
            store=store,
            confirmed_by=str(payload.get("confirmed_by") or "local-user"),
            evidence={
                key: str(value)
                for key, value in evidence_payload.items()
            },
        )
        store.record_setup_wizard_event(
            step_id=event.step_id,
            action_id=event.action_id,
            status=event.status,
            summary=event.summary,
            evidence_json=json.dumps(event.evidence, ensure_ascii=False),
            stdout_excerpt=event.stdout_excerpt,
            stderr_excerpt=event.stderr_excerpt,
        )
        return _setup_action_response(request, event)

    @app.get("/tasks", response_class=HTMLResponse)
    def tasks_page(request: Request) -> str:
        return render_tasks_page(
            AutoReplyStore(db_path),
            query=str(request.query_params.get("q") or ""),
            category=str(request.query_params.get("category") or ""),
            task_state=str(request.query_params.get("task_state") or ""),
            sort=str(request.query_params.get("sort") or ""),
            page=_positive_int_query(request, "page", default=1),
            page_size=_positive_int_query(request, "page_size", default=DEFAULT_TASK_PAGE_SIZE),
        )

    @app.get("/tasks/{project_id}", response_class=HTMLResponse)
    def task_project_detail(project_id: int) -> HTMLResponse:
        status, html = render_task_project_detail(AutoReplyStore(db_path), project_id)
        return HTMLResponse(html, status_code=status)

    @app.get("/logs", response_class=HTMLResponse)
    def log_list(request: Request) -> str:
        return render_log_list(
            AutoReplyStore(db_path),
            page=_positive_int_query(request, "page", default=1),
        )

    @app.get("/errors", response_class=HTMLResponse)
    def error_list(request: Request) -> str:
        return log_list(request)

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

    @app.post("/attempts/{attempt_id}/rerun")
    def rerun_attempt(attempt_id: int):
        status, headers, html = handle_rerun_attempt_post(
            AutoReplyStore(db_path),
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


_DWS_MESSAGE_ID_KEYS = {
    "openMessageId",
    "open_message_id",
    "messageId",
    "message_id",
    "msgId",
    "msg_id",
    "openMsgId",
    "open_msg_id",
}
_DWS_OPEN_TASK_ID_KEYS = {
    "openTaskId",
    "open_task_id",
    "open_taskId",
}


def _sent_reply_send_result_payload(sent_reply: SentReply | None) -> dict[str, object]:
    if sent_reply is None or not sent_reply.send_result_json.strip():
        return {}
    try:
        payload = json.loads(sent_reply.send_result_json)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _find_string_value(payload: object, keys: set[str]) -> str:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys and isinstance(value, str) and value.strip():
                return value.strip()
        for value in payload.values():
            found = _find_string_value(value, keys)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = _find_string_value(item, keys)
            if found:
                return found
    return ""


def _sent_reply_recall_message_id(sent_reply: SentReply | None) -> str:
    return _find_string_value(
        _sent_reply_send_result_payload(sent_reply),
        _DWS_MESSAGE_ID_KEYS,
    )


def _sent_reply_open_task_id(sent_reply: SentReply | None) -> str:
    return _find_string_value(
        _sent_reply_send_result_payload(sent_reply),
        _DWS_OPEN_TASK_ID_KEYS,
    )


def _sent_reply_has_recall_target(sent_reply: SentReply | None) -> bool:
    if sent_reply is None:
        return False
    return bool(
        _sent_reply_recall_message_id(sent_reply)
        or _sent_reply_open_task_id(sent_reply)
        or sent_reply.recall_key.strip()
    )


def _recall_card(attempt: ReplyAttempt, sent_reply: SentReply | None) -> str:
    if sent_reply is None:
        return ""
    status = sent_reply.recall_status.strip().lower()
    status_html = ""
    if status == "recalled":
        recalled_at = _format_local_time(sent_reply.recalled_at or "")
        return (
            "<section class=\"card recall-card\"><h2>撤销发送</h2>"
            f"<p><span class=\"pill status-sent\">已撤销</span> "
            f"<span class=\"muted\">{escape(recalled_at)}</span></p></section>"
        )
    if status == "failed":
        status_html = (
            "<p><span class=\"pill status-failed\">上次撤销失败</span></p>"
            f"<pre class=\"mini-pre\">{escape(sent_reply.recall_error)}</pre>"
        )
    if not _sent_reply_has_recall_target(sent_reply):
        return status_html and (
            "<section class=\"card recall-card\"><h2>撤销发送</h2>"
            f"{status_html}"
            "<p class=\"muted\">没有可撤销消息 ID 或 key。</p></section>"
        )
    return (
        "<section class=\"card recall-card\"><h2>撤销发送</h2>"
        "<p class=\"muted\">撤回这条 attempt 已发送到钉钉的回复。</p>"
        f"{status_html}"
        f"<form method=\"post\" action=\"/attempts/{attempt.id}/recall\" "
        "onsubmit=\"return confirm('确认撤销这条已发送消息？')\">"
        "<button class=\"danger\" type=\"submit\">撤销发送</button>"
        "</form></section>"
    )


def _rerun_card(attempt: ReplyAttempt) -> str:
    return (
        "<section class=\"card compact-card rerun-card\">"
        "<h2>重跑 attempt</h2>"
        "<p class=\"muted\">用当前代码和 prompt 重新处理原 trigger。"
        "可能实际发送回复、处理日历或执行审批。</p>"
        f"<form method=\"post\" action=\"/attempts/{attempt.id}/rerun\" "
        "onsubmit=\"return confirm('确认重跑这条 attempt？可能会实际发送新回复或执行日历/OA动作。')\">"
        "<button type=\"submit\">重跑</button>"
        "</form></section>"
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
        reply_text = _reaction_display_text(attempt) or "No generated reply recorded."
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
        f"{_rerun_card(attempt)}"
        f"{_recall_card(attempt, sent_reply)}"
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
    actions = [] if calendar_only else [_send_status_action(attempt)]
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
                else _send_status_action(attempt)[0]
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


def _send_status_action(attempt: ReplyAttempt) -> tuple[str, str]:
    send_status = attempt.send_status
    if send_status.strip().lower() == "reacted":
        return "🙂 Reacted", send_status
    return f"💬 {_display_action_state(send_status)}", send_status


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


def _attempt_reply_line(attempt: ReplyAttempt) -> str:
    reaction = _reaction_display_text(attempt)
    if reaction:
        return (
            "<div class=\"attempt-line\">"
            "<span class=\"attempt-label\">答</span>"
            f"<span class=\"attempt-copy attempt-reaction-copy\">{escape(reaction)}</span>"
            "</div>"
        )
    return _attempt_text_line("答", _reply_preview_text(attempt), 320)


def _reply_preview_text(attempt: ReplyAttempt) -> str:
    text = attempt.final_reply_text or attempt.draft_reply_text
    if not text.strip():
        return _reaction_display_text(attempt)
    lines = text.splitlines()
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith(">")):
        lines.pop(0)
    preview = "\n".join(lines).strip()
    return preview or text


def _reaction_display_text(attempt: ReplyAttempt) -> str:
    if attempt.send_status.strip().lower() != "reacted":
        return ""
    summary = attempt.send_error.strip()
    if not summary or summary == "message_reaction":
        return ""
    values = []
    for part in summary.split(", "):
        kind, separator, value = part.partition(":")
        if separator and kind.strip().lower() in {"emoji", "text_emotion"}:
            value = value.strip()
            if value:
                values.append(value)
    return " ".join(values) if values else summary


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
