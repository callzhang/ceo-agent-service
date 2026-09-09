# Email Console 高密度列表、类型描述与模型训练 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已确认的 Email Console 视觉方案落成可用产品：邮件列表高密度分页、结构化类别描述、可解释的模型训练历史，以及由用户显式控制且后端强校验的主模型切换。

**Architecture:** 邮箱服务器文件夹继续是分类事实源；Email Store 保存版本化晋升门槛和模式切换记录；Registry/runtime 验证模型证据并原子切换运行模式；Console API 只投影后端判定结果；React 只渲染事实和发起带前置条件的请求，不自行计算模型是否可晋升。

**Tech Stack:** Python 3.12、SQLite、FastAPI、Pydantic、pytest；React 19、TypeScript、React Router、Recharts、Testing Library、Vitest、Vite；launchd 本地服务。

---

## 实施边界

- 权威设计：[2026-09-08-email-console-density-and-model-training-design.md](../specs/2026-09-08-email-console-density-and-model-training-design.md)。
- 保留既有生命周期：冷启动 Agent 主分类，阶段性离线训练；完整模型成熟后由用户切换；模型主分类时仅在拒判、超时、不可用或完整性失败时调用一次 Agent fallback。
- 不改 Audit、自动退订、回复授权、附件内容读取、任务生命周期或 SMTP 禁用规则。
- 不重新引入 `subscription`、`other` 或可配置 `important`/`junk`；`important` 是 Star/Flag，`junk` 固定映射系统 Trash。
- 不把分类确认创建成 Email task。
- Toggle 的本地状态不是运行事实；任何切换都以后端 readback 为准。
- 工作树可能含并行任务的未提交文件。每次提交只暂存本任务列出的精确文件。

## 完成标准

- 四个 Tab 为 `已处理 | 待反馈 | 邮件配置 | 模型训练`。
- 已处理和待反馈均为默认 50 封/页的单行列表，支持 20/50/100、URL 状态和右侧详情抽屉。
- 类别配置使用 `core_description/include/exclude`，展示 folder binding、描述版本和配置版本。
- 模型训练页清楚展示 Agent 主分类、候选已达标、模型主分类三种状态。
- 默认可配置门槛为 Macro F1 0.95、逐类 Precision 0.95、逐类独立验证样本 20、P95 500ms；系统完整性条件不可被 UI 关闭。
- 达标候选显示红点并启用 Toggle，但不自动晋升；开启或关闭后保存可追溯切换历史。
- Registry 单条损坏只显示该条异常，不导致整个 Email 页面崩溃。
- Python、前端测试、构建、服务重启、HTTP readback 和真实浏览器检查全部通过。

## Task 1：持久化版本化晋升门槛和模式切换历史

**Files:**

- Modify: `app/email_store.py`
- Modify: `tests/test_email_store.py`

- [ ] **Step 1: 为默认门槛、版本追加和切换历史写失败测试**

新增测试覆盖：

```python
def test_email_model_promotion_config_is_versioned_and_defaults_are_stable(tmp_path):
    store = EmailStore(tmp_path / "email.sqlite3")
    initial = store.current_model_promotion_config()
    assert initial["config_version"] == "email-promotion-gate-v1"
    assert initial["macro_f1_min"] == 0.95
    assert initial["category_precision_min"] == 0.95
    assert initial["category_validation_samples_min"] == 20
    assert initial["p95_latency_max_ms"] == 500.0

    updated = store.create_model_promotion_config(
        config_version="email-promotion-gate-v2",
        macro_f1_min=0.96,
        category_precision_min=0.97,
        category_validation_samples_min=25,
        p95_latency_max_ms=450.0,
        expected_current_version="email-promotion-gate-v1",
    )
    assert updated["config_version"] == "email-promotion-gate-v2"
    assert [row["config_version"] for row in store.list_model_promotion_configs()] == [
        "email-promotion-gate-v2",
        "email-promotion-gate-v1",
    ]


def test_email_model_mode_transition_is_idempotent_and_append_only(tmp_path):
    store = EmailStore(tmp_path / "email.sqlite3")
    model_id = build_embedding_model_id(
        trained_at=datetime(2026, 9, 8, 8, 1, tzinfo=timezone.utc),
        artifact_sha256="a" * 64,
    )
    payload = {
        "request_id": "switch-1",
        "actor": "console-user",
        "from_mode": "agent_primary",
        "to_mode": "model_primary",
        "from_model_id": None,
        "target_model_id": model_id,
        "promotion_config_version": "email-promotion-gate-v1",
        "status": "applied",
        "reason": "user_enabled_primary_model",
    }
    transition = store.record_model_mode_transition(**payload)
    assert store.record_model_mode_transition(**payload) == transition
    assert len(store.list_model_mode_transitions()) == 1
```

