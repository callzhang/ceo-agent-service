# Project-centered Work Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在一个 Task Agent 内，以真实 Project 汇总多来源事实与人员分工，独立判断需关注，更新真实 Task 并展示按职责推导的建议，保留独立任务及全部历史。

**Architecture:** 沿用来源扫描、共享 session、Task 生命周期和现有页面。共享不可变原文；Project 持有独立的当前上下文和来源关系，Task 保留明确的原文安排/Agent 建议之分。项目更新与判断从 TaskDecision 中拆出，统一在同次处理返回；不增加 Agent、后台重评、自动派发或双写兼容路径。

**Tech Stack:** Python、Pydantic v2、SQLite、pytest；React、TypeScript、Vite、Vitest；现有 native Agent replay 与只读检查工具。

---

## 状态、边界与依赖

设计于 2026-10-04 获 Derek 确认：`../specs/2026-10-04-project-centered-work-design.md`。开发已在新对话开始，负责全部 Task 1–9：当前 Task 1 实施中，其余任务尚未实施。本次业务评测和发布尚未开始；复用旧测试通过记录不能勾选新任务。

执行记录（2026-10-04）：复用下列工作目录，基线 `64dc4c8d`，独立步骤采用单作者实施、需求审查、质量审查。Task 1 文件已认领；因正文列移除直接影响听记负责人回填 SQL 及两个 API 测试的原始 INSERT，认领范围增加这些直接受影响的读路径/fixture，仅改变存储表示，不改变负责人或 API 业务语义。冻结 W39 初态经只读检查仍为 259 Tasks、16 Projects、0 Attention、398 Signals、434 Task events，`quick_check=ok`；该检查不算本次迁移或业务回放通过。

工作目录：`/Users/derek/.codex/worktrees/task-attention-multisource/ceo-agent-service`。设计基线 `030378d7`；旧 native 结果冻结于 `da368453`，其首轮项目漏评与普通进展判断问题尚未解决。本计划替代旧 Attention 计划中与新设计冲突的后续实现，不删除旧实验或把它们改成成功。

任务依赖：`1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9`。核心顺序完成，API 契约稳定后才可把页面实现单独交给一个作者。每个任务先写失败回归、确认失败原因，再实现、读回、更新对应行为文档并提交。各任务可独立验证代码，但 **Task 1–7 是一个整体发布单元，不能把中间状态部署上线**。

执行前读取 `/Users/derek/.agents/AGENTS.md`、本仓库 `AGENTS.md`、`docs/architecture.md`、`docs/runtime-mechanism.md`，核对 `docs/agent-claims.md` 并只认领当前步骤涉及的文件。保留其他作者的改动；按普通合并纳入新的 `origin/main`，不重写他人提交。以下行号是设计基线定位，实施前按函数名重新定位。

不做：自动派活、周期性完成检查、模型更换、关键词分类、额外审批/审计体系、通用知识图谱、组织职责引擎、读路径自愈、全量生产重放。只运行覆盖改动的测试文件，不在运行服务的开发树串行跑全套。

## 文件职责

| 文件 | 本次职责 |
| --- | --- |
| `app/task_semantic_models.py` | 来源、项目上下文及任务来源的持久化类型 |
| `app/task_source_documents.py`（新） | 纯函数：精确来源版本身份与本轮共享正文组织，不作业务分类 |
| `app/store.py` | 共享正文、项目上下文版本/证据关系及任务字段；一次性结构迁移与有界读写 |
| `app/project_context_service.py`（新） | 同一事务中保存已解析 Project 上下文及来源关系，不调用 Agent |
| `app/task_semantic_service.py` | 在既有 Task 表记录展示型建议，保留真实指派/接受语义 |
| `app/task_retrieval.py` | 项目资料独立于 Task 检索，Task/Project 使用共享正文引用 |
| `app/task_models.py` | 当前 Agent 输出：独立项目决定、任务决定、项目评估 |
| `app/task_agent.py` | prompt、当前输出应用及真实 ID 回执；删除旧嵌套项目/关注入口 |
| `app/task_business_resolution.py` | 复用真实项目登记与关联，不从相似性新造项目 |
| `app/task_attention_projection.py` | 允许零 Task 的项目关注；Task 仅为可选的真实成员 |
| `app/web_api/tasks.py` | 项目与建议的页面契约，删除 Task 周报反推项目的读模型 |
| `frontend/src/api/console.ts`、现有 Tasks 页面/组件 | 明确项目分工、任务来源和建议人选，保持列表密度 |
| `scripts/inspect_task_attention.py`、`scripts/replay_task_attention.py` | 只读诊断、同案例旧/新对照和重复处理验证 |
| `ci/shared-skills/ceo-work-tracking/SKILL.md`、行为文档 | 与实现同提交更新；全局 Skill 不提前发布 |

## 契约约定（本计划统一使用这些名称）

