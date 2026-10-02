# 多来源项目风险进入「需关注」 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让一个 Task Agent 综合周报、会议和聊天的可核对来源，更新已确认项目的需关注卡片，并能定位每轮没有生成关注的原因。

**Architecture:** 保留 `scan evidence → Task Agent → update Tasks` 和共享 session。拆开 Task 行、Project 登记行和风险证据的引用，先提交 Task/Project，再按已确认项目投影一张当前关注卡；运行记录保存独立投影回执。周报优先定义项目，不成为风险输入的唯一来源或展示门槛。

**Tech Stack:** Python、Pydantic v2、SQLite、现有 Task Agent runner、React/TypeScript、pytest、Vitest。

---

## 开发边界与已确认依据

设计：[2026-10-01-task-attention-w39-phase1-design.md](../specs/2026-10-01-task-attention-w39-phase1-design.md)，Derek 已确认。本计划正在隔离工作区执行，不代表生产修复。

当前断点是 Agent 没有提出 Project/Attention，而不是前端过滤掉卡片。W39 输入 `27465`、运行 `10634` 的六条 Task 决定中，两种 proposal 都为空。数值 anchor ID 必填、风险只能引用 Task 摘录、投影错误只有日志，是需要一起打通的后续断点，不能声称它们已实际拦截该运行。

第一版只处理有已确认 Project anchor、可关联真实 Task 的重大项目风险。允许候选 Task 支持关注，不凭关注把它提升成正式任务或承诺。不新增 Agent、定时维护、自动重试投影循环、外部写工具权限层或业务关键词规则。不恢复独立 completion 路径；钉钉人类完成反馈沿用现有路径。

周报、会议、聊天可以共同构成推断。周报优先定义正式 Project 和明确登记字段；明确会议立项也可登记 Project。聊天不能单独登记或改写正式项目字段，但它对已确认项目报告的新风险必须及时参与判断。来源冲突同时保留，标明时间和待核对之处，不能把上期周报解释成屏蔽新事实的理由。

延后：无 Project anchor 的 NPS/跨部门事项、四周批量回放、历史项目别名合并。不得把“中汽”直接硬编码成“中汽创智”。

## 文件与责任

| 文件 | 本次责任 |
| --- | --- |
| `app/task_models.py` | 独立 Project 引文、Attention 多来源证据引用、投影回执模型 |
| `app/task_agent.py` | 来源引用解析、同轮项目关联、prompt、提交后的投影结果 |
| `app/task_retrieval.py` | 在现有有界检索中提供相关当前关注及其证据，不全量加载历史 |
| `app/task_attention_projection.py` | 同一 Project 一张卡、关联任务及证据沿革 |
| `app/task_semantic_models.py`, `app/store.py` | 关注判断依据和运行投影回执的存储/升级 |
| `app/web_api/tasks.py`, `frontend/src/api/console.ts` | 详情中分开提供事实引用和 Agent 推断 |
| `frontend/src/pages/TasksPage.tsx`, `frontend/src/pages/TaskAttentionDetailPage.tsx` | watch 的“关注点”和可见依据 |
| `ci/shared-skills/ceo-work-tracking/SKILL.md` | 隔离开发/CI 验证副本；与 prompt 完全一致的来源/关注规则 |
| `/Users/derek/.agents/skills/ceo-work-tracking/SKILL.md` | 运行时权威 Skill，代码部署成功后发布相同内容；开发期间不提前改动 |
| `docs/architecture.md`, `docs/runtime-mechanism.md` | 同步描述新运行行为，不写成已部署 |
| `scripts/inspect_task_attention.py` | 只读读取运行、计数、原因和卡片 ID |
| `scripts/replay_task_attention.py` | 仅数据库副本上的指定输入评估，不加入服务调度 |
| `tests/test_task_attention_multisource.py` | 新增窄集成回归，统一使用真实来源形状 |
| `tests/fixtures/task_attention_multisource.json` | 固定脱敏语义样本和预期；不复制内部全文 |
| `docs/task-attention-phase1-validation.md` | 记录基线/候选、上线/回放各自结果 |

开始代码前重新读取 `docs/agent-claims.md`，逐任务认领涉及文件。已有 claim 要先与 owner 协调，只改已协调的函数/段落。测试仅跑本计划列出的文件；不在运行服务的开发机执行串行全套。每次提交只暂存自己的文件或 hunks；不用 `git add -A`。当前另两份未跟踪文件不属于本计划。

执行工作区：`/Users/derek/.codex/worktrees/task-attention-multisource/ceo-agent-service`，分支 `codex/task-attention-multisource`。基线 `tests/test_task_models.py` + `tests/test_task_attention_projection.py`：47 passed。Task 1 已提交 `79c8c38c`；Task 2 已提交到 `9efcab15`，201 项聚焦回归通过；Task 3 已提交 `29f492f2`，553 项相关测试及 7 项 Store 测试通过。Task 4 已提交到 `bdd2e524`，239 项聚焦回归通过，折叠证据保留与两项计数修正均经独立复核。Task 5 已提交到 `00527ba7`，140 项 Agent/检索/会话测试通过，规格与质量复核通过。Task 6 已提交到 `f92d0a92`，7 项 API、22 项页面测试与构建通过，独立复核及模拟浏览器亲验通过。前六步已完成，正在执行 Task 7。未合并、未部署、未回放生产数据，全局 Skill 仍为原版本。

## Task 1：明确来源引用和同轮项目选择的契约

**Files:** Modify `app/task_models.py`; Test `tests/test_task_models.py`, Create `tests/test_task_attention_multisource.py`。

- [x] **1. 写失败测试：风险引用不再受限于任务行动行。** 在新集成测试文件先建立下面的公共样本函数；后续任务在同一文件使用，不依赖其他测试文件的私有 fixture。

