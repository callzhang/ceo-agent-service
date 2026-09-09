import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  assertBottomCoverage,
  cleanupBrowserSession,
  commitScreenshotAfterValidation,
  preservePrimaryError,
} from "./scheduled-tasks-viewport-lib.mjs";

class FakeChild extends EventEmitter {
  constructor(onKill) {
    super();
    this.exitCode = null;
    this.signalCode = null;
    this.onKill = onKill;
    this.signals = [];
  }

  kill(signal) {
    this.signals.push(signal);
    this.onKill?.(signal, this);
    return true;
  }
}

const validGeometry = {
  innerWidth: 390,
  documentScrollWidth: 390,
  bodyScrollWidth: 390,
  description: { left: 12, right: 378, width: 366 },
  newButton: { left: 12, right: 378, width: 366 },
  workspace: { left: 12, right: 378, width: 366 },
  descriptionText: "配置 Cron、Agent Skills 与执行 Runtime。Connector 只提供连接能力。",
  pageOverflowX: "visible",
  colorScheme: "dark",
  routeClassName: "console-root scheduled-tasks-route",
};

test("cleanupBrowserSession escalates a stuck child and leaves no profile", async () => {
  const profile = mkdtempSync(join(tmpdir(), "scheduled-tasks-viewport-test-"));
  const child = new FakeChild((signal, process) => {
    if (signal === "SIGKILL") setTimeout(() => process.emit("close", null, "SIGKILL"), 5);
  });
  await cleanupBrowserSession(child, profile, { gracefulTimeoutMs: 5, forcedTimeoutMs: 50 });
  assert.deepEqual(child.signals, ["SIGTERM", "SIGKILL"]);
  assert.equal(existsSync(profile), false);
});

test("cleanupBrowserSession removes its profile only after Chrome closes", async () => {
  const profile = mkdtempSync(join(tmpdir(), "scheduled-tasks-viewport-test-"));
  let closed = false;
  const child = new FakeChild((signal, process) => {
    if (signal === "SIGTERM") setTimeout(() => {
      closed = true;
      process.emit("close", null, "SIGTERM");
    }, 5);
  });
  await cleanupBrowserSession(child, profile, { gracefulTimeoutMs: 50, forcedTimeoutMs: 50 });
  assert.equal(closed, true);
  assert.equal(existsSync(profile), false);
  assert.deepEqual(child.signals, ["SIGTERM"]);
});

test("failed validation leaves existing screenshot intact and no temp artifact", () => {
  const directory = mkdtempSync(join(tmpdir(), "scheduled-tasks-screenshot-test-"));
  const target = join(directory, "evidence.png");
  try {
    writeFileSync(target, "valid-evidence");
    assert.throws(() => commitScreenshotAfterValidation({
      geometry: { ...validGeometry, workspace: { left: 12, right: 410, width: 398 } },
      screenshotData: Buffer.from("invalid-new-image"),
      screenshotPath: target,
      viewportWidth: 390,
    }), /Workspace exceeds/);
    assert.equal(readFileSync(target, "utf8"), "valid-evidence");
    assert.deepEqual(readdirSync(directory), ["evidence.png"]);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("valid screenshot replaces evidence atomically after validation", () => {
  const directory = mkdtempSync(join(tmpdir(), "scheduled-tasks-screenshot-test-"));
  const target = join(directory, "evidence.png");
  try {
    writeFileSync(target, "old-evidence");
    commitScreenshotAfterValidation({
      geometry: validGeometry,
      screenshotData: Buffer.from("new-evidence"),
      screenshotPath: target,
      viewportWidth: 390,
    });
    assert.equal(readFileSync(target, "utf8"), "new-evidence");
    assert.deepEqual(readdirSync(directory), ["evidence.png"]);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("cleanup failures never replace the original assertion error", () => {
  const assertionError = new Error("viewport assertion failed");
  const cleanupError = new Error("profile cleanup failed");
  assert.equal(preservePrimaryError(assertionError, cleanupError), assertionError);
  assert.equal(preservePrimaryError(null, cleanupError), cleanupError);
});

test("long editor must keep the dark route canvas under the bottom viewport", () => {
  assert.doesNotThrow(() => assertBottomCoverage({
    innerHeight: 844,
    documentScrollHeight: 1420,
    scrollY: 576,
    routeDocumentBottom: 1420,
    bottomBackground: "rgb(17, 20, 17)",
  }));
  assert.throws(() => assertBottomCoverage({
    innerHeight: 844,
    documentScrollHeight: 1420,
    scrollY: 576,
    routeDocumentBottom: 844,
    bottomBackground: "rgb(246, 246, 243)",
  }), /does not cover the document bottom/);
});