1. `SourceCitation`：`signal_id`（当前 wire 可空，保存后为实际 ID）、`source_ref`、`source_excerpt`。所有摘录仍回到来源原文，原有引用核验不扩展成另一套策略。
2. `ProjectResponsibility`：`person_user_id`、`person_name`、`responsibility`、`evidence: list[SourceCitation]`。
3. `ProjectContext`：`goal`、`scope`、`overall_owner: ProjectResponsibility | None`、`responsibilities: list[ProjectResponsibility]`、`facts: list[ProjectFact]`。`ProjectFact` 含 `key`、`text`、`evidence`、`date_type` 和 `date_value`；日期两字段为空表示普通事实，不猜时间。`key` 是 Agent 选择的项目内事实身份，不是关键词分类规则。
4. `ProjectDecision`：`anchor_id` 或 `registration: ProjectProposal` 二选一，`context: ProjectContext | None`、非空 `evidence`、`reason`。`context=null` 只补证据，不覆写上下文；提供 context 时为读取当前资料后形成的完整当前快照。替换的旧值保留在版本历史。
5. `ProjectSelector`：`anchor_id` 或 `project_decision_index` 二选一。索引指向新的顶层 `project_decisions`，不再指向 `task_decisions`。无正式身份的线索由 assessment 保留原名与证据，不创建正式 Project。
6. `TaskSuggestion`：`reason`、`suggested_owner_user_id`、`suggested_owner_name`、`responsibility_evidence`、`basis_evidence`。建议负责人不写入实际 `owner_name`；不伪造 assignment 或 acceptance。任务本身仍在 `business_tasks`。
7. `BusinessTask.origin`：`source` 或 `agent_suggestion`；历史任务迁移为 source（含历史候选，不代表提升为正式）。另加 `suggestion_json` 保存建议依据。候选阶段的 agent_suggestion 显示为建议；后来有真实人类指派后沿用同一 ID 晋升，origin 保留发现历史，实际负责人来自人类证据。

固定 schema 标签只表达对象类型/生命周期，不按文本关键词决定项目、人物或风险。`SourceCitation` 定义在 `task_semantic_models.py`，wire 与 domain 共用，避免互相 import 造成循环。

## Task 1：来源正文共享，保留原来的证据身份

**Files:** 新建 `app/task_source_documents.py`、`tests/test_task_source_documents.py`；修改 `app/store.py` 的 Business Signal DDL/写入/读取、`app/task_semantic_models.py:164`、`tests/test_task_semantic_store.py`、`docs/task-semantic-storage.md`。

- [ ] 在新测试文件先写精确身份回归：同一原文的两个任务 Signal 共享正文；不同 ref、source type、原文版本或作者不共享；不把 memory/session 引文升级为已观察原文。

```python
from app.task_source_documents import source_document_key

def test_body_identity_does_not_use_task_dedupe_key():
    source = dict(source_type="message", source_ref="m:1", source_time="t1",
                  conversation_id="c1", author_user_id="u1", author_name="张三",
                  author_kind="human", evidence_text="李四负责材料，王五负责商务。")
    assert source_document_key(**source) == source_document_key(**dict(source))
    assert source_document_key(**source) != source_document_key(
        **(source | {"evidence_text": "李四已提交材料，王五负责商务。"}))
    assert source_document_key(**source) != source_document_key(
        **(source | {"source_type": "memory_provenance"}))
    assert source_document_key(**source) != source_document_key(
        **(source | {"author_user_id": "u2"}))
```

- [ ] 运行 `python -m pytest -q tests/test_task_source_documents.py`，确认因尚无 helper 而 RED。实现以下纯函数（不做空白归一化或正文相似合并）：

```python
import hashlib
import json

def source_document_key(*, source_type: str, source_ref: str, source_time: str,
                        conversation_id: str, author_user_id: str, author_name: str,
                        author_kind: str, evidence_text: str) -> str:
    identity = [source_type, source_ref, source_time, conversation_id,
                author_user_id, author_name, author_kind, evidence_text]
    encoded = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
```

- [ ] 增加共享表，Signal 新增 `source_document_id` 指向它。Signal ID、dedupe_key、每条来源元数据和 Task evidence 关系不变；所有正文读取统一 JOIN 共享表，返回的 `BusinessTaskSignal.evidence_text` 保持原文接口，调用者不另写兼容分支。

```sql
CREATE TABLE business_source_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_key TEXT NOT NULL UNIQUE,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_business_signal_document
    ON business_task_signals(source_document_id);
```

- [ ] 在 Store 既有 schema 初始化/升级事务中，对旧 Signal 按上述精确身份逐条迁移正文并填引用，保持 ID 及所有关系。完成逐条读回相等、外键和行数核对后，迁移中移除 Signal 的重复正文列，之后只有一条 JOIN 读取路径；新库直接建最终结构。历史 run/event JSON 不重写。这是数据表示迁移，不做业务合并；先仅在临时测试库与备份副本验证。
- [ ] `tests/test_task_semantic_store.py` 增加真实旧库→新库回归：两个同正文 Signal、独立 evidence 角色、一个不同作者 Signal；迁移前后每个 Signal 原有公开字段、任务与事件完全相同，正文行数为 2；再次初始化无新增正文。增加当前 observer 与 memory provenance 同 ref 的分离用例。
- [ ] 运行 `python -m pytest -q tests/test_task_source_documents.py tests/test_task_semantic_store.py tests/test_task_semantic_service.py`，预期 GREEN；读回数据库而非只检查函数返回。
- [ ] 同提交更新 schema 版本标记、存储说明和 Signal 读取契约；提交 `refactor(tasks): share immutable source bodies without changing evidence identities`。本步骤不得自行对生产库运行迁移。

## Task 2：Project 独立上下文、唯一总负责与人员分工

**Files:** 修改 `app/task_semantic_models.py:308`、`app/store.py:3863`、`app/task_business_resolution.py:150`；新建 `app/project_context_service.py`、`tests/test_project_context_service.py`；修改 `tests/test_task_semantic_store.py`、`docs/architecture.md` 和 `docs/runtime-mechanism.md` 的项目部分。

- [ ] 在 semantic models 定义上述 `SourceCitation`、`ProjectResponsibility`、`ProjectFact`、`ProjectContext`。`overall_owner` 是单个对象或空值，不接受列表；每条负责事项必须有人员名、非空职责及来源。持久化要求 citations 已解析为实际 Signal，不能猜未来 ID。

