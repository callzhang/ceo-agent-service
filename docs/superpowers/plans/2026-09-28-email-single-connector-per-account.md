# One Shared IMAP Connector Per Email Account

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every email account has exactly one live IMAP connection at any moment, shared by every subsystem in the worker process that needs it. No code path dials its own socket to an account that already has a connection open.

**Architecture:** A new per-account `EmailAccountConnector` owns the one raw `imaplib.IMAP4_SSL` session for that account behind a real mutex (blocking, priority-ordered acquire, not "take it if idle else open another" like the current warm-session helper). Callers acquire it through a context manager that blocks (up to 600s) until free, hands back a live session (reused if healthy and not stale, freshly connected otherwise) wrapped in whichever adapter class the caller needs (`ImapReadonlyAdapter` or `ImapDeterministicProvider` — both already accept a pre-connected `session` in their constructor, so the registry does not need to unify their APIs, only share the underlying socket). On a clean return the session is kept for the next acquirer (mirrors the existing 45s-idle / 300s-max-age eviction policy); on an exception the session is closed and dropped so the next acquirer reconnects fresh. A process-wide `EmailConnectorRegistry` holds one `EmailAccountConnector` per `account_id`, created lazily.

**Derek 2026-09-28 拍板（三点都定了，替换原来"待你拍板"里的对应项）：**
1. **要优先级**，不是纯 FIFO——见下面"优先级设计"。
2. **全部 6 个 Task 都做**，不是先做 1-2 看效果。
3. **排队超时 600 秒**（不是我建议的 120 秒）。

### 优先级设计

两档：`HIGH`（邮箱动作投递、周期分类扫描——直接影响主人能不能及时看到/处理邮件）、`LOW`（训练观察任务、历史回溯、模型动作补建——后台批量，慢一点不影响主人）。

`acquire(account_id, kind, priority)` 排队时按优先级插队：连接一释放，唤醒等待队列里优先级最高的那个（同优先级内先来后到）。这解决的是"抢空闲连接时排在前面"，**不解决"低优先级正在用的连接能不能被打断"**——Python 线程没法在一次阻塞的 socket 读取中间被强制抢占，所以如果 LOW 优先级的训练观察任务正卡在读一封慢邮件，HIGH 优先级的动作投递还是得等它读完（或超时）才能拿到连接。这是 v1 的已知局限，不是遗漏；有了 600 秒的超时兜底和今天已经上线的 90 秒单次读取超时（训练观察任务一次卡住的读取最多 90 秒就会失败让出），加起来能把"HIGH 优先级最坏要等多久"限制在有限时间内，不是无限期。如果实测下来还是等太久，再考虑让训练观察任务在消息之间主动检查排队情况并让出——这个不在本次范围内。

**Why now:** 2026-09-28 incident. `py-spy dump` on the live `app.cli email-worker` process showed the `ceo-agent-email-training` thread and the `ceo-agent-email-provider-actions` thread both blocked on a raw `ssl.read()` to the same Gmail account at the same moment, on two separate sockets. Gmail throttled the account (compounded by ad-hoc diagnostic connections opened outside the worker during the same investigation). The worker's own architecture already had this exposure before today: every subsystem that touches IMAP dials its own connection.

**Tech Stack:** Python 3, `imaplib`/`ssl`, `threading.Lock`, pytest.

---

## 边界和前置条件

- 这是运行时连接管理的改动，不改邮箱账号配置、密钥解析、动作语义（move/trash/mark_read 等）或 `ImapReadonlyAdapter`/`ImapDeterministicProvider` 各自的公开方法签名——只改这两个类怎么拿到已连接的 `session`。
- 不引入跨进程协调；今天只有一个 `app.cli email-worker` 进程持有账号连接。以后如果拆成多进程，这个假设要重新审视——先记在案，不在本计划范围内解决。
- 排队按优先级（HIGH/LOW），不是纯 FIFO；不做"打断正在进行的读取"这种抢占，理由和局限见上面"优先级设计"。
- 复用现有失败即丢弃重连的原则（`app/email_provider_actions.py` 里 `DeterministicEmailActionExecutor` 已经这样做；`app/email_imap_readonly.py` 今天刚加的"单条消息解析失败跳过、游标照常前进"和"90 秒 socket 超时"不受影响，继续在各自的 fetch 方法里起作用——新连接层只管"连接本身怎么被拿到和归还"，不改这两处已经上线的容错逻辑）。
- 本工作树按仓库规矩走：开工前查 `docs/agent-claims.md` 认领文件；每个 Task 完成后单独 `git commit --only` 自己的改动；改完行为要在同一提交里同步 `docs/architecture.md`（尤其是当前写着"写连接不关闭，交给同一账号的下一个动作当写连接……闲置超过 45 秒、连续复用超过 300 秒……就丢弃重连"的那一段——新设计吸收并取代这段描述的手写热连接逻辑，文档要跟着改，不能两套并存的描述都留着）；不自行重启 `com.ceo-agent-service.main`，按规矩推送后跑 `python -m app.deploy`。
- 每个 Task 迁移一个调用点，各自单独部署验证，不要一次性把 8 个调用点全改了再部署——今天的事故说明这类改动出错的代价很高。