```python
import json
import pytest
from pydantic import ValidationError
from app.store import AutoReplyStore
from app.task_agent import apply_task_agent_decision
from app.task_models import TaskAgentDecision, WorkItem

REGISTRY = '| 示例项目 | 回款复核 | 降低现金流风险 | 09-30 | 有风险 |'
ACTION = '复核示例项目回款及供应商付款计划。'
RISK = '已交付收入因客户确认延迟，尚未进入当期确认，供应商付款需要协调。'

def report_item():
    return WorkItem.model_validate({
        'source': {'type': 'project_weekly_report', 'ref': 'report:fixture',
                   'created_at': '2026-09-25T09:00:00Z'},
        'summary': json.dumps({'report': {'reporting_period': '2026-W39'},
            'markdown': '## 手头项目\n| 项目名 | 负责内容 | 目标 | DDL | 状态 |\n'
            '|---|---|---|---|---|\n' + REGISTRY + '\n## 本周进展\n'
            + RISK + '\n## 下周工作重点\n' + ACTION}, ensure_ascii=False),
        'context': {'source_conversation_kind': 'group'},
    })

def decision_payload():
    return {'task_decisions': [{
        'action': 'record_candidate', 'transition': 'none',
        'source_ref': 'report:fixture', 'source_excerpt': ACTION,
        'source_description': '项目管理部固定测试周报',
        'title': '复核示例项目回款及供应商付款计划',
        'missing_evidence': ['owner'],
        'project_proposal': {'title': '示例项目', 'reason': '周报登记项目',
            'authority': 'project_weekly_report', 'source_excerpt': REGISTRY},
        'attention_proposal': {'category': 'watch', 'title': '示例项目回款风险',
            'why_attention': '确认延迟同时影响收入确认与付款安排，需观察现金流风险。',
            'current_state': '已交付收入确认延迟',
            'ceo_action': '当前无需你处理；观察客户确认及付款安排是否恢复。',
            'anchor_id': None, 'related_task_ids': [],
            'material_trigger': 'risk_escalation',
            'evidence': [{'signal_id': None, 'source_ref': 'report:fixture',
                          'source_excerpt': RISK}]},
    }]}

def test_attention_can_reference_separate_project_and_risk_quotes():
    item = TaskAgentDecision.model_validate(decision_payload()).task_decisions[0]
    assert item.project_proposal.source_excerpt == REGISTRY
    assert item.attention_proposal.anchor_id is None
    assert item.attention_proposal.evidence[0].source_excerpt == RISK
```

- [x] **2. 运行失败测试。** `python -m pytest -q tests/test_task_attention_multisource.py::test_attention_can_reference_separate_project_and_risk_quotes`。现状预期是 validation failure：Project 无独立引文、anchor 不接受 null、evidence 非现有字段。
- [x] **3. 替换字段契约。** ProjectProposal 增加必填 `source_excerpt: str` 并纳入 nonblank 检查。TaskAttentionProposal 删除 `trigger_evidence`；保留 `why_attention` 为推断说明，用下面的类型替代旧引用和 anchor 字段。

```python
class TaskAttentionEvidence(StrictTaskModel):
    signal_id: int | None = Field(default=None, strict=True, gt=0)
    source_ref: str = Field(min_length=1)
    source_excerpt: str = Field(min_length=1)

# TaskAttentionProposal 内：
anchor_id: int | None = Field(default=None, strict=True, gt=0)
related_task_ids: list[int] = Field(default_factory=list)
evidence: list[TaskAttentionEvidence] = Field(min_length=1)

# TaskDecision 的 after validator 内：
proposal = self.attention_proposal
if proposal is not None and proposal.anchor_id is None and self.project_proposal is None:
    raise ValueError('attention requires an existing anchor or this decision project proposal')
if proposal is not None and any(value <= 0 for value in proposal.related_task_ids):
    raise ValueError('attention related Task IDs must be positive')
```

null anchor 只表示本条 Task 的 Project proposal 将在本轮取得 anchor，不按标题猜测其他项目。`signal_id=None` 只表示当前 Work Item 原文；正整数表示已经保存、与相关 Task/已确认项目关联的 signal。不是任意 session 记忆引用。更新所有生成这两种 proposal 的生产代码/测试，不能保留两个新旧输出 schema 或静默兼容旧 trigger 字段；历史 decision_json 原样保留，不重新当新决策应用。

- [x] **4. 补两个拒绝测试并运行。** 从 `decision_payload()` 删除 project_proposal 并保留 null anchor，应报上述 anchor 错误；把 evidence 设为 [] 应报 validation error。命令 `python -m pytest -q tests/test_task_models.py tests/test_task_attention_multisource.py`。预期新契约测试通过；尚未实现的 apply 回归在下一任务加入。
- [x] **5. 提交契约及同步说明。** 在 `docs/runtime-mechanism.md` 说明字段变化是开发契约，Project/task/risk 各有自己的引用。提交 `feat(tasks): separate project and attention evidence references`。运行行为变更必须带文档，不等最后才补。

## Task 2：项目登记行与 Task 行分离，先取得真实 anchor

Task 1 的五个步骤均已完成；上方契约代码仅作实施记录，最终投影仍以 Task 4 的完整实现为准。Task 2 的六个步骤已完成，两轮独立检查发现的原文列移位、普通段落及起始换行边界问题已补失败回归并修正；最终确认到 `9efcab15`。

**Files:** Modify `app/task_agent.py`, `tests/test_task_agent.py`, `docs/architecture.md`, `docs/runtime-mechanism.md`; Test `tests/test_task_attention_multisource.py`。

- [x] **1. 写失败集成测试。**

```python
def test_separate_registry_row_links_candidate_to_official_project(tmp_path):
    store = AutoReplyStore(tmp_path / 'project.sqlite3')
    result = apply_task_agent_decision(store, summary_input_id=1,
        work_item=report_item(), decision=TaskAgentDecision.model_validate(decision_payload()),
        record_run=False)
    (project,) = store.list_business_projects()
    assert project.title == '示例项目'
    links = store.list_business_task_project_links(task_id=result.task_ids[0])
    assert [link.id for link in links] == [project.id]
    assert store.get_business_task(result.task_ids[0]).stage.value == 'candidate'

def test_project_title_must_match_its_separate_registry_quote(tmp_path):
    store = AutoReplyStore(tmp_path / 'wrong-project.sqlite3')
    payload = decision_payload()
    payload['task_decisions'][0]['project_proposal']['title'] = '无来源的另一个项目'
    with pytest.raises(ValueError, match='cited report registry row'):
        apply_task_agent_decision(store, summary_input_id=1,
            work_item=report_item(), decision=TaskAgentDecision.model_validate(payload),
            record_run=False)
    assert store.list_business_projects() == []
```

