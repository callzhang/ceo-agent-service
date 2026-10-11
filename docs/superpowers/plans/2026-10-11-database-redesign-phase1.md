# Provider Lookup Index Deduplication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 本阶段可由当前会话顺序执行，无需为了机械索引变更启动多名 agent。

**Goal:** 移除重复 provider lookup 索引，保持当前 provider 库存、查询结果及现有主键/文件夹索引不变。

**Architecture:** Email schema 从当前 47 升到 48，在现有初始化事务内删除重复索引并记版本；新建库不再创建或要求该索引。只调整 schema，不添加业务表、修改保留期限、合并业务记录或重放动作。

**Tech Stack:** Python 3.11+、SQLite、现有 EmailStore migration/validation、pytest、uv。

---

工作目录：`/Users/derek/.codex/worktrees/database-redesign-plan/ceo-agent-service`；基线 `36e42c17`。开始代码执行前更新 docs/agent-claims.md，声明 app/email_store.py、两个相关测试文件和 architecture/runtime 文档；本计划提交目前只声明文档。若届时 origin/main 已占用 schema 48，先 rebase/更新干净工作树，再用实际下一个版本同步所有测试，不覆盖其他人的 migration。

## Task 1：加入索引迁移回归测试

**Files:**
- Create: `tests/test_email_provider_lookup_index.py`
- Read: `app/email_store.py:3018` 初始化事务及 schema validator

- [ ] **Step 1: 创建完整测试文件**