## 文件地图

| 文件 | 单一职责 |
| --- | --- |
| `app/email_account_connector.py`（新） | `EmailAccountConnector`（单账号锁 + 会话生命周期）、`EmailConnectorRegistry`（按 account_id 懒创建）。 |
| `tests/test_email_account_connector.py`（新） | 排队互斥、健康复用、闲置/超龄淘汰、失败即丢弃重连、阻塞超时、并发线程互斥的单元测试。 |
| `app/email_worker.py` | 8 处 `source_factory(account)` 调用点、`_build_imap_direct_action_executor_factory`（连同 `WARM_SESSION_MAX_IDLE_SECONDS`/`WARM_SESSION_MAX_AGE_SECONDS`/`warm_sessions`）、训练观察任务的 `source_factory` 构造处，逐个迁移到经过 registry 获取连接。 |
| `docs/architecture.md`、`docs/runtime-mechanism.md` | 把"每个子系统各自开连接"改成"每账号一个共享 connector"；替换写连接热复用那一段描述。 |

## 现状盘点（写 spec 时已查清，实现前不用重查）

`app/email_worker.py` 里独立调用 `source_factory(account)` 的 8 处（各自打开一条新连接）：

| 调用点 | 所在函数 | 用途 | 优先级 |
| --- | --- | --- | --- |
| 2959 | `resolve`（OTP 相关） | 按需读取一次性验证码邮件 | HIGH（挡在一次登录流程前面，用户在等） |
| 3001 | `_read_historical_provider_state` | 历史回溯读当前 provider 状态 | LOW |
| 3096 | `_reread_historical_candidate_message` | 历史候选邮件重读 | LOW |
| 3605 | `read_current_classification_message`（`build_dependencies` 内） | 分类 Agent 任务按需读当前邮件 | HIGH |
| 3658 | `scan_account`（`build_dependencies` 内） | 周期性分类扫描，每账号每轮 | HIGH |
| 3791 | `run_historical_once` | 历史回溯批处理 | LOW |
| 4045 | `load_model_action_repair_message` | 模型动作补建按需读消息 | LOW |
| 4342 | `resolve_entries` | 退订入口解析 | HIGH（用户在等退订结果） |

另外两处不经过 `source_factory` 直接开连接：

- `ProviderTrainingObservationJob`（`app/email_training_observer.py`）：训练观察任务，`run_once` 内对每个账号建一条连接，跑完这一轮才关（本次事故里这条线程正卡在这里）。**LOW**。
- `_build_imap_direct_action_executor_factory`（`app/email_worker.py:4206`）：邮箱动作投递的"热连接"，`warm_sessions[account_id]` + `warm_lock`，**并发下不互斥**——两个线程同时调用 `executor_factory(account_id)` 时，`take_warm_session` 用 `pop()` 原子地只让一个线程拿到热连接，另一个线程会直接 `connect_provider()` 另开一条，这正是"看起来在复用连接、其实挡不住并发多开"的地方，也是本次事故复现的确切机制。**HIGH**（真正执行邮箱动作，用户在等结果）。

`ImapReadonlyAdapter`（只读，`email_imap_readonly.py`）和 `ImapDeterministicProvider`（读写，`email_provider_actions.py`）是两个独立实现、各自 `.connect()`，但都接受一个已连接的 `session` 对象初始化——这是新 registry 能直接复用、不用改两个类内部逻辑的原因。

## Task 1：`EmailAccountConnector` / `EmailConnectorRegistry`，先写测试

**Files:** Create `app/email_account_connector.py`, `tests/test_email_account_connector.py`.

- [ ] 先写失败的测试：两个线程同时对同一 `account_id` 调用 `registry.acquire(account_id, kind="readonly")`，用一个假的 `connect_fn`（计数 + 可控延迟）断言任意时刻只有一个线程持有连接、`connect_fn` 只被调用一次（第二个线程复用第一个线程归还的连接，不新开）。
- [ ] 再写：连接健康（`NOOP` 成功）且未超过 idle/max-age 时复用；探活失败或已过期时丢弃重连（沿用现在 `take_warm_session` 的判断，改成阻塞版）。
- [ ] 再写：`with registry.acquire(...) as adapter:` 块内抛异常时，连接被关闭丢弃，不放回复用；下一个 acquirer 拿到全新连接。
- [ ] 再写：`kind="readonly"` 返回 `ImapReadonlyAdapter` 包着共享 session，`kind="deterministic"` 返回 `ImapDeterministicProvider` 包着**同一条**共享 session（用同一个假 session 对象断言 `is`）。
- [ ] 再写：`acquire` 阻塞超时 600 秒，超时抛一个清楚说明"等哪个账号的连接、等了多久、自己排的什么优先级"的异常，不是裸 `TimeoutError`。
- [ ] 再写优先级：三个线程同时排队同一账号（1 个 LOW 先到、2 个 HIGH 后到但仍在等待窗口内），连接一释放，断言下一个拿到的是 HIGH（先到的那个 LOW 排在两个 HIGH 后面，除非它已经在使用中）；同优先级内断言先到先得。
- [ ] 实现 `EmailAccountConnector`（单账号：`threading.Lock`、当前 session、`kept_at`、`chain_began`，`acquire()`/内部 `_connect()`/`_alive()`）与 `EmailConnectorRegistry`（`dict[str, EmailAccountConnector]` + 小锁保护懒创建，不用于每次借用）。
- [ ] 跑 `pytest -q tests/test_email_account_connector.py`，全绿后 `git commit --only` 这两个文件。