另加：门槛越界、expected version 冲突、同一 `request_id` 不同 payload、未知模式、空 actor/model ID 的拒绝测试。

- [ ] **Step 2: 运行测试并确认因接口不存在而失败**

```bash
pytest -q tests/test_email_store.py -k 'model_promotion_config or model_mode_transition'
```

- [ ] **Step 3: 增加严格 schema contract 和不可变数据类型**

在 `app/email_store.py` 增加：

```python
@dataclass(frozen=True)
class EmailModelPromotionConfig:
    config_version: str
    macro_f1_min: float
    category_precision_min: float
    category_validation_samples_min: int
    p95_latency_max_ms: float
    created_at: str


@dataclass(frozen=True)
class EmailModelModeTransition:
    request_id: str
    actor: str
    from_mode: str
    to_mode: str
    from_model_id: str | None
    target_model_id: str | None
    promotion_config_version: str
    status: str
    reason: str
    created_at: str
```

创建两张 append-only 表：

```sql
create table if not exists email_model_promotion_configs (
    config_version text primary key check(trim(config_version) != ''),
    macro_f1_min real not null check(macro_f1_min > 0 and macro_f1_min <= 1),
    category_precision_min real not null check(category_precision_min > 0 and category_precision_min <= 1),
    category_validation_samples_min integer not null check(category_validation_samples_min > 0),
    p95_latency_max_ms real not null check(p95_latency_max_ms > 0),
    created_at text not null
);

create table if not exists email_model_mode_transitions (
    request_id text primary key check(trim(request_id) != ''),
    actor text not null check(trim(actor) != ''),
    from_mode text not null check(from_mode in ('agent_primary','model_primary')),
    to_mode text not null check(to_mode in ('agent_primary','model_primary')),
    from_model_id text,
    target_model_id text,
    promotion_config_version text not null,
    status text not null check(status in ('applied','rejected','failed')),
    reason text not null check(trim(reason) != ''),
    created_at text not null,
    foreign key(promotion_config_version)
        references email_model_promotion_configs(config_version)
);
```

把两张表加入现有 table/column/check/foreign-key contract 验证和备份恢复顺序。初始化用 `insert ... on conflict do nothing` 写入默认 `email-promotion-gate-v1`，不得覆盖已有版本。

- [ ] **Step 4: 实现 Store API 和 CAS 语义**

`create_model_promotion_config(...)` 必须在 `begin immediate` 中检查 `expected_current_version`，只追加新版本；`current_model_promotion_config()` 按 `created_at desc, rowid desc` 读取；切换记录按 `request_id` 幂等，同 ID 不同 payload 抛专门 conflict。

- [ ] **Step 5: 验证迁移、损坏检测和幂等测试**

```bash
pytest -q tests/test_email_store.py -k 'model_promotion_config or model_mode_transition or schema_contract or backup'
```

- [ ] **Step 6: 提交 Store 层**

```bash
git add app/email_store.py tests/test_email_store.py
git commit -m "feat(email): persist model promotion controls"
```

## Task 2：后端统一判定晋升资格并支持原子启用/停用

**Files:**

- Modify: `app/email_classifier_runtime.py`
- Modify: `tests/test_email_classifier_runtime.py`

- [ ] **Step 1: 为四项可配置门槛、不可配置完整性门槛和停用写失败测试**

覆盖以下矩阵：