- [x] **2. 运行失败测试。** `python -m pytest -q tests/test_task_attention_multisource.py -k 'separate_registry or separate_project or project_title'`。旧逻辑虽不能以 Task 行定位登记表，通用 proposal 分支仍可能登记 Project，因此正例单独通过不能证明绑定正确；负例应失败于“没有拒绝错误项目标题”，这是本任务明确的红测试。
- [x] **3. 让已有登记表识别器只解析明确 Project 引文。** 将 `_report_project_registry_title(work_item, item)` 的第二参数改成 `source_excerpt: str`，函数内对 `item.source_excerpt` 的引用全部换成参数。沿用现有表格结构解析，不新增业务名称、风险关键词、章节别名或 regex。调用必须显式来自 `item.project_proposal.source_excerpt`；删除“Task 行恰好在登记表中就自动注册”的隐式路径。旧登记行测试要显式构造 Project proposal，而不是继续支持旧输出。

```python
proposal = item.project_proposal
report_project_title = (
    _report_project_registry_title(work_item, proposal.source_excerpt)
    if proposal is not None and proposal.authority.endswith('weekly_report') else ''
)
if proposal is not None and proposal.authority.endswith('weekly_report'):
    if report_project_title != proposal.title.strip():
        raise ValueError('project proposal does not match the cited report registry row')
```

保留现在的规范化标题 hash/anchor_ref、register_official_project、confirm_anchor_match 和事务。会议 Project proposal 仍需已有明确会议决策规则；聊天不进入注册路径。Project 行要存在于当前原始来源并与标题一致，不使用 `reason` 中的章节关键词冒充位置证明。

- [x] **4. 将关联结果传入提交后投影。** 给 `TaskAgentApplyResult` 的 attention 项使用明确 dataclass，替换 `(decision, task_id, signal_id)` 的匿名 tuple；anchor_id 来自当前事务的已确认关联，不让投影重新猜。

```python
@dataclass(frozen=True)
class AppliedTaskAttention:
    decision: TaskDecision
    task_id: int
    signal_id: int
    anchor_id: int

# 已登记/关联 Task 后收集 attention（existing anchor 仍必须确认关联）：
if item.attention_proposal is not None:
    selected_anchor = item.attention_proposal.anchor_id
    if selected_anchor is None:
        selected_anchor = applied_project_anchor_id
    if selected_anchor is None:
        raise ValueError('attention project proposal was not applied')
    attention_proposals.append(AppliedTaskAttention(
        decision=item, task_id=task_id, signal_id=result.signal_id,
        anchor_id=selected_anchor,
    ))
```

仅在该 Task 的 project_proposal 确实应用后引用本条结果，不能使用上一条循环残留变量。每条 Task 循环开头置 `applied_project_anchor_id=None`，周报/会议两种注册分支写入该变量，null selector 读取此变量。无效 Project 引文在原有领域验证事务中拒绝本轮，不部分提交伪项目；已提交事实上的 Attention 引用/投影错误则由 Task4 单独记录，不回滚 Task。

- [x] **5. 补两项回归。** 行标题不一致和“下周工作重点”行动行冒充 Project 登记引文均不能登记 Project；重复相同决定复用 Task/Project/anchor。原文表格的有/无 leading pipe 两种形态继续通过结构解析测试。`python -m pytest -q tests/test_task_agent.py tests/test_task_attention_multisource.py -k 'project or registry or dedupe'`，预期通过。
- [x] **6. 同提交更新项目来源文档。** 写明显式项目引文、行动行独立、会议/聊天源界限。提交 `fix(tasks): bind report actions to separately cited project rows`。

## Task 3：保存最小投影回执与可显示的判断依据

**Files:** Modify `app/task_models.py`, `app/task_semantic_models.py`, `app/store.py`, `tests/test_store.py`, `docs/runtime-mechanism.md`; Create `scripts/inspect_task_attention.py`。

- [x] **1. 添加存储回归。** 在 `tests/test_store.py` 的现有 Task Agent run lifecycle fixture 中：begin/finish 后写回 receipt，读取应与输入一致，run 的 status/decision_json/finished_at 不变。旧数据库升级后两列存在，历史行仍无回执，不显示成功。新测试名 `test_task_agent_projection_receipt_preserves_terminal_run`、`test_task_attention_receipt_migrates_without_claiming_historical_success`。
- [x] **2. 跑失败测试。** `python -m pytest -q tests/test_store.py -k 'projection_receipt or receipt_migrates'`。预期缺字段/方法。
- [x] **3. 在现有表增加两个 JSON 字段，不建新业务队列。** `task_agent_runs.projection_json text not null default '{}'`；`business_attention_items.assessment_json text not null default '{}'`。同时更新 create-table、现有逐列升级、业务表 column manifest、TaskAgentRun/BusinessAttentionItem 模型和 attention create/update SQL。`{}` 表示历史未记录，不表示通过或零提议。定义以下回执。

```python
class TaskAttentionProjectionOutcome(BaseModel):
    task_id: int | None
    anchor_id: int | None = None
    attention_id: int | None = None
    status: Literal['applied', 'rejected', 'error']
    reason: str = ''

class TaskAttentionProjectionReceipt(BaseModel):
    status: Literal['pending', 'no_proposal', 'completed', 'partial', 'failed']
    source_type: str
    task_decision_count: int
    project_link_count: int
    registry_row_count: int | None = None
    proposal_count: int
    applied_count: int = 0
    outcomes: list[TaskAttentionProjectionOutcome] = Field(default_factory=list)
    recompute_error: str = ''
```

`project_link_count` 是本轮成功确认 Task↔Project 关联数（去重后）；`registry_row_count` 是周报原文已有解析器识别的项目登记行数，非周报 null。前者不能代替后者；没有提议也要记原文行数。运行中断在 Task 提交之后、投影记录之前，receipt 留 pending，诊断必须说“投影尚未确认”。`applied_count` 计成功应用的不同项目卡，不按同项目 Task 数累加。

- [x] **4. 在已有 run 写事务提供回执写入方法。**

```python
def record_task_agent_projection(self, run_id: int, projection_json: str,
                                 *, _db: sqlite3.Connection | None = None) -> None:
    TaskAttentionProjectionReceipt.model_validate_json(projection_json)
    def write(db: sqlite3.Connection) -> None:
        cursor = db.execute(
            'update task_agent_runs set projection_json=? where id=?',
            (projection_json, run_id),
        )
        if cursor.rowcount != 1:
            raise ValueError('task agent run does not exist')
    if _db is not None:
        write(_db)
        return
    with self._agent_run_write_transaction(None) as (db, _):
        write(db)
```

