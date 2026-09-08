import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const VIEWPORT = { width: 390, height: 844 };
const args = process.argv.slice(2);

function option(name, fallback = "") {
  const index = args.indexOf(name);
  return index === -1 ? fallback : args[index + 1];
}

const url = option("--url");
const screenshotPath = option("--screenshot");
if (!url) {
  throw new Error("Usage: node scripts/verify-scheduled-tasks-viewport.mjs --url <url> [--screenshot <path>]");
}

const chromeCandidates = [
  process.env.CHROME_PATH,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
].filter(Boolean);
const chromePath = chromeCandidates.find((candidate) => existsSync(candidate));
if (!chromePath) throw new Error("Chrome not found. Set CHROME_PATH to a Chromium executable.");

const profileDirectory = mkdtempSync(join(tmpdir(), "scheduled-tasks-viewport-"));
const chrome = spawn(chromePath, [
  "--headless",
  "--disable-gpu",
  "--hide-scrollbars",
  "--remote-debugging-port=0",
  `--user-data-dir=${profileDirectory}`,
  `--window-size=${VIEWPORT.width},${VIEWPORT.height}`,
  "about:blank",
], { stdio: ["ignore", "ignore", "pipe"] });

function waitForDevtoolsUrl() {
  return new Promise((resolveUrl, reject) => {
    let stderr = "";
    const timeout = setTimeout(() => reject(new Error(`Chrome DevTools did not start.\n${stderr}`)), 10_000);
    chrome.stderr.setEncoding("utf8");
    chrome.stderr.on("data", (chunk) => {
      stderr += chunk;
      const match = stderr.match(/DevTools listening on (ws:\/\/[^\s]+)/);
      if (!match) return;
      clearTimeout(timeout);
      resolveUrl(match[1]);
    });
    chrome.once("exit", (code) => {
      clearTimeout(timeout);
      reject(new Error(`Chrome exited before DevTools connected (${code}).\n${stderr}`));
    });
  });
}

class CdpClient {
  constructor(webSocketUrl) {
    this.nextId = 1;
    this.pending = new Map();
    this.socket = new WebSocket(webSocketUrl);
  }

  async connect() {
    await new Promise((resolveOpen, reject) => {
      this.socket.addEventListener("open", resolveOpen, { once: true });
      this.socket.addEventListener("error", reject, { once: true });
    });
    this.socket.addEventListener("message", ({ data }) => {
      const message = JSON.parse(data);
      if (!message.id) return;
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(message.error.message));
      else pending.resolve(message.result);
    });
  }

  call(method, params = {}) {
    const id = this.nextId++;
    const response = new Promise((resolveCall, reject) => this.pending.set(id, { resolve: resolveCall, reject }));
    this.socket.send(JSON.stringify({ id, method, params }));
    return response;
  }

  close() {
    this.socket.close();
  }
}

const pause = (milliseconds) => new Promise((resolvePause) => setTimeout(resolvePause, milliseconds));

async function pageTarget(debugPort) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    const targets = await fetch(`http://127.0.0.1:${debugPort}/json/list`).then((response) => response.json());
    const target = targets.find((candidate) => candidate.type === "page");
    if (target) return target;
    await pause(100);
  }
  throw new Error("Chrome did not expose a page target.");
}

function assertInsideViewport(name, rect, minimumInset = 0) {
  if (rect.left < minimumInset || rect.right > VIEWPORT.width - minimumInset) {
    throw new Error(`${name} exceeds the viewport: ${JSON.stringify(rect)}`);
  }
}

let client;
try {
  const browserUrl = await waitForDevtoolsUrl();
  const debugPort = new URL(browserUrl).port;
  const target = await pageTarget(debugPort);
  client = new CdpClient(target.webSocketDebuggerUrl);
  await client.connect();
  await client.call("Page.enable");
  await client.call("Runtime.enable");
  await client.call("Emulation.setDeviceMetricsOverride", {
    width: VIEWPORT.width,
    height: VIEWPORT.height,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await client.call("Emulation.setEmulatedMedia", {
    media: "screen",
    features: [{ name: "prefers-color-scheme", value: "dark" }],
  });
  await client.call("Page.navigate", { url });

  for (let attempt = 0; attempt < 100; attempt += 1) {
    const ready = await client.call("Runtime.evaluate", {
      expression: "document.readyState === 'complete' && Boolean(document.querySelector('.scheduled-task-workspace'))",
      returnByValue: true,
    });
    if (ready.result.value) break;
    if (attempt === 99) throw new Error("Scheduled Tasks page did not become ready.");
    await pause(100);
  }

  const result = await client.call("Runtime.evaluate", {
    expression: `(() => {
      const box = (element) => {
        const rect = element.getBoundingClientRect();
        return { left: rect.left, right: rect.right, width: rect.width };
      };
      const elements = {
        description: document.querySelector('.scheduled-tasks-page .console-page-header .muted'),
        newButton: document.querySelector('.scheduled-tasks-page .console-page-header > .primary-button'),
        workspace: document.querySelector('.scheduled-task-workspace'),
      };
      const offenders = [...document.querySelectorAll('body *')]
        .map((element) => ({
          element: element.tagName.toLowerCase() + (element.className ? '.' + String(element.className).trim().replace(/\\s+/g, '.') : ''),
          ...box(element),
        }))
        .filter((entry) => entry.left < -0.5 || entry.right > innerWidth + 0.5)
        .slice(0, 20);
      return {
        innerWidth,
        documentScrollWidth: document.documentElement.scrollWidth,
        bodyScrollWidth: document.body.scrollWidth,
        description: box(elements.description),
        newButton: box(elements.newButton),
        workspace: box(elements.workspace),
        descriptionText: elements.description.textContent,
        pageOverflowX: getComputedStyle(document.querySelector('.scheduled-tasks-page')).overflowX,
        offenders,
      };
    })()`,
    returnByValue: true,
  });
  const geometry = result.result.value;

  if (screenshotPath) {
    const screenshot = await client.call("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
    writeFileSync(resolve(screenshotPath), Buffer.from(screenshot.data, "base64"));
  }

  const expectedDescription = "配置 Cron、Agent Skills 与执行 Runtime。Connector 只提供连接能力。";
  if (geometry.descriptionText !== expectedDescription) {
    throw new Error(`Header description changed: ${JSON.stringify(geometry.descriptionText)}`);
  }
  if (geometry.pageOverflowX === "clip" || geometry.pageOverflowX === "hidden") {
    throw new Error(`Page hides horizontal overflow instead of fitting its content: ${JSON.stringify(geometry)}`);
  }
  if (geometry.documentScrollWidth > geometry.innerWidth || geometry.bodyScrollWidth > geometry.innerWidth) {
    throw new Error(`Page scrolls horizontally: ${JSON.stringify(geometry)}`);
  }
  assertInsideViewport("Header description", geometry.description, 12);
  assertInsideViewport("New task button", geometry.newButton, 12);
  assertInsideViewport("Workspace", geometry.workspace, 12);
  process.stdout.write(`${JSON.stringify(geometry, null, 2)}\n`);
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
} finally {
  client?.close();
  chrome.kill("SIGTERM");
  rmSync(profileDirectory, { recursive: true, force: true });
}
