import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SkillSearchPanel } from "./SkillSearchPanel";

const candidate = {repo_url: "https://github.com/example/skills", commit: "a".repeat(40), skill_path: "skills/example", name: "example", description: "A useful example capability.", author: "Example Author", author_kind: "author", updated_at: "2026-01-01T00:00:00Z", available: true, unavailable_reason: ""};
function response(value: unknown, status = 200) { return new Response(JSON.stringify(value), {status, headers: {"Content-Type": "application/json"}}); }
afterEach(() => vi.unstubAllGlobals());

describe("Skill search and installation", () => {
  it("searches on submit, shows metadata, and installs the exact result once", async () => {
    const calls: {path: string; body: unknown}[] = [];
    vi.stubGlobal("fetch", async (path: string, init: RequestInit) => {
      calls.push({path, body: JSON.parse(String(init.body))});
      return path.endsWith("skill-search") ? response({items: [candidate], errors: []}) : response({name: "example", skill_id: 1, revision_id: 2, sha256: "b".repeat(64), package_sha256: "c".repeat(64), source: "source", install_status: "installed", activation_status: "pending_restart"});
    });
    const user = userEvent.setup();
    const installed = vi.fn();
    render(<SkillSearchPanel onInstalled={installed} />);
    await user.type(screen.getByRole("textbox", {name: "搜索 Skills"}), candidate.repo_url);
    expect(calls).toHaveLength(0);
    await user.keyboard("{Enter}");
    const row = await screen.findByRole("article", {name: "example"});
    expect(within(row).getByText("A useful example capability.")).toBeInTheDocument();
    expect(within(row).getByText(/Example Author/)).toBeInTheDocument();
    expect(within(row).getByText(/更新于/)).toBeInTheDocument();
    await user.dblClick(within(row).getByRole("button", {name: "Install"}));
    expect(await within(row).findByText("已安装 · 待加载")).toBeInTheDocument();
    expect(calls.filter(c => c.path.endsWith("skill-installs"))).toEqual([{path: "/api/console/settings/skill-installs", body: {repo_url: candidate.repo_url, commit: candidate.commit, skill_path: candidate.skill_path}}]);
    expect(installed).toHaveBeenCalledTimes(1);
    expect(within(row).queryByText("已加载")).not.toBeInTheDocument();
  });

  it("shows an empty result and an explicit private access failure", async () => {
    vi.stubGlobal("fetch", async (_path: string, init: RequestInit) => String(init.body).includes("private.example") ? response({message: "Check Git access for the service account", code: "upstream_error"}, 502) : response({items: [], errors: []}));
    const user = userEvent.setup();
    render(<SkillSearchPanel onInstalled={() => {}} />);
    const input = screen.getByRole("textbox", {name: "搜索 Skills"});
    await user.type(input, "missing");
    await user.click(screen.getByRole("button", {name: "搜索"}));
    expect(await screen.findByText("没有找到 Skills")).toBeInTheDocument();
    await user.clear(input); await user.type(input, "git@private.example:team/skills.git");
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("service account");
    expect(screen.queryByText("没有找到 Skills")).not.toBeInTheDocument();
  });

  it("ignores an older search response after a newer search", async () => {
    let finishOld: (value: Response) => void = () => {};
    vi.stubGlobal("fetch", async (_path: string, init: RequestInit) => String(init.body).includes("older") ? new Promise<Response>(resolve => {finishOld = resolve;}) : response({items: [{...candidate, name: "newer"}], errors: []}));
    const user = userEvent.setup();
    render(<SkillSearchPanel onInstalled={() => {}} />);
    const input = screen.getByRole("textbox", {name: "搜索 Skills"});
    await user.type(input, "older{Enter}");
    await user.clear(input); await user.type(input, "newer{Enter}");
    expect(await screen.findByRole("article", {name: "newer"})).toBeInTheDocument();
    finishOld(response({items: [candidate], errors: []}));
    await waitFor(() => expect(screen.queryByRole("article", {name: "example"})).not.toBeInTheDocument());
  });

  it("uses honest missing metadata and cannot install an unavailable result", async () => {
    vi.stubGlobal("fetch", async () => response({items: [{...candidate, author: "", author_kind: "unknown", updated_at: null, available: false, unavailable_reason: "Invalid Skill metadata"}], errors: []}));
    const user = userEvent.setup();
    render(<SkillSearchPanel onInstalled={() => {}} />);
    await user.type(screen.getByRole("textbox", {name: "搜索 Skills"}), "example{Enter}");
    expect(await screen.findByText("作者未提供")).toBeInTheDocument();
    expect(screen.getByText("更新时间未知")).toBeInTheDocument();
    expect(screen.getByRole("button", {name: "Install"})).toBeDisabled();
  });
});
