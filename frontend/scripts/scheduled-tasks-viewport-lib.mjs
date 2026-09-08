import { randomUUID } from "node:crypto";
import { renameSync, rmSync, writeFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";

const EXPECTED_DESCRIPTION = "配置 Cron、Agent Skills 与执行 Runtime。Connector 只提供连接能力。";

function waitForClose(child, timeoutMs) {
  return new Promise((resolveWait) => {
    let settled = false;
    const finish = (closed) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      child.removeListener("close", onClose);
      resolveWait(closed);
    };
    const onClose = () => finish(true);
    const timer = setTimeout(() => finish(false), timeoutMs);
    child.once("close", onClose);
  });
}

export async function stopChildProcess(child, {
  gracefulTimeoutMs = 2_000,
  forcedTimeoutMs = 2_000,
} = {}) {
  if (child.exitCode !== null || child.signalCode !== null) return;

  const gracefulClose = waitForClose(child, gracefulTimeoutMs);
  child.kill("SIGTERM");
  if (await gracefulClose) return;

  const forcedClose = waitForClose(child, forcedTimeoutMs);
  child.kill("SIGKILL");
  if (!await forcedClose) throw new Error("Chrome did not close after SIGKILL.");
}

export async function cleanupBrowserSession(child, profileDirectory, options) {
  await stopChildProcess(child, options);
  rmSync(profileDirectory, { recursive: true, force: true });
}

function assertInsideViewport(name, rect, viewportWidth, minimumInset = 0) {
  if (rect.left < minimumInset || rect.right > viewportWidth - minimumInset) {
    throw new Error(`${name} exceeds the viewport: ${JSON.stringify(rect)}`);
  }
}

export function assertScheduledTasksGeometry(geometry, viewportWidth) {
  if (geometry.descriptionText !== EXPECTED_DESCRIPTION) {
    throw new Error(`Header description changed: ${JSON.stringify(geometry.descriptionText)}`);
  }
  if (!geometry.routeClassName.split(/\s+/).includes("scheduled-tasks-route")) {
    throw new Error(`Scheduled Tasks route theme is missing: ${JSON.stringify(geometry.routeClassName)}`);
  }
  if (geometry.colorScheme !== "dark") {
    throw new Error(`Scheduled Tasks route did not compute a dark color scheme: ${JSON.stringify(geometry.colorScheme)}`);
  }
  if (geometry.pageOverflowX === "clip" || geometry.pageOverflowX === "hidden") {
    throw new Error(`Page hides horizontal overflow instead of fitting its content: ${JSON.stringify(geometry)}`);
  }
  if (geometry.documentScrollWidth > geometry.innerWidth || geometry.bodyScrollWidth > geometry.innerWidth) {
    throw new Error(`Page scrolls horizontally: ${JSON.stringify(geometry)}`);
  }
  assertInsideViewport("Header description", geometry.description, viewportWidth, 12);
  assertInsideViewport("New task button", geometry.newButton, viewportWidth, 12);
  assertInsideViewport("Workspace", geometry.workspace, viewportWidth, 12);
}

function isDarkRgb(color) {
  const channels = color.match(/[\d.]+/g)?.slice(0, 3).map(Number);
  if (!channels || channels.length !== 3) return false;
  const perceivedLightness = (channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722) / 255;
  return perceivedLightness < 0.35;
}

export function assertBottomCoverage(geometry) {
  if (geometry.documentScrollHeight <= geometry.innerHeight) {
    throw new Error(`New-task editor did not produce a long page: ${JSON.stringify(geometry)}`);
  }
  if (geometry.routeDocumentBottom < geometry.documentScrollHeight - 1) {
    throw new Error(`Scheduled Tasks route does not cover the document bottom: ${JSON.stringify(geometry)}`);
  }
  if (geometry.scrollY + geometry.innerHeight < geometry.documentScrollHeight - 1) {
    throw new Error(`Browser did not reach the document bottom: ${JSON.stringify(geometry)}`);
  }
  if (geometry.documentScrollWidth > geometry.innerWidth || geometry.bodyScrollWidth > geometry.innerWidth) {
    throw new Error(`Long editor scrolls horizontally: ${JSON.stringify(geometry)}`);
  }
  if (!isDarkRgb(geometry.bottomBackground)) {
    throw new Error(`Bottom viewport exposed a non-dark canvas: ${JSON.stringify(geometry)}`);
  }
}

export function commitScreenshotAfterValidation({
  geometry,
  screenshotData,
  screenshotPath,
  viewportWidth,
}) {
  assertScheduledTasksGeometry(geometry, viewportWidth);
  if (!screenshotPath) return;

  writeScreenshotAtomically(screenshotPath, screenshotData);
}

export function writeScreenshotAtomically(screenshotPath, screenshotData) {
  const target = resolve(screenshotPath);
  const temporary = join(dirname(target), `.${basename(target)}.${process.pid}.${randomUUID()}.tmp`);
  try {
    writeFileSync(temporary, screenshotData, { flag: "wx" });
    renameSync(temporary, target);
  } finally {
    rmSync(temporary, { force: true });
  }
}

export function preservePrimaryError(primaryError, cleanupError) {
  return primaryError || cleanupError;
}