不改 completed/failed 定义，不重写已完成的 Agent 输出，也不把投影失败伪装成 Task 事务失败。Attention `assessment_json` 内容只含 trigger 类型、推断、已验证 quotes 的 signal_id/source_ref/source_time 和来源链接，不复制全文；现有事件快照自动包含该字段。只用当前提议引用的已验证证据，不把所有检索候选都说成依据。

- [x] **5. 写只读诊断脚本。** 参数 `--db PATH --input-id N`；只使用 `sqlite3.connect(f'file:{path}?mode=ro', uri=True)`，按 summary_input_id 读取 work_summary_inputs 和 task_agent_runs，不实例化可能执行升级的 Store。输出 JSON 为 input_id/run_id/source_type/input_status/run_status/projection/audit_summary；不输出 payload、完整 decision、凭证或原文。运行 `python scripts/inspect_task_attention.py --db <测试库绝对路径> --input-id 1` 应显示与测试一致的 counts；缺失输入以 exit 1 + `input_not_found` 退出。
- [x] **6. 验证并提交。** `python -m pytest -q tests/test_store.py -k 'task_agent or business_attention or receipt_migrates'`。同步运行文档描述投影回执，提交 `feat(tasks): persist attention projection outcomes per input run`。

## Task 4：综合相关证据，按 Project 更新同一张卡

Task 3 的六个步骤已完成：两个 JSON 字段、类型模型、只更新回执的方法、历史迁移及只读诊断均通过验证。运行中实际计数和卡片依据的组装属于本任务，不以存储实现完成代替端到端结果。

**Files:** Modify `app/task_agent.py`, `app/task_attention_projection.py`, `tests/test_task_agent.py`, `tests/test_task_attention_projection.py`, `docs/architecture.md`, `docs/runtime-mechanism.md`; Test `tests/test_task_attention_multisource.py`。

- [x] **1. 添加当前来源跨章节回归。**

```python
def test_project_watch_can_use_risk_outside_task_excerpt(tmp_path):
    store = AutoReplyStore(tmp_path / 'attention.sqlite3')
    result = apply_task_agent_decision(store, summary_input_id=1,
        work_item=report_item(), decision=TaskAgentDecision.model_validate(decision_payload()),
        record_run=False)
    (attention,) = store.list_business_attention_items()
    (project,) = store.list_business_projects()
    assert attention.stable_key == f'project:{project.canonical_anchor_id}'
    assert attention.category.value == 'watch'
    assert '当前无需你处理' in attention.ceo_action
    evidence = json.loads(attention.assessment_json)['evidence']
    assert evidence[0]['source_excerpt'] == RISK
    assert evidence[0]['signal_id'] > 0
    assert store.get_business_task(result.task_ids[0]).stage.value == 'candidate'
```

- [x] **2. 跑失败测试。** `python -m pytest -q tests/test_task_attention_multisource.py::test_project_watch_can_use_risk_outside_task_excerpt`。现状是 quote 不在 Task excerpt 内而抑制，或旧 tuple 接口不匹配。
- [x] **3. 引文匹配原始来源，不匹配 Task 行。** 定义并测试该小函数；JSON 原文中的连续字符串可匹配解码后的文本，不能把两个不连续段落拼接成虚假引文。

```python
def source_contains_quote(raw: str, quote: str) -> bool:
    if not quote.strip():
        return False
    def texts(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for child in value.values():
                yield from texts(child)
        elif isinstance(value, list):
            for child in value:
                yield from texts(child)
    if quote in raw:
        return True
    if not raw.lstrip().startswith(('{', '[')):
        return False
    decoded = json.loads(raw)
    return any(quote in text for text in texts(decoded))
```

这是来源表示解析，不是业务关键词判定。当前 evidence 取本条提交的 signal，核对 source_ref；历史 evidence 按 signal_id 从 DB 取完整原始文本，核对 source_ref，再核对它已有的 Task 证据关联和本次确认项目。缺失信号、跨项目信号、伪造引用、只有未落库 session/memory 总结分别记 rejected 原因，不借用当前 signal 冒充历史来源。来源时间/链接由 signal 读取，不信任模型补日期。

- [x] **4. 组装并 upsert 项目卡。** `AppliedTaskAttention.anchor_id` 必须对应正式 Project。候选或正式 Task 都可参与，但必须 relevant、开放、已确认到同一 anchor；proposal.related_task_ids 是支持本风险的已有 Task，不是整项目全部任务。本轮相同项目的所有 attention Task + 显式 related_task_ids + 该卡仍合格的现有成员，去重后传入现有 projection；每次更新不丢掉未完成的兄弟 Task。闭合/取消/不相关成员仍由原有 recompute 移出，不自行宣布风险已解决。

```python
assessment_json = json.dumps({
    'material_trigger': proposal.material_trigger,
    'inference': proposal.why_attention,
    'evidence': verified_evidence,
}, ensure_ascii=False, sort_keys=True)
projected = BusinessAttentionProjection(store).upsert(AttentionProposal(
    stable_key=f'project:{applied.anchor_id}', category=proposal.category,
    title=proposal.title, business_area='', why_attention=proposal.why_attention,
    current_state=proposal.current_state, ceo_action=proposal.ceo_action,
    anchor_id=applied.anchor_id, task_ids=tuple(sorted(member_ids)),
    evidence_signal_id=applied.signal_id, assessment_json=assessment_json,
))
```

给 AttentionProposal 增加 `assessment_json` 字段，并在 `_item_changed`、create/model_copy/update 中传递。`verified_evidence` 是第3步成功引用的列表；`member_ids` 是本步核验的集合；upsert 当前直接返回 `int` 卡片 ID，将 `projected` 写入 outcome.attention_id。同项目每轮只一条 attention_proposal（其他 Task 用 related_task_ids），prompt 明确此契约；遇到同项目相互矛盾的多条 proposal 记 rejected，而不是依列表顺序选择最后一条。成员仍合并本轮正确支持同项目的 Task。

`evidence_signal_id` 保留当前输入 signal，保证旧投影 linkage 检查；真正支持推断的当前/历史证据集合保存在 assessment_json。不能要求所有 quotes 都来自该 primary signal。使用已有 Task 更新服务保存新证据，不凭文字相似度创建新的正式关系。

- [x] **5. `_project_task_attention` 返回回执，不再只有吞错日志。** 已有异常边界只增加结果记录，不新建重试/恢复层。引用/关联错误记 rejected；upsert 异常记 error；recompute 异常写 recompute_error。无 proposal 则 no_proposal。成功卡数等于全部有效提议时 completed；成功+失败为 partial；无成功但有失败为 failed。

