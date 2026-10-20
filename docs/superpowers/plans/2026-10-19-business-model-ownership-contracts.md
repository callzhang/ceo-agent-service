# Business Model Ownership Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为业务模型简化生成完整、可重复的字段/记录类型/调用方/页面事实清单，防止把旧表包装、只改页面或回滚旧文件误判为完成。

**Architecture:** 一个标准库只读盘点工具读取显式数据库路径和源码路径，输出 schema/类型计数/元数据差异/源码引用/真实前端路由，不导入 Store、不保存任何业务正文。字段所有权及 UI 验收清单由实际清单生成，经过人工逐项归属后作为总计划四个独立迁移单元的输入。

**Tech Stack:** Python 标准库 sqlite3/ast/json/pathlib、pytest；既有 SQLite 和 TSX 源码。

---

## 范围与基线

总计划：`docs/superpowers/plans/2026-10-19-business-model-simplification.md`。

本子计划可以独立执行、测试和提交；不改变 app/frontend 运行行为，不触发任何发送、不提供新的运行时策略，也不把“清单完成”算为业务重构上线。首次读取生产库只用 mode=ro；所有后续写测试在 tmp_path 下。

执行根目录：`/Users/derek/.codex/worktrees/business-model-plans/ceo-agent-service`。实施时如果使用新 worktree，使用其 git 根路径替换命令中的此路径，并在交付记录实际绝对路径。

## 文件结构

- Create: `tools/business_model_inventory.py`：只读 CLI 与 inventory/callsites/route 纯函数。
- Create: `tests/test_business_model_inventory.py`：独立合成 fixture，不使用当前 Store 构造目标库作为唯一 oracle。
- Create: `docs/business-model-ownership.json`：逐物理/生成字段处置，非运行时数据。
- Create: `docs/business-model-ui-coverage.json`：真实路由及已知业务场景清单。
- Modify: `docs/agent-claims.md`：仅自己的 claim。
- Modify: 总计划的基线/现状核对段：只在确有新证据时更新。

不修改 `app/`、`frontend/`、schema version 或生产库；工具输出不要提交真实 ID、消息、邮件、正文、secret 或整库副本。

### Task 1: 合成库 RED 测试

- [ ] **Step 1: 新建完整测试文件** `tests/test_business_model_inventory.py`：

```python
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

TOOL = Path(__file__).parents[1] / "tools" / "business_model_inventory.py"


def module():
    spec = importlib.util.spec_from_file_location("business_model_inventory", TOOL)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    (root / "frontend/src/app").mkdir(parents=True)
    (root / "app/store.py").write_text(
        'def save(db):\n'
        '    db.execute("insert into tasks(record_type) values (?)", ("reply_tasks",))\n'
    )
    (root / "frontend/src/app/router.tsx").write_text(
        '<Route path="/tasks" element={<Tasks />} />\n'
        '<Route path="/tasks/item/:taskId" element={<Detail />} />\n'
        '<Route path="*" element={<Missing />} />\n'
    )
    path = tmp_path / "source.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
        create table tasks(id integer primary key,record_type text,parent_id integer);
        insert into tasks values(1,'reply_tasks',null),(2,'business_object_tasks',1),
          (3,'business_object_tasks',1);
        create table runs(id integer primary key,record_type text,parent_id integer,
          structured_error_json text);
        insert into runs values(1,'agent_runs',null,'parent'),
          (2,'agent_run_state_events',1,'different');
        create table business(id integer primary key,record_type text,import_task_id integer);
        insert into business values(1,'work_todo',null);
        create table emails(id integer primary key,classification_id integer,
          class_sender text,message_sender text,class_subject text,message_subject text,
          class_received_at text,message_received_at text,class_folder text,message_folder text,
          class_rfc_message_id text,message_rfc_message_id text);
        insert into emails values(1,1,'s','s','a','a','t','t','in','in',null,'<one>');
        insert into emails values(2,2,'different','s','b','b','t','t','in','in','<two>','<two>');
        """)
    return root, path


def test_schema_types_and_divergence_are_exact_and_body_free(corpus):
    root, path = corpus
    report = module().inventory(path, root)
    assert report["types"]["tasks"]["reply_tasks"] == 1
    assert report["checks"]["business_aliases"] == {
        "rows": 2, "parents": 1, "multi_key_parents": 1, "max_keys": 2,
    }
    assert report["checks"]["email_pairs"]["class_sender"] == {
        "rows": 2, "equal": 1, "different": 1,
    }
    assert report["checks"]["email_pairs"]["class_rfc_message_id"]["different"] == 1
    assert report["checks"]["work_todo_import"] == {"rows": 1, "mapped": 0}
    assert report["checks"]["run_state_errors"] == {"rows": 1, "equal": 0}
    assert report["routes"] == ["*", "/tasks", "/tasks/item/:taskId"]
    encoded = json.dumps(report)
    assert "<one>" not in encoded
    assert "<two>" not in encoded
    assert '"message_sender"' in encoded


def test_source_database_and_schema_are_unchanged(corpus):
    root, path = corpus
    before = path.read_bytes()
    report = module().inventory(path, root)
    assert path.read_bytes() == before
    assert report["tables"]["tasks"][0]["name"] == "id"
    assert report["types"]["runs"]["agent_run_state_events"] == 1


def test_missing_source_is_not_created(tmp_path):
    missing = tmp_path / "absent.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        module().inventory(missing, tmp_path)
    assert not missing.exists()


def test_references_keep_function_and_line_without_capturing_values(corpus):
    root, path = corpus
    refs = module().inventory(path, root)["references"]["reply_tasks"]
    assert refs == [{"file": "app/store.py", "function": "save", "line": 2}]


def test_missing_optional_tables_are_explicit_not_zero(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    path = tmp_path / "minimal.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("create table unrelated(id integer)")
    report = module().inventory(path, root)
    assert report["checks"]["email_pairs"] is None
    assert report["checks"]["business_aliases"] is None
    assert report["checks"]["work_todo_import"] is None
    assert report["checks"]["run_state_errors"] is None
```