```python
import sqlite3

import pytest

from app.email_store import EmailPersistenceCorruption, EmailStore
from tests.test_email_store import _classification, _persist_scan

LOOKUP = "idx_email_provider_observations_lookup"
FOLDER = "idx_email_provider_observations_folder"
POINT_SQL = ("select state, observed_at from email_provider_observations "
             "where account_id=? and stable_message_identity=?")
FOLDER_SQL = ("select stable_message_identity from email_provider_observations "
              "where account_id=? and provider_folder_id=?")


def index_names(db):
    return {row[1] for row in db.execute("pragma index_list(email_provider_observations)")}


def seed_provider(path):
    store = EmailStore(path)
    _persist_scan(store, _classification())
    with sqlite3.connect(path) as db:
        db.execute(
            "insert into email_provider_observations "
            "(account_id,stable_message_identity,state,provider_folder_id,"
            "provider_folder_name,category_key,important,important_signals_json,observed_at) "
            "values (?,?, 'available', 'INBOX', 'INBOX', null,0,'[]',?)",
            ("dingtalk-account", "provider-example", "2026-10-11T00:00:00Z"),
        )
    return store


def snapshot(path):
    with sqlite3.connect(path) as db:
        return {table: db.execute(f"select * from {table} order by rowid").fetchall()
                for table in ("email_accounts", "email_messages", "email_classifications",
                              "email_action_plans", "email_actions", "email_scan_cursors",
                              "email_provider_observations")}


def legacy_v47(path):
    seed_provider(path)
    with sqlite3.connect(path) as db:
        db.execute(
            f"create index if not exists {LOOKUP} on email_provider_observations"
            "(account_id,stable_message_identity,observed_at desc)"
        )
        db.execute("delete from email_schema_migrations where version>=47")
        db.execute("insert into email_schema_migrations(version,applied_at) values (47,?)",
                   ("2026-10-11T00:00:00Z",))
    return path


def test_new_schema_omits_lookup_but_keeps_folder_and_pk(tmp_path):
    path = tmp_path / "fresh.sqlite3"
    EmailStore(path)
    with sqlite3.connect(path) as db:
        indexes = index_names(db)
        assert LOOKUP not in indexes
        assert FOLDER in indexes
        assert any(row[3] == "pk" for row in db.execute(
            "pragma index_list(email_provider_observations)"))


def test_v47_migration_preserves_business_rows_and_reopens_idempotently(tmp_path):
    path = legacy_v47(tmp_path / "legacy.sqlite3")
    before = snapshot(path)
    EmailStore(path)
    with sqlite3.connect(path) as db:
        assert LOOKUP not in index_names(db)
        assert FOLDER in index_names(db)
        assert db.execute("select max(version) from email_schema_migrations").fetchone()[0] == 48
        versions = db.execute("select * from email_schema_migrations order by version").fetchall()
        assert db.execute("pragma foreign_key_check").fetchall() == []
        assert db.execute("pragma quick_check").fetchone()[0] == "ok"
    assert snapshot(path) == before
    EmailStore(path, validate_rows=False)
    EmailStore(path)
    with sqlite3.connect(path) as db:
        assert LOOKUP not in index_names(db)
        assert db.execute("select * from email_schema_migrations order by version").fetchall() == versions
    assert snapshot(path) == before


def test_failed_migration_restores_lookup_version_and_rows(tmp_path, monkeypatch):
    path = legacy_v47(tmp_path / "rollback.sqlite3")
    before = snapshot(path)
    original = EmailStore._validate_durable_state

    def fail_validation(self, db):
        if db.execute("select max(version) from email_schema_migrations").fetchone()[0] == 48:
            raise RuntimeError("injected provider-index migration failure")
        return original(self, db)

    monkeypatch.setattr(EmailStore, "_validate_durable_state", fail_validation)
    with pytest.raises(RuntimeError, match="injected provider-index migration failure"):
        EmailStore(path)
    with sqlite3.connect(path) as db:
        assert LOOKUP in index_names(db)
        assert db.execute("select max(version) from email_schema_migrations").fetchone()[0] == 47
    assert snapshot(path) == before


def test_lookup_uses_pk_and_folder_query_keeps_its_index(tmp_path):
    path = tmp_path / "query.sqlite3"
    seed_provider(path)
    with sqlite3.connect(path) as db:
        assert db.execute(POINT_SQL, ("dingtalk-account", "provider-example")).fetchall() == [
            ("available", "2026-10-11T00:00:00Z")]
        point_plan = " ".join(row[3] for row in db.execute(
            "explain query plan " + POINT_SQL, ("dingtalk-account", "provider-example")))
        assert "sqlite_autoindex_email_provider_observations" in point_plan
        folder_plan = " ".join(row[3] for row in db.execute(
            "explain query plan " + FOLDER_SQL, ("dingtalk-account", "INBOX")))
        assert FOLDER in folder_plan
        assert db.execute(FOLDER_SQL, ("dingtalk-account", "INBOX")).fetchall() == [
            ("provider-example",)]
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("insert into email_provider_observations select * "
                       "from email_provider_observations where stable_message_identity=?",
                       ("provider-example",))


def test_required_folder_index_is_not_silently_recreated(tmp_path):
    path = tmp_path / "broken.sqlite3"
    EmailStore(path)
    with sqlite3.connect(path) as db:
        db.execute(f"drop index {FOLDER}")
    with pytest.raises(EmailPersistenceCorruption, match=FOLDER):
        EmailStore(path, validate_rows=False)
```

- [ ] **Step 2: 运行新测试，验证明确失败**

```bash
uv run --extra dev pytest -q tests/test_email_provider_lookup_index.py
```

预期当前 schema 47 的 fresh 测试因 LOOKUP 仍存在失败，migration 测试因没有 48 失败；不是因导入或运行环境错误失败。所有 DB 都由 tmp_path 创建。

## Task 2：最小 schema 48 迁移

**Files:**
- Modify: `app/email_store.py:74`、`:966`、`:3207`、`:5380`、`:6425`

- [ ] **Step 1: 版本常量和迁移链**

将 `EMAIL_SCHEMA_VERSION = 47` 改为 `48`。在 46→47 分支之后、最终 `_validate_durable_state` 之前加入：

```python
            if latest_version == 47:
                self._migrate_v47_to_v48(db, replace_version=is_prototype)
                latest_version = 48
```

- [ ] **Step 2: 添加完整 migration 方法**