`process_work_item` 在原有 Task+input+run 事务写 pending 初始 receipt；提交后保存投影函数返回的最终 receipt。保留 run ID 的局部副本，不复用 `active_run_id` 的失败路径。回执保存或投影异常发生在提交之后，不能进入原有将 input 改 failed 的 precommit except 分支；把提交后处理移到该 try/except 之外。direct apply 路径沿用返回结果，不造不存在的 run。

删除 Task1 消费者阶段的“multisource/project attention projection is not implemented yet”与仅单引文限制；它们不能进入发布版本。验证时必须同时读取 completed Task run 与独立投影 receipt：前者不是“已生成关注”的证据，投影失败不能倒改已提交 Task 状态。

- [x] **6. 补回归并验证。** 每个测试先观察失败再实现对应行为：

| 新测试 | 核对结果 |
| --- | --- |
| `test_meeting_risk_updates_confirmed_project_without_report_input` | 已确认项目，ai_minutes 新风险，同卡 ID 更新 |
| `test_chat_risk_uses_linked_report_and_current_chat_evidence` | reply_attempt 可使用两份证据，不登记新 Project |
| `test_newer_chat_risk_does_not_overwrite_report_registry_fields` | 风险可见，登记状态保持原周报事实 |
| `test_unlinked_or_cross_project_signal_is_rejected_with_receipt` | 无卡，明确拒绝原因 |
| `test_one_project_keeps_related_open_tasks_without_duplicate_cards` | 两项行动、一张卡，更新后保留兄弟成员 |
| `test_completed_member_leaves_open_sibling_attention` | 完成任务移出、卡不被自动 resolved |
| `test_projection_error_preserves_committed_task_and_records_error` | monkeypatch upsert 报错，Task/run 已提交，receipt failed |
| `test_no_proposal_receipt_is_not_projection_failure` | proposal=0/applied=0/no_proposal |
| `test_source_quote_does_not_join_noncontiguous_json_values` | 引文不可跨无关字段拼接 |

命令 `python -m pytest -q tests/test_task_attention_multisource.py tests/test_task_attention_projection.py tests/test_task_agent.py`。测试用真实类型 WorkItem+TaskAgentDecision，不通过 monkeypatch 跳过身份/引用核验。
- [x] **7. 同提交更新文档。** 解释事实 vs 推断、Project 身份、投影事务边界与回执。提交 `fix(tasks): project multisource risk evidence into one project attention card`。

Task 4 复核记录：`2bb67cd2` 实现完整投影；`0b9eabd8` 修正相同提案折叠时遗漏第二个真实信号的问题，保留全部已核验引文并精确去重；`bdd2e524` 结构性排除重复表头、计入候选聚类确认的实际链接。新增缺陷回归均观察到对应失败，最终四文件 239 项通过，两阶段独立复核通过。无字段变化而未应用的提案仍如实 rejected，outcome.task_id 可为 null，不以假 ID 代替。生产尚未变更。

## Task 5：统一 Agent prompt、检索上下文和业务 Skill

**Files:** Modify `app/task_agent.py`, `app/task_retrieval.py`, `ci/shared-skills/ceo-work-tracking/SKILL.md`, `tests/test_task_retrieval.py`, `tests/test_task_agent.py`, `docs/architecture.md`, `docs/runtime-mechanism.md`。

- [x] **1. 写检索测试。** 用 Task 4 的已落库项目卡，在同项目聊天 WorkItem 的 context JSON 中读到该卡及 evidence 引用；无关项目卡不出现。新测试 `test_semantic_context_includes_related_current_project_attention`、`test_semantic_context_excludes_unrelated_attention`。命令 `python -m pytest -q tests/test_task_retrieval.py -k project_attention`，预期当前没有该字段。
- [x] **2. 最小扩展检索。** TaskSemanticContext 增加 `attention_items: tuple[BusinessAttentionItem, ...]`；只选择现有 context.official_projects 的 canonical_anchor_id 对应当前卡，沿用 limit_per_kind 有界限制。render 加 `current_project_attention`，包含 id、anchor_id、why_attention、current_state、assessment_json、updated_at。不扩大 source_signals 全文预算，不增加独立检索 Agent。

```python
project_anchor_ids = {project.canonical_anchor_id for project in projects}
attention_items = tuple(
    item for item in store.list_business_attention_items()
    if item.anchor_id in project_anchor_ids and item.status.value == 'active'
)[:limit_per_kind]
# TaskSemanticContext 返回值：attention_items=attention_items
# render 的 payload：
'current_project_attention': [_model_payload(item) for item in context.attention_items]
```

被上下文截断的信号仍不可靠 Agent 猜原文；它可引用当前可见的精确文本，服务再在 DB 原文核对。现有共享 session 只提供背景，不替代证据 ID。
- [x] **3. 将下列统一说明放进 build_task_agent_prompt 和 Skill 的生命周期7/9及来源章节。** 替换旧“只有当前 Task 摘录/必须 CEO action/周报覆盖一切状态”的段落，删除相互矛盾的旧文字，不另加并行规则。

```text
周报、会议和聊天都是 Task 与风险证据来源，不能等待周报才记录新风险。
正式 Project 的定义和明确登记字段优先引用已确认周报；明确会议立项亦可注册。
聊天不能单独注册正式 Project 或改写周报明确登记字段，但可更新已有项目的风险。
Task 行引用行动原句；project_proposal.source_excerpt 引用项目登记/立项原句。
attention_proposal.evidence 可引用当前全文的其他章节，或相关已落库 signal 的原文。
给每条历史引用真实 signal_id/source_ref；当前引用 signal_id=null，source_ref=当前来源。
新 Project 用本条 project_proposal，attention anchor_id=null；已有 Project 用真实 anchor_id。
综合证据解释经营影响，分别写来源事实 current_state 和推断 why_attention。
只有风险标签、日期临近、相关性、正常进展、静态重复事实，均不足以产生需关注。
每个已确认项目每轮最多一条关注提议，关联支持该风险的真实任务。
watch 可写“当前无需你处理”，并指出接下来观察的结果；需关注不等于需介入。
对未提出关注的 Task，update_summary 说明没有实质影响、未确认项目或缺少可核对依据。
所有 Task、负责人、指派、承诺、日期都来自真实上下文，不能为风险卡创造一个任务。
较新会议/聊天与上期周报冲突时，保留两份证据及时间，说明待核对，不静默覆盖。
只通过本次结构化决定更新本地 Tasks；不得自行通过 CLI/API/MCP 写外部系统。
```

