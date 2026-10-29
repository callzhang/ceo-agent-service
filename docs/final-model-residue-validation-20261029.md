# 2026-10-29 最后一批业务模型残留清理验收

本轮四个实施单元、测试维护、正式发布／读回及自有备份／临时清理均已收口。后续独立发布所需的新备份按其负责人明确要求保留。范围以 [实施计划](superpowers/plans/2026-10-29-final-model-residue.md) 为准，不重复前五批已完成工作。

## 已实施

- 删除无调用的 execution owner 更新入口；冻结来源造数移到测试支持。当前 Task delivery／Feedback 投影使用当前字段，历史投影仅供显式来源工具；Workbench 不再接受退休的 waiting_confirmation 状态。
- Store28.5／Email54 正向到 Store29.1／Email54：退休两个已证明为零的邮件标记、分类输入固定 channel 列，以及已完成输入 JSON 内与标量 owner 完全一致的身份副本。NULL 授权快照保留为缺失，不推断权限。
- 删除退休的 direct_v1 启动盘点、门控和一次性终态改写。现行 audited_v2 的领取、恢复、授权、重试和 provider 回执语义未改。
- 历史分类读取保留真实旧分类值，新增配置写入继续使用当前严格合同。OA History／质量统计从准确 Attempt→原始正文回执→同一 Action 事实引用读取；旧响应格式只由离线迁移解码。缺失 Run／Task／Audit／provider 身份保持 NULL，不造验证证明。

## 数据与发布证据

2026-10-29T11:36:16Z 的最新生产派生副本完成 49 张原生表（含 sequence）的正向、新数据写入、inverse、真实旧代码 e052b814 冷启动读写、再次正向与第二次 inverse。比较类型、身份、水位、引用和既有 FK 集合；1,145 条 OA Attempt／249 组、原始正文 hash 和独立质量谓词全部保持；新增合成结果及替身回执可查询。完整摘要在 [结构化验收](validation/2026-10-29-final-email-field-retirement.json)。未触发真实对外测试发送。

精确提交 1b0c36d44548b321674e7c63643cbc97f292ff9f 标准部署成功：强制 gate 7,720 passed／2 skipped／1 deselected，452.31 秒，396 个文件；自然安静窗口、备份校验和正常安装完成。此前 303f87ff 的 gate 在安装前因七个冻结来源夹具失败而拒绝发布，未创建备份或改动生产；这些夹具已修正，没有恢复旧业务写入器。

随后生产已由负责邮件恢复的任务推进至 773376576b6d9426c1d8ca5b7576bd197ce990e8；该提交包含上述全部模型变更。独立刷新确认 PID869、health200、Store29.1／Email54、463 个准确声明与当前 manifest 完全一致、FK[]。完整 schema 的紧凑 JSON SHA256 为 f5cd6496df716bf9a997c57403310c98ab393500b2de75dc48b8fe9ba10c6a94。前端 tree／stamp 均为 2a410cf4fcfc5aedfad286fad59a94b51d511047。

生产读回覆盖 History 翻页和 OA 筛选、历史 Attempt3538 的 done／同意历史事实及明确缺失 Consumer 的展示（没有伪造当前审批核验回执）、正式任务列表、Workbench、Email 分类与退订历史。未点击发送、重试、标星、已读或其他 provider 操作。迁移前后失败 Task19 条、失败／阻塞 Attempt4,977 条的原身份与值完全相同；Dashboard 两项既有人工事项未被静默改写。

## CI 维护与边界

完整 CI 暴露七个本轮受影响夹具及四个已有 Python／三个前端基线用例。修正落在测试：冻结源使用冻结布局，当前构造器使用当前准确 manifest，旧 State 字节仍对照原始归档 hash，WeChat fake 验证持久 prepared 对象，调度页面断言对齐已授权的现行页面合同。独立 SPEC／QUALITY 通过；相关 Python157项、前端114项、根代理最终标题断言1项、合并后邮件影响22项通过。四个当前 schema／来源合同文件已进入 gate。