新增类型主体如下，复用该文件已有的 `_FrozenBusinessModel`、`Nonblank`、`ReferenceId`；`Field` 从 pydantic 导入。职责冲突保留为有来源的 facts，当前总负责未知时置空，而不是把两个名字串进一个人名字段。

```python
class SourceCitation(_FrozenBusinessModel):
    signal_id: ReferenceId | None = None
    source_ref: Nonblank
    source_excerpt: Nonblank

class ProjectResponsibility(_FrozenBusinessModel):
    person_user_id: str = ""
    person_name: Nonblank
    responsibility: Nonblank
    evidence: list[SourceCitation] = Field(min_length=1)

class ProjectFact(_FrozenBusinessModel):
    key: Nonblank
    text: Nonblank
    evidence: list[SourceCitation] = Field(min_length=1)
    date_type: str = ""
    date_value: str = ""

class ProjectContext(_FrozenBusinessModel):
    goal: str
    scope: str
    overall_owner: ProjectResponsibility | None
    responsibilities: list[ProjectResponsibility]
    facts: list[ProjectFact]

class TaskSuggestion(_FrozenBusinessModel):
    reason: Nonblank
    suggested_owner_user_id: str = ""
    suggested_owner_name: str = ""
    responsibility_evidence: list[SourceCitation]
    basis_evidence: list[SourceCitation] = Field(min_length=1)
```

日期字段必须同时提供或同时为空；指定建议人选时 responsibility_evidence 不得为空，未指定人选则允许空。用两个 model validator 和正反例验证这两条结构关系，不能用关键词判定职责是否合理。
- [ ] 先写模型回归：一个总负责＋两项分工成功；两个总负责人数组失败；未知总负责但有项目事实成功。测试数据如下，固定来源不包含任何模型预期结论：

```python
def test_project_context_has_one_overall_owner():
    import pytest
    from pydantic import ValidationError
    from app.task_semantic_models import ProjectContext
    proof = [{"signal_id": 1, "source_ref": "meeting:1",
              "source_excerpt": "张三总负责，李四负责材料。"}]
    owner = {"person_user_id": "u1", "person_name": "张三",
             "responsibility": "交付与验收", "evidence": proof}
    context = ProjectContext(goal="完成一期交付", scope="一期",
                             overall_owner=owner, responsibilities=[], facts=[])
    assert context.overall_owner.person_name == "张三"
    with pytest.raises(ValidationError):
        ProjectContext(goal="交付", scope="一期", overall_owner=[owner, owner],
                       responsibilities=[], facts=[])
```

- [ ] 运行 `python -m pytest -q tests/test_project_context_service.py`，确认 RED 后增加两个小表：`business_project_context_revisions(id, project_id, context_json, evidence_json, created_at)`，索引 `(project_id, id DESC)`；`business_project_evidence(project_id, signal_id, created_at)`，主键 `(project_id, signal_id)`、两个真实对象外键。当前上下文取最新版本，不再另存一份可失配的 current JSON。
- [ ] 实现 `ProjectContextService.apply(*, project_id, context, signal_ids, db) -> int | None`：核对现存正式 Project 和实际 Signal；插入尚不存在的证据关系；读取最后快照；context 为空或规范化 JSON 与上次相等时不追加快照；变更时追加并返回真实版本 ID。函数使用调用方同一事务，不自行调用 Agent 或提交外部操作。
- [ ] 快照比较固定为结构比较，不把 dict 顺序当业务变化。实现服务内纯比较方法：

```python
def context_changed(previous_json: str | None, current_json: str) -> bool:
    import json
    if previous_json is None:
        return True
    return json.loads(previous_json) != json.loads(current_json)
```

- [ ] Store 提供 `get_business_project_context(project_id)`、`list_business_project_context_revisions(project_id)`、`list_business_project_evidence(project_id)`；事务内对应方法沿用 `_db` 风格。`BusinessProject` 读取提供当前 context；无记录是未知，不从任意 Task owner 补全。
- [ ] 在真实 SQLite 测试登记一个零 Task 项目，存一版分工，再更新负责人；断言当前只有新负责人、两版历史均可读、证据关系均保留；相同输入再应用，版本/关系行数不变。再测试缺失来源导致事务不写入、context=null 保留原分工、明确空分工快照与“不更新”不同。
- [ ] 运行 `python -m pytest -q tests/test_project_context_service.py tests/test_task_business_resolution.py tests/test_task_semantic_store.py`，预期 GREEN。同步行为文档并提交 `feat(projects): persist source-backed project context and responsibilities`。

## Task 3：在同一个 Task 体系记录“Agent 建议”

**Files:** 修改 `app/task_semantic_models.py:180`、`app/task_semantic_service.py`、`app/store.py`；新建 `tests/test_task_suggestions.py`；回归 `tests/test_todo_sync.py`、`tests/test_task_semantic_rules.py`；同步存储与运行文档。

- [ ] 增加 `BusinessTask.origin` 和 `suggestion_json`，旧行默认 source，不改变 stage/status/owner/commitment。`TaskSuggestion` 为有类型的建议资料；实际 owner 字段继续只承载实际安排，建议人选只在 suggestion 中。
- [ ] 添加命令 `RecordTaskSuggestion(title, description, signal, suggestion, project_anchor_id, task_id=None)` 和 `TaskSemanticService.record_suggestion(command)`。新建沿用候选 Task 存储、证据、事件及 project link 操作；更新要求引用已存在建议的 task_id，保留其身份。这里的 signal 是实际原始来源，不把 Agent 生成的理由伪装成原文。
- [ ] 先写以下状态回归（测试文件中构造实际 SourceSignal、登记项目和职责，再调用新命令，不 mock Store）：