- [x] **4. 清除业务 Skill 中已停用的 completion turn 说明。** 与现行架构一致写成：一个共享 Task Agent 消费新来源，判断新建/更新/完成；不产生三类旧 completion Work Item，枚举仅保留历史；DingTalk TODO 人类完成反馈确定性更新。保留已确认的 prompt-only 只读说明，不新增权限实现。此处是规则一致性修正，不恢复 completion orchestration。
- [x] **5. 测试 prompt 契约与运行 Skill 加载。** 更新 `tests/test_task_agent.py` 的 prompt 测试为上述新字段和规则；候选测试显式设置 `CEO_SKILLS_ROOT` 到隔离工作区的 `ci/shared-skills` 并验证 runner 实际读取新规则。全局权威 Skill 的 metadata/version 随 Task 8 发布更新，开发期间不提前修改。`python -m pytest -q tests/test_task_agent.py tests/test_task_retrieval.py tests/test_task_agent_session.py`，预期通过。检查 `rg -n 'completion|trigger_evidence|明确 CEO action'` 的命中逐项判断，代码/当前规则不得残留已删除路径；历史叙述可以明确标为历史。
- [x] **6. 提交。** `feat(tasks): align multisource attention prompt retrieval and skill`，包含准确的架构/运行说明。

Task 5 复核记录：`2cc1f56d` 接线 prompt、当前卡片上下文与隔离 Skill revision 3；`68fc7a98` 明示分支尚未部署；`00527ba7` 补强筛选回归，将无关/已解决卡放在有效卡之前。删除任一实际筛选条件均会让该回归失败，原实现通过。独立复核及主 Agent 各重跑 140 项相关测试通过；旧定时快照不入指令、业务范围/元数据与原 payload 保留，全局 Skill 文件 hash 未变。

Task 5 Skill 发布边界：当前 `app.business_skills.bundled_business_skills_root()` 默认读取全局 `~/.agents/skills`，CI 副本不是生产权威来源。开发测试及候选语义评估显式设置 `CEO_SKILLS_ROOT` 指向隔离工作区 `ci/shared-skills`，不在开发时修改运行中的全局 Skill。Task 8 先完成代码部署，再按现有 Skill 仓库流程发布相同内容到权威文件，核对实际加载路径、内容及版本；在两份规则一致之前不宣称发布完成。不得为此另建 Skill 代理、临时配置文件、复制循环或永久切换生产到 CI 副本。

Task 5 还需统一当前执行规则的实际输入：`build_task_agent_prompt` 已从来源 JSON 移除 `scheduled_consumer.skill_protocol`，却仍把旧规则作为 `Scheduled Consumer Skill Snapshot` 再次加入 prompt；既有测试明确包含 `Return update_project with todo_changes.`。旧快照保留在输入/运行历史，不再作为本轮执行指令。保留定时来源的元数据与业务范围 prompt，实际工作规则仅加载当前权威 `ceo-work-tracking`。补一个旧快照不能进入新 prompt 的失败回归，不采用关键词清洗或新旧协议兼容分支。

## Task 6：用户能看到依据，watch 不误写成“你的动作”

**Files:** Modify `app/web_api/tasks.py`, `frontend/src/api/console.ts`, `frontend/src/pages/TasksPage.tsx`, `frontend/src/pages/TaskAttentionDetailPage.tsx`; Test `tests/test_web_api_task_attention.py`, `frontend/src/pages/TasksPage.test.tsx`, `frontend/src/pages/TaskAttentionDetailPage.test.tsx`; Modify `docs/architecture.md`。

- [x] **1. 写 API 失败测试。** 两来源 assessment 的详情应返回 inference、精确 quote、source_ref/source_time/signal_id，并在 evidence_signals 提供这两份被引用信号。原来只读取 primary/event signal 会漏 supplemental，因此必须测试完整证据集合。历史 assessment={} 应显示没有结构化依据，不能伪造证明。
- [x] **2. 实现 detail 字段。** ConsoleBusinessAttentionDetail 新增 `assessment: dict[str, Any]`；解析 `item.assessment_json`，仅增加已保存 assessment evidence 的 signal IDs 到 detail.evidence_signals。frontend 类型增加如下结构，fact 日期来自存储的 signal。

```typescript
assessment: {
  material_trigger?: string;
  inference?: string;
  evidence?: Array<{
    signal_id: number;
    source_ref: string;
    source_excerpt: string;
    source_time: string;
    source_link: string;
  }>;
};
```

- [x] **3. 写页面失败测试。** watch 卡/详情包含“关注点”“当前无需你处理”，不出现“你的动作”；decision/push 继续显示动作。详情分别有“来源事实”和“Agent 判断”，两条证据可读且有来源时间/链接。空、loading、请求失败、历史无 assessment、已关闭 Task 成员变化都覆盖。
- [x] **4. 改标签并显示证据。** 两页采用一致 label。

```tsx
const actionLabel = item.category === 'watch' ? '关注点' : '你的动作';
// 列表 dt 与详情 aria-label/h2 均使用 actionLabel。
// 详情新增：
<DetailSection title="Agent 判断">
  <p>{item.why_attention}</p>
</DetailSection>
<DetailSection title="来源事实" count={detail.assessment.evidence?.length ?? 0}>
  {detail.assessment.evidence?.map((quote, index) => (
    <blockquote key={`${quote.signal_id}:${index}`}>
      <p>{quote.source_excerpt}</p>
      <p>{quote.source_time} · {quote.source_ref}</p>
      {quote.source_link && <a href={quote.source_link}>查看原文</a>}
    </blockquote>
  ))}
</DetailSection>
```

历史记录沿用其原有 why_attention，是已有显示字段，不补造 assessment。保留当前状态、Project、相关 Task、更新时间、来源/历程入口；不重做列表布局，不更改主题。亮色/暗色、窄屏/桌面均验证文字可读。
- [x] **5. 验证。** `python -m pytest -q tests/test_web_api_task_attention.py`；前端目录 `npm test -- --run src/pages/TasksPage.test.tsx src/pages/TaskAttentionDetailPage.test.tsx` 和 `npm run build`。预期全部通过，包含类型检查。浏览器亲自打开列表与两种详情验证，不用 HTTP 200 代替页面效果。
- [x] **6. 提交。** `feat(console): explain project attention evidence and watch focus`，同步架构文档说明只读展示。

