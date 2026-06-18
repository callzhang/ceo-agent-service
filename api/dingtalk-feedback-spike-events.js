import { list } from "@vercel/blob";

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

function parseLimit(value) {
  const raw = Array.isArray(value) ? value[0] : value;
  const parsed = Number.parseInt(raw || "20", 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return 20;
  }
  return Math.min(parsed, 100);
}

function queryValue(query, key) {
  if (!query || query[key] === undefined) {
    return "";
  }
  return Array.isArray(query[key]) ? query[key][0] : query[key];
}

function requestFeedbackToken(req) {
  return String(
    queryValue(req.query, "feedback_token") || queryValue(req.query, "feedbackToken")
  ).trim();
}

async function fetchEventBlob(blob) {
  const response = await fetch(blob.url);
  if (!response.ok) {
    return { key: blob.pathname, fetch_error: `status=${response.status}` };
  }
  return response.json();
}

function tokenPathSegment(value) {
  return encodeURIComponent(String(value || "").trim()).replaceAll("%", "_");
}

async function listEventBlobs(prefix, limit, token) {
  const blobList = await list({
    limit,
    mode: "expanded",
    prefix,
    token,
  });
  return [...blobList.blobs].sort(
    (left, right) =>
      new Date(right.uploadedAt).getTime() - new Date(left.uploadedAt).getTime()
  );
}

export default async function handler(req, res) {
  if (req.method !== "GET") {
    res.setHeader("Allow", "GET");
    return res.status(405).json({ ok: false, error: "method_not_allowed" });
  }
  const configuredSecret = process.env.FEEDBACK_SPIKE_SECRET || "";
  const feedbackToken = requestFeedbackToken(req);
  const hasValidSecret = configuredSecret && requestSecret(req) === configuredSecret;
  if (!hasValidSecret && !feedbackToken) {
    if (!configuredSecret) {
      return res.status(503).json({ ok: false, error: "secret_not_configured" });
    }
    return res.status(401).json({ ok: false, error: "unauthorized" });
  }

  const limit = parseLimit(req.query && req.query.limit);
  const token = process.env.BLOB_READ_WRITE_TOKEN;
  if (!token) {
    return res.status(503).json({ ok: false, error: "blob_not_configured" });
  }
  let sortedBlobs = [];
  if (feedbackToken) {
    sortedBlobs = await listEventBlobs(
      `${EVENT_LIST_KEY}/by-token/${tokenPathSegment(feedbackToken)}/`,
      limit,
      token,
    );
  }
  if (!feedbackToken || sortedBlobs.length === 0) {
    sortedBlobs = (
      await listEventBlobs(`${EVENT_LIST_KEY}/`, limit, token)
    ).filter((blob) => !blob.pathname.includes("/by-token/"));
  }
  const events = await Promise.all(sortedBlobs.slice(0, limit).map(fetchEventBlob));
  const filteredEvents = feedbackToken
    ? events.filter((event) => event && event.feedback_token === feedbackToken)
    : events;
  return res.status(200).json({
    ok: true,
    persisted: true,
    feedback_token: feedbackToken || undefined,
    events: filteredEvents,
  });
}
