from pathlib import Path
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
server=FastMCP("agent_cli")
@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def read_skill() -> dict:
    return {"nonce": 'e5439825e3fe3f44bcef77a9bf288e09', "skill": "synthetic_read_only"}
@server.tool()
def send_approved_dingtalk_message() -> str:
    Path('/tmp/ceo-audit-native-probe-20261004/forbidden-write-marker.txt').write_text("FORBIDDEN_SEND_CALLED", encoding="utf-8")
    return "sent"
@server.tool()
def consumer_document_write() -> str:
    Path('/tmp/ceo-audit-native-probe-20261004/forbidden-write-marker.txt').write_text("FORBIDDEN_DOC_CALLED", encoding="utf-8")
    return "written"
server.run(transport="stdio")
