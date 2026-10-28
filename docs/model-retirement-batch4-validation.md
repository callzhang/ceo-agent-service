# 模型退休第四批：Feedback 唯一事实归属

状态：7a6b73ee9标准部署、独立生产API／UI读回与本轮备份／临时目录清理完成。
副本验证基线为当时真实已安装af24bc485的Store28.2/Email54，目标Store28.3/Email54；正式发布前实际b3abfe4e的来源声明已再次核对完全相同。

## 字段与模型

`feedback_processing_items` 从14列减为4列：task_id、feedback_key、batch_id、current_round_id。
workbench_task_id、workbench_turn_id、attempt_id、agent_run_id、commit_sha、三项 evidence_json
和 note 由准确指针所指的 Round 持有；resolved_at 只由同一 feedback_key 的来源 Action 持有。
读取沿 id+feedback_key+batch_id 三项精确匹配，禁止选择最近一轮。无当前 Round 时使用已有
中性值，但46条无 Round 的已解决事实继续由来源事件返回，不能因此丢失或推断为未解决。

没有新建字段、容器、备用JSON、FK或约束。原表未声明指针FK，不虚构一项；原来已有的
Task FK、feedback_key UNIQUE、batch索引及Round/Action真实约束保留。仍48表/463对象，
1232→1222列（524普通、698虚拟），精确目标compact schema hash为
`ec77e9b01dc0915a387ed8b01d8d28cd9f150d7addaed9262b3ecb9d2b129a0a`。

创建、claim、关联、证据patch、resolve和reopen不再双写镜像。移除同一Round经两条别名
读取后再互相比对的无效分支；Task状态、来源解决时间、准确指针/批次、Round字段类型、
外部执行关联/commit/evidence回执资格仍按原合同核验。关联/patch在Round写后保留准确
指针/批次/processing条件检查，用SELECT替代没有实际内容的Item自赋值；触发器回归确认
写入时指针改变会使整个事务回滚。授权、Audit、重试、收据规则不变。

## 显式迁移和当前数据 inverse

迁移先验证完整准确28.2/54来源，逐行证明9份镜像与准确Round的值及SQLite类型相等，
或在0指针时全部中性；解决时间与同一来源Action的值/类型相等。缺失、冲突、非中性
或非法指针拒绝，不猜测、不删除异常、不写占位实体。检查时54 Item、8有指针、46无Round
已解决均匹配；这些数量只作该次证据，不硬编码为今后的前置条件。

同一FK-off事务在TEMP转移4个保留列，重建准确表/index，incoming FK不级联；逐值保留
原Task身份、原生类型及全部当前sequence水位。inverse从当前准确Round/来源Action重建
退休字段；不存旧正文/镜像副本。中途失败原子回滚，正反后完整catalog、版本及FK集合核对。
当前constructor只接受当前声明，发布选择器只接收准确28.2前序与当前28.3，不兼容旧结构。

实际生产派生副本49张原生表（含sqlite_sequence）通过：forward后当前公开API执行创建、
claim、关联、patch、解决、重开、下一轮；inverse后新增业务事实/回执仍在；真实af24旧代码
冷启动读回并继续新一轮写入；再次forward和当前代码读写。原始保留事实、类型、身份和
权威Round/Action不变；不输出原文，只保存逐表hash。子进程拒绝网络及CLI，全部证据为
合成替身，没有生产测试任务或真实外发。

## 必要提前的第五批闭包清理

四列正面DDL不再借用Task20旧字段映射。删除已无当前调用的task_entity_schema、
task_entity_inverse_files、task_entity_migration、run_entity_schema、run_adoption_schema、
run_adoption_inverse，及独占验证脚本/工具和8个历史转换专用测试文件。保留真实原生
Task/Run/Adoption正面声明、identity_map/provenance和当前coalescer；不为旧测试另造builder。

微信导入完成/失败与写入回滚、审批approve/return及公开Action身份、Workbench父子和
原生结果读取、Feedback竞争/reopen/来源类型等当前功能测试改用原生夹具。旧映射JSON
作为带日期的历史工件保留，仅退休“必须等于当前映射”的再生成断言，不改写原来源事实。
SPEC发现遗漏的旧脚本动态加载专用测试，已随脚本及gate条目一起退休并复查所有路径。

## 测试和范围

完整Feedback文件208项、写后指针竞争/无Round来源等6项、根代理发布/声明/实际回执
集成39项、Feedback API/iteration/source四文件39项、创建器完整98项、其他原生功能46项、
保留DDL/Adoption/Run/Task/assembly51项、最后release/声明13项、Feedback网页专项19项通过；有重叠，不相加宣称独立case总数。
两个早期测试运行被子代理未经授权Ctrl-C（分别仅运行到131项/4项）不计通过，已纠正
执行方式并保留现场；验收只引用完整结束的相关运行。2GB/10分钟限制没有提高。
默认Ruff及差异检查、独立SPEC和QUALITY代码审查通过；最终完整门禁仍必须标准执行。

## 标准发布、独立生产读回与清理

准确7a6b73ee9486699c51315fd30e08315e329a0c25通过完整标准gate、自然quiet、
备份验证及paired发布；operation deploy-1bce6d7a-8401-4949-b3ca-d32c12494047，
2026-10-28T11:30:41Z成功，从b3abfe4e切到7a6b73ee9。没有skip-quiet、force或
生产测试任务／重发。实际PID39304、health200、frontend tree stamp完全一致。

生产463项完整SQL逐字等于目标ec77…129a0a，FK[]，Item准确4列；46条无Round
但来源已解决事实仍保留，API读取source resolved_at逐值相等。UI实际打开54条反馈
列表，展开评论与处理历史，筛选已处理48条；History点击下一页、勾选已完成，
Attempt16220显示receipt20893/All approved actions verified，Consumer／Audit执行
步骤页可读。所有操作只读，没有点击处理、同步、重开、发送或retry。

History失败18条的完整身份集合与发布前b3完全相等；summary failed26是另一口径，
没有本轮新增失败。Attention0；原未解决反馈批次造成的degraded1单独保留，native
回执覆盖375项/不可用0。真实业务后续任务由原worker处理，不把队列变化当测试任务。

最新daily备份514969600字节，精确inode832638187/mtime1793187077893415481，
在backup目录锁下重验CEOB完成标记、integrity、全schema/FK、本operation及无打开
引用后删除并确认不存在。之前pre-upgrade文件已由验证后的daily正常退休，未误删
其他workflow的新代次。临时rawGit worktree确认clean且已推送合并后正常unregister，
父根7234文件/972661524字节与已验证归属的6615字节编辑脚本一并删除；无打开引用，
路径不存在。保留生产数据库、原生trajectory和提交的简洁证据。这不是数据库容量收益。

第五批Business／Email／Workbench／Runner及剩余映射闭包已分单元完成并推主线，
其最终标准发布与读回另见[第五批验收](model-retirement-batch5-validation.md)。
结构化证据：[第四批JSON](validation/2026-10-28-model-retirement-batch4.json)。