Task 6 复核记录：`128ea5b4` 完成保存的 assessment API 与事实/判断展示；`f92d0a92` 直接验证真实 Attention 成员完成、重算移除、保留 open sibling 与 active 风险、不因 GET 改写状态。独立规格及质量审查通过；7 项 API、22 项页面测试及 TypeScript/Vite 构建通过。主 Agent 亲验隔离模拟列表和 decision/watch 详情，桌面亮/暗色与 390×844 窄屏亮/暗色文字可读，临时 viewport/media 已恢复。模拟卡片只证明展示，尚不证明真实 Agent 语义或生产效果。

## Task 7：固定样本比较与数据库副本定点回放

**Files:** Create `tests/fixtures/task_attention_multisource.json`, `scripts/replay_task_attention.py`, `docs/task-attention-phase1-validation.md`; Test `tests/test_task_attention_multisource.py`。

Task 7 开始前的实测基线（尚未回放）：当前生产 revision `7bf7be5e6dcdb181b0674e79ace17a598dcf87e0`，已固定到 `/Users/derek/Projects/ceo-agent-service/.worktrees/attention-eval-baseline`；该基线 56 项 models/retrieval 测试通过，其 CI Skill 与未修改全局 version 2 文件 hash 相同。候选分支已正常合并此 origin/main revision，保留他人修复。SQLite 在线 backup 到 `/var/folders/74/yj2lxqs162q7rqzm0mj8nv1c0000gn/T/ceo-attention-eval-40j4skvv/baseline.sqlite3`，完整性 `ok`、大小 3,223,863,296 bytes；该不可变初态有 259 Task、16 Project、0 Attention，输入 27465 精确来源 ref 匹配、done、attempts=1，已有 Task 129–134 均 candidate/open。生产配置的路由、模型、工作目录和 MCP 连接用于两侧比较；只对评估显式使用各侧 Skill root 和副本 session scope，不打印秘密配置。此处只证明准备和只读核对，不证明候选模型效果。

- [x] **1. 建立固定版本样本，不把 Agent 正确输出写进输入。** JSON 顶层 version=1，每项 work_item、existing_context、expected 互相独立；expected 只供评估断言，不能发给 Agent。

| case_id | 固定输入与预期 |
| --- | --- |
| `w39-project-risk` | W39 脱敏项目登记、行动和收入/回款/争议说明；中汽创智、岚图各一张，NPS 不创建 Project |
| `meeting-new-risk` | 已确认 Project 的会议新风险+真实行动；无需当前周报，更新已有卡 |
| `chat-with-report-context` | 已确认 Project 的聊天新风险+历史相关周报；两来源可核对，更新同卡 |
| `newer-conflicting-chat` | 旧周报正常、新聊天具体异常；风险显示但不覆盖登记字段 |
| `risk-label-only` | 只有“有风险”，没有具体经营影响；不产生关注 |
| `routine-progress` | 常规进展、临期、相关性；不产生关注 |
| `unconfirmed-project` | 有风险但无确认项目；保留真实 Task/线索，不造 Project/Attention |
| `no-real-task` | 信息/指标异常但无真实关联 Task；不为关注造行动 |
| `same-project-two-actions` | 独立行动同一项目；任务独立、一张关注卡 |

真实 W39 只复制必要脱敏段落：保留逻辑关系和金额量级，不提交全文、signed 图片 URL、个人信息。原文引用与读取证据保留在现有生产 signal，不上传新记忆文档。

- [x] **2. 副本回放脚本保持一次性和单输入。** 支持 `--db COPY --input-id ID`，加载该 WorkSummaryInput，沿用 `TaskAgentRunner/TaskAgentCodexRunner` 和 `build_production_routed_codex_execution` 的现有配置；直接 `process_work_item`，不调用会遍历其他 pending 输入或初始化 outbound DWS 的业务 CLI。显式取得该副本的 TaskAgentSessionLease。副本上的 route session 指针不续接生产 session：脚本使用下列 scoped runner，每种版本从相同副本初态开始；不改变服务的共享 scope。

```python
class AttentionEvaluationRunner(TaskAgentRunner):
    def decide(self, *args, **kwargs):
        kwargs['session_scope_id'] = 'task-agent:attention-eval:v1'
        return super().decide(*args, **kwargs)
```

脚本只允许库副本：参数路径不得等于 `worker_db_path()`，这是该一次性评估工具的范围检查，不增添运行服务权限层。按 ID 读取、确认源 ref 后，只在副本把这条 done/skipped 输入置为可处理状态；不修改它的旧 runs，不重新扫描所有来源，不重开其他输入。runner 外部写操作仍按现行 prompt 禁止；本次不声称是硬隔离。

- [ ] **3. 基线/候选用相同样本、模型、路由、timeout、concurrency=1。** 不切换模型来解释改进。评估输出 case_id、proposal 数、入库数、项目/Task 变化、evidence 核验、重复卡、缺失/误关注、失败原因。保存 readback 而非只看 Agent 说成功。先完成纯 fake-runner 确定性回归，再运行真实 runner 的固定样本。
- [x] **4. 把 production DB 做 SQLite backup 到唯一临时目录。** 用 `sqlite3.Connection.backup`，不复制正在写入的裸 DB 文件。完整性检查 `pragma integrity_check` 返回 ok。记录副本基线 Task/Project 数及 W39 已有 Task IDs，不提交或打印全文。对 `27465` 精确回放；若 source_ref 不等于设计固定的 W39 ref，停止并重新定位，不能按旧 ID 误跑其他输入。

此步骤只完成备份和精确输入定位：完整性 ok，259 Tasks、16 Projects、0 Attention，W39 Tasks 129–134 及输入 27465 的 source_ref 已核对。实际副本回放属于步骤 5，尚未执行；此勾选不表示副本语义或幂等验收通过。

固定 source ref：`dingtalk-doc:a9E05BDRVQvy7QEacPZLB4anJ63zgkYA#sha256=21661643562265ca27e3369112a7ce3e91d9cbb6d21733050b5c3e7a9d42bf1e`。