- [ ] **Step 2: 运行 RED**：

```bash
python -m pytest -q tests/test_business_model_inventory.py
```

预期：5 个测试因工具文件不存在失败；不是 fixture 导入、依赖安装或语法错误。记录实际失败信息。

### Task 2: 实现只读工具并 GREEN

- [ ] **Step 1: 新建完整工具** `tools/business_model_inventory.py`：

```python
"""Read schema and aggregate model facts without loading app runtime or bodies."""
import argparse
import ast
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def routes(root):
    source = root / "frontend/src/app/router.tsx"
    if not source.exists():
        return []
    return sorted(set(re.findall(r'<Route\s+path="([^"]+)"', source.read_text())))


def references(root, tokens):
    result = {token: [] for token in tokens}
    for path in sorted((root / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        parents = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        found = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            for token in tokens:
                if token not in node.value:
                    continue
                owner = node
                while owner in parents and not isinstance(
                    owner, (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    owner = parents[owner]
                function = owner.name if isinstance(
                    owner, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) else "<module>"
                key = (token, function, node.lineno)
                if key in found:
                    continue
                found.add(key)
                result[token].append({
                    "file": str(path.relative_to(root)),
                    "function": function,
                    "line": node.lineno,
                })
    return result


def checks(db, columns):
    def has(table, *fields):
        return set(fields) <= set(columns.get(table, ()))

    output = {
        "email_pairs": None, "business_aliases": None,
        "work_todo_import": None, "run_state_errors": None,
    }
    pairs = {
        "class_sender": "message_sender",
        "class_subject": "message_subject",
        "class_received_at": "message_received_at",
        "class_folder": "message_folder",
        "class_rfc_message_id": "message_rfc_message_id",
    }
    if has("emails", "classification_id", *pairs, *pairs.values()):
        output["email_pairs"] = {}
        for left, right in pairs.items():
            count, equal = db.execute(
                f"select count(*),coalesce(sum({quote(left)} is {quote(right)}),0) "
                "from emails where classification_id is not null"
            ).fetchone()
            output["email_pairs"][left] = {
                "rows": count, "equal": equal, "different": count - equal,
            }
    if has("tasks", "record_type", "parent_id"):
        rows, parents, multi, maximum = db.execute(
            "select coalesce(sum(n),0),count(*),coalesce(sum(n>1),0),coalesce(max(n),0) "
            "from (select parent_id,count(*) n from tasks "
            "where record_type='business_object_tasks' group by parent_id)"
        ).fetchone()
        output["business_aliases"] = {
            "rows": rows, "parents": parents,
            "multi_key_parents": multi, "max_keys": maximum,
        }
    if has("business", "record_type", "import_task_id"):
        rows, mapped = db.execute(
            "select count(*),coalesce(sum(import_task_id is not null),0) "
            "from business where record_type='work_todo'"
        ).fetchone()
        output["work_todo_import"] = {"rows": rows, "mapped": mapped}
    if has("runs", "id", "record_type", "parent_id", "structured_error_json"):
        rows, equal = db.execute(
            "select count(*),coalesce(sum(e.structured_error_json is p.structured_error_json),0) "
            "from runs e join runs p on p.id=e.parent_id "
            "where e.record_type='agent_run_state_events'"
        ).fetchone()
        output["run_state_errors"] = {"rows": rows, "equal": equal}
    return output


def inventory(database, root):
    database, root = Path(database).resolve(), Path(root).resolve()
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("pragma query_only=on")
        db.execute("pragma busy_timeout=2000")
        db.execute("begin")
        names = [row[0] for row in db.execute(
            "select name from sqlite_schema where type='table' "
            "and name not glob 'sqlite_*' order by name"
        )]
        tables, columns, types = {}, {}, {}
        for name in names:
            tables[name] = [
                dict(zip(("cid", "name", "affinity", "not_null", "default", "pk", "hidden"), row))
                for row in db.execute(f"pragma table_xinfo({quote(name)})")
            ]
            columns[name] = [field["name"] for field in tables[name]]
            if "record_type" in columns[name]:
                types[name] = dict(db.execute(
                    f"select record_type,count(*) from {quote(name)} group by record_type"
                ))
        facts = checks(db, columns)
        db.rollback()
    tokens = sorted({kind for counts in types.values() for kind in counts if kind})
    return {
        "database_name": database.name,
        "tables": tables, "types": types, "checks": facts,
        "references": references(root, tokens), "routes": routes(root),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(inventory(args.database, args.root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
```

