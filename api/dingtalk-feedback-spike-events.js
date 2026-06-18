import {
  EVENT_LIST_KEY,
  listFeedbackEventPaths,
  readFeedbackEvent,
  storageConfigError,
  tokenPathSegment,
} from "./feedback-storage.js";

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

async function fetchEventPath(path) {
  try {
    return await readFeedbackEvent(path);
  } catch (error) {
    return {
      key: path,
      fetch_error: error instanceof Error ? error.message : String(error),
    };
  }
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

  const configError = storageConfigError();
  if (configError) {
    return res.status(503).json({ ok: false, error: configError });
  }
  const limit = parseLimit(req.query && req.query.limit);
  let eventPaths = [];
  if (feedbackToken) {
    eventPaths = await listFeedbackEventPaths(
      `${EVENT_LIST_KEY}/by-token/${tokenPathSegment(feedbackToken)}/`,
      limit,
    );
  }
  if (!feedbackToken || eventPaths.length === 0) {
    eventPaths = (
      await listFeedbackEventPaths(`${EVENT_LIST_KEY}/`, limit)
    ).filter((path) => !path.includes("/by-token/"));
  }
  const events = await Promise.all(eventPaths.slice(0, limit).map(fetchEventPath));
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