- [ ] **5. 连续回放两次验证身份，不只验卡片数。** W39 原有同一交付事项不新增重复 Task；新增官方 Project 只来源于有效登记行且复用已有同名 anchor；两张目标卡分别对应其正式项目、实际关联 Task 和准确原文依据。第二次卡 ID 不变，不重复 Task/Project，未变化不重复事件。若标题改写导致现有身份逻辑无法确认同一 Task，应使用已有 Task identity 契约修正决定或检索，不能强制同名 merge、删除旧记录或忽略额外 Task。
- [ ] **6. 记录验收门槛。** 所有固定正例达到预期项目卡；全部负例没有误关注/伪项目/伪 Task；关联引用全部可核对；重放无重复。真实模型不达标则记录失败 case 并修正 prompt/context，再做同样比较，不能通过测试 fake 输出宣布效果完成。`docs/task-attention-phase1-validation.md` 分别写代码测试、语义评估、副本回放、上线四栏结果和准确 revision。
- [x] **7. 提交样本与评估工具。** `test(tasks): add multisource attention evaluation and targeted replay`。按共享规则，改变谁进入关注的语义变更通过 PR + 固定 eval 对比再合并；不要把用户批准设计说成已证明模型效果。

Task 7 中间读回（2026-10-02，尚未上线）：工具已支持逐条固定样本及精确生产输入副本、保留历史 run、相同卡/Task 身份及来源核验；102 项评估确定性测试通过。固定输入及初态不变，两个原精确任务计数经独立来源/交付身份复核修正为有界可接受集合（`1bf9de93`，仅 W39 3/4/5、会议 1/2），其他精确计数、零行动负例、引用和成员检查不变。baseline 原生 9 样本已完成：四个负例通过，五个正例未达到卡片/证据要求。首个资格修订及同项目新行动成员修订后，候选 W39 实际 3 Tasks/2 cards、同项目两行动实际 2 Tasks/1 card 且两者都是成员，通过相同模型/路由核验。会议候选仍因模型未明确任务业务相关性及确认新任务的项目关联而投影拒绝，不能把完成的 Task run 或旧卡说成更新成功。此处明确保留语义全样本和实际 W39 副本两次回放门槛；工具实现与 backup 准备不替代这些结果。

## Task 8：按现有流程上线、单输入生产回放和页面验收

**Files:** Update `docs/task-attention-phase1-validation.md` only after verified outcomes；生产不编辑源码。

- [ ] **1. 开发验收汇总。** 在部署前跑本次涉及的 Python 文件和两份前端测试/build，检查 import `app.cli, app.worker, app.email_worker, app.service_supervisor`。不用全套串行测试；未通过项写明并修好，不能混同其他 Agent 的失败。
- [ ] **2. 推送已合并 runtime commit。** 读回 origin/main revision。按共享发布流程 `python -m app.deploy`，等待安静窗口、backup、fast-forward/build/import/restart/health；不手动 kill/kickstart，不在 `~/Services/ceo-agent-service` 编辑或测试。只说“代码已推送”直到部署实际结束。
- [ ] **3. 部署读回与权威 Skill 发布。** 记录 production checkout HEAD、新 PID、healthz、相关队列、Attention 和 History API。确认代码部署成功后，通过现有 Skill 仓库流程把已验证副本的相同内容发布到全局权威 `ceo-work-tracking/SKILL.md`，核对实际配置的 Skill root、加载内容及 metadata/version 与副本一致。新 prompt/receipt 来自生产 checkout，Skill 默认来自全局权威目录，不能把 checkout 里的 CI 副本当作生产读取证明。schema 升级只表示字段存在，不等于已生成关注。
- [ ] **4. 备份生产 DB 后仅重排已确认的 W39 输入。** 该数据操作只有在用户已授权上线后重跑，且 Task7 副本验证全过时执行。使用下面单行限定更新；保留 attempts、payload 和全部旧 run。更新必须恰好一行，否则 rollback。不能调用只接受 failed 的 requeue 方法来重开 done，也不能把旧 run 改成 running。

```sql
update work_summary_inputs
set status='pending', error='', available_at='', updated_at=current_timestamp
where id=27465
  and source_ref='dingtalk-doc:a9E05BDRVQvy7QEacPZLB4anJ63zgkYA#sha256=21661643562265ca27e3369112a7ce3e91d9cbb6d21733050b5c3e7a9d42bf1e'
  and status in ('done', 'skipped');
```
- [ ] **5. 交给原服务队列完成。** 读回新独立 run 及 receipt，Task/Project IDs、两张目标 Attention 卡和 `/api/console/tasks/attention?page=1&page_size=20`。原文依据、项目关联、候选/正式状态分别核对。不能仅凭输入 done 或 receipt completed 判断语义效果。
- [ ] **6. 验证非周报来源。** 在部署后已完成的会议/聊天输入中找相关真实项目样本，读回 proposal/receipt/实际卡片。存在输入但无 material risk 时 no_proposal 合理；没有合适真实样本就明确“非周报生产语义尚未验证”，保留固定真实-runner样本验证结果，不能伪造生产消息。无需为了测试向人发送消息。
- [ ] **7. 页面亲验。** 打开 `/tasks` 的需关注，核对列表两项风险重要信息；点入详情看事实、推断、时间、来源链接、关联任务；watch 显示当前无需处理/观察点。确认 Project/正式任务/待确认线索列表和 detail 没有因新关联失真；亮色/暗色与窄屏自检。
- [ ] **8. 清理与报告。** 删除自己创建的已用临时副本；备份只保留最新经完整性验证的一份，明确路径和可恢复性。报告实现、测试、eval、提交/推送、部署、生产回放、页面效果各自证据。最后更新 validation 文档和释放文件 claims。

## 自检结果与执行顺序

Task 1→2→3→4→5→6→7→8；契约、关联、回执先于语义回放。所有步骤由同一会话执行亦可，不需要新增 Agent 才能工作。若选择 subagent 开发，Task 1–5 共享核心文件，必须顺序交接；Task 6 在契约稳定后才可独立开发。

设计覆盖：多来源/综合推断→Task4/5/7；独立 Project 行→Task1/2；新 anchor→Task2；候选支持→Task4；一项目一卡→Task4；不造任务/不误升级→Task1/5/7；watch→Task6；可观测→Task3/4；来源冲突→Task5/7；W39+非周报验收→Task7/8；架构/Skill一致→每次行为提交及Task5；无新增维护/completion/权限层→全程边界。

每项代码任务必须先有失败回归，再实施，再跑对应文件；步骤中的模型/函数名为同一契约，不另外开新旧并行路径。执行前仍应检查最新代码/claim，因为其他 Agent 会继续提交。本计划不宣称当前生产计数或历史状态在今后保持不变。
