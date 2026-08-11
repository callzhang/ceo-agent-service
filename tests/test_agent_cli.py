import asyncio
from pathlib import Path
import zipfile

import pytest

import app.agent_cli as agent_cli
from app.native_cli_metadata import AgentReadOnlyViolationError


def test_agent_cli_mcp_tools_publish_searchable_descriptions():
    tools = asyncio.run(agent_cli.server.list_tools())
    descriptions = {tool.name: tool.description for tool in tools}

    assert set(descriptions) == {
        "read_skill",
        "read_spreadsheet",
        "execute_reviewed_read",
        "execute_reviewed_write",
    }
    assert all(description.strip() for description in descriptions.values())
    assert "calendar event" in descriptions["execute_reviewed_read"]


def test_read_skill_allows_markdown_referenced_by_an_installed_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "skills"
    skill = root / "dingtalk-misc"
    reference = skill / "references" / "oa.md"
    reference.parent.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# DingTalk misc", encoding="utf-8")
    reference.write_text("# OA reference", encoding="utf-8")
    monkeypatch.setattr(agent_cli, "AGENT_SKILL_ROOTS", (root,))

    result = agent_cli.read_skill(str(reference))

    assert result["content"] == "# OA reference"
    assert result["sha256"]


@pytest.mark.parametrize("filename", ("notes.md", "references/oa.txt"))
def test_read_skill_rejects_files_outside_an_installed_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filename: str
):
    root = tmp_path / "skills"
    target = root / filename
    target.parent.mkdir(parents=True)
    target.write_text("not an installed skill document", encoding="utf-8")
    monkeypatch.setattr(agent_cli, "AGENT_SKILL_ROOTS", (root,))

    with pytest.raises(AgentReadOnlyViolationError, match="skill_path_forbidden"):
        agent_cli.read_skill(str(target))


def test_read_spreadsheet_reads_downloaded_xlsx_without_shell(tmp_path: Path):
    workbook_path = tmp_path / "material.xlsx"
    with zipfile.ZipFile(workbook_path, "w") as workbook:
        workbook.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet 1" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        workbook.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/></Relationships>',
        )
        workbook.writestr(
            "xl/sharedStrings.xml",
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>领域</t></si><si><t>高招募难度</t></si></sst>',
        )
        workbook.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row></sheetData></worksheet>',
        )

    result = agent_cli.read_spreadsheet(str(workbook_path))

    assert result == {
        "format": "xlsx",
        "sheets": [
            {
                "name": "Sheet 1",
                "rows": [{"row": 1, "cells": {"A": "领域", "B": "高招募难度"}}],
                "truncated": False,
            }
        ],
    }
