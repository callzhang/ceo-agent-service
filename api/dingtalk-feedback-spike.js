import { put } from "@vercel/blob";

const EVENT_LIST_KEY = "feedback-spike-events";
const EVENT_KEY_PREFIX = "feedback-spike:";
const RATING_OPTIONS = [
  { value: "very_unhelpful", label: "特别没用" },
  { value: "not_useful", label: "不太有用" },
  { value: "neutral", label: "一般" },
  { value: "useful", label: "很有用" },
  { value: "very_useful", label: "非常有用" },
];
const QUICK_RATING_DEFAULTS = {
  up: "useful",
  down: "not_useful",
};

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function safeHeaders(headers) {
  const allowed = new Set([
    "content-type",
    "user-agent",
    "x-forwarded-for",
    "x-vercel-id",
    "x-dingtalk-signature",
    "x-dingtalk-timestamp",
  ]);
  const output = {};
  for (const [key, value] of Object.entries(headers || {})) {
    const normalized = key.toLowerCase();
    if (allowed.has(normalized)) {
      output[normalized] = String(value).slice(0, 500);
    }
  }
  return output;
}

function extractBody(req) {
  if (req.body === undefined || req.body === null) {
    return null;
  }
  if (typeof req.body === "string") {
    try {
      return JSON.parse(req.body);
    } catch {
      const parsed = Object.fromEntries(new URLSearchParams(req.body.slice(0, 10000)));
      return Object.keys(parsed).length ? parsed : req.body.slice(0, 2000);
    }
  }
  return req.body;
}

function extractField(body, query, key) {
  if (query && query[key] !== undefined) {
    return Array.isArray(query[key]) ? query[key][0] : query[key];
  }
  if (body && typeof body === "object" && body[key] !== undefined) {
    return body[key];
  }
  if (body && typeof body === "object" && body.value && body.value[key] !== undefined) {
    return body.value[key];
  }
  if (body && typeof body === "object" && body.data && body.data[key] !== undefined) {
    return body.data[key];
  }
  return "";
}

function extractFeedbackToken(body, query) {
  return extractField(body, query, "feedback_token") || extractField(body, query, "feedbackToken");
}

function normalizeRating(value) {
  const raw = String(value || "").trim();
  if (QUICK_RATING_DEFAULTS[raw]) {
    return QUICK_RATING_DEFAULTS[raw];
  }
  if (RATING_OPTIONS.some((option) => option.value === raw)) {
    return raw;
  }
  return "neutral";
}

function ratingLabel(value) {
  const match = RATING_OPTIONS.find((option) => option.value === value);
  return match ? match.label : "一般";
}

function formAction(req) {
  const host = req.headers && (req.headers["x-forwarded-host"] || req.headers.host);
  const protocol = req.headers && req.headers["x-forwarded-proto"];
  if (host) {
    return `${protocol || "https"}://${host}/api/dingtalk-feedback-spike`;
  }
  return "/api/dingtalk-feedback-spike";
}

function feedbackContext(req, body) {
  const rating = normalizeRating(extractField(body, req.query, "rating"));
  return {
    source: String(extractField(body, req.query, "source") || ""),
    feedback_token: String(extractFeedbackToken(body, req.query) || ""),
    rating,
    original_text: String(extractField(body, req.query, "original_text") || ""),
    reply_text: String(extractField(body, req.query, "reply_text") || ""),
    comment: String(extractField(body, req.query, "comment") || "").slice(0, 2000),
  };
}

function renderRatingOptions(selected) {
  return RATING_OPTIONS.map((option) => {
    const checked = option.value === selected ? "checked" : "";
    return `
      <label class="rating-option">
        <input type="radio" name="rating" value="${option.value}" ${checked} />
        <span>${escapeHtml(option.label)}</span>
      </label>
    `;
  }).join("");
}

function renderContextBlock(title, text, emptyText) {
  const content = text.trim() ? escapeHtml(text) : escapeHtml(emptyText);
  const emptyClass = text.trim() ? "" : " muted";
  return `
    <section class="context-block">
      <div class="context-title">${escapeHtml(title)}</div>
      <div class="context-text${emptyClass}">${content}</div>
    </section>
  `;
}