注意：source string 引用清单只用于导航，既可能是运行 writer，也可能是历史声明、reader、说明字符串，不能当作自动调用图或写入判定。schema 中声明但当前 0 行的类型须由定义模块补充，不能从 populated types 缺席推导功能不存在。工具没有输出业务行、消息 ID、正文或 secret 值。

- [ ] **Step 2: 跑 GREEN**：

```bash
python -m pytest -q tests/test_business_model_inventory.py
```

预期：5 passed。若环境不含 pytest，使用仓库既有 Python 环境；不要在生产 checkout 安装或跑测试。

- [ ] **Step 3: 在副本 schema / 生产只读 source 上运行 CLI**：

```bash
python tools/business_model_inventory.py '/Users/derek/Library/Application Support/ceo-agent-service/auto-reply.sqlite3' --root '/Users/derek/.codex/worktrees/business-model-plans/ceo-agent-service'
```

预期：JSON 输出，有实际 tables/types/checks/references/routes，生产没有写事务。不要将这个命令改成 Store 构造生产库。大输出重定向到本任务临时目录，审查后只提交允许的 schema/汇总证据。

- [ ] **Step 4: 提交工具与测试**：

```bash
git add tools/business_model_inventory.py tests/test_business_model_inventory.py
git commit -m "test(storage): inventory model ownership without loading runtime"
```

### Task 3: 完整字段所有权与真实页面契约

- [ ] **Step 1: 生成两个文档的初始精确内容**。以下代码作为一次性任务命令运行，不新增运行时代码：

```python
import importlib.util
import json
from pathlib import Path

root = Path.cwd()
spec = importlib.util.spec_from_file_location("inventory", root / "tools/business_model_inventory.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
report = module.inventory(
    Path("/Users/derek/Library/Application Support/ceo-agent-service/auto-reply.sqlite3"), root
)
fields = []
for table, columns in report["tables"].items():
    for column in columns:
        fields.append({
            "table": table, "field": column["name"],
            "storage": "generated" if column["hidden"] else "stored",
            "disposition": "retain_current",
            "owner": f"{table}.{column['name']}",
            "reason": "现有职责默认保留；只有逐项有证据的合并/删除才改此决策",
        })
(root / "docs/business-model-ownership.json").write_text(
    json.dumps({"fields": fields, "references": report["references"]}, ensure_ascii=False, indent=2) + "\n"
)
coverage = [
    {"route": route, "scenarios": [
        {"name": state, "result": "not_run", "evidence": []}
        for state in ("loading", "empty", "populated", "api_error", "refresh_back")
    ]}
    for route in report["routes"]
]
(root / "docs/business-model-ui-coverage.json").write_text(
    json.dumps({"routes": coverage}, ensure_ascii=False, indent=2) + "\n"
)
```

`retain_current` 是保守处置，不是占位的删除许可；`not_run` 明确表示未执行，不能算验证。字段清单完整不等于所有字段已被证明可删除。