```python
def assert_display_only_suggestion(task):
    assert task.origin == "agent_suggestion"
    assert task.stage.value == "candidate"
    assert task.commitment_status.value == "none"
    assert task.formal_basis is None
    assert task.owner_name == ""
    assert task.owner_user_id == ""
    assert task.deadline_at == ""
```

- [ ] 运行 `python -m pytest -q tests/test_task_suggestions.py`，确认状态/新命令 RED。实现存储时的新行固定值：

```python
suggestion_fields = {
    "origin": "agent_suggestion", "stage": "candidate", "status": "open",
    "formal_basis": None, "commitment_status": "none",
    "owner_user_id": "", "owner_name": "", "owner_evidence_json": "{}",
    "deadline_at": "",
}
```

- [ ] 建议幂等使用现有 Task 身份及证据幂等机制，不用“reason 文案”生成新身份；重复已知 task_id 更新只有实际建议资料变化才记事件。没有 ID 时先由 Agent 比较已检索事项，不用相似度代码自动合并。
- [ ] 增加三项真实库回归：新消息未点名王五但已有职责证明可存建议；后续人的明确指派晋升同一 ID、原建议保留、实际 owner 才写入；重复输入无第二条任务、接受事件、follow-up 或 TODO outbox。原始待明确事项保持 origin=source，不和建议混成一个来源标签。
- [ ] 使用既有候选→正式与 outbox 语义，不另加工具权限层或自动派发路径。检验建议未满足现有正式/接受条件，因此没有外发；转为真实已接受任务后按原生命周期运行，不能因历史 origin 永久阻止它。
- [ ] 运行 `python -m pytest -q tests/test_task_suggestions.py tests/test_task_semantic_service.py tests/test_task_semantic_rules.py tests/test_todo_sync.py`，预期 GREEN。同步文档并提交 `feat(tasks): distinguish displayed agent suggestions from real assignments`。

## Task 4：项目上下文优先检索，共同原文只装载一次

**Files:** 修改 `app/task_retrieval.py:46,153,315`、`app/task_source_documents.py`、`app/store.py` 相关有界查询；测试 `tests/test_task_retrieval.py`、`tests/test_task_source_documents.py`；同步运行文档。

- [ ] 先扩展现有真实库 fixture：零 Task 的已登记 Project 有职责/来源/风险；同一来源涉及两个项目；80 个 Signal 指向少量共享正文；source/memory 同 ref 不同身份；保留实际 Attention 成员与项目同伴 Task 的区分。
- [ ] 运行 `python -m pytest -q tests/test_task_retrieval.py -k 'project_context or shared_source or project_without_task'`，新增用例必须实际被收集并 RED，不接受“0 tests”作为验证。
- [ ] `TaskSemanticContext` 增加相关 `project_contexts` 和 `project_evidence`。保留已有正式项目匹配入口，从已选项目直接读取它的资料/证据，再读取相关 Tasks；同来源既有 Task 继续强制包含以避免重建。项目分工不再靠 Task owner 推测。
- [ ] `render_task_semantic_context` 用 `source_documents` 提供正文，用 `source_signals` 提供真实 Signal 身份及 `document_id`，不再在每个 Signal 中复制 body。引用仍使用实际 Signal ID；这只是输入呈现，不改变证明身份。核心装配函数如下：

```python
def source_bundle(signals):
    documents = {}
    references = []
    for signal in signals:
        document_id = signal.source_document_id
        documents.setdefault(document_id, {"id": document_id,
                                            "body": signal.evidence_text})
        reference = signal.model_dump(mode="json")
        reference.pop("evidence_text")
        reference["document_id"] = reference.pop("source_document_id")
        references.append(reference)
    return {"source_documents": list(documents.values()),
            "source_signals": references}
```

- [ ] 当前 WorkItem 正文也纳入同一来源呈现，不能在顶层 prompt 和 source_documents 再各放一遍；保留输入的 source/context/task_signals 元数据，原 immutable WorkItem 继续用于应用核验。长文沿已有预算处理，但输出实际可见字符区间及 full_length，不把首尾截取称为全量；当前正文与已有关键引用区间必须可定位。
- [ ] 断言正文出现一次、每条 Signal 引用可反解；不同版本仍有两份、被省略范围可见；无 Task 项目资料可检索；Task 来源 owner 校验与历史比较仍取得正确原文。不得因压缩而删掉来源时间、作者、引用类型或证明上下文。
- [ ] 运行 `python -m pytest -q tests/test_task_retrieval.py tests/test_task_source_documents.py tests/test_task_agent_session.py`，预期 GREEN；记录同一测试输入的前后正文字符数，不预先承诺固定压缩比例。同步文档并提交 `refactor(tasks): retrieve project context and share source bodies in prompts`。

## Task 5：同一 Agent 返回独立项目更新、任务与判断

**Files:** 修改 `app/task_models.py:379,451,662,730`、`app/task_agent.py:439,1092,1437,1764,2652,2981`、`app/project_context_service.py`、`app/task_business_resolution.py`；测试 `tests/test_task_models.py`、`tests/test_task_agent.py`、`tests/test_task_agent_session.py`；同步 `ci/shared-skills/ceo-work-tracking/SKILL.md`、`docs/architecture.md`、`docs/runtime-mechanism.md`。