function renderFeedbackPage(req, context) {
  const action = formAction(req);
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>反馈这条回复</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f7fb;
      --card: #ffffff;
      --text: #19212e;
      --muted: #697386;
      --line: #dde4ee;
      --accent: #2563eb;
      --accent-soft: #e8f0ff;
      --shadow: 0 18px 45px rgba(31, 42, 68, 0.12);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        linear-gradient(180deg, #eef4ff 0%, var(--bg) 42%),
        var(--bg);
      color: var(--text);
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 28px 16px;
    }
    main {
      width: min(760px, 100%);
      background: var(--card);
      border: 1px solid rgba(221, 228, 238, 0.8);
      border-radius: 18px;
      box-shadow: var(--shadow);
      padding: 28px;
    }
    header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 22px;
    }
    h1 {
      margin: 0;
      font-size: 24px;
      line-height: 1.25;
      letter-spacing: 0;
    }
    .sub {
      margin-top: 8px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.5;
    }
    .badge {
      flex: none;
      padding: 7px 10px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 13px;
      font-weight: 650;
    }
    .context-grid {
      display: grid;
      gap: 14px;
      margin: 20px 0 22px;
    }
    .context-block {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 14px;
      background: #fbfcff;
    }
    .context-title {
      color: var(--muted);
      font-size: 13px;
      font-weight: 650;
      margin-bottom: 8px;
    }
    .context-text {
      white-space: pre-wrap;
      line-height: 1.58;
      font-size: 15px;
    }
    .muted { color: var(--muted); }
    .rating-label {
      display: block;
      font-size: 14px;
      font-weight: 700;
      margin-bottom: 10px;
    }
    .rating-row {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 8px;
      margin-bottom: 18px;
    }
    .rating-option input { position: absolute; opacity: 0; pointer-events: none; }
    .rating-option span {
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 42px;
      padding: 8px 10px;
      border-radius: 10px;
      border: 1px solid var(--line);
      color: #344054;
      background: #fff;
      font-size: 14px;
      cursor: pointer;
      transition: border-color 120ms ease, background 120ms ease, color 120ms ease;
    }
    .rating-option input:checked + span {
      border-color: var(--accent);
      background: var(--accent-soft);
      color: var(--accent);
      font-weight: 700;
    }
    textarea {
      width: 100%;
      min-height: 96px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px 13px;
      font: inherit;
      line-height: 1.5;
      outline: none;
      margin-bottom: 18px;
    }
    textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
    .actions { display: flex; justify-content: flex-end; }
    button {
      appearance: none;
      border: 0;
      border-radius: 10px;
      background: var(--accent);
      color: white;
      font-weight: 700;
      font-size: 15px;
      padding: 11px 18px;
      cursor: pointer;
    }
    @media (max-width: 640px) {
      main { padding: 22px; border-radius: 14px; }
      header { display: block; }
      .badge { display: inline-flex; margin-top: 14px; }
      .rating-row { grid-template-columns: 1fr; }
      .rating-option span { justify-content: flex-start; }
      .actions { display: block; }
      button { width: 100%; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>这条回复有帮助吗？</h1>
        <div class="sub">你的反馈会帮助改进自动回复质量。</div>
      </div>
      <div class="badge">${escapeHtml(ratingLabel(context.rating))}</div>
    </header>
    <form method="post" action="${escapeHtml(action)}">
      <input type="hidden" name="source" value="${escapeHtml(context.source)}" />
      <input type="hidden" name="feedback_token" value="${escapeHtml(context.feedback_token)}" />
      <input type="hidden" name="original_text" value="${escapeHtml(context.original_text)}" />
      <input type="hidden" name="reply_text" value="${escapeHtml(context.reply_text)}" />
      <div class="context-grid">
        ${renderContextBlock("原话", context.original_text, "这条反馈链接没有携带原话。")}
        ${renderContextBlock("回复样例", context.reply_text, "这条反馈链接没有携带回复样例。")}
      </div>
      <label class="rating-label">评分</label>
      <div class="rating-row">${renderRatingOptions(context.rating)}</div>
      <label class="rating-label" for="comment">评语（可选）</label>
      <textarea id="comment" name="comment" maxlength="2000" placeholder="可以补充哪里没答好、哪里有帮助。">${escapeHtml(context.comment)}</textarea>
      <div class="actions"><button type="submit">提交反馈</button></div>
    </form>
  </main>
</body>
</html>`;
}

function renderSubmittedPage(context, persisted, persistError) {
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>反馈已提交</title>
  <style>
    body {
      margin: 0;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
      background: #f5f7fb;
      color: #19212e;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    main {
      width: min(560px, 100%);
      background: #fff;
      border: 1px solid #dde4ee;
      border-radius: 18px;
      box-shadow: 0 18px 45px rgba(31, 42, 68, 0.12);
      padding: 30px;
      text-align: center;
    }
    .mark {
      width: 48px;
      height: 48px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 50%;
      background: #e8f0ff;
      color: #2563eb;
      font-size: 26px;
      font-weight: 800;
      margin-bottom: 14px;
    }
    h1 { margin: 0 0 10px; font-size: 24px; letter-spacing: 0; }
    p { margin: 0; color: #697386; line-height: 1.6; }
    .meta {
      margin-top: 18px;
      padding: 12px;
      border-radius: 12px;
      background: #fbfcff;
      color: #344054;
      font-size: 14px;
    }
  </style>
</head>
<body>
  <main>
    <div class="mark">✓</div>
    <h1>反馈已提交</h1>
    <p>感谢反馈。评分：${escapeHtml(ratingLabel(context.rating))}</p>
    <div class="meta">${persisted ? "已记录" : `暂未写入存储：${escapeHtml(persistError || "存储未配置")}`}</div>
  </main>
</body>
</html>`;
}

async function persistEvent(event) {
  const token = process.env.BLOB_READ_WRITE_TOKEN;
  if (!token) {
    return false;
  }
  await put(`${EVENT_LIST_KEY}/${event.key}.json`, JSON.stringify(event), {
    access: "public",
    allowOverwrite: true,
    contentType: "application/json",
    token,
  });
  return true;
}

function wantsJson(req) {
  const format = req.query && (Array.isArray(req.query.format) ? req.query.format[0] : req.query.format);
  return format === "json" || (req.headers && String(req.headers.accept || "").includes("application/json"));
}

export default async function handler(req, res) {
  if (!["GET", "POST"].includes(req.method)) {
    res.setHeader("Allow", "GET, POST");
    return res.status(405).json({ ok: false, error: "method_not_allowed" });
  }

  const body = extractBody(req);
  const context = feedbackContext(req, body);

  if (req.method === "GET" && !wantsJson(req)) {
    res.setHeader("Content-Type", "text/html; charset=utf-8");
    return res.status(200).send(renderFeedbackPage(req, context));
  }

  const receivedAt = new Date().toISOString();
  const suffix = Math.random().toString(36).slice(2, 10);
  const event = {
    key: `${EVENT_KEY_PREFIX}${Date.now()}:${suffix}`,
    received_at: receivedAt,
    method: req.method,
    source: context.source,
    feedback_token: context.feedback_token,
    rating: context.rating,
    rating_label: ratingLabel(context.rating),
    original_text: context.original_text,
    reply_text: context.reply_text,
    comment: context.comment,
    query: req.query || {},
    body,
    headers: safeHeaders(req.headers),
  };

  let persisted = false;
  let persistError = "";
  try {
    persisted = await persistEvent(event);
  } catch (error) {
    persistError = error instanceof Error ? error.message : String(error);
  }

  if (wantsJson(req)) {
    return res.status(200).json({
      ok: true,
      persisted,
      persist_error: persistError,
      feedback_token: event.feedback_token,
      rating: event.rating,
      rating_label: event.rating_label,
    });
  }

  res.setHeader("Content-Type", "text/html; charset=utf-8");
  return res.status(200).send(renderSubmittedPage(context, persisted, persistError));
}