- [ ] **Step 2: 逐项记录总计划 A/B/C/D 的真实 owner**。每个改为 merge/delete/derive 的字段条目加入以下全部键，保留 JSON 值为实际代码证据：`source_function`、`readers`、`writer_to_remove`、`target_existing_field`、`forward_rule`、`inverse_rule`、`scenario_ids`。无目标已有字段的不能编造目标列；如果属于功能取消，disposition 写 cancel_feature 并说明具体用户可见行为。
- [ ] **Step 3: 两种不能合并的反例写入决策文档**：邮件 model_text 与 normalized_text 不同但合法；旧 work_project 是工作聚类而无正式项目证据。记录 retain_current 或保留已有语义，不能把生产部分值相等当作所有未来状态相等。
- [ ] **Step 4: 从总计划第 8 节给每条路由补充所有适用交互和业务场景**；已有 redirects/别名/动态参数/404 都必须在记录中。没有本轮影响的页面仍做冒烟，不能删除该 route 行。
- [ ] **Step 5: 运行字段/路由穷尽性检查**：

```python
import importlib.util
import json
from pathlib import Path

root = Path.cwd()
spec = importlib.util.spec_from_file_location("inventory", root / "tools/business_model_inventory.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
report = module.inventory(
    Path("/Users/derek/Library/Application Support/ceo-agent-service/auto-reply.sqlite3"), root
)
fields = json.loads((root / "docs/business-model-ownership.json").read_text())["fields"]
assert len(fields) == len({(row["table"], row["field"]) for row in fields})
assert {(row["table"], row["field"]) for row in fields} == {
    (table, column["name"]) for table, columns in report["tables"].items() for column in columns
}
for row in fields:
    if row["disposition"] != "retain_current":
        assert all(row.get(key) for key in (
            "source_function", "readers", "writer_to_remove", "forward_rule", "inverse_rule", "scenario_ids"
        ))
routes = json.loads((root / "docs/business-model-ui-coverage.json").read_text())["routes"]
assert len(routes) == len({row["route"] for row in routes})
assert {row["route"] for row in routes} == set(report["routes"])
assert all(row["scenarios"] for row in routes)
print("field and route coverage exact; behavioral checks remain explicitly not_run")
```

预期：上面的确切输出；此检查不验证页面实际运行。运行时业务证据仍按总计划副本/CLI/浏览器场景获取。

- [ ] **Step 6: 提交清单文档**：

```bash
git add docs/business-model-ownership.json docs/business-model-ui-coverage.json
git commit -m "docs(storage): assign field owners and enumerate console acceptance"
```

### Task 4: 为每个独立发布单元固定准确迁移补丁与验收入口

- [ ] **Step 1: 依次按总计划 A1/A2、B1、A3、B2、C、D 的范围读取函数**。对 schema 的每次更改输出完整目标 DDL、原→新字段规则、现有调用方逐项修改内容和 paired inverse 的完整代码；这些是对应单元的实施输入，不能沿用此次只读工具作为迁移器。
- [ ] **Step 2: 每个单元的精确设计加入“迁移后新增事实”反例**：新候选修订、新回执、新输入版本、新邮件、旧业务对象拆为多个事实。无法由新模型重建旧模型的情况必须先选择有既有字段依据的表达并测试，不允许用恢复整库文件规避。
- [ ] **Step 3: 把对应完整代码和 RED 测试写入该单元实施文档**，测试使用原始 source 独立 fixture；覆盖预期退出的 writer、schema 和读取投影。未形成完整 patch 的单元不得发布。
- [ ] **Step 4: 按总计划第 7/8 节逐个运行副本、真实 CLI、替身 provider、页面和 inverse**。每条场景填实际 PASS/失败与证据；not_run 不能转成“无影响”。
- [ ] **Step 5: 只在该单元所有条件已通过后按现有 app.deploy 发布**。真实旧版本冷启动和当前事实 inverse 不通过时留在隔离 worktree，不触碰生产。

本 Task 的交付是逐单元的精确 schema/patch/测试设计；不意味着执行本清单即可自动实现四个子系统。总计划负责顺序和完成标准，首子计划负责可运行事实清单。这里明确保留设计工作，避免为满足“完整计划”而发明尚未验证的 DB 字段或虚假的迁移 API。

## 自检与交接

- [ ] 代码块全部 ast.parse 通过；工具可独立运行，不导入 app。
- [ ] 5 个 RED/GREEN 测试的失败原因和通过结果可追溯。
- [ ] 生成字段清单等于实际 table_xinfo；零数据类型的声明另行人工核对。
- [ ] 路由完整匹配；每个具体页面与别名都有覆盖行。
- [ ] 检查所有 merge/delete/cancel_feature 条目有实际代码证据、正逆规则和场景。
- [ ] 不把这些文档/测试通过报告为模型迁移或生产发布完成。

执行选择：Subagent-Driven 或 Inline Execution。选择后使用对应 skill；当前仅写计划，不启动子 agent 或实施。
