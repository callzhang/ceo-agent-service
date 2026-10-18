"""Lossless projection of repeated DingTalk Sheet styles in one range read."""

import asyncio
import json
from copy import deepcopy

import pytest
import pydantic_core

from app.agent_cli import build_role_server
from app.dingtalk_sheet_style_sharing import share_sheet_cell_styles


def _source_result() -> dict[str, object]:
    style = {
        "fontColor": "#224466",
        "fontFamily": "Arial",
        "fontSize": 12,
        "horizontalAlignment": "center",
        "numberFormat": "yyyy-mm-dd;@",
        "wordWrap": True,
        "special:key": {"nested": None},
    }
    cells = [
        [
            {"value": {"text": f"匿名值 {row}-{column}", "empty": None},
             "cellStyles": deepcopy(style), "displayValue": f"D{row}-{column}"}
            for column in range(8)
        ]
        for row in range(12)
    ]
    return {
        "success": True,
        "returnedRange": "A1:H12",
        "rowIndices": list(range(12)),
        "colIndices": list(range(8)),
        "cells": cells,
        "hasMore": False,
        "truncationReasons": [],
        "message": "ok",
    }


def _expand_styles(result: dict[str, object]) -> dict[str, object]:
    restored = deepcopy(result)
    styles = restored.pop("sharedCellStyles")
    for row_index, row in enumerate(restored["cells"]):
        for column_index, cell in enumerate(row):
            row[column_index] = {
                "cellStyles" if key == "cellStyleRef" else key:
                    deepcopy(styles[value]) if key == "cellStyleRef" else value
                for key, value in cell.items()
            }
    return restored


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_sheet_tool_shares_styles_without_losing_any_cell_or_metadata(monkeypatch, role):
    source = _source_result()
    saved = deepcopy(source)

    class FakeDwsClient:
        dws_bin = "dws"

        def run_json(self, command):
            assert command == [
                "dws", "sheet", "range", "read", "--node", "anonymous-node",
                "--sheet-id", "anonymous-sheet", "--range", "A1:H12", "--format", "json",
            ]
            return source

    monkeypatch.setattr("app.dws_client.DwsClient", FakeDwsClient)
    server = build_role_server(role)
    content, structured = asyncio.run(server.call_tool("read_dingtalk_sheet_range", {
        "node_id": "anonymous-node",
        "sheet_id": "anonymous-sheet",
        "cell_range": "A1:H12",
    }))

    projected = json.loads(content[0].text)
    assert structured == projected
    assert source == saved
    assert projected["cells"][0][0]["cellStyleRef"] == 0
    assert "cellStyles" not in projected["cells"][0][0]
    assert json.dumps(_expand_styles(projected), ensure_ascii=False) == json.dumps(
        source, ensure_ascii=False
    )
    assert len(content[0].text) < len(
        pydantic_core.to_json(source, fallback=str, indent=2).decode()
    )


def test_short_or_unshared_sheet_result_is_unchanged(monkeypatch):
    source = {"success": True, "cells": [[{"value": 1, "cellStyles": {"fontSize": 12}}]]}

    class FakeDwsClient:
        dws_bin = "dws"

        def run_json(self, _command):
            return source

    monkeypatch.setattr("app.dws_client.DwsClient", FakeDwsClient)
    server = build_role_server("consumer")
    content, structured = asyncio.run(server.call_tool("read_dingtalk_sheet_range", {
        "node_id": "anonymous-node",
        "sheet_id": "anonymous-sheet",
        "cell_range": "A1",
    }))

    assert structured == source
    assert json.loads(content[0].text) == source


def test_provider_failure_payload_is_not_projected():
    style = {"fontColor": "#ff0000", "numberFormat": "yyyy-mm-dd"}
    failure = {
        "success": False,
        "message": "provider rejected range",
        "cells": [[{"value": index, "cellStyles": deepcopy(style)} for index in range(100)]],
    }

    assert share_sheet_cell_styles(failure) is failure


def test_unexpected_provider_shape_remains_untouched():
    response = ["provider payload"]

    assert share_sheet_cell_styles(response) is response


def test_style_keys_and_order_round_trip_exactly():
    first = {"fontColor": "#ff0000", "numberFormat": "yyyy-mm-dd"}
    reordered = {"numberFormat": "yyyy-mm-dd", "fontColor": "#ff0000"}
    result = {
        "success": True,
        "cells": [[
            {"value": index, "cellStyles": deepcopy(first if index % 2 else reordered)}
            for index in range(100)
        ]],
    }

    projected = share_sheet_cell_styles(result)

    assert len(projected["sharedCellStyles"]) == 2
    assert json.dumps(_expand_styles(projected), ensure_ascii=False) == json.dumps(
        result, ensure_ascii=False
    )


def test_large_rectangular_result_fits_one_native_text_block_losslessly():
    styles = [
        {
            "fontColor": f"#{index:06x}",
            "fontFamily": "Arial",
            "fontSize": 12,
            "horizontalAlignment": "center",
            "numberFormat": "yyyy-mm-dd;@",
            "verticalAlignment": "middle",
            "wordWrap": True,
        }
        for index in range(80)
    ]
    result = {
        "success": True,
        "returnedRange": "A1:BV66",
        "rowIndices": list(range(66)),
        "colIndices": list(range(74)),
        "cells": [[
            {"value": row * 74 + column, "cellStyles": styles[(row * 74 + column) % 80]}
            for column in range(74)
        ] for row in range(66)],
        "hasMore": False,
    }

    projected = share_sheet_cell_styles(result)

    assert len(pydantic_core.to_json(result, fallback=str, indent=2)) > 1024 * 1024
    assert len(pydantic_core.to_json(projected, fallback=str, indent=2)) < 1024 * 1024
    assert json.dumps(_expand_styles(projected), ensure_ascii=False) == json.dumps(
        result, ensure_ascii=False
    )
