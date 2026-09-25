# 运行线路：除三条内置外，都是同一种线路、都能改名

状态：已实施（2026-09-24，Derek 已批准；两处决定见「需要 Derek 决定」）。实施与设计的差异见文末「实施记录」。

Derek 2026-09-24：「除了内置的 cli 不能改名外，其他都应该是一样的啊，而且可以改名」「Friday runtime 也不可改名」。

## 结论先说

固定名字、不能改名的只有三条：**Codex OAuth（`codex_oauth`）、Claude OAuth（`claude_oauth`）、Friday Runtime（`friday_runtime`）**。
其余线路（现在的内置 Codex API、Claude API，以及添加的 qwen_gpu4 等）统一成同一种「添加的线路」：
名字自己起、能改名、模型自由填。改名由服务端完成，所有按名字引用它的地方一起改过去。

## 现在的问题

1. **内置 Codex API / Claude API 和添加的线路是两套东西。** 内置的用固定配置项（`CEO_CODEX_API_*`、`CEO_CLAUDE_API_*`），
   模型只能从清单里选、不能改名；添加的用 `CEO_RUNTIME_<名字>_*`，模型自由填、页面上能改名。两者跑起来完全一样。
2. **添加的线路「改名」只在页面上换了配置项的名字**（`SettingsPage.tsx` 的 `renameRoute`），服务端把它当成删旧建新。
   按名字引用线路的地方不跟着改：
   - 定时任务的首选线路 `scheduled_tasks.runtime_id`：改名后变成「线路未配置」，任务被跳过；
   - 会话续接 `conversation_runtime_sessions`（按「会话 + 线路名」）：改名后续不上原会话；
   - 线路暂停 `runtime_route_pauses`、能力探测缓存 `agent-runtime-capability:<名字>`：丢失（后者下次探测会自愈）。
3. **好几处代码按名字认线路**，名字一改就失效：
   - 邮件分类写死用名为 `codex_api` 的线路和它的密钥（`app/email_worker.py`、`app/email_training_labeler.py`）；
   - 「临时失败后换新会话重试」只对名为 `codex_api`、`claude_api` 的线路生效（`app/agent_runtime_router.py`、
     `app/agent_turn_runner.py`），添加的 API 线路享受不到；
   - 安装向导只检查 `codex_oauth` 和 `codex_api` 两条（`app/setup_wizard.py`）；
   - 命令行诊断对 `codex_api` 特判（`app/cli.py`）。

## 改法

### 1. 线路分两类

| 类 | 线路 | 名字 | 配置项 |
|---|---|---|---|
| 内置 | `codex_oauth`、`claude_oauth`、`friday_runtime` | 固定，不能改 | 保持现状 |
| 添加 | 其余全部 | 自己起，可改名 | `CEO_RUNTIME_<名字>_{KIND,MODEL,API_KEY,BASE_URL}` |

添加线路的种类不变：Codex API、Claude API，以及本机登录的 Codex / Claude（给同一个登录再配一个模型）。
内置的 `codex_api`、`claude_api` 两个名字不再保留，配置项 `CEO_CODEX_API_*`、`CEO_CLAUDE_API_*` 去掉。

### 2. 已有配置的迁移（一次性，服务启动时）

现有的内置 Codex API、Claude API 配置原样搬成添加的线路，**名字先保持 `codex_api`、`claude_api`**，
这样定时任务、会话、顺序这些引用都不用动；之后 Derek 在页面上改名（比如改成 `kksj`），走下面的改名流程。
迁移只在旧配置项还存在时执行，搬完即删旧项；写 `.env` 前先备份。

### 3. 服务端改名

新增改名操作：一次改名在服务端完成，并把引用一起带过去：

- 线路顺序 `CEO_AGENT_RUNTIME_ROUTES`、隐藏列表 `CEO_AGENT_RUNTIME_HIDDEN_ROUTES`；
- 配置项 `CEO_RUNTIME_<旧名>_*` → `CEO_RUNTIME_<新名>_*`；
- 定时任务 `scheduled_tasks.runtime_id`；
- 会话续接 `conversation_runtime_sessions.route_name`；
- 线路暂停 `runtime_route_pauses.route_name`；能力缓存键。

历史记录（`agent_runtime_attempts.route_name`）不改：它记的是当时用的名字。
新名字要合规（小写字母开头）、不与现有线路或三个内置名重复。改名在运行中的轮次结束后才完全生效（需重启，和其他设置保存一样）。
页面上的改名改为调用这个操作，而不是自己改配置项。

### 4. 去掉按名字认线路的代码

- 邮件分类：按系统的模型路由走（Derek 2026-09-24）。去掉「直接 HTTP 调 `codex_api` 线路的 API」这条主路径，只保留现在作为兜底的系统路由（`EmailClassifierRoutedBackend`，与其他 Agent 轮次同一线路顺序和 fallback）。代价：每封邮件变成一次完整的命令行 Agent 轮次，比一次 HTTP 调用慢、消耗更多额度。
- 「临时失败换新会话重试」：按线路种类判断（凭 API 密钥访问的线路都适用），不按名字。
- 安装向导、命令行诊断：列出全部已配置线路，不只两条。
- 旧的服务端渲染的 Agent Runtime 设置页（React 设置页上线后已不用，但仍可访问）：删除，只保留 React 设置页和它的接口。

## 不改的

- 三条内置线路的行为和配置方式。
- 失败切换顺序、同线路满载重试等统一 fallback 规则。
- 历史记录里的线路名。

## 需要 Derek 决定

1. ~~邮件分类用哪条线路~~：按系统模型路由走（已定）。
2. ~~旧的服务端渲染设置页直接删掉，可以吗~~：删掉（已定）。保存逻辑移进 JSON 接口（`app/web_api/agent_runtime_settings.py`）。

## 验证

1. 单元测试：迁移（旧配置搬成添加的线路、删旧项、只执行一次）；改名带走顺序、配置项、定时任务、会话、暂停；
   改成已存在或内置名被拒；按种类判断的换会话重试；邮件分类只走系统路由。
2. 前端测试：内置三条没有改名框，其余都有；改名调用服务端操作。
3. 上线后：设置页里把 `codex_api` 改名为 `kksj`，核对定时任务首选线路、线路顺序跟着变，下一轮任务正常走这条线路。

## 实施记录

- 迁移放在 supervisor 启动子进程之前，以独立进程运行 `python -m app.agent_runtime_migration`；
  备份为 `.env.runtime-routes-migration.bak`（固定文件名，只留最新一份）。
- 不在 `CEO_AGENT_RUNTIME_ROUTES` 里的 `codex_api` / `claude_api` 不迁移：添加的线路只在列表里时才存在，
  其旧键只留在备份里；隐藏列表去掉这两个名字（隐藏列表只记内置卡片）。
- 改名额外带走 `scheduled_task_runs` 里 `pending` / `dispatched` 运行快照的 `runtime_id`：执行前会按快照
  里的线路名重新解析，不改会在重启后找不到线路。
- 离线训练标注命令 `app/email_training_labeler.py` 没有持久化的分类任务，路由器不接受，所以没有改成走路由，
  而是用 `--route <名字>` 指定一条带地址和 Key 的添加线路直连调用，代码里不再写死 `codex_api`。
- 「Consumer 连续两次结果不可用时强制换新会话」原来对名为 `codex_api` 的线路例外，现在按种类对所有
  凭 Key 的 Codex CLI 线路例外（包括 `qwen_gpu4`），行为对 `codex_api` 不变。