- [ ] 先添加最小 envelope RED：必须有 `project_decisions`，零 Task 不等于零项目；新项目引用的是项目决定索引；越界/双重 ProjectSelector 拒绝；旧嵌套 project/attention 字段不被当前 parser 接受。历史 decision_json 原样读取，不通过当前 parser 重放。

```python
def test_current_envelope_requires_independent_project_decisions():
    import pytest
    from pydantic import ValidationError
    from app.task_models import TaskAgentDecision
    with pytest.raises(ValidationError, match="project_decisions"):
        TaskAgentDecision.model_validate({"task_decisions": [],
            "project_assessments": [], "update_summary": "没有新事项"})
    result = TaskAgentDecision.model_validate({"project_decisions": [],
        "task_decisions": [], "project_assessments": [],
        "update_summary": "本轮未涉及业务项目或具体任务"})
    assert result.project_decisions == []
```

- [ ] 运行 `python -m pytest -q tests/test_task_models.py -k independent_project`，确认 RED。实现 header 中的 ProjectDecision/ProjectSelector；TaskDecision 用 `project: ProjectSelector | None` 和 `project_link_evidence` 引用项目，用 `suggestion: TaskSuggestion | None` 表达推导，沿用 record_candidate/update_task 等真实 Task transition，不另建第二个候选状态机。
- [ ] `TaskAgentDecision` 的当前列表字段明确为下面三项，无旧输出自动升级；其余现存字段保持各自语义：

```python
project_decisions: list[ProjectDecision]
task_decisions: list[TaskDecision]
project_assessments: list[TaskProjectAssessment]
```

- [ ] TaskProjectAssessment 仍使用 `anchor_id`/`project_decision_index`、`project_title`、`outcome`、`reason`、`assessment_basis`、`evidence`、`decision_indexes`（Task 索引）、`task_ids`、`existing_attention_id`；新增自身的 `attention_proposal`。TaskAttentionProposal 去掉重复的 anchor/task selectors，由所属 assessment 的实际 Project 和真实成员唯一决定。`decision_indexes=[]`、`task_ids=[]` 是有效的项目判断。移除 TaskDecision 内旧 `project_proposal`、`project_link_proposal`、`attention_proposal`，逐个修改所有当前 producer/fixtures，不加旧字段 alias。
- [ ] 在现有领域事务中按实际依赖顺序应用：核验全部 selector/原始引用 → 登记/复用正式 Project → 保存 Project 来源与 context → 调用原 Task 生命周期或 record_suggestion → 收集项目判断及待应用 Attention。当前来源可独立形成原始 Signal，不再依赖某个 Task 才保存它。沿用现有实际来源类型校验、真实项目登记和精确标题复用，不放宽为聊天标题即正式项目。
- [ ] 上下文中每一条已确认职责须有其原始证明；更新当前快照时未改变的职责保留历史引用，不能拿新消息替换为无依据的人名。新任务建议引用职责来源与项目事实来源两个维度；对当前未点名的人选不执行“必须出现在当前消息”的正式指派判断，但也不提升为正式指派。
- [ ] 项目改变或建议产生不能触发无关 Task 的 update_fields、next_check、外部 TODO 或催办。对现有 Task 的真实更新继续走原业务方法；不以新项目输出绕开 Task 的接受、身份合并和日期语义。
- [ ] 修正 `process_work_item` 的完成与回执分支：项目-only 与建议-only 都是有实际业务结果的 run；空 Task 列表不再表示没有任何变化。回执分别记录 `project_decision_index → actual project/anchor/revision/signal IDs` 与 Task 决定映射；没有实际写入不能推测 ID。
- [ ] Prompt 与 Skill 同步替换为以下规则，删除与其冲突的旧 Task-first/只允许原文明示动作的绝对表述：

```text
先综合本次相关真实项目的原始资料、当前分工和已有任务。
项目发现与项目情况更新不以产生 Task 为前提；不把部门、主题或小任务当项目。
原文明示任务按证据记录和更新；按项目需要推导的动作标为 Agent 建议。
建议人选可依据项目分工或有出处的岗位职责；建议不等于指派或接受。
每个相关项目都说明需关注、不需关注或证据不足的具体原因。
没有 Task 不是证据不足的理由；正常进展不应仅因没有风险而判缺证据。
引用共享原文并区分事实与推断；不要把自己的摘要或上一轮建议当人类安排。
```

- [ ] 新增真实库回归：会议登记零 Task 项目；同一会议两个项目；一个项目两项动作；已有独立 Task 无需项目；同一 ID 从建议变真实安排；未明项目线索不注册；项目上下文更新但 Task 行/事件不变；一个成员 Task 完成不改其他成员或项目判断。
- [ ] 运行 `python -m pytest -q tests/test_task_models.py tests/test_task_agent.py tests/test_task_agent_session.py tests/test_project_context_service.py tests/test_task_suggestions.py`；更新当前输出 fixtures 后预期 GREEN。保留旧业务反例，只对本次批准改变的规则明确改预期。同步文档提交 `feat(tasks): apply project context and task decisions in one agent turn`。

## Task 6：项目关注不依赖 Task；保留真实应用回执

**Files:** 修改 `app/task_attention_projection.py:103,176,334`、`app/task_agent.py:2507,2652,2906`、`app/task_models.py` 回执类型、`scripts/inspect_task_attention.py`；测试 `tests/test_task_attention_projection.py`、`tests/test_task_attention_multisource.py`、`tests/test_inspect_task_attention.py`；同步架构/运行文档及 CI Skill。