## Task 2：邮箱动作投递迁移到 registry，退役 `warm_sessions`

**Files:** `app/email_worker.py`（`_build_imap_direct_action_executor_factory` 及其两个常量、`warm_sessions`/`warm_lock`），`tests/test_email_worker.py`。

- [ ] 先在现有热连接测试基础上加一个新测试：两个线程"同时"（用一个受控的假连接延迟制造重叠窗口）请求同一账号的 executor，断言只建了一条连接，不是两条——现状下这个测试应该失败（现有实现允许并发多开）。
- [ ] 把 `_build_imap_direct_action_executor_factory` 改成从 `EmailConnectorRegistry.acquire(account_id, kind="deterministic")` 拿 provider，`hand_over_session`/`keep_for_next_action` 直接对应 registry 的"正常返回即保留复用"；删除 `warm_sessions`/`warm_lock`/`WARM_SESSION_MAX_IDLE_SECONDS`/`WARM_SESSION_MAX_AGE_SECONDS`（数值原样带进 `EmailAccountConnector` 的默认值，不悄悄改行为）。
- [ ] 跑 `pytest -q tests/test_email_worker.py -k action_executor or warm or hand_over`，全绿后单独提交这一个 Task 的 hunks，更新 `docs/architecture.md` 里写连接那一段（改成描述共享 connector，而不是"warm_sessions 字典"），推送、`python -m app.deploy`，读回健康状态确认真实邮箱动作正常执行。

## Task 3：周期性分类扫描（`scan_account`）迁移

**Files:** `app/email_worker.py`（`build_dependencies` 内 `scan_account`，约 3658 行）。

- [ ] 先写测试：`scan_account` 用假 registry 断言它通过 `registry.acquire(account_id, kind="readonly")` 拿连接，不再直接调用 `source_factory`。
- [ ] 改实现；跑该函数相关的现有扫描测试（`tests/test_email_classifier_scan_model.py` 等如涉及 `scan_account` 的集成路径）确认没有破坏批次/游标行为。
- [ ] 单独提交、推送、部署、读回：确认分类扫描仍按正常节奏产出。

## Task 4：训练观察任务（`ProviderTrainingObservationJob`）迁移

**Files:** `app/email_training_observer.py`、`app/email_worker.py`（`observation_job = ProviderTrainingObservationJob(...)` 的 `source_factory` 构造处）。

- [ ] 先写测试：`run_once` 对同一账号的多个文件夹复用**同一条**连接（现状已经是这样，一个账号一次 `run_once` 内只连一次），但现在要断言这条连接来自 registry，而不是 `run_once` 自己 `source_factory(account)` 独占一整轮——即：如果这一轮跑很久，其间别的子系统（比如动作投递）来抢同一账号的连接，应该排队等，而不是各自另开。
- [ ] 改实现；这是本次事故直接相关的调用点，改完要特别验证：部署后观察 `email_worker_health:component:email-training-observation` 连续几轮都能推进（不只是成功一次），且不再看到同账号两条并发连接。
- [ ] 单独提交、推送、部署、读回，观察至少 10-15 分钟确认没有再复现今天的卡死。

## Task 5：剩余按需调用点迁移

**Files:** `app/email_worker.py`（`resolve` OTP、`_read_historical_provider_state`、`_reread_historical_candidate_message`、`read_current_classification_message`、`run_historical_once`、`load_model_action_repair_message`、`resolve_entries`，共 7 处）。

- [ ] 逐个迁移到 `registry.acquire(...)`，每处改完跑其自身覆盖的测试文件；这 7 处都是低频、按需触发，风险比 Task 3/4 低，可以一次提交里改多处，但仍要在提交信息里逐条列出改了哪几个调用点。
- [ ] 全部迁移完成后，全仓库搜索确认 `source_factory(account)` 只在 registry 内部出现一次（连接真正建立的地方），其余全部改成走 `registry.acquire`。
- [ ] 提交、推送、部署、读回。

## Task 6：收尾文档

**Files:** `docs/architecture.md`、`docs/runtime-mechanism.md`。

- [ ] 确认这两份文档不再有"每个子系统各自开连接"或旧 `warm_sessions` 字典的描述，改成统一描述"每账号一个共享 connector，按优先级排队，超时 600 秒"。
- [ ] 在 `docs/agent-claims.md` 补齐这次改动的认领行，状态标 done。

## 已拍板（不用再问）

排队分 HIGH/LOW 两档、6 个 Task 全做、超时 600 秒——见文件顶部"Derek 2026-09-28 拍板"和上面各调用点的优先级标注。