```python
    def _migrate_v47_to_v48(
        self, db: sqlite3.Connection, *, replace_version: bool = False
    ) -> None:
        """Use the existing provider primary key for identity lookups."""
        db.execute("drop index if exists idx_email_provider_observations_lookup")
        if replace_version:
            db.execute(
                "update email_schema_migrations set version=48, applied_at=? "
                "where version=47", (self._now(),),
            )
        else:
            db.execute(
                "insert into email_schema_migrations(version, applied_at) values (48, ?)",
                (self._now(),),
            )
```

只使用现有初始化事务，不新增事务/锁/异常 fallback。失败由现有 connection transaction rollback。

- [ ] **Step 3: 删除新建与 required-schema 中的重复索引**

从 required index mapping 删除以下整个条目：

```python
    "idx_email_provider_observations_lookup": (
        "email_provider_observations",
        ("account_id", "stable_message_identity", "observed_at"),
    ),
```

从 `_create_indexes_and_triggers` 的 SQL 列表删除以下整个字符串项：

```sql
create index if not exists idx_email_provider_observations_lookup
on email_provider_observations(
    account_id, stable_message_identity, observed_at desc
)
```

不得删除 `idx_email_provider_observations_folder`、主键、分类 locator 索引或任何业务触发器。

- [ ] **Step 4: 验证新测试全部通过**

```bash
uv run --extra dev pytest -q tests/test_email_provider_lookup_index.py
```

预期 5 passed。若原始 seed 的 durable 校验失败，按当前已有 _persist_scan/record_current_provider_observations 合法 fixture 修正测试，禁止降低生产 validator。

## Task 3：保持此前 locator migration 测试的完整性

**Files:**
- Modify: `tests/test_email_classification_locator_index.py`

- [ ] **Step 1: 更新升级后的版本历史断言**

现有两个 `== [(46,), (47,)]` 改为：

```python
        assert db.execute("select version from email_schema_migrations order by version").fetchall() == [(46,), (47,), (48,)]
```

保持升级前 46 的断言、业务行相等、index sort/collation/covering 和 FK 检查不变。

- [ ] **Step 2: 让故障注入仍在最终校验发生**

文件导入改为：

```python
from app.email_store import EMAIL_SCHEMA_VERSION, EmailPersistenceCorruption, EmailStore
```

`fail_after_migration` 的版本判断改为：

```python
        if db.execute("select max(version) from email_schema_migrations").fetchone()[0] == EMAIL_SCHEMA_VERSION:
            raise RuntimeError("injected locator migration failure")
```

仍要求整个 46→47→48 事务失败回滚到 46，不得通过删测试规避 migration 行为。

- [ ] **Step 3: 执行精确测试组**

```bash
uv run --extra dev pytest -q tests/test_email_provider_lookup_index.py tests/test_email_classification_locator_index.py tests/test_email_store_startup_validation.py tests/test_email_folder_truth.py tests/test_email_provider_actions.py
```

预期全部通过。不要运行全套串行 pytest；只有出现相关失败或读写行为改变时扩大到 test_email_store、test_email_web_api 和 test_email_pipeline。

## Task 4：副本演练和同提交文档

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/runtime-mechanism.md`
- Modify: `docs/agent-claims.md`
- Read: `app/database_backup.py:create_database_backup`

- [ ] **Step 1: 在 task-owned 临时目录演练一致副本**

下面是独立演练命令内容，不保存生产数据副本到仓库。命令不输出业务行或凭据，只输出统计；使用 Python 临时目录在结束时清理。

```bash
uv run --extra dev python - <<'PY'
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
from app.database_backup import create_database_backup
from app.email_store import EmailStore