- [ ] 先添加零 Task 关注的完整失败回归，使用已经存在的 Store/resolution/projection 入口。该测试要求 Task 2 的项目证据关系先成立：

```python
def test_project_attention_does_not_require_a_task(tmp_path):
    from app.store import AutoReplyStore
    from app.task_business_resolution import BusinessResolutionService
    from app.task_attention_projection import AttentionProposal, BusinessAttentionProjection
    from app.task_semantic_models import AttentionCategory
    store = AutoReplyStore(tmp_path / "project-attention.sqlite3")
    resolver = BusinessResolutionService(store)
    anchor_id = resolver.register_anchor(anchor_type="project", anchor_ref="project:p1",
                                         title="一期交付")
    project_id = resolver.register_official_project(anchor_id=anchor_id,
                                                   registry_source="meeting:1")
    signal_id = store.create_business_task_signal(source_type="message", source_ref="m:2",
        evidence_text="客户通知付款顺延，影响本期资金安排。", dedupe_key="m:2")
    with store.business_task_transaction() as db:
        from app.project_context_service import ProjectContextService
        ProjectContextService(store).apply(project_id=project_id, context=None,
                                           signal_ids=(signal_id,), db=db)
    proposal = AttentionProposal(stable_key=f"project:{anchor_id}",
        category=AttentionCategory.WATCH, title="付款顺延", business_area="交付回款",
        why_attention="影响本期资金安排", current_state="付款顺延",
        ceo_action="持续观察，当前无需处理", anchor_id=anchor_id,
        task_ids=(), evidence_signal_id=signal_id)
    projection = BusinessAttentionProjection(store)
    card_id = projection.upsert(proposal)
    before = store.get_business_attention_item(card_id)
    assert projection.upsert(proposal) == card_id
    assert store.get_business_attention_item(card_id) == before
    assert not store.list_business_tasks()
    assert not store.list_business_attention_tasks(card_id)
```

- [ ] 运行 `python -m pytest -q tests/test_task_attention_projection.py -k does_not_require_a_task`，预期在现有“requires at least one Task”处 RED（如实际集合为 tuple，按既有返回类型比较空集合，不改变业务断言）。
- [ ] `_validate_proposal` 去掉必有 Task 和必须通过 Task 连证据的条件，改为正式活动 Project 与 `business_project_evidence` 的真实关系；有 task_ids 时逐个核验真实关联，不能因为放开空集合而接受别的项目的 Task。普通进展/风险判断仍由 Agent 负责，不在此添加付款关键词规则。
- [ ] 迁移旧活动卡片的原始证明关系：仅将已存在且确认属于该 Project 的 Task 所链接的原 Signal 建成 Project evidence，不改写卡片、任务或引用身份；不为无法证明的关系补造证据。此步骤随一次性结构迁移在副本验证，重复迁移幂等，不放到读取接口中修复。
- [ ] `_project_task_attention` 替换为读取顶层 assessment 的唯一投影入口；删除从多个 Task proposal 汇集卡片的旧入口。同 Project 的多个 Task 引用同一判断；卡片成员只取实际提案成员，不自动吞入项目内全部 Task。
- [ ] 维持 `recompute_for_tasks` 仅重算成员；Task 完成、成员为空或本轮 not_needed 不自动 resolve 卡片。新增风险消失来源时沿原显式 resolve 操作关闭，保留证据与历史，不按“没有任务了”关闭。
- [ ] 回执扩展独立项目版本/来源映射；0 Task 的 positive assessment 只有卡片真实写入并读回后才为 applied。project-only negative 为 recorded；existing 与 error/rejected 区分不变；上下文持久化成功但投影失败时显示两个实际结果，不新增补偿循环。
- [ ] 只读检查输出以下可诊断关系；历史缺字段显示缺失，不回填为不关注：

```json
{
  "project_decisions": [{"project_decision_index": 0, "anchor_id": 7,
                         "context_revision_id": 3, "status": "applied"}],
  "project_assessments": [{"anchor_id": 7, "outcome": "needs_attention",
                           "reason": "原文说明付款顺延影响本期资金安排"}],
  "projection": {"project_assessments": [{"anchor_id": 7, "task_ids": [],
                  "attention_id": 9, "status": "applied", "reason": ""}]}
}
```

- [ ] 回归：未知 Project、来源未关联、同名异项目、无实际引用、普通进展不应造卡；零 Task 重复无新增事件；旧卡片原成员/同项目非成员不混；模拟应用错误不显示成功。运行 `python -m pytest -q tests/test_task_attention_projection.py tests/test_task_attention_multisource.py tests/test_inspect_task_attention.py tests/test_task_agent.py`，预期 GREEN。同步文档提交 `feat(attention): derive project attention independently of task existence`。

## Task 7：API 与现有页面同步展示整体情况和建议

**Files:** 修改 `app/web_api/tasks.py:713,728,801,891,1035,1153`、`frontend/src/api/console.ts:106,119,172`、`frontend/src/pages/TasksPage.tsx`、`TaskProjectDetailPage.tsx`、`TaskAttentionDetailPage.tsx`、`TaskParts.tsx`、`taskLabels.ts`，必要时仅编辑 Tasks 局部样式；测试 `tests/test_web_api_task_project_summary.py`、`tests/test_web_api_task_attention.py`、三页同名 `.test.tsx`；同步 `frontend/dev-mock/tasksMock.ts` 和行为文档。