- Macro F1 未达标；
- 任一启用类别 Precision 未达标；
- 任一启用类别独立验证样本不足；
- P95 超过门槛；
- 四项通过但 Registry、artifact、snapshot/description/category compatibility、important head 或连续候选证据失败；
- 所有条件通过时返回 `promotion_eligible=True` 和逐项实际值/目标值；
- 已激活模型可通过 `deactivate_online_model(...)` 原子恢复 Agent 主分类；
- expected model/mode 不匹配时拒绝，不能误删另一个激活记录；
- 重复停用同一事实幂等；无临时文件残留。

核心断言形状：

```python
assert result.as_dict()["checks"][0] == {
    "key": "macro_f1",
    "actual": 0.94,
    "target": 0.95,
    "operator": ">=",
    "passed": False,
    "reason": "macro_f1_below_threshold",
}
```

- [ ] **Step 2: 运行测试并确认失败**

```bash
pytest -q tests/test_email_classifier_runtime.py -k 'promotion_gate or deactivate_online_model'
```

- [ ] **Step 3: 实现纯函数 `assess_online_promotion_gate`**

输入只包含最新 staged evidence、当前启用类别、版本化门槛、Registry 完整性问题和既有 `assess_whole_model_readiness` 结果。输出稳定、受控、可序列化的检查项；不访问前端、不写 Registry、不降低既有双候选和 important 门槛。

每个启用类别分别生成 precision/support 检查。验证样本只读 evidence 中明确代表独立验证集的字段；缺失时判失败并返回 `independent_validation_support_missing`，不得拿训练样本总数代替。

- [ ] **Step 4: 实现 `deactivate_online_model`**

使用 Registry 根目录的同目录临时文件和原子替换保存受控 inactive readback，再精确移除 `online-active.json`。操作前重读并验证 expected model；操作后 `derive_runtime_mode` 必须为 `AGENT_PRIMARY`。不得删除模型 artifact、staged evidence 或 lifecycle history。

- [ ] **Step 5: 运行 runtime 全量回归**

```bash
pytest -q tests/test_email_classifier_runtime.py
```

- [ ] **Step 6: 提交 runtime 层**

```bash
git add app/email_classifier_runtime.py tests/test_email_classifier_runtime.py
git commit -m "feat(email): enforce model promotion gate"
```

## Task 3：扩展 Console API，提供可配置门槛和可追溯模式切换

**Files:**

- Modify: `app/web_api/email.py`
- Modify: `tests/test_email_web_api.py`
- Modify: `docs/architecture.md`
- Modify: `docs/runtime-mechanism.md`

- [ ] **Step 1: 先写 API contract 失败测试**

新增测试验证：

```python
def test_email_learning_projects_server_decided_promotion_gate(tmp_path):
    client, store, registry = _learning_client_with_ready_pair(tmp_path)
    response = client.get("/api/console/email/learning")
    assert response.status_code == 200
    learning = response.json()["learning"]
    assert learning["runtime"]["mode"] == "agent_primary"
    assert learning["runtime"]["toggle_enabled"] is True
    assert learning["runtime"]["candidate_ready"] is True
    assert learning["promotion_gate"]["config"]["p95_latency_max_ms"] == 500.0
    assert learning["promotion_gate"]["promotion_eligible"] is True


def test_email_runtime_mode_requires_current_fact_and_records_readback(tmp_path):
    client, store, registry = _learning_client_with_ready_pair(tmp_path)
    candidate_id = registry.latest_staged_evidence()["model_id"]
    response = client.put("/api/console/email/runtime-mode", json={
        "mode": "model_primary",
        "model_id": candidate_id,
        "request_id": "runtime-switch-1",
        "expected_mode": "agent_primary",
        "expected_model_id": None,
    })
    assert response.status_code == 200
    assert response.json()["runtime"]["mode"] == "model_primary"
    assert response.json()["runtime"]["active_model_id"] == candidate_id
    assert store.list_model_mode_transitions()[0]["status"] == "applied"
```

在同一测试文件实现 `_learning_client_with_ready_pair`，复用现有 staged-evidence fixture
构造两个不同 snapshot 的兼容达标候选，不复制生产判定算法。另覆盖：未达标 409、expected
fact 冲突 409、Registry 损坏 409、重复 request ID 幂等、关闭 Toggle、切换失败后仍投影
真实模式、文件切换成功但 transition 落库失败时恢复原运行事实、门槛更新生成新版本、旧候选
不会因降低门槛自动激活。

