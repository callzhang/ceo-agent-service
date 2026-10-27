# 无调用业务入口与并行读取清退 — 2026-10-26

本轮落实已批准清单，以业务模型与调用链简化为目标，不宣称数据库容量收益。
应用基线为013af535e；同轮通知开关发布后，生产重新绑定为 Store26.1／Email54。
代码、定向验收、标准发布、生产API／页面读回及本轮产物清理均已完成。
当前运行版本为0f8c47eaaca8aa90dc60d9a71c28691c624cc6ee（包含实现6e7572e3e）。
此前的排空超时已在Derek明确授权本次中断恢复后，通过现有skip-quiet-wait选项解除；
没有修改默认等待策略或绕过强制gate。

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

## 先前发布阻塞与首轮清理（历史，现已解除）

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

当时尚未完成标准发布、上线后读回与该次备份清理；这些项随后按下节的实际证据完成。
原生产页面或本地测试未被用作上线后验收替代。

精确删除清单、分组测试、发布回执边界及清理路径见
[验收记录](validation/2026-10-26-unused-business-path-retirement.json)。

## 最终上线与生产验收 — 2026-10-27 UTC（本地10月26日）

Derek明确授权“本次跳过等待，允许中断后恢复”后，执行现有标准命令：

```sh
python -m app.deploy --target-commit 0f8c47eaaca8aa90dc60d9a71c28691c624cc6ee --skip-quiet-wait
```

目标提交的强制gate通过，2GB／300秒限制未改，随后正常停服、备份、切换、
tsc／Vite构建、重启及健康检查通过。回执deploy-4a90af6a-759b-420d-bec9-5af33c9d3502
确认安装目标0f8c47eaa，生产新PID83958。七个受影响应用文件与已验收代码逐字一致，
退休函数定义均不存在，Email计划写入签名为persist_email_plan(db,values,*,ignore=False)。

生产只读核对完成：healthz／Status／Attention／History均HTTP200；476项schema与当前
声明逐值相同、前后摘要相同，FK异常仍为空；五类历史记录的数量及逐值摘要与原基线
完全一致。00:16 UTC汇总pending6、processing3、failed24、运行时Attention0；failed未增加。
系统健康仅剩一项既有未解决反馈，原在途Reply超租约提示已消失。此处不宣称系统无遗留反馈。

重启恢复已实际发生：原Consumer Run399751和RuntimeAttempt399516记录中断失败，
新Consumer399754／RuntimeAttempt399519已运行，保留generation scheduled-run-410282，
并以session_mode=resume续接相同Codex session。证明恢复执行，不等同于周报业务结果完成；
该报告当时仍在运行，其最终结果由原任务流程验收。

浏览器实际操作覆盖：当前Tasks需关注／正式列表和Task862详情；历史Project1的owner、
事实、来源、Todo及跟进回执；History钉钉类型筛选和第二页（6381条、21—40）；
Task417569→Attempt16137→Consumer／Audit详情；36条原生轨迹读取与展开；
Action125048／Receipt20527已有provider回执；Project140#todo-3206的钉钉关联634及同步回执、
历史Update展开；Attention12的来源及3个关联任务。Project JSON、sent-todos、History分页与
Attempt API同步读回。未点击发送、撤回、重跑或保存反馈，未创建生产测试任务，验收标签已关闭。

部署备份在服务启动后由同工作流00:11:37创建的日备份替换；该最新备份再次完整
integrity_check=ok、完成标记CEOB核验通过。验收后持目录锁并核对mtime／大小未变，
按已获授权删除该514969600字节备份。原部署备份已由工作流轮换删除，gate clone与
pytest-4554在验收时已不存在；本轮ceo-retirement-release.64Zlft临时目录也已删除并确认路径消失。
生产数据库、原生trajectory及提交的简洁证据保留。本轮没有DDL或数据迁移，不宣称数据库容量缩减。
