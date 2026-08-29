import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getSettings = vi.hoisted(() => vi.fn());
const saveSettings = vi.hoisted(() => vi.fn());
const getStatus = vi.hoisted(() => vi.fn());
const listAttention = vi.hoisted(() => vi.fn());
const listWechat = vi.hoisted(() => vi.fn());
const listWechatTargets = vi.hoisted(() => vi.fn());
const saveWechatReplyScope = vi.hoisted(() => vi.fn());

vi.mock("../api/console", () => ({ getSettings, saveSettings, getStatus, listAttention, listWechat, listWechatTargets, saveWechatReplyScope, displayValue: (value: unknown) => typeof value === "string" ? value || "未提供" : JSON.stringify(value) || "未提供" }));

import { SettingsPage } from "./SettingsPage";

function renderSettings(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><Routes><Route path="/settings" element={<SettingsPage />} /></Routes></MemoryRouter>);
}

describe("SettingsPage", () => {
  beforeEach(() => {
    listWechat.mockResolvedValue({ items: [], meta: { page: 1, page_size: 20, total: 0, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T00:00:00Z" } });
    listWechatTargets.mockResolvedValue({ items: [], account_id: "", meta: { page: 1, page_size: 50, total: 0, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T00:00:00Z" } });
    getSettings.mockResolvedValue({
      item: {
        section: "configuration",
        fields: { USER_ALIAS: "磊哥" },
        notes: ["Producer 负责发现候选消息，Consumer 负责执行 reply task。"],
        sections: [{ title: "快路径", items: [{ label: "入口", description: "扫描未读会话并进入队列。" }] }],
        groups: [{ name: "Runtime & Identity", items: [{ key: "USER_ALIAS", value: "磊哥", description: "用户别名", editable: true }] }],
        compatibility: [],
      },
      meta: { snapshot_at: "2026-08-29T00:00:00Z" },
    });
  });

  it("restores the explanatory producer routing sections on Info", async () => {
    getSettings.mockResolvedValueOnce({ item: { section: "info", fields: { principal: "磊哥" }, notes: ["Producer 负责发现候选消息，Consumer 负责执行 reply task。"], sections: [{ title: "快路径", items: [{ label: "入口", description: "扫描未读会话并进入队列。" }] }] }, meta: { snapshot_at: "2026-08-29T00:00:00Z" } });
    renderSettings("/settings?tab=info");

    expect(await screen.findByRole("heading", { name: "快路径" })).toBeInTheDocument();
    expect(screen.getByText("扫描未读会话并进入队列。")).toBeInTheDocument();
    expect(screen.getByText("Producer 负责发现候选消息，Consumer 负责执行 reply task。")).toBeInTheDocument();
  });

  it("renders configuration groups instead of flattening the settings DTO", async () => {
    renderSettings("/settings?tab=configuration");

    expect(await screen.findByRole("heading", { name: "Runtime & Identity" })).toBeInTheDocument();
    expect(screen.getByLabelText("USER_ALIAS")).toHaveValue("磊哥");
    expect(screen.getByText("用户别名")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Configuration" })).toHaveClass("active");
    expect(screen.getByRole("link", { name: "Status" })).not.toHaveClass("active");
  });

  it("keeps prompt and audit editor values visible while highlighting template tokens", async () => {
    getSettings.mockResolvedValueOnce({ item: { section: "prompts", fields: { developer_template: "hello {{principal}}" }, preview: { developer: "hello 磊哥" } }, meta: { snapshot_at: "2026-08-29T00:00:00Z" } });
    renderSettings("/settings?tab=prompts&prompt=developer");

    expect(await screen.findByRole("textbox", { name: "Template" })).toHaveValue("hello {{principal}}");
    expect(screen.getByText("{{principal}}", { selector: "mark" })).toBeInTheDocument();
  });

  it("shows the WeChat reply scope editor inline instead of linking away", async () => {
    getSettings.mockResolvedValueOnce({ item: { section: "connectors", fields: {}, wechat: { state: "ready" } }, meta: { snapshot_at: "2026-08-29T00:00:00Z" } });
    listWechat.mockResolvedValueOnce({ items: [{ account_id: "wx-account", target_type: "direct", target_id: "melody115", conversation_id: "melody115", display_name: "Melody", trigger_mode: "every_inbound_text", enabled: true }], meta: { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T00:00:00Z" } });

    renderSettings("/settings?tab=connectors&connector=wechat");

    expect(await screen.findByRole("heading", { name: "微信自动回复对象" })).toBeInTheDocument();
    expect(await screen.findAllByText("Melody")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "保存回复范围" })).toBeDisabled();
    expect(screen.queryByRole("link", { name: "打开回复范围" })).not.toBeInTheDocument();
    expect(screen.queryByText("unknown")).not.toBeInTheDocument();
  });
});