source = Path('/Users/derek/Library/Application Support/ceo-agent-service/auto-reply.sqlite3')
with TemporaryDirectory(prefix='ceo-provider-index-', dir='/tmp') as directory:
    copy = create_database_backup(source, Path(directory) / 'copy.sqlite3')
    with sqlite3.connect(copy) as db:
        assert db.execute('pragma quick_check').fetchone()[0] == 'ok'
        before = db.execute('select * from email_provider_observations order by account_id,stable_message_identity').fetchall()
        index_before = db.execute("select sum(pgsize) from dbstat where name='idx_email_provider_observations_lookup'").fetchone()[0] or 0
        before_bytes = copy.stat().st_size
    EmailStore(copy)
    with sqlite3.connect(copy) as db:
        assert db.execute('select * from email_provider_observations order by account_id,stable_message_identity').fetchall() == before
        assert db.execute('pragma foreign_key_check').fetchall() == []
        assert db.execute('pragma integrity_check').fetchone()[0] == 'ok'
        assert db.execute("select count(*) from sqlite_master where name='idx_email_provider_observations_lookup'").fetchone()[0] == 0
        assert db.execute("select count(*) from sqlite_master where name='idx_email_provider_observations_folder'").fetchone()[0] == 1
        db.execute('pragma wal_checkpoint(truncate)')
        db.execute('vacuum')
    print({'provider_rows': len(before), 'removed_index_bytes': index_before,
           'copy_bytes_before': before_bytes, 'copy_bytes_after_vacuum': copy.stat().st_size})
PY
```

预期 quick/integrity/FK 通过、provider 行逐值相等、重复索引消失、folder 索引存在。这个演练只证明副本，不证明生产已释放空间。before 与 after 的文件差值包含该副本已有空页，不能全部归因本次索引。

- [ ] **Step 2: 在测试通过后更新行为文档**

architecture.md 增加/更新：

```text
Email provider 当前状态以 (account_id, stable_message_identity) 主键定位，
按账户/文件夹使用独立 folder 索引。schema 48 移除了覆盖同一身份键的
observed_at lookup 索引；provider 库存、刷新、分类及外部动作语义不变。
```

runtime-mechanism.md 增加/更新：

```text
Email schema 47→48 在现有初始化事务内移除重复 provider lookup 索引，
同时推进 schema 版本。新库不创建该索引；重复启动不重新创建。
folder 索引仍为 required schema，缺失时沿用既有失败行为。
DROP INDEX 只释放数据库页；文件空间回收是独立的演练/维护步骤。
```

- [ ] **Step 3: 审阅并仅提交本期 hunks**

```bash
git diff --check
git diff -- app/email_store.py tests/test_email_provider_lookup_index.py tests/test_email_classification_locator_index.py docs/architecture.md docs/runtime-mechanism.md docs/agent-claims.md
git add app/email_store.py tests/test_email_provider_lookup_index.py tests/test_email_classification_locator_index.py docs/architecture.md docs/runtime-mechanism.md docs/agent-claims.md
git commit -m "refactor(storage): remove duplicate provider identity lookup index"
```

若出现其他会话的新 hunks，用 git add -p 只提交本期。确认阶段代码和测试通过后再按既有流程 push/deploy；不要先 push 半完成 schema。上线前重新核对生产版本，若有并发 schema migration 重排版本并复测。

## 上线与回退

实现、测试、副本演练及提交后，使用已规定的 python -m app.deploy 路径；部署后的 PID、healthz、queues、Attention、History 均需回读。旧 schema 47 runtime 会拒绝 schema 48，所以不能只回滚代码；部署脚本现有备份/rollback 是否包含 DB 恢复必须另核实。生产产生新业务写入后不能恢复陈旧 DB。生产 VACUUM 不包含在本期默认部署，不按演练结果宣称 live 文件已缩小。

## 自检与状态

本计划覆盖 fresh DB、旧 schema、重复启动、失败回滚、主键唯一性、点查与文件夹查询、required schema 校验、已有 locator 索引回归、在线 provider 库存读取、副本 integrity/FK、文档及部署边界。未变动页面 DTO/业务状态，Email 页面显示需在实际发布后按主程序矩阵读回。计划中的代码尚未写入运行文件，命令尚未执行，测试与生产迁移均未完成。
