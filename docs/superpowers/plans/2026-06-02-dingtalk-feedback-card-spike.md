# DingTalk Feedback Link Spike Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a non-production spike that can send one DingTalk feedback message and capture up/down link clicks through Vercel.

**Architecture:** Keep the production reply path unchanged. Add a local Python spike helper that builds a tokenized text message with two feedback links and sends it through the existing current-user `dws chat message send` path, plus small Vercel API endpoints that store and list callback events. Do not use robot cards for this spike, because each group would need the robot installed first and private chats require a different card path.

**Tech Stack:** Python CLI, pytest, DWS CLI, Vercel serverless functions, Vercel KV REST API.

---

### Task 1: Local Spike Payload And Sender

**Files:**
- Create: `app/feedback_spike.py`
- Modify: `app/cli.py`
- Test: `tests/test_feedback_spike.py`

- [ ] Add pure helpers for token generation, callback URLs, feedback-link text, and DWS command construction.
- [ ] Add `ceo-agent feedback-spike send-links` with exactly one DingTalk target arg.
- [ ] Add `ceo-agent feedback-spike events-url` for local verification of the Vercel diagnostic URL.
- [ ] Test helper output without sending live DingTalk messages.

### Task 2: Vercel Callback Endpoints

**Files:**
- Create: `api/dingtalk-feedback-spike.js`
- Create: `api/dingtalk-feedback-spike-events.js`
- Create: `tests/test_feedback_spike_api.py`

- [ ] Add a callback endpoint that accepts GET and POST, records only minimal safe data, and works without KV for local smoke checks.
- [ ] Add a diagnostic events endpoint protected by `FEEDBACK_SPIKE_SECRET`.
- [ ] Test the JavaScript endpoint source for required methods, secret handling, header redaction, and KV key shape.

### Task 3: Documentation And Verification

**Files:**
- Modify: `docs/superpowers/specs/2026-06-02-dingtalk-feedback-card-spike-design.md`

- [ ] Add exact spike run commands and required environment variables.
- [ ] Run focused tests for the new helper and endpoint source.
- [ ] Run the repository tests that cover CLI parsing.
- [ ] Commit the spike separately from the already committed design spec.