- [ ] **Step 2: 运行新 API 测试并确认失败**

```bash
pytest -q tests/test_email_web_api.py -k 'promotion_gate or runtime_mode or promotion_config'
```

- [ ] **Step 3: 增加严格 Pydantic payload**

```python
class EmailPromotionConfigPayload(BaseModel):
    macro_f1_min: float = Field(gt=0, le=1)
    category_precision_min: float = Field(gt=0, le=1)
    category_validation_samples_min: int = Field(gt=0)
    p95_latency_max_ms: float = Field(gt=0)
    config_version: str = Field(min_length=1)
    expected_current_version: str = Field(min_length=1)


class EmailRuntimeModePayload(BaseModel):
    mode: Literal["agent_primary", "model_primary"]
    model_id: str | None = None
    request_id: str = Field(min_length=1)
    expected_mode: Literal["agent_primary", "model_primary"]
    expected_model_id: str | None = None
```

切到 `model_primary` 时 `model_id` 必填；切回 `agent_primary` 时必须为空。

- [ ] **Step 4: 扩展 learning read model**

`GET /api/console/email/learning` 增加：

```json
{
  "runtime": {
    "mode": "agent_primary",
    "active_model_id": null,
    "candidate_model_id": "email-embedding-mlp-...",
    "candidate_ready": true,
    "toggle_enabled": true,
    "fallback_counts": {},
    "timing": {}
  },
  "promotion_gate": {
    "config": {},
    "promotion_eligible": true,
    "checks": []
  },
  "mode_transitions": []
}
```

保留现有 `registry_issues` 隔离行为。`candidate_ready` 和 `toggle_enabled` 完全由后端 gate 决定。

- [ ] **Step 5: 增加两个写接口**

- `PUT /api/console/email/promotion-config`：追加版本，返回新配置和重算 gate；不切换模型。
- `PUT /api/console/email/runtime-mode`：CAS 检查当前模式/模型，重算 gate，原子 activate/deactivate，保存 transition，再 readback。

它们是 Email runtime 控制，不引入 CEO task、Consumer/Audit run 或新审批逻辑。

- [ ] **Step 6: 更新架构文档**

明确后端统一计算晋升资格、用户 Toggle 才切换、threshold config 和切换记录均 append-only、runtime tick 可无重启 adopt/revoke，而代码部署仍按项目规则重启服务。

- [ ] **Step 7: 运行后端回归**

```bash
pytest -q tests/test_email_web_api.py tests/test_email_classifier_runtime.py tests/test_email_store.py
```

- [ ] **Step 8: 提交 API 层**

```bash
git add app/web_api/email.py tests/test_email_web_api.py docs/architecture.md docs/runtime-mechanism.md
git commit -m "feat(email): expose model training controls"
```

## Task 4：对齐前端 Email API 类型和客户端方法

**Files:**

- Modify: `frontend/src/api/console.ts`
- Modify: `frontend/src/api/console.test.ts`

- [ ] **Step 1: 写 mapper 和请求失败测试**

覆盖：

- `classification_source` 支持服务器实际来源，不错误收窄为 `"model" | "user"`；
- category config 使用 `category_key/display_name/core_description/include/exclude/description_version/bindings`；
- learning 映射 runtime、promotion gate、staged versions、transition、registry issue；
- folder bindings 和 model version detail 有专用方法；
- runtime mode 和 promotion config 请求发送完整 CAS/version payload。

```ts
expect(await listEmailConfigs()).toEqual(expect.objectContaining({
  items: [expect.objectContaining({
    category_key: "external_billing",
    core_description: "外部服务商向本公司收取费用的账单。",
    include: ["云服务账单"],
    exclude: ["内部预算审批应归工作"],
    bindings: [expect.objectContaining({ binding_status: "active" })],
  })],
}));
```

- [ ] **Step 2: 运行失败测试**

```bash
pnpm --dir frontend test --run src/api/console.test.ts
```

