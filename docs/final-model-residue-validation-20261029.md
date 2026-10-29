# 2026-10-29 最后一批业务模型残留清理验收

本轮四个实施单元已通过独立 SPEC／QUALITY 审查并上线；测试维护和本轮临时清理已收口；共享备份等待正在使用它的后续发布完成。范围以 [实施计划](superpowers/plans/2026-10-29-final-model-residue.md) 为准，不重复前五批已完成工作。

## 已实施

- 删除无调用的 execution owner 更新入口；冻结来源造数移到测试支持。当前 Task delivery／Feedback 投影使用当前字段，历史投影仅供显式来源工具；Workbench 不再接受退休的 waiting_confirmation 状态。
- Store28.5／Email54 正向到 Store29.1／Email54：退休两个已证明为零的邮件标记、分类输入固定 channel 列，以及已完成输入 JSON 内与标量 owner 完全一致的身份副本。NULL 授权快照保留为缺失，不推断权限。
- 删除退休的 direct_v1 启动盘点、门控和一次性终态改写。现行 audited_v2 的领取、恢复、授权、重试和 provider 回执语义未改。
- 历史分类读取保留真实旧分类值，新增配置写入继续使用当前严格合同。OA History／质量统计从准确 Attempt→原始正文回执→同一 Action 事实引用读取；旧响应格式只由离线迁移解码。缺失 Run／Task／Audit／provider 身份保持 NULL，不造验证证明。

## 数据与发布证据

2026-10-29T11:36:16Z 的最新生产派生副本完成 49 张原生表（含 sequence）的正向、新数据写入、inverse、真实旧代码 e052b814 冷启动读写、再次正向与第二次 inverse。比较类型、身份、水位、引用和既有 FK 集合；1,145 条 OA Attempt／249 组、原始正文 hash 和独立质量谓词全部保持；新增合成结果及替身回执可查询。完整摘要在 [结构化验收](validation/2026-10-29-final-email-field-retirement.json)。未触发真实对外测试发送。

精确提交 1b0c36d44548b321674e7c63643cbc97f292ff9f 标准部署成功：强制 gate 7,720 passed／2 skipped／1 deselected，452.31 秒，396 个文件；自然安静窗口、备份校验和正常安装完成。此前 303f87ff 的 gate 在安装前因七个冻结来源夹具失败而拒绝发布，未创建备份或改动生产；这些夹具已修正，没有恢复旧业务写入器。

随后生产已由负责邮件恢复的任务推进至 773376576b6d9426c1d8ca5b7576bd197ce990e8；该提交包含上述全部模型变更。独立刷新确认 PID869、health200、Store29.1／Email54、463 个准确声明与当前 manifest 完全一致、FK[]。完整 schema 的紧凑 JSON SHA256 为 f5cd6496df716bf9a997c57403310c98ab393500b2de75dc48b8fe9ba10c6a94。前端 tree／stamp 均为 2a410cf4fcfc5aedfad286fad59a94b51d511047。

生产读回覆盖 History 翻页和 OA 筛选、历史 Attempt3538 的 done／审批决定及明确缺失 Consumer 的展示、正式任务列表、Workbench、Email 分类与退订历史。未点击发送、重试、标星、已读或其他 provider 操作。迁移前后失败 Task19 条、失败／阻塞 Attempt4,977 条的原身份与值完全相同；Dashboard 两项既有人工事项未被静默改写。

## CI 维护与边界

完整 CI 暴露七个本轮受影响夹具及四个已有 Python／三个前端基线用例。修正落在测试：冻结源使用冻结布局，当前构造器使用当前准确 manifest，旧 State 字节仍对照原始归档 hash，WeChat fake 验证持久 prepared 对象，调度页面断言对齐已授权的现行页面合同。独立 SPEC／QUALITY 通过；相关 Python157项、前端114项、根代理最终标题断言1项、合并后邮件影响22项通过。四个当前 schema／来源合同文件已进入 gate。

合并 tip c99e2eeda631fba1e9a7bab807d3acbb39324a9b 相对已安装773仅含测试与验收记录，不需要为测试另起部署。该提交的 [Quality run40875724251](https://github.com/callzhang/ceo-agent-service/actions/runs/40875724251) 已全部成功：51/51 作业通过，无失败。当前生产773的独立正式 gate 记录亦已核实：7,723 passed／2 skipped／1 deselected，542.24秒。

## 清理与剩余边界

本轮约3.19GB的父目录、两个单元临时目录、首轮失败gate副本及其pytest目录、五个编辑脚本均已按精确inode／mtime和无打开引用检查后删除。上一代21.1逆迁移交接JSON已无应用／工具／测试消费者，亦已按确切hash验证后退休；当前29.1副本回退证明不依赖它。路径与删除结果见 [清理证据](validation/2026-10-29-final-model-cleanup.json)。生产库及原生trajectory保留。

本轮1b0的predeploy备份已被标准backup工作流退休。当前daily副本inode837613187、mtime2026-10-29T12:29:57Z已经校验完整性、完成标记、当前schema和FK[]。发布负责人明确要求继续保留，供其正在进行的另一项已授权JunHe修复部署／回退使用；我方不提前删除共享恢复点。待该负责人完成部署和最终读回后再验证精确代次并清理，不重复索取Derek已给出的删除授权。

截至本记录，代码修复、当前数据回退、相关测试、正式部署、生产读回及本轮临时文件清理均已通过；仅共享备份的最后删除等待当前使用者释放。原有人工事项和真实历史失败仍如实保留，不属于静默改写为成功的对象。

本轮没有证明数据库“全局最优”；必要的显式迁移／inverse源合同继续保留，不属于运行时兼容读取。没有把逻辑内容去重或删除测试副本宣称为生产SQLite文件缩小。
