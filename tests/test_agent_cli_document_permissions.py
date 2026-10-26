import asyncio

from app import agent_cli


def test_document_permissions_uses_supported_drive_command(monkeypatch):
    calls = []

    class FakeDwsClient:
        dws_bin = "dws"

        def run_json(self, command):
            calls.append(command)
            return {"items": []}

    monkeypatch.setattr("app.dws_client.DwsClient", FakeDwsClient)
    server = agent_cli.build_role_server("audit")

    async def call_tool():
        return await server.call_tool(
            "read_dingtalk_document_permissions", {"node_id": "doc-node-123"}
        )

    _, result = asyncio.run(call_tool())

    assert result == {"items": []}
    assert calls == [[
        "dws", "drive", "permission", "list", "--node", "doc-node-123",
        "--limit", "50", "--format", "json",
    ]]


def test_document_permissions_passes_next_page_token(monkeypatch):
    calls = []

    class FakeDwsClient:
        dws_bin = "dws"

        def run_json(self, command):
            calls.append(command)
            return {"items": [], "hasMore": True, "nextToken": "next-page-token"}

    monkeypatch.setattr("app.dws_client.DwsClient", FakeDwsClient)
    server = agent_cli.build_role_server("audit")

    async def call_tool():
        return await server.call_tool(
            "read_dingtalk_document_permissions",
            {"node_id": "doc-node-123", "next_token": "next-page-token"},
        )

    _, result = asyncio.run(call_tool())

    assert result == {"items": [], "hasMore": True, "nextToken": "next-page-token"}
    assert calls == [[
        "dws", "drive", "permission", "list", "--node", "doc-node-123",
        "--limit", "50", "--next-token", "next-page-token", "--format", "json",
    ]]