- [ ] **Step 3: 替换陈旧类型，不保留 legacy fallback**

```ts
export interface EmailCategoryConfig {
  category_key: string;
  display_name: string;
  core_description: string;
  include: string[];
  exclude: string[];
  threshold: number;
  actions: string[];
  action_parameters: Record<string, Record<string, unknown>>;
  enabled: boolean;
  description_version: string;
  config_version: string;
  updated_at: string;
  bindings: EmailFolderBinding[];
}
```

补充 `EmailRuntimeState`、`EmailPromotionGate`、`EmailPromotionCheck`、`EmailModelVersionDetail`、`EmailModelModeTransition`。删除旧 `category/description` 可编辑事实源。

- [ ] **Step 4: 增加 API 方法**

增加 `getEmailModelVersion`、`listEmailFolderBindings`、`createEmailConfig`、`updateEmailPromotionConfig` 和 `updateEmailRuntimeMode`；`saveEmailConfig` 改用结构化 payload。所有动态 path segment 使用 `encodeURIComponent`。

- [ ] **Step 5: 类型和 API 单测通过**

```bash
pnpm --dir frontend test --run src/api/console.test.ts
pnpm --dir frontend build
```

- [ ] **Step 6: 提交前端 contract**

```bash
git add frontend/src/api/console.ts frontend/src/api/console.test.ts
git commit -m "refactor(email): align console API contracts"
```

## Task 5：实现共用高密度邮件列表、URL 分页和右侧详情抽屉

**Files:**

- Create: `frontend/src/components/email/EmailMessageList.tsx`
- Create: `frontend/src/components/email/EmailMessageDrawer.tsx`
- Create: `frontend/src/components/email/EmailPagination.tsx`
- Modify: `frontend/src/pages/EmailPage.tsx`
- Modify: `frontend/src/pages/PendingEmailFeedback.tsx`
- Modify: `frontend/src/pages/EmailPage.test.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: 写交互失败测试**

覆盖：

- Tab 文案为“模型训练”，不再出现“学习”；
- 已处理和待反馈首次请求都显式发送 `page=1&page_size=50`；
- 可切换 20/50/100；
- `tab/page/page_size/selected` 存入 URL，刷新后恢复；
- 一封邮件只占一行，正文和附件不在列表展开；
- 点击行才请求 detail 并打开 drawer；关闭后焦点回原行；
- 快速切换时前一请求被取消，迟到结果不覆盖当前详情；
- 已处理 drawer 只读；待反馈在 drawer 中提交；
- 提交防重复，成功后选下一封/上一封/合法上一页；失败保留当前项；
- 正文为空显示既定提示；
- drawer 开关不丢列表滚动位置。

```tsx
expect(screen.getAllByRole("row", { name: /邮件/ })).toHaveLength(50);
await user.click(screen.getByRole("row", { name: /合同确认/ }));
expect(await screen.findByRole("dialog", { name: "邮件详情" })).toBeVisible();
expect(mockGetEmailClassification).toHaveBeenCalledWith(
  "42",
  expect.any(AbortSignal),
);
```

- [ ] **Step 2: 运行测试确认失败**

```bash
pnpm --dir frontend test --run src/pages/EmailPage.test.tsx
```

- [ ] **Step 3: 提取共用列表和分页组件**

`EmailMessageList` 只接收行数据和事件，不自己请求 API：

```ts
interface EmailMessageListProps {
  rows: EmailClassificationItem[];
  selectedId: string | null;
  mode: "processed" | "pending_feedback";
  disabled: boolean;
  onSelect: (id: string, trigger: HTMLElement) => void;
}
```

桌面列顺序为 Star/Flag、发件人、主题+单行摘要、类别/建议、状态、时间。`important` 只读独立 provider/分类信号，不从 category 推断。

- [ ] **Step 4: 实现受控详情抽屉**

使用 `role="dialog"`、`aria-modal="true"`、Escape 关闭和焦点恢复；按需读取详情。附件只显示 filename/MIME/size/inline。已处理显示动作 readback；待反馈沿用现有反馈请求语义，不创建 task。

- [ ] **Step 5: 实现 URL 状态与加载稳定性**

统一 URL：

```text
?tab=processed&page=2&page_size=50&selected=8423079112545370123
```

非法 page/page_size 回默认；删除当前项造成空页时跳合法上一页；翻页加载保留旧 rows 并设置 `aria-busy`。

- [ ] **Step 6: 实现高密度和窄屏 CSS**

- 桌面行高 40–48px，文本单行省略。
- drawer 宽度 `min(680px, 52vw)`。
- 小于 760px 时隐藏独立发件人和低优先级状态列，drawer 宽度 `min(92vw, 680px)`。
- 沿用现有 CSS token，不写只适配一条样例的硬编码。

- [ ] **Step 7: 运行列表测试和构建**

```bash
pnpm --dir frontend test --run src/pages/EmailPage.test.tsx
pnpm --dir frontend build
```

- [ ] **Step 8: 提交高密度列表**

```bash
git add frontend/src/components/email/EmailMessageList.tsx frontend/src/components/email/EmailMessageDrawer.tsx frontend/src/components/email/EmailPagination.tsx frontend/src/pages/EmailPage.tsx frontend/src/pages/PendingEmailFeedback.tsx frontend/src/pages/EmailPage.test.tsx frontend/src/styles.css
git commit -m "feat(email): add dense paginated message review"
```

## Task 6：实现三段式邮件类型配置和文件夹事实展示

**Files:**

- Create: `frontend/src/components/email/EmailCategoryConfigPanel.tsx`
- Modify: `frontend/src/pages/EmailPage.tsx`
- Modify: `frontend/src/pages/EmailPage.test.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: 写配置 UX 失败测试**

