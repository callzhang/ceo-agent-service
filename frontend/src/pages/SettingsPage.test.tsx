import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
    vi.clearAllMocks();
    listAttention.mockResolvedValue({ items: [], meta: { page: 1, page_size: 20, total: 0, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T00:00:00Z" } });
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

  it("shows a red unresolved Attention badge in the Settings navigation", async () => {
    listAttention.mockResolvedValueOnce({
      items: [
        { count: 4 },
        { count: 1 },
      ],
      meta: { page: 1, page_size: 20, total: 2, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T00:00:00Z" },
    });

    renderSettings("/settings?tab=configuration");

    const badge = await screen.findByLabelText("5 个未解决问题");
    expect(badge).toHaveClass("settings-nav-badge");
    expect(badge).toHaveTextContent("5");
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

  it("prefills saved Agent Runtime credentials from the settings DTO", async () => {
    const user = userEvent.setup();
    getSettings.mockResolvedValueOnce({ item: { section: "agent-runtime", fields: {
      CEO_CODEX_API_KEY: "codex-token",
      CEO_FRIDAY_RUNTIME_PROVIDER_API_KEY: "provider-token",
      CEO_FRIDAY_RUNTIME_TICKET: "runtime-ticket",
      CEO_FRIDAY_SESSION_TOKEN: "session-token",
    } }, meta: { snapshot_at: "2026-08-29T00:00:00Z" } });
    renderSettings("/settings?tab=agent-runtime");

    expect(await screen.findByLabelText("API Token")).toHaveValue("codex-token");
    expect(screen.getByLabelText("Provider API Token")).toHaveValue("provider-token");
    expect(screen.getByLabelText("Runtime ticket")).toHaveValue("runtime-ticket");
    expect(screen.getByLabelText("Session token")).toHaveValue("session-token");
    expect(screen.getAllByText(/已保存的凭据已回填/)).toHaveLength(4);
    expect(screen.getAllByRole("option", { name: "MiniMax M2.5" })).toHaveLength(2);
    expect(screen.getAllByRole("option", { name: "Qwen3 Max" })).toHaveLength(2);
    expect(screen.getAllByRole("option", { name: "GLM-5" })).toHaveLength(2);

    const token = screen.getByLabelText("API Token");
    await user.clear(token);
    await user.type(token, "replacement-token");
    expect(token).toHaveValue("replacement-token");
    saveSettings.mockResolvedValueOnce({ ok: true, message: "已保存", meta: { updated_at: "2026-08-29T00:00:00Z" } });
    fireEvent.submit(screen.getByRole("button", { name: "保存" }).closest("form")!);
    expect(saveSettings).toHaveBeenCalledWith("agent-runtime", expect.objectContaining({ CEO_CODEX_API_KEY: "replacement-token" }), {});
  });

  it("keeps prompt and audit editor values visible while highlighting template tokens", async () => {
    getSettings.mockResolvedValueOnce({ item: { section: "prompts", fields: { developer_template: "hello {{principal}}" }, preview: { developer: "hello 磊哥" } }, meta: { snapshot_at: "2026-08-29T00:00:00Z" } });
    renderSettings("/settings?tab=prompts&prompt=developer");

    expect(await screen.findByRole("textbox", { name: "Template" })).toHaveValue("hello {{principal}}");
    expect(screen.getByText("{{principal}}", { selector: "mark" })).toBeInTheDocument();
  });

  it("highlights runtime substitutions in rendered prompt previews", async () => {
    getSettings.mockResolvedValueOnce({ item: { section: "prompts", fields: { user_template: "Reply to {{principal}} in {{conversation}}." }, preview: { user: "Reply to 磊哥 in Friday." } }, meta: { snapshot_at: "2026-08-29T00:00:00Z" } });
    renderSettings("/settings?tab=prompts&prompt=user&view=preview");

    expect(await screen.findByText("磊哥", { selector: "mark" })).toBeInTheDocument();
    expect(screen.getByText("Friday", { selector: "mark" })).toBeInTheDocument();
  });

  it("highlights template substitutions inside audit wrapper previews", async () => {
    getSettings.mockResolvedValueOnce({
      item: {
        section: "audit-rules",
        fields: { template: "Escalate to {{principal}} only when needed." },
        preview: {
          consumer: "Consumer wrapper\n\nEscalate to Alex only when needed.\n\nConsumer footer",
        },
      },
      meta: { snapshot_at: "2026-08-29T00:00:00Z" },
    });
    renderSettings("/settings?tab=audit-rules&rule=consumer&view=preview");

    expect(await screen.findByText("Alex", { selector: "mark" })).toBeInTheDocument();
    expect(screen.getByRole("tabpanel", { name: "Consumer rendered preview" })).toHaveTextContent("Consumer wrapper");
    expect(screen.getByRole("tabpanel", { name: "Consumer rendered preview" })).toHaveTextContent("Consumer footer");
  });

  it("keeps audit rule selection independent from the template or preview view", async () => {
    getSettings.mockResolvedValueOnce({
      item: {
        section: "audit-rules",
        fields: { template: "Escalate to {{principal}} only when needed." },
        preview: {
          template: "Escalate to Alex only when needed.",
          consumer: "Escalate to Alex only when needed.\n\nConsumer wrapper",
          audit: "Escalate to Alex only when needed.\n\nAudit wrapper",
        },
      },
      meta: { snapshot_at: "2026-08-29T00:00:00Z" },
    });
    const firstRender = renderSettings("/settings?tab=audit-rules&rule=template&view=preview");

    expect(await screen.findByText("Alex", { selector: "mark" })).toBeInTheDocument();
    expect(screen.getByRole("tabpanel", { name: "Template rendered preview" })).toHaveTextContent("Escalate to Alex only when needed.");
    const ruleTabs = within(screen.getByRole("tablist", { name: "Audit Rule sections" }));
    const viewTabs = within(screen.getByRole("tablist", { name: "Audit Rule view" }));
    expect(screen.getByText("规则类型")).toBeInTheDocument();
    expect(screen.getByText("查看方式")).toBeInTheDocument();
    expect(ruleTabs.getByRole("tab", { name: /^Template$/ })).toHaveAttribute("aria-selected", "true");
    expect(viewTabs.getByRole("tab", { name: /^Template$/ })).toHaveAttribute("aria-selected", "false");
    expect(viewTabs.getByRole("tab", { name: "Rendered preview" })).toHaveAttribute("aria-selected", "true");
    expect(ruleTabs.getByRole("tab", { name: /^Consumer$/ })).toHaveAttribute("href", "/settings?tab=audit-rules&rule=consumer&view=preview");

    firstRender.unmount();
    getSettings.mockResolvedValueOnce({
      item: {
        section: "audit-rules",
        fields: { template: "Escalate to {{principal}} only when needed." },
        preview: { template: "Escalate to Alex only when needed." },
      },
      meta: { snapshot_at: "2026-08-29T00:00:00Z" },
    });
    renderSettings("/settings?tab=audit-rules&rule=consumer&view=template");

    expect(await screen.findByText("Escalate to {{principal}} only when needed.", { selector: "pre" })).toBeInTheDocument();
    expect(screen.getByText("当前 tab 使用同一份 Audit Rules template；切换到 Template tab 编辑。")).toBeInTheDocument();
    const consumerRuleTabs = within(screen.getByRole("tablist", { name: "Audit Rule sections" }));
    const consumerViewTabs = within(screen.getByRole("tablist", { name: "Audit Rule view" }));
    expect(consumerRuleTabs.getByRole("tab", { name: /^Consumer$/ })).toHaveAttribute("aria-selected", "true");
    expect(consumerViewTabs.getByRole("tab", { name: /^Template$/ })).toHaveAttribute("aria-selected", "true");
    expect(consumerViewTabs.getByRole("tab", { name: "Rendered preview" })).toHaveAttribute("aria-selected", "false");
  });

  it("shows the WeChat reply scope editor inline instead of linking away", async () => {
    getSettings.mockResolvedValueOnce({ item: { section: "connectors", fields: {}, wechat: { state: "ready" } }, meta: { snapshot_at: "2026-08-29T00:00:00Z" } });
    listWechat.mockResolvedValueOnce({ items: [{ account_id: "wx-account", target_type: "direct", target_id: "melody115", conversation_id: "melody115", display_name: "Melody", trigger_mode: "every_inbound_text", enabled: true }], meta: { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T00:00:00Z" } });

    renderSettings("/settings?tab=connectors&connector=wechat");

    expect(await screen.findByRole("heading", { name: "微信自动回复对象" })).toBeInTheDocument();
    expect(await screen.findAllByText("Melody")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "保存回复范围" })).toBeDisabled();
    expect(screen.queryByRole("link", { name: "打开回复范围" })).not.toBeInTheDocument();
    expect(screen.queryByText("unknown")).not.toBeInTheDocument();
  });

  it("brings the active Settings section into view on narrow navigation", async () => {
    const original = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollIntoView");
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scrollIntoView });
    getSettings.mockResolvedValueOnce({ item: { section: "connectors", fields: {}, wechat: { state: "ready" } }, meta: { snapshot_at: "2026-08-29T00:00:00Z" } });

    try {
      renderSettings("/settings?tab=connectors&connector=wechat");

      expect(await screen.findByRole("heading", { name: "微信自动回复对象" })).toBeInTheDocument();
      expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "auto", block: "nearest", inline: "center" });
    } finally {
      if (original) Object.defineProperty(HTMLElement.prototype, "scrollIntoView", original);
      else delete (HTMLElement.prototype as { scrollIntoView?: unknown }).scrollIntoView;
    }
  });

  it("shows idle guidance before any WeChat target search", async () => {
    getSettings.mockResolvedValueOnce({ item: { section: "connectors", fields: {}, wechat: { state: "ready" } }, meta: { snapshot_at: "2026-08-29T00:00:00Z" } });
    listWechat.mockResolvedValueOnce({ items: [{ account_id: "wx-account", target_type: "direct", target_id: "melody115", conversation_id: "melody115", display_name: "Melody", trigger_mode: "every_inbound_text", enabled: true }], meta: { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T00:00:00Z" } });

    renderSettings("/settings?tab=connectors&connector=wechat");

    expect(await screen.findByText("输入名称或 ID 后搜索；留空可浏览全部对象。")).toBeInTheDocument();
    expect(screen.queryByText("搜索结果中的对象已在当前回复范围。")).not.toBeInTheDocument();
  });

  it("submits a WeChat target search with Enter and reports visible results", async () => {
    const user = userEvent.setup();
    getSettings.mockResolvedValueOnce({ item: { section: "connectors", fields: {}, wechat: { state: "ready" } }, meta: { snapshot_at: "2026-08-29T00:00:00Z" } });
    listWechat.mockResolvedValueOnce({ items: [{ account_id: "wx-account", target_type: "direct", target_id: "melody115", conversation_id: "melody115", display_name: "Melody", trigger_mode: "every_inbound_text", enabled: true }], meta: { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T00:00:00Z" } });
    listWechatTargets.mockResolvedValueOnce({ items: [
      { account_id: "wx-account", target_type: "direct", target_id: "melody115", conversation_id: "melody115", display_name: "Melody", trigger_mode: "every_inbound_text", enabled: true },
      { account_id: "wx-account", target_type: "direct", target_id: "alex", conversation_id: "alex", display_name: "Alex", trigger_mode: "every_inbound_text", enabled: false },
    ], account_id: "wx-account", meta: { page: 1, page_size: 50, total: 2, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T00:00:00Z" } });

    renderSettings("/settings?tab=connectors&connector=wechat");
    const searchbox = await screen.findByRole("searchbox", { name: "搜索好友或群聊" });
    await user.type(searchbox, "{Enter}");

    expect(listWechatTargets).toHaveBeenCalledWith({ query: "", kind: "all", limit: 50 });
    expect(saveSettings).not.toHaveBeenCalled();
    expect(await screen.findByText("共匹配 2 个，当前显示 1 个可添加对象。")).toBeInTheDocument();
    expect(screen.getByText("Alex")).toBeInTheDocument();
  });

  it("does not repeat selected WeChat targets in search results", async () => {
    const user = userEvent.setup();
    getSettings.mockResolvedValueOnce({ item: { section: "connectors", fields: {}, wechat: { state: "ready" } }, meta: { snapshot_at: "2026-08-29T00:00:00Z" } });
    listWechat.mockResolvedValueOnce({ items: [{ account_id: "wx-account", target_type: "direct", target_id: "melody115", conversation_id: "melody115", display_name: "Melody", trigger_mode: "every_inbound_text", enabled: true }], meta: { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T00:00:00Z" } });
    listWechatTargets.mockResolvedValueOnce({ items: [
      { account_id: "wx-account", target_type: "direct", target_id: "melody115", conversation_id: "melody115", display_name: "Melody", trigger_mode: "every_inbound_text", enabled: true },
      { account_id: "wx-account", target_type: "direct", target_id: "alex", conversation_id: "alex", display_name: "Alex", trigger_mode: "every_inbound_text", enabled: false },
    ], account_id: "wx-account", meta: { page: 1, page_size: 50, total: 2, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T00:00:00Z" } });

    renderSettings("/settings?tab=connectors&connector=wechat");
    expect(await screen.findByRole("heading", { name: "微信自动回复对象" })).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "搜索" }));

    expect(await screen.findByText("Alex")).toBeInTheDocument();
    expect(screen.getAllByText("Melody")).toHaveLength(1);
  });

  it("recovers from an inline WeChat reply scope load failure", async () => {
    const user = userEvent.setup();
    getSettings.mockResolvedValueOnce({ item: { section: "connectors", fields: {}, wechat: { state: "ready" } }, meta: { snapshot_at: "2026-08-29T00:00:00Z" } });
    listWechat
      .mockRejectedValueOnce(new Error("回复范围暂时不可用"))
      .mockResolvedValueOnce({ items: [{ account_id: "wx-account", target_type: "direct", target_id: "melody115", conversation_id: "melody115", display_name: "Melody", trigger_mode: "every_inbound_text", enabled: true }], meta: { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T00:00:00Z" } });

    renderSettings("/settings?tab=connectors&connector=wechat");

    expect(await screen.findByRole("alert")).toHaveTextContent("回复范围暂时不可用");
    expect(screen.getByRole("status", { name: "回复范围同步状态" })).toHaveTextContent("加载失败");
    await user.click(screen.getByRole("button", { name: "重试加载回复范围" }));

    expect(await screen.findByText("Melody")).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "回复范围同步状态" })).toHaveTextContent("已同步");
    expect(listWechat).toHaveBeenCalledTimes(2);
  });

  it("keeps selected WeChat targets while searching and reports a successful save", async () => {
    const user = userEvent.setup();
    getSettings.mockResolvedValueOnce({ item: { section: "connectors", fields: {}, wechat: { state: "ready" } }, meta: { snapshot_at: "2026-08-29T00:00:00Z" } });
    listWechat.mockResolvedValueOnce({ items: [{ account_id: "wx-account", target_type: "direct", target_id: "melody115", conversation_id: "melody115", display_name: "Melody", trigger_mode: "every_inbound_text", enabled: true }], meta: { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T00:00:00Z" } });
    listWechatTargets.mockResolvedValueOnce({ items: [
      { account_id: "wx-account", target_type: "direct", target_id: "melody115", conversation_id: "melody115", display_name: "Melody", trigger_mode: "every_inbound_text", enabled: true },
      { account_id: "wx-account", target_type: "direct", target_id: "alex", conversation_id: "alex", display_name: "Alex", trigger_mode: "every_inbound_text", enabled: false },
    ], account_id: "wx-account", meta: { page: 1, page_size: 50, total: 2, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T00:00:00Z" } });
    saveWechatReplyScope.mockResolvedValueOnce({ ok: true, message: "已保存", meta: { updated_at: "2026-08-29T00:00:00Z" } });

    renderSettings("/settings?tab=connectors&connector=wechat");
    await user.click(await screen.findByRole("button", { name: "搜索" }));
    await user.click(await screen.findByRole("checkbox", { name: /Alex/ }));
    await user.click(screen.getByRole("button", { name: "保存回复范围" }));

    expect(saveWechatReplyScope).toHaveBeenCalledWith("wx-account", expect.arrayContaining([
      expect.objectContaining({ target_id: "melody115" }),
      expect.objectContaining({ target_id: "alex" }),
    ]));
    expect(await screen.findByText("回复范围已保存")).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "回复范围同步状态" })).toHaveTextContent("已保存");
  });
});
