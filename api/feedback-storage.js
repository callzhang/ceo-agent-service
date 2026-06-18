import { get, list, put } from "@tigrisdata/storage";

const REQUIRED_TIGRIS_ENV = [
  "TIGRIS_STORAGE_ACCESS_KEY_ID",
  "TIGRIS_STORAGE_SECRET_ACCESS_KEY",
  "TIGRIS_STORAGE_BUCKET",
];

export const EVENT_LIST_KEY = "feedback-spike-events";

export function tokenPathSegment(value) {
  return encodeURIComponent(String(value || "").trim()).replaceAll("%", "_");
}

export function storageConfigError() {
  const missing = REQUIRED_TIGRIS_ENV.filter((key) => !process.env[key]);
  if (missing.length === 0) {
    return "";
  }
  return `tigris_not_configured:${missing.join(",")}`;
}

function throwIfStorageError(result, action) {
  if (result && result.error) {
    throw new Error(`Tigris ${action} failed: ${result.error.message}`);
  }
  return result.data;
}

export async function persistFeedbackEvent(event) {
  const configError = storageConfigError();
  if (configError) {
    return { persisted: false, error: configError };
  }

  const payload = JSON.stringify(event);
  const options = {
    allowOverwrite: true,
    contentType: "application/json",
  };
  throwIfStorageError(
    await put(`${EVENT_LIST_KEY}/${event.key}.json`, payload, options),
    "put",
  );
  if (event.feedback_token) {
    throwIfStorageError(
      await put(
        `${EVENT_LIST_KEY}/by-token/${tokenPathSegment(event.feedback_token)}/${event.key}.json`,
        payload,
        options,
      ),
      "put",
    );
  }
  return { persisted: true, error: "" };
}

export async function listFeedbackEventPaths(prefix, limit) {
  const configError = storageConfigError();
  if (configError) {
    throw new Error(configError);
  }
  const data = throwIfStorageError(
    await list({
      limit,
      prefix,
    }),
    "list",
  );
  return [...(data.items || [])]
    .sort(
      (left, right) =>
        new Date(right.lastModified).getTime() - new Date(left.lastModified).getTime(),
    )
    .map((item) => item.name);
}

export async function readFeedbackEvent(path) {
  const data = throwIfStorageError(await get(path, "string"), "get");
  return JSON.parse(data);
}
