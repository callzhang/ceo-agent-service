# Phase5C 独立存储与迁移验证

本阶段属于已批准的十张业务／运行表加一张迁移表设计。这里只完成
`outbound_postfixes`、`outbound_postfix_receipts`、`native_reply_dispatches`
三张表合入 `actions` 的独立基础层及正逆迁移，尚未激活 Store、调用方、
bootstrap 或发布机制，也不代表完整数据库设计已经完成。

## 精确源与映射

独立源声明位于 `docs/superpowers/specs/2026-10-13-database-redesign-phase5c-postfix-source.json`。
保留全部原有 6 + 4 + 3 个字段；复合身份 `(channel, delivery_key)`
映射为有限类型与 JSON 元组身份，从身份生成键投影，不重复保存键字段。
两个子类型保留立即检查的复合外键及 NO ACTION 更新／删除行为。
`native_reply_dispatches` 不增加原表没有的 channel 或空白键 CHECK。
provider 回执保留原始 TEXT，未增加 JSON 有效性约束。

十二类型声明仅供显式 opt-in 使用。原六类型、九类型 schema 声明保持原样。
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
