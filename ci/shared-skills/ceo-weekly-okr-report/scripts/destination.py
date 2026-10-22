"""Resolve this Skill's daily report location using native DingTalk operations."""

import argparse
import json
import subprocess


def run(args):
    return json.loads(
        subprocess.run(
            ["dws", *args, "--format", "json"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )


def listing(payload, key):
    data = payload.get("data")
    if (
        not isinstance(data, dict)
        or not isinstance(data.get(key), list)
        or data.get("hasMore")
    ):
        raise ValueError("incomplete DingTalk listing")
    return data[key]


def single(items, label, optional=False):
    if not items and optional:
        return None
    if len(items) != 1:
        raise ValueError(f"expected unique {label}, found {len(items)}")
    return items[0]


def resolve(title, parent_id=""):
    space = single(
        [
            x
            for x in listing(
                run(
                    [
                        "wiki",
                        "+space-list",
                        "--type",
                        "orgWikiSpace",
                        "--page-all",
                        "--limit",
                        "50",
                    ]
                ),
                "spaces",
            )
            if x.get("name") == "🎯  目标与执行"
        ],
        "management wiki",
    )
    ws = space["workspaceId"]

    def nodes(parent=None):
        return listing(
            run(
                [
                    "wiki",
                    "+node-list",
                    "--workspace",
                    ws,
                    "--page-all",
                    *(["--folder", parent] if parent else []),
                ]
            ),
            "nodes",
        )

    folder = {"nodeId": parent_id} if parent_id else single(
        [x for x in nodes() if x.get("name") == "管理者 OKR 进度周报"], "daily folder", True
    )
    if folder is None:
        run(
            [
                "wiki",
                "+node-create",
                "--workspace",
                ws,
                "--name",
                "管理者 OKR 进度周报",
                "--type",
                "folder",
            ]
        )
        folder = single(
            [x for x in nodes() if x.get("name") == "管理者 OKR 进度周报"], "daily folder"
        )
    found = single(
        [x for x in nodes(folder["nodeId"]) if x.get("name") == title],
        "daily document",
        True,
    )
    return {
        "workspace_id": ws,
        "parent_id": folder["nodeId"],
        "title": title,
        "node_id": found["nodeId"] if found else "",
        "folder": folder,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True)
    parser.add_argument("--parent", default="")
    args = parser.parse_args()
    print(json.dumps(resolve(args.title, args.parent), ensure_ascii=False))
