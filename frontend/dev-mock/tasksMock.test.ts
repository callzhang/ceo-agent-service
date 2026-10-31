import { EventEmitter } from "node:events";
import type { IncomingMessage, ServerResponse } from "node:http";
import type { Connect, ViteDevServer } from "vite";
import { expect, it } from "vitest";
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
