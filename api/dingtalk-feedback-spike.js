const EVENT_LIST_KEY = "feedback-spike-events";
const EVENT_KEY_PREFIX = "feedback-spike:";

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
      return req.body.slice(0, 2000);
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

async function kvCommand(command) {
  const url = process.env.KV_REST_API_URL;
  const token = process.env.KV_REST_API_TOKEN;
  if (!url || !token) {
    return { persisted: false, result: null };
  }
  const response = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(command),
  });
  if (!response.ok) {
    throw new Error(`KV command failed status=${response.status}`);
  }
  return { persisted: true, result: await response.json() };
}

async function persistEvent(event) {
  const eventJson = JSON.stringify(event);
  await kvCommand(["SET", event.key, eventJson, "EX", "604800"]);
  await kvCommand(["LPUSH", EVENT_LIST_KEY, eventJson]);
  await kvCommand(["LTRIM", EVENT_LIST_KEY, "0", "99"]);
  return Boolean(process.env.KV_REST_API_URL && process.env.KV_REST_API_TOKEN);
}

export default async function handler(req, res) {
  if (!["GET", "POST"].includes(req.method)) {
    res.setHeader("Allow", "GET, POST");
    return res.status(405).json({ ok: false, error: "method_not_allowed" });
  }

  const body = extractBody(req);
  const receivedAt = new Date().toISOString();
  const suffix = Math.random().toString(36).slice(2, 10);
  const event = {
    key: `${EVENT_KEY_PREFIX}${Date.now()}:${suffix}`,
    received_at: receivedAt,
    method: req.method,
    source: extractField(body, req.query, "source"),
    feedback_token: extractFeedbackToken(body, req.query),
    rating: extractField(body, req.query, "rating"),
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

  return res.status(200).json({
    ok: true,
    persisted,
    persist_error: persistError,
    feedback_token: event.feedback_token,
    rating: event.rating,
  });
}