覆盖：

- 左侧类别来自 API，不使用静态八类数组；
- 展示工作、人事、法务、融资、个人、通知、外部账单、购物；
- 不出现 subscription/other，不把 important/junk 当普通类别；
- 分别编辑核心定义、包括场景、排除场景；
- folder binding 按账号显示 active/unavailable 和 provider 文件夹名；
- `junk → Trash`、`important → Star / Flag` 是只读规则；
- 保存成功显示服务器返回的新 description/config version；失败保留输入；
- 新建类别只有全部启用账号 binding readback 成功后显示 enabled；
- 切换类别时未保存输入不能被静默覆盖。

- [ ] **Step 2: 运行失败测试**

```bash
pnpm --dir frontend test --run src/pages/EmailPage.test.tsx -t '邮件配置'
```

- [ ] **Step 3: 实现 API 驱动的导航和单项编辑器**

```ts
type EmailCategoryDraft = Pick<EmailCategoryConfig,
  | "core_description"
  | "include"
  | "exclude"
  | "threshold"
  | "actions"
  | "action_parameters"
  | "enabled"
>;
```

`include`/`exclude` 在 textarea 中一行一项，提交前只剔除空行；业务合法性和 folder readback 由后端验证。

- [ ] **Step 4: 实现版本与并发处理**

前端版本只作为请求身份，最终展示服务器返回值。后端 409 时保留 draft，重新读取服务器版本并提示用户选择，不自动覆盖较新配置。

- [ ] **Step 5: 运行配置测试和构建**

```bash
pnpm --dir frontend test --run src/pages/EmailPage.test.tsx
pnpm --dir frontend build
```

- [ ] **Step 6: 提交配置 UI**

```bash
git add frontend/src/components/email/EmailCategoryConfigPanel.tsx frontend/src/pages/EmailPage.tsx frontend/src/pages/EmailPage.test.tsx frontend/src/styles.css
git commit -m "feat(email): edit structured category descriptions"
```

## Task 7：实现模型训练运营总览、趋势图、版本详情和主模型 Toggle

**Files:**

