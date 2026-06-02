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

async function fetchEventBlob(blob) {
  const response = await fetch(blob.url);
  if (!response.ok) {
    return { key: blob.pathname, fetch_error: `status=${response.status}` };
  }
  return response.json();
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
  const token = process.env.BLOB_READ_WRITE_TOKEN;
  if (!token) {
    return res.status(503).json({ ok: false, error: "blob_not_configured" });
  }
  const blobList = await list({
    limit,
    mode: "expanded",
    prefix: `${EVENT_LIST_KEY}/`,
    token,
  });
  const sortedBlobs = [...blobList.blobs].sort(
    (left, right) =>
      new Date(right.uploadedAt).getTime() - new Date(left.uploadedAt).getTime()
  );
  const events = await Promise.all(sortedBlobs.slice(0, limit).map(fetchEventBlob));
  return res.status(200).json({ ok: true, persisted: true, events });
}