- [ ] 先写 API 回归：零 Task 项目的总负责/分工从持久化 ProjectContext 读出；建议负责人不混入实际 owner；项目事实与 Task 完成统计同时存在但不互相替代；两个项目不能借到彼此的人员。运行 `python -m pytest -q tests/test_web_api_task_project_summary.py tests/test_web_api_task_attention.py`，新增断言预期 RED。
- [ ] `ConsoleBusinessProjectSummary` 增加 `overall_owner`、`overall_responsibility`、`attention_reason`；detail 增加 `context`、`responsibilities`、`suggestions`、`evidence_signals`、`context_revisions`。项目标题、登记来源及真实任务计数继续保留。删除 `_report_project_registry_snapshot` 这条从 Task 周报重新推导项目的读路径；旧项目无 context 时显示待明确，历史资料由 Task 2/9 的明确重读流程补充，不在页面查询时推断。
- [ ] `ConsoleBusinessTaskSummary` 与 TS 类型同步增加 `origin`、`suggested_owner`、`suggestion_reason`、`deadline_type`。实际 owner、承诺状态和实际日期仍是原字段；UI 不从 suggestion 复制覆盖它们。正式 Task 列表按 stage 取真实任务；来源线索与 Agent 建议在候选区域分组，项目详情把建议与已安排任务分开；不增加自动派发按钮。
- [ ] 创建/修改组件测试，至少验证以下直接可见文字，而不是只测快照：

```tsx
expect(screen.getByText("总负责人")).toBeInTheDocument();
expect(screen.getByText("张三")).toBeInTheDocument();
expect(screen.getByText("交付与验收")).toBeInTheDocument();
expect(screen.getByText("人员分工")).toBeInTheDocument();
expect(screen.getByText("Agent 建议")).toBeInTheDocument();
expect(screen.getByText("建议负责人：王五")).toBeInTheDocument();
expect(screen.queryByRole("button", { name: "自动派发" })).not.toBeInTheDocument();
```

- [ ] 项目详情顺序固定：目标/总体情况 → 唯一总负责及分工 → 关注理由 → 已安排任务 → Agent 建议 → 来源与历史。项目列表不点开就能见负责人/事项/近况/关注原因。Task 列表显示项目、实际或建议负责人、来源标签和明确日期类型，不出现含糊“截止/周期”。
- [ ] 无 Task 的 Attention detail 显示“暂无关联任务”，仍展示事实/推断及来源，不显示加载错误；缺负责人显示“待明确”；冲突事实展示各自来源而非拼成共同负责人。保留 loading、empty、error、长标题与来源链接状态。
- [ ] 运行 `npm --prefix frontend test -- --run src/pages/TasksPage.test.tsx src/pages/TaskProjectDetailPage.test.tsx src/pages/TaskAttentionDetailPage.test.tsx` 和 `npm --prefix frontend run build`，预期测试/构建成功。并重新运行上述两个 API 文件。
- [ ] 用合成 mock 与浏览器逐页检查：需关注列表/详情、正式项目列表/详情、正式任务、待明确线索/建议、任务详情；每页浅色/深色、宽屏/窄屏各查看关键字段、换行、对比度和横向溢出。UI 通过不等于真实项目判断通过。同步文档提交 `feat(console): show project accountability and distinguish task suggestions`。

## Task 8：固定案例、真实原文与可观测结果验收

**Files:** 新建 `tests/fixtures/task_project_centered_v1.json`、`tests/test_task_project_centered_eval.py`、`docs/task-project-centered-validation.md`；修改 `scripts/replay_task_attention.py`、`scripts/inspect_task_attention.py` 的新契约读回，保留旧 fixture/oracle 的历史版本。

- [ ] 固定至少下列案例，预期只在离线 oracle，不能放进 Agent 输入：多来源同项目、单报告多项目、无周报的会议/聊天、零 Task 的真实风险、正常进展、模糊风险、未确认 Project、独立明确 Task、按职责推导未点名人选、未知总负责、职责变更/冲突、建议变实际指派、同 Task 更新、不同交付不可合并、同源重复、同 ref 不同版本、旧卡片成员与同项目同伴、Task 完成但项目风险未消除。
- [ ] fixture 沿用现有版本 1/`cases` 读取格式，扩展 `existing_context.projects` 的 context/evidence，而不是另写 runner。具体建议案例内容为：项目负责人张三、商务职责王五；当前来源只有“付款日期未确定，影响本期现金安排”；预期已有项目、watch、建议王五确认排期、没有实际 owner/承诺/外发。另加完全相同职责但“款项已按计划到账”的负例，不应继续提出同一风险建议。
- [ ] 旧/新对照必须具有相同的原始职责/项目证据，不能只给 candidate 额外的人工分工结论。需要形成先前上下文时，对两边按同一顺序输入原始会议与当前消息；扩展现有 replay 为按 fixture 的 `source_inputs` 顺序调用原来的单次处理函数，各次保存实际结果。单元测试可直接 seed 新 context，native 业务对照不能以这种 seed 替代原文理解；旧能力不支持的新结果按明确的预期差异记录。
- [ ] `tests/test_task_project_centered_eval.py` 先用错误持久化结果测试 oracle 确实拒绝：错误总负责、建议伪装正式、漏掉第二项目、空 Task 卡片未真正保存、重复副本、外发 intent 增加。再给正确结果验证接受。读取 domain 行而非模型总结作为实际结果。
- [ ] 扩展只读检查的 context 指标：独立来源数、Signal 数、实际可见范围、完整/可见字符数；按项目列出识别、判断、上下文/任务/关注实际应用结果。原文项目未被识别的遗漏仍由固定人工预期集合检查，不声称程序能从 Agent 自己的输出证明零遗漏。
- [ ] 运行 `python -m pytest -q tests/test_task_project_centered_eval.py tests/test_inspect_task_attention.py`，预期 GREEN；冻结代码与 CI Skill 哈希，baseline/candidate 同样例、模型、路由、时间预算和并发。
- [ ] 使用现有 replay 参数运行，每个候选库是已确认的数据库副本；命令形状由现有入口支持，真实路径在运行前填入验证记录：

