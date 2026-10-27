# 无调用业务入口与并行读取清退 — 2026-10-26

本轮落实已批准清单，以业务模型与调用链简化为目标，不宣称数据库容量收益。
应用基线为013af535e；同轮通知开关发布后，生产重新绑定为 Store26.1／Email54。
代码和定向验收已完成并推送，发布候选为cc90d2e16（包含实现6e7572e3e）。
标准发布gate已通过，但30分钟安静窗口超时，生产没有切换；上线后读回尚未执行。

## 变更与保留边界

| 单元 | 已落实变更 | 保留的业务合同 |
| --- | --- | --- |
| Store | 删除13个无调用或仅测试使用的查询；移除独占的旧状态归一化辅助函数 | 历史详情使用当前批量读取器；回复去重使用has_sent_reply_for_trigger；History测试走正式列表／计数，纯落库检查只使用测试目录的无参数typed snapshot |
| 历史详情 | 删除独立renderer、三个独占辅助函数及固定score／match_reasons | HTML／JSON仍共用原loader，原URL、Todo／跟进锚点、来源、事实、Update、完成证据、钉钉回执与Owner展示保持不变 |
| Run | 删除get_professional_run／list_okr_review_runs | 测试直接检查Run、provenance、projection与既有native resolver；保留身份、NULL事实、父关系、顺序、精确range和原生不可用边界 |
| 其他入口 | 删除record_viewed与三个无调用的旧构造／变体入口 | Attention资格、重算和解决合同不变；显式历史转换使用的声明工厂保留 |
| Email／Reviewed | persist_email_plan成为Email计划唯一写入入口；删除旧origin分派、update_plan及无调用conflict参数 | 计划ID、版本和幂等冲突规则不变；旧Candidate／Review／Selection／Supplement造数只在冻结来源测试夹具中 |

没有新增旧结构兼容、fallback、自动补建或备用JSON。显式迁移／逆迁工具、历史事实、
provenance与回执保留。BusinessProject.anchor_type未改动；审查、授权、重试、
发送准备、执行身份和provider回执语义不变。

## 定向验证与审查

- Store直接受影响节点修改前87pass、初次修改后87pass；Quality发现两个退休筛选
  测试只是在测试Python列表逻辑，因此整项删除。剩余三项受改名／事实断言影响的
  用例再跑3pass，不把已删除的旧筛选规则当成当前覆盖。
- 历史详情、Attention与构造入口基线84pass；实施后的重叠分组为37／32／6pass。
  最终URL、锚点、Todo标题与完成证据检查再跑1pass。
- 原生Run受影响节点展开为28例，前后均通过；六个相关文件52pass；最终真实
  envelope解析单项通过。未在测试中重新创建旧的通用聚合Run DTO。
- Email／Reviewed基线与修改后各29pass，新增公开EmailStore冲突测试2pass，
  冻结来源迁移与现行Email DAO最终18pass；移除空SQL引用脚手架后再跑4pass。
- 根代理追加历史读取边界、旧Todo隔离与文档检查17pass。变更文件Ruff与差异检查通过。全仓另有四项既存lint发现，不在本轮修改范围。
- 四个单元均经过独立SPEC后QUALITY审查。没有本地全量测试，没有提高2GB／5分钟限制，
  没有真实模型／provider测试发送或生产测试任务。

这些分组存在重叠，不能相加成一个完整套件的通过数。

## Schema与生产验收

应用声明前后476个对象的SHA256均为
`8d87a56c15429d971564a59de6b752d655500a46b407b0fef435180a1a0c882f`。
部署前生产与该声明一致，外键异常集合为空。本轮没有数据库迁移。
部署前还对历史Project975、Todo3327、Update5468、钉钉关联875、跟进2477条记录
保存了仅含数量与逐值校验摘要的基线，供发布后比较，不保存正文副本。

## 发布阻塞与清理结果

应用共删除26个函数／方法定义和2个固定DTO字段，净减少499行应用代码。
正常合并后追加编排／session测试18pass和不确定发送跟进1pass。四个单元SPEC、
QUALITY及最终集成审查均通过。冻结Candidate来源的362项catalog、分配序列及
7行值／类型一致；仅归一两项测试执行时钟，未放宽正文或业务事实比较。

首次自己的标准发布gate在300秒处终止，未记录断言失败；结束节点单跑0.79秒通过。
之后与通知页面任务合并发布，cc90d2e16的强制gate在串行重试中通过，但仍有3项在途
owner记录，安静窗口等待30分钟后拒绝切换。真实Reply Task417568／Consumer Run399751
持续运行并更新心跳，未为发布取消、改状态或重放。没有绕过gate或安静窗口。

23:59 UTC只读确认生产仍是013af535ee36f9f951e69293c3689d9b73f2be1d／PID33729。
healthz、Status、Attention、History均HTTP200；队列pending6、processing2、failed24，
运行时Attention0。这是未切换生产的状态核对，不是新版上线验收。
系统健康此时有2项发现，其中在途Reply已超过恢复租约；本轮代码尚未加载，
不能将其称作本轮上线回归。历史五类记录数量与逐值摘要均与基线一致，476项schema
仍逐值相同、FK异常集合仍为空。没有业务数据迁移或删除。

本轮三个明确归属的临时目录均在lsof确认无打开文件后删除，精确路径已验证不存在。
清理的普通文件逻辑字节合计359909512（约343.2MiB）；嵌套符号链接未跟随，
没有删除源代码或原生trajectory。本轮发布在备份步骤前退出，新增部署备份为0；
其他任务产物和先前工作流备份未动。这不是数据库容量缩减。

尚未完成：等待真实在途工作结束后重新执行标准exact-target发布，再核对新版本／PID、
健康、队列、Attention、History、Project HTML／JSON、Attempt／Run及历史回执，
验证并清理该次发布新产生的备份。不能把原生产页面或本地测试冒充上线后页面验证。

精确删除清单、分组测试、发布回执边界及清理路径见
[验收记录](validation/2026-10-26-unused-business-path-retirement.json)。
