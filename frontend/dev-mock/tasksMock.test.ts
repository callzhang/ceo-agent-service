import { EventEmitter } from "node:events";
import type { IncomingMessage, ServerResponse } from "node:http";
import type { Connect, ViteDevServer } from "vite";
import { expect, it } from "vitest";
import type { BusinessTaskDetail, BusinessAttentionDetail, BusinessTaskSignal } from "../src/api/console";
import { tasksMock } from "./tasksMock";

it("serves project responsibilities and citations only through context", async () => {
  let middleware: Connect.NextHandleFunction | undefined;
  const server = { middlewares: { use: (handler: Connect.NextHandleFunction) => { middleware = handler; } } } as unknown as ViteDevServer;
  const configure = tasksMock().configureServer;
  if (typeof configure !== "function") throw new Error("missing configureServer hook");
  configure(server);
  if (!middleware) throw new Error("missing mock middleware");
  const request = Object.assign(new EventEmitter(), { url: "/api/console/tasks/projects/1", method: "GET", headers: {} }) as IncomingMessage;
  const responseJson = new Promise<{ status: number; body: string }>((resolve, reject) => {
    const response = { statusCode: 0, setHeader: () => {}, end(body: string) { resolve({ status: this.statusCode, body }); } } as unknown as ServerResponse;
    middleware!(request, response, () => reject(new Error("request was not handled")));
    request.emit("end");
  });
  const result = await responseJson;
  expect(result.status).toBe(200);
  const { item } = JSON.parse(result.body);
  expect(item).not.toHaveProperty("responsibilities");
  expect(item.evidence_signals[0]).not.toHaveProperty("evidence_text");
  expect(item.evidence_signals[0]).not.toHaveProperty("source_available");
  expect(item.context.responsibilities).toEqual([
    { person_user_id: "li", person_name: "李四", responsibility: "整理验收材料", evidence: [{ signal_id: 501, source_ref: "project-meeting-501", source_excerpt: "张三总负责交付与验收，李四负责材料，王五负责商务与回款。" }] },
    { person_user_id: "wang", person_name: "王五", responsibility: "商务与回款", evidence: [{ signal_id: 501, source_ref: "project-meeting-501", source_excerpt: "张三总负责交付与验收，李四负责材料，王五负责商务与回款。" }] },
  ]);
});

it.each(["items/1", "attention/1"])("%s mock uses references and serves the selected original separately", async (path) => {
  let middleware: Connect.NextHandleFunction | undefined;
  const configure = tasksMock().configureServer;
  if (typeof configure !== "function") throw new Error("missing configureServer");
  configure({ middlewares: { use(handler: Connect.NextHandleFunction) { middleware = handler; } } } as unknown as ViteDevServer);
  const request = <T,>(url: string) => new Promise<{ item: T }>((resolve, reject) => {
    const req = Object.assign(new EventEmitter(), { url, method: "GET", headers: {} }) as IncomingMessage;
    const res = { statusCode: 0, setHeader: () => {}, end(body: string) { if (this.statusCode !== 200) reject(new Error(body)); else resolve(JSON.parse(body)); } } as unknown as ServerResponse;
    middleware!(req, res, () => reject(new Error("not handled"))); req.emit("end");
  });
  const { item } = await request<BusinessTaskDetail | BusinessAttentionDetail>(`/api/console/tasks/${path}`);
  const reference = "evidence" in item ? item.evidence[0].signal! : item.evidence_signals[0];
  expect(reference).not.toHaveProperty("evidence_text");
  expect(reference).not.toHaveProperty("source_available");
  const selected = await request<BusinessTaskSignal>(`/api/console/tasks/signals/${reference.id}`);
  expect(selected.item.id).toBe(reference.id);
  expect(selected.item.source_ref).toBe(reference.source_ref);
  expect(selected.item.source_available).toBe(true);
  expect(selected.item.evidence_text).toBeTruthy();
});
