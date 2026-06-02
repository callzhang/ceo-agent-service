const EVENT_LIST_KEY = "feedback-spike-events";

function requestSecret(req) {
  if (req.headers && req.headers["x-feedback-spike-secret"]) {
    return String(req.headers["x-feedback-spike-secret"]);
  }
  if (req.query && req.query.secret) {
    return Array.isArray(req.query.secret) ? req.query.secret[0] : req.query.secret;
  }
  return "";
}

async function kvCommand(command) {
  const url = process.env.KV_REST_API_URL;
  const token = process.env.KV_REST_API_READ_ONLY_TOKEN || process.env.KV_REST_API_TOKEN;
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

function parseLimit(value) {
  const raw = Array.isArray(value) ? value[0] : value;
  const parsed = Number.parseInt(raw || "20", 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return 20;
  }
  return Math.min(parsed, 100);
}

export default async function handler(req, res) {
  if (req.method !== "GET") {
    res.setHeader("Allow", "GET");
    return res.status(405).json({ ok: false, error: "method_not_allowed" });
  }
  const configuredSecret = process.env.FEEDBACK_SPIKE_SECRET || "";
  if (!configuredSecret) {
    return res.status(503).json({ ok: false, error: "secret_not_configured" });
  }
  if (requestSecret(req) !== configuredSecret) {
    return res.status(401).json({ ok: false, error: "unauthorized" });
  }

  const limit = parseLimit(req.query && req.query.limit);
  const kv = await kvCommand(["LRANGE", EVENT_LIST_KEY, "0", String(limit - 1)]);
  const rawEvents = kv.result && Array.isArray(kv.result.result) ? kv.result.result : [];
  const events = rawEvents.map((value) => {
    try {
      return JSON.parse(value);
    } catch {
      return { raw: value };
    }
  });
  return res.status(200).json({ ok: true, persisted: kv.persisted, events });
}