合并 tip c99e2eeda631fba1e9a7bab807d3acbb39324a9b 相对当时已安装773仅含测试与验收记录，不需要为测试另起部署。该提交的 [Quality run40875724251](https://github.com/callzhang/ceo-agent-service/actions/runs/40875724251) 已全部成功：51/51 作业通过，无失败。中间发布773的独立正式 gate 记录亦已核实：7,723 passed／2 skipped／1 deselected，542.24秒。

## 最终生产读回与清理边界

本轮约3.19GB的父目录、两个单元临时目录、首轮失败gate副本及其pytest目录、五个编辑脚本均已按精确inode／mtime和无打开引用检查后删除。上一代21.1逆迁移交接JSON已无应用／工具／测试消费者，亦已按确切hash验证后退休；当前29.1副本回退证明不依赖它。路径与删除结果见 [清理证据](validation/2026-10-29-final-model-cleanup.json)。生产库及原生trajectory保留。

最终生产由发布负责人正常推进到 bfabd2be011d2c9d89dbd2a0adaa0b068812530a，包含全部本轮清理及其独立邮件恢复修复。独立读回PID60340／health200、队列pending4／processing0／Attention2、History与Attempt3538历史同意事实可查；后者没有当前核验审批回执，仍明确不伪造Consumer。schema仍为上述463对象与f5cd hash，FK[]，前端build stamp匹配。bfab正式gate为7,772 passed／2 skipped／1 deselected，402文件，476.17秒。CI51/51结论对应本轮c99精确提交；后续独立修复的业务执行结果由其负责人核验，不用本轮CI代替。

本轮1b0的predeploy0fa10c33已缺失；原daily代次inode837613187亦已被标准backup工作流退休。现在同名路径是后续发布的新代次inode837712864、mtime2026-10-29T13:05:42Z。根代理在目录锁内重新验证integrity ok、完成标记、当前schema与FK[]，并确认本轮全部已列临时路径缺失。新代次的hash与归属记录在清理JSON；它由正在处理独立JunHe身份／回执恢复问题的负责人明确要求保留。其后续验证和清理责任已同步给该负责人；我方不误删，也不宣称整个备份目录为空。

本轮没有尚未修复的清单内代码／测试问题。当前真实历史失败和需人工事项如实保留；另一任务的新业务恢复问题不被伪装成本轮通过，也不触发我方重复开发、部署或外部重放。当前main上的收尾文档不需要另一次部署。

本轮没有证明数据库“全局最优”；必要的显式迁移／inverse源合同继续保留，不属于运行时兼容读取。没有把逻辑内容去重或删除测试副本宣称为生产SQLite文件缩小。

## JunHe 后续系统标记与显式恢复（实施验证中）

模型精简本身的发布证据保持不变。后续邮件恢复独立保留：日期修复 ec70a75e0 已标准部署，gate 7,783 passed／2 skipped／1 deselected，402 文件、591.81 秒。独立读回 PID5099、health200、Store29.1／Email54、463 个准确声明、FK[]；前端 stamp395fce9b6。来源 RFC 日期与 DWS ISO 时刻复用原 MOVE 解析，保留原值与 10 秒条件。

日期修复后，仅调用一次原恢复入口，当前计划 v9（7041fd99…）。FLAG_IMPORTANT 127062／失败回执22181 为写后读回不匹配；MARK_READ127063仍 pending。原 MOVE111699 的回执8468／8641／21597及摘要保持不变，没有再次移动。不能把调用成功当外部效果已完成。

真实协议取证区分了三个不同信号：自定义“重要”107、DWS系统重要11、原 FLAG_IMPORTANT 要求的 IMAP Flagged。该账号 PERMANENTFLAGS 声明 Flagged、不声明 $Important；只读交叉核对一封已有 Flagged 的邮件，其 DWS 单邮件会话标签只有1。原生 KQL tag:107 在 JunHe 上精确命中，tag:1 也精确命中；tag:important 全局零命中，不能作为系统效果证明。JunHe 同时已读。保留107及其他已有标签；代码不擅自添加11。

Derek 单独批准并再次要求完成系统标记映射与显式源核验恢复。修复同一提交保持当前真实 owner：系统1写入和逐封读取一致，原始失败保留；使用现有 retry request 释放同一 Action/计划，Worker 再读，满足即 noop。额外邮箱标记要求不能忽略；factory 配置、MOVE 来源和所有失败/执行身份在事务内重验。没有 DDL、Audit、授权或自动重试政策变更。

修改前真实协议替身回归失败（1 failed，错误标记未完成）；同一 Action 显式恢复入口缺失回归失败。首轮64通过；补充共享邮箱合同、配置竞争核对和非自动重试边界后，六个直接相关测试文件207通过（98.86秒），Ruff与差异检查通过，独立SPEC／QUALITY复审通过。新增恢复测试已加入gate；只有最终精确提交、完整门禁及生产原动作回执通过后才能关闭。一次新代码只读生产 dry-run 已返回 source_verified／eligible=true／released=false／source_satisfied=true；它没有释放任务或写邮箱，不能代替最终执行回执。

发布与另一任务串行：本任务完成 JunHe 修复后交精确提交给发布负责人，负责人独占标准 gate/quiet/deploy，并将独立 Attention 变更保持独立提交；本任务不并行部署。发布后只释放127062，读回其追加回执及后继127063，不重发其他业务。备份仍保留到两项验收与发布依赖解除，再锁目录核验准确代次并清理。

邮件原文详情 API 此前仍为502／email_original_unavailable；不能以动作回执或摘要页面冒充原文读取恢复。该限制独立记录，不新建正文副本。
