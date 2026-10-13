# Phase5C 独立存储与迁移验证

本阶段属于已批准的十张业务／运行表加一张迁移表设计。这里只完成
`outbound_postfixes`、`outbound_postfix_receipts`、`native_reply_dispatches`
三张表合入 `actions` 的基础层、正逆迁移和 Store17 开发候选调用／bootstrap／配对发布机制。尚未发布，也不代表完整数据库设计已经完成。

## 精确源与映射

独立源声明位于 `docs/superpowers/specs/2026-10-13-database-redesign-phase5c-postfix-source.json`。
保留全部原有 6 + 4 + 3 个字段；复合身份 `(channel, delivery_key)`
映射为有限类型与 JSON 元组身份，从身份生成键投影，不重复保存键字段。
两个子类型保留立即检查的复合外键及 NO ACTION 更新／删除行为。
`native_reply_dispatches` 不增加原表没有的 channel 或空白键 CHECK。
provider 回执保留原始 TEXT，未增加 JSON 有效性约束。

Store17 开发候选明确选择十二类型声明。原六类型、九类型 schema 声明保持原样。
原九类型的全部持久字段、物理 ID、公开身份、引用及两个原生分配器水位
均由迁移保留。逆迁恢复当前事实，包括迁移后的新增、修改、删除，
不会恢复旧数据快照，也不触发发送或任务回放。

## 已完成的验证边界

- 原始三表声明的独立行为 oracle、十二类型 storage、正迁／逆迁测试已通过。
- 独立审查验证了两种行工厂、原九类型全部当前值和类型、复合键改绑、
  带 NUL 正文、非规范 JSON 键编码、当前分配器水位以及拒绝前不修改数据库。
- 审查发现十二类型部分索引与数字身份查询谓词不匹配。新增查询计划和
  500／5,000 行增长回归复现了整类扫描；补齐相同谓词后恢复索引定位。
  schema DDL 未因该修复改变。独立窄复核覆盖 44 个查询计划，全部使用数字 ID 索引定位，P2 已关闭。
- root 定向组合：191 项通过。此组合与其他定向运行有重叠，不累计计数。

## 可复现副本验证

`scripts/validate_action_postfix_copy.py` 只使用在线备份、独立存储迁移、
schema 逆迁和源版本构造函数；不启动 worker、模型或 provider。

```sh
python -m scripts.validate_action_postfix_copy \
  --source /absolute/path/to/source16.sqlite3 \
  --source-ref <source16-commit>
```

脚本逐表记录值和类型指纹，比较原九类型完整持久行及分配器水位；
迁移后新增 parent、dispatch、receipt，修改正文、改绑两个子类型、
修改无引用父键并删除旧 parent，逆迁后用源代码的新进程打开，
再正迁检查当前事实。保留原有外键诊断，不把已有 finding 描述为零错误。

当前执行的来源是 Phase5B 候选 Store16 副本，未核实为已部署的生产来源。
观察到物理表 129 → 126，124 张未受影响表的值和类型保持一致。
proof 明确标注 `actual_source_deployment_verified=false`。
这属于下一阶段的预验证，不能代替正式生产副本验证和发布失败回退演练。

## 发布前仍需完成

Phase5B 发布后重新核对实际来源、marker 和当前代码；转换全部发送准备、
native claim、receipt、echo、meeting 恢复和 WeChat bootstrap 调用；接入
fresh layout、manifest、版本及配对 release；用最新生产副本完成正式迁移、
当前事实逆迁、实际旧代码打开、再次正迁和发布失败演练。随后执行受影响
业务流程、真实 CLI 加替身发送、独立审查及完整 CI，再通过正常 deploy 上线。
上线后读回进程、健康、队列、Attention、History 和受影响页面。

## 已发布来源重新核对

第五期B正式部署到ebaf0d96后，当前生产Store16／Email51的九类型actions与postfix三表准确声明通过capture_action_postfix_schema只读校验；水位560／115，postfix行数1079／613／67。简明schema-only evidence见phase5c_published_source.v1.json。C worktree已合并已发布B及其历史fixture修正，合并后268项定向通过，包含C独立迁移、既有action回归、D source oracle及真实source-chain回归；选集有重叠，不与既往总数相加。允许继续Store17接入，仍不表示目标运行、正式生产副本或部署验收完成。

## 实际构造函数与生产副本验证（开发候选）

fresh Store17 与现有 prepare／claim／receipt／echo API 定向回归 RED→GREEN；
包括重复调用保留首次值、真实第二次构造及 meeting 恢复。相关调用和业务检查
136 项通过，其他 foundation／migration 组合 93 项通过，组合有重叠不累计。
Ruff 和 diff 检查通过。