- Create: `frontend/src/components/email/EmailModelTrainingPanel.tsx`
- Create: `frontend/src/components/email/EmailModelTrendChart.tsx`
- Create: `frontend/src/components/email/EmailModelVersionDrawer.tsx`
- Modify: `frontend/src/pages/EmailPage.tsx`
- Modify: `frontend/src/pages/EmailPage.test.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: 写三种状态与版本历史失败测试**

覆盖：

- Agent 主分类且未达标：Toggle disabled、无红点、显示失败原因；
- 候选已达标：Tab/标题红点、Toggle enabled 但 off；
- 模型主分类：Toggle on、显示完整 active model ID 和 fallback 计数；
- 切换前显示确认文案；取消不发请求；确认发送 CAS payload；
- 成功以后端 readback 更新；失败恢复服务器事实；
- 门槛编辑显示四项，保存只生成新版本；
- 趋势图默认最近 10 个可比较版本的 Macro F1 和门槛线；不兼容版本断开；
- 可切换 Accuracy、逐类 Precision/Recall/F1、P50/P95/P99；
- 版本主表一行一个完整模型 ID；点击按需加载 detail drawer；
- 单条 Registry 损坏显示异常行，其他版本和主页面仍可操作；
- 无版本、单版本、无可比较版本有真实空状态。

```tsx
expect(screen.getByRole("switch", {
  name: "使用主模型分类新邮件",
})).toBeEnabled();
expect(screen.getByText("有候选模型已达到全部晋升门槛")).toBeVisible();
await user.click(screen.getByRole("switch", {
  name: "使用主模型分类新邮件",
}));
await user.click(screen.getByRole("button", { name: "确认切换" }));
expect(mockUpdateRuntimeMode).toHaveBeenCalledWith(expect.objectContaining({
  mode: "model_primary",
  expected_mode: "agent_primary",
}));
```

- [ ] **Step 2: 运行失败测试**

```bash
pnpm --dir frontend test --run src/pages/EmailPage.test.tsx -t '模型训练'
```

- [ ] **Step 3: 实现状态总览和门槛检查**

直接使用 `learning.runtime` 和 `learning.promotion_gate`。红点条件严格为后端 `candidate_ready` 且当前是 Agent 主分类；Toggle enabled 条件严格为 `runtime.toggle_enabled`，React 不复算。

- [ ] **Step 4: 实现版本化门槛编辑**

用百分比/毫秒显示，提交时转回 API 数值。保存后重读 learning，显示新 configuration version；不得连带调用 runtime-mode API。

- [ ] **Step 5: 实现 Recharts 趋势图**

复用现有 chart token。按后端 comparability key 分段再绘制 `Line`，门槛用 `ReferenceLine`。tooltip/键盘摘要包含版本、训练时间、样本数、数据版本和指标。P95 图显示当前上限线。

- [ ] **Step 6: 实现紧凑版本表和详情抽屉**

主表列为完整版本、状态、训练时间、样本、Macro F1、P95、完整性和详情。抽屉按需读取每类指标、important head、coverage、训练 lineage、artifact SHA、P50/P95/P99、加载/首次调用耗时和生命周期原因。

- [ ] **Step 7: 实现 Toggle 的确认、CAS 和错误恢复**

打开时使用后端 candidate ID；关闭时使用 active model ID 作为 expected fact。请求期间禁用；返回后使用 response runtime；网络失败则重读 learning 并提示“切换未完成，已恢复服务器实际状态”。不做乐观成功。

- [ ] **Step 8: 运行模型训练 UI 测试和构建**

```bash
pnpm --dir frontend test --run src/pages/EmailPage.test.tsx
pnpm --dir frontend build
```

- [ ] **Step 9: 提交模型训练 UI**

```bash
git add frontend/src/components/email/EmailModelTrainingPanel.tsx frontend/src/components/email/EmailModelTrendChart.tsx frontend/src/components/email/EmailModelVersionDrawer.tsx frontend/src/pages/EmailPage.tsx frontend/src/pages/EmailPage.test.tsx frontend/src/styles.css
git commit -m "feat(email): add model training operations dashboard"
```

## Task 8：全量自检、真实运行验证和视觉验收

**Files:**

- Verify: `docs/superpowers/specs/2026-09-08-email-console-density-and-model-training-design.md`
- Verify: `docs/architecture.md`
- Verify: `docs/runtime-mechanism.md`
- Test: all task-owned source and tests from Tasks 1–7

- [ ] **Step 1: 运行精确后端测试**

```bash
pytest -q tests/test_email_store.py tests/test_email_classifier_runtime.py tests/test_email_web_api.py
```

- [ ] **Step 2: 运行全部前端测试两次和生产构建**

```bash
pnpm --dir frontend test --run
pnpm --dir frontend test --run
pnpm --dir frontend build
```

两次应有相同通过数，TypeScript 和 Vite build 成功。

- [ ] **Step 3: 运行完整 Python suite**

```bash
pytest -q
```

若出现并行 WIP 失败，先在干净提交状态复现；不能用 focused suite 冒充完成。

- [ ] **Step 4: 检查工作树和提交边界**

```bash
git status --short
git diff --check
git log --oneline -8
```

不得把 `.superpowers/`、`su-candidate.pdf`、`..env-write.lock` 或其他并行任务文件加入提交。

- [ ] **Step 5: 重启本地服务并验证新进程**

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

记录重启前后 PID，确认是新进程。

- [ ] **Step 6: 做 HTTP readback**

```bash
curl --fail --silent http://127.0.0.1:8000/healthz
curl --fail --silent http://127.0.0.1:8000/api/console/email/learning
curl --fail --silent 'http://127.0.0.1:8000/api/console/email/classifications?status=processed&page=1&page_size=50'
curl --fail --silent http://127.0.0.1:8000/api/console/email/config
```

实际端口以 launchd 配置为准。readback 必须确认 mode、gate config/version、candidate/toggle、列表 meta、结构化描述和 bindings，不能只看 HTTP 200。

- [ ] **Step 7: 检查 Email backlog 和 worker 状态**

确认没有 unresolved `failed` 或长期 `processing` backlog；scanner、deterministic action、Agent classifier 和 trainer 分别读取，不能用 web 健康代替 worker 健康。missing model 下的 Agent 主分类可以是合法状态，但必须与页面一致。

- [ ] **Step 8: 用真实浏览器完成视觉和交互矩阵**

桌面宽度和小于 760px 窄屏分别检查：

1. 已处理：50 行分页、单行省略、drawer、正文空状态、附件 metadata、动作证据。
2. 待反馈：建议不冒充最终分类、保存锁、自动前进、失败保留、末页回退。
3. 邮件配置：三段描述、类别导航、固定规则、folder binding、错误保留输入。
4. 模型训练：三种状态、红点、disabled/enabled/on Toggle、四项门槛、趋势图、版本表、Registry 异常、详情 drawer。
5. 键盘：Tab 顺序、Enter/Space、Escape、焦点恢复、图表可读摘要。
6. 视觉：无横向压缩、无逐字竖排、无大卡片堆叠、翻页不跳动。

每个场景保留截图或测试证据。发现问题先补回归测试再修复，不做只对截图有效的 CSS patch。

- [ ] **Step 9: 若验证产生修复，单独提交**

逐个列出 `git diff --name-only` 中确属本计划的路径并显式暂存；禁止使用
`git add -A`、`git add .` 或目录级暂存。提交前用 `git diff --cached --name-only`
确认只包含 Tasks 1–7 中列出的文件，然后执行：

```bash
git commit -m "fix(email): close console verification gaps"
```

- [ ] **Step 10: 最终验收摘要**

最终报告包含：

- 最终 commit SHA 列表；
- Python/前端测试通过数、build 结果；
- launchd 新 PID、health 和 Email worker/backlog 事实；
- API readback 的 mode/gate/config version；
- 浏览器桌面/窄屏场景；
- 未提交且不属于本任务的文件；
- 明确没有自动上线模型，也没有改变 Audit/退订/回复行为。

## 实施顺序与回滚边界

```text
Store 版本化事实
  → Runtime 统一判定和原子切换
  → Console API
  → 前端 contract
  → 高密度邮件列表
  → 三段式类别配置
  → 模型训练总览
  → 全量、运行和浏览器验收
```

每个提交独立通过 focused tests。若 UI 发现后端 contract 不足，回到 Task 3 增加明确 API 和测试，不在 React 写推断或兼容分支。若模式切换失败，恢复 Agent 主分类事实，但保留模型 artifact、证据和 transition 历史，再修复并重验。