```text
python scripts/replay_task_attention.py --db <已验证副本的绝对路径> --fixtures tests/fixtures/task_project_centered_v1.json --case-id project-risk-without-task --code-root <冻结候选代码绝对路径>
python scripts/replay_task_attention.py --db <真实W39副本绝对路径> --input-id 27465 --code-root <冻结候选代码绝对路径>
python scripts/inspect_task_attention.py --db <同一个真实W39副本绝对路径> --input-id 27465
```

尖括号是运行时必须核实的环境路径，不是待定义产品行为。不能把生产库路径代入；不得把旧 freeze 的 PASS 当作本次运行。沿用评测独立共享 session，不混入生产会话。

- [ ] 对同一 frozen W39 原文逐项目比“预期 vs 实际”：首轮覆盖人工核对的全部相关项目/线索（包括此前遗漏的 Einride POC；身份不足可明确记录，不强登记），按经营影响解释正常/风险、人员/负责事项、来源与建议。第二次重复检查所有原 domain 表及新增表，不新增任务、项目、重复版本或无变化事件；run/attempt 记录独立计数。
- [ ] 比较正文体积和首轮判断覆盖。若去重已改善但仍漏判，记录两者分别结果，继续定位输入是否送达、是否读取、判断还是应用错误，不强造结果、不调低预期或偷偷换模型。只有本次已批准“无 Task 可关注、允许职责推导建议”等预期变化可以版本化更新 oracle。
- [ ] 输出验证文档：代码/Skill 版本、来源清单、旧/新逐案例结论、首次与重复域快照、原始失败、业务人工核对、未完成发布项。提交 `test(tasks): evaluate project-centered work against frozen evidence`。

## Task 9：统一旧逻辑、发布与真实页面读回

**Files:** 本计划、`docs/task-project-centered-validation.md`、行为文档及发布所需受控 Skill 文件；不在生产 checkout 编辑代码或运行测试。

- [ ] 检查所有当前生产入口、prompt、模型字段、CI Skill、API/UI：不再把 ProjectProposal/AttentionProposal 放在 TaskDecision，不再以“没有 Task”拒绝项目风险，不再从 Task owner 推断总负责人；旧 run 和旧 spec 保持历史身份，不作为当前规则执行。精确查找旧字段名后逐处分类，不按全文替换误伤历史材料。
- [ ] 独立代码审查和固定 eval 比较完成后，以 PR 合并本次产品行为变化。PR 描述明确旧/新取舍与真实 W39 结果；创建 PR 后附加到当前任务。不以单元测试通过替代业务评测，不直接绕过产品变更的 PR 要求。
- [ ] 发布前核实原有回复、会议、已领取工作和外部动作的既有可恢复性；不趁此新增安全/审计机制。列出将补充上下文的明确项目/输入及备份位置，先在副本证明结构迁移前后 Signal/Task/历史等价。未知旧记录仍留历史，禁止全库自动升级为项目或候选。
- [ ] 推送并合并后使用 `python -m app.deploy`，让标准流程等待空闲、备份、推进生产 checkout、构建与重启；不手动 kill/kickstart、不编辑生产目录。代码部署与实际加载 Skill 的发布必须同版本衔接；服务开始处理新输入前核验契约哈希和实际 Skill，不能长期留下新代码配旧规则。
- [ ] 仅对验证记录中已明确的近期来源进行小批重处理，走新的唯一正常入口，不使用直接 SQL 补卡、改 owner 或改 Task 状态。先读回样本再扩大；结构迁移和业务补充分别记录结果。
- [ ] 部署后读回新 PID、healthz、queues、Attention、History；页面亲自核对真实项目唯一总负责/负责事项、建议标签、零 Task 关注及证据详情。不得打印 settings 中的秘密字段。
- [ ] 只有“代码与局部测试、固定 native、真实原文结果、部署健康、真实页面”分别通过才报告上线完成。若任何一步失败，记录确切层次及尚未完成项；按 deploy 自身机制回退，不改旧实验、补偿造卡或重复外发。

## 自检与执行交接

| 设计要求 | 实施任务 |
| --- | --- |
| 原文共享、版本与出处不丢 | 1、4、8 |
| 真实项目、唯一总负责、分工、历史 | 2、5、7 |
| Task 可独立、实际安排与建议分开 | 3、5、7 |
| 单 Agent/共享 session/新信息驱动 | 5；会话回归保留 |
| 无 Task 可关注、关注与建议互不强制 | 5、6、8 |
| 正常不关注/证据不足/遗漏/失败可分辨 | 6、8 |
| 表单列表信息、深浅主题、窄屏 | 7 |
| 历史不被伪造、无重复、统一替换旧逻辑 | 1、3、6、8、9 |
| 真实效果而非仅技术成功 | 8、9 |

自检：新类型名称/索引约定统一；TaskSuggestion 不引入第二张任务表；空负责人不假装多人共管；明确的项目/证据资料不依赖 session compact 保留；发布前验证、schema 迁移和业务重新推断分开。Core 文件一个作者顺序修改，页面待契约稳定后才拆分。执行方式交接遵循 writing-plans：可选逐任务 subagent（每步主 Agent 审阅），或当前会话顺序执行；两者采用相同验收，不能跳过业务结果读回。
