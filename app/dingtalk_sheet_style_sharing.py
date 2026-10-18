"""Share exact repeated cell styles within one DingTalk Sheet range result."""

import json
from typing import Any

from pydantic_core import to_json


def share_sheet_cell_styles(result: Any) -> Any:
    """Reference repeated styles only when the native MCP text becomes smaller."""
    if not isinstance(result, dict):
        return result
    rows = result.get("cells")
    if result.get("success") is not True or "sharedCellStyles" in result or not isinstance(rows, list):
        return result

    styles: list[dict[str, Any]] = []
    style_indices: dict[str, int] = {}
    projected_rows = []
    for row in rows:
        if not isinstance(row, list):
            return result
        projected_row = []
        for cell in row:
            if not isinstance(cell, dict) or "cellStyleRef" in cell:
                return result
            style = cell.get("cellStyles")
            if not isinstance(style, dict):
                projected_row.append(cell)
                continue
            key = json.dumps(style, ensure_ascii=False, separators=(",", ":"))
            index = style_indices.get(key)
            if index is None:
                index = len(styles)
                style_indices[key] = index
                styles.append(style)
            projected_row.append({
                "cellStyleRef" if name == "cellStyles" else name:
                    index if name == "cellStyles" else value
                for name, value in cell.items()
            })
        projected_rows.append(projected_row)

    if not styles:
        return result
    projected = {**result, "cells": projected_rows, "sharedCellStyles": styles}
    return projected if len(to_json(projected, fallback=str, indent=2)) < len(
        to_json(result, fallback=str, indent=2)
    ) else result