phase5c_constructor_copy.v1.json 记录当前生产在线副本，经正式 factory 和新进程
Store17 构造函数迁移 129→126；123 张其他表（不计本次应改变的版本记录表）
值及类型不变。替身新增 parent、dispatch、receipt 后当前事实逆迁与重复逆迁通过；
原 marker 值和时间完整恢复，实际捕获来源 Store16 db0c2b73 新进程能够读取这些新增事实，
再正迁与 integrity_check 通过；保留既有 FK finding。真实发送为零。
私有副本及临时代码在验证后已清理，仅保存摘要证据。

正式构造函数证明已重新绑定生产 db0c2b73；原九类型完整持久值／类型和两个水位也经正逆正比较。
后续生产代码继续前进到 d7397203，Store 来源仍为16；已安全合并新 main，
不将此前捕获副本冒充更晚快照。独立审查与 CLI 替身批准验收通过；
完整 CI 和正常部署仍需完成。完整十表目标尚未完成。

当前源存储独立演练见 phase5c_current_source_storage_copy.v1.json：同一 Store16
在线副本原九类型完整持久值与类型、两个水位以及124张其他表不变；
迁移后改正文、子记录改绑、无引用父键改名、删除均经当前事实逆迁／正迁检验。
此独立脚本不发布 marker，证据保留这一范围，不代替实际构造函数证明。


## Store17 真实 CLI 与替身发送验收（2026-10-13）

证据见 `evals/database_redesign/phase5c_native_copy.v1.json`。原生产在线快照绑定
`db0c2b7331deb80418b959ad0f556f96a9ef96d2`、Store16／Email51，先由真实
AutoReplyStore 构造函数准备 Store17 基线，再原样运行现有
`validate_native_copy_lifecycle.py --case approve`。原始 Store16 快照和 Store17
基线分别保存 SHA256；本项证明 Store17 的 native 生命周期，正式迁移与逆迁证明
仍由构造函数副本证据承担。临时目录名称遵守现有脚本的 `ceo-native-copy.` 前缀断言，
未修改运行代码、脚本或 oracle 来容纳来源版本差异。

真实 Consumer 和 Audit 各执行一次，CLI 均退出 0、未超时，持久状态均为 completed。
测试任务 404585 最终 done；脚本的三条输入与合计 100 校验通过。内存 provider 调用
1 次、验证调用 1 次，external action result、sent reply、business attempt 各 1 条。
再次打开显示 executed，没有新增模型或 provider 调用。批准候选、review、execution、
attempt、external receipt 的实际物理关联与 digest 均通过只读核对。

120 张原有应用表完整值和 SQLite/Python 类型保持一致：117 张严格比较 rowid，
3 张既有 WITHOUT ROWID 表按声明主键比较；原有 1 条 FK finding 原样保留。
临时独立检查器起初把所有表当作有 rowid，按上述真实表身份修正后只读复查通过，
未变更运行代码或现有验收脚本。两份来源文件哈希不变，生产中测试 task、两条
native session 和替身 receipt 标记均为 0。

本案例在 actions 中新增 outbound_postfixes parent 1 条和 outbound_postfix_receipts
1 条，复合父键一致，provider_result_json 保持原始 TEXT 且与替身返回完全一致；
final_body 与批准候选的发送内容逐值相等。现有案例选择 send_group_message，
所以 native_reply_dispatches 新增 0 条，**未覆盖 native reply claim 分支**；未人为补造
dispatch。真实 DingTalk 发送为 0，也未执行部署。

运行时 332 个 app 文件总 SHA256 为
`8aca0de65e927d703d8271154d81c149899e9fbfe12c2d66662447164fd4c89f`，
验收脚本 SHA256 为
`f4123fc7ce7d8f07510a64bed3bdcfd294c2520da21e51f1018648174fa19e35`。
验收起点 HEAD 为 40dfa5b9，结束绑定 758c5f7f，期间 app 与脚本未改变。
最终只读看到生产代码已前进到 d7397203，Store 仍为16；原在线快照的代码绑定仍是
db0c2b73，不将这份副本表述为 d7397203 的新快照。一次 2.978 秒慢 SQLite context
诊断未导致验收失败。

证据仅保留 native session ID、offset、状态和摘要哈希，原生轨迹仍在本机原有
Codex home；未复制 auth、配置或 trajectory。核对证据写入后，已删除本次唯一拥有的
`/private/tmp/ceo-native-copy.phase5c-jTOQja`，并确认路径不存在；其他临时目录及
原生会话均未清理。完整 CI、最终主线整合与正常部署由根任务另行完成。

最新 main d7397203 已合并到 d8c2afcd，89项 OA／postfix release／caller／native recovery 定向检查通过。
native验收绑定较早候选的完整app哈希；新main只改变独立OA函数。下列AST和模块hash
核对将其与当前候选关联，不宣称对无关OA改动重跑同一native案例。
