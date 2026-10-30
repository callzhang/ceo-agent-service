# 邮件原文详情与 History 主题搜索验收（2026-10-29）

## 已证实根因与当前实现

修复前，生产原文详情 `/api/console/email/classifications/949082524487675729` 返回 502 / `email_original_unavailable`。异常在 `locate_message`，尚未读取正文。当前来源指向法务；原始邮件确实位于 UID 60。现有 Message-ID 合同规范化域名为小写，但该服务器的 HEADER Message-ID 检索只在原始大小写的完整值下命中，规范值、本地部分及子串检索均为零。只读获取原始头信息确认是同一完整身份，不是邮件被删除。

修复替换现行原文重新定位算法：UID SEARCH ALL，按最多 50 UID 一批只读 FETCH Message-ID 头字段；复用既有规范身份全等、严格 UID literal 解析、唯一性和 UIDVALIDITY 合同后读取正文。没有 DWS/旧结构回退、标题猜测、正文缓存、读取自修补或 DDL。正常已知 UID 的直接读取不变。代价是重新定位需要遍历目标文件夹的头信息，大文件夹耗时尚未实测。

本轮诊断时，History 生产主题查询 `Quotation for On-Premise` 命中 44 条；加 failed 为零，当前全部 18 条失败也没有该主题。JunHe MOVE 111699、FLAG 127062、READ 127063 都已 done；保留的失败尝试不重新决定 Action 当前状态。本轮未证实当前搜索缺陷，不修改搜索 SQL。新增当前模型 API 回归覆盖主题大小写、failed/全状态、无命中、计数和翻页。

## 验证

- 原文协议回归在修复前 2 failed / 4 passed；修复后相关测试通过。
- 直接相关测试共 201 项通过；包含只读 IMAP、完整原文、来源版本、classification reader/API、History 搜索与分页。测试中的 provider 均为替身。两次 History 测试初始失败是新断言误用 API 的 id 表示，已按实际当前契约修正，没有改变产品期望。
- 独立 SPEC / QUALITY 审查通过；Ruff、差异检查通过。
- 修复代码经生产来源只读验证：法务 UID 60、稳定身份准确、完整正文 6417 UTF-8 bytes；摘要见 JSON。没有保存原文、修改邮箱或重放 JunHe 动作。
- 发布前 Store / Email schema 共 463 项，SHA-256 `f5cd6496df716bf9a997c57403310c98ab393500b2de75dc48b8fe9ba10c6a94`，FK 异常为空；已安装版本 `616e237ec28ad4a913ee09d2f5e3e3ef1193b727`。这是冻结时点的证据，不是滚动当前状态。

## 发布阻塞的独立性能修复

8f341ef85 的完整标准 gate 两次累计 600 秒被固定预算停止，未观察到断言失败；另一次随交互会话中断，未生成通过记录。生产均未切换。不提高预算、不减少测试或调整门禁政策。

对两个耗时文件做只计时、不改语义的诊断：181 项全部通过，270.55 秒。其中 Meeting 测试累计约 219 秒。精确 SQL 记录定位到两个领取入口的无目标 `ON CONFLICT DO UPDATE`；冻结当前 schema 的合成 EXPLAIN 探针证明它生成 14,154,431 条指令，准备约 5.26 秒。限定到现有 public identity 唯一键和原 partial predicate 后为 422,230 条，准备约 0.19 秒。

两个入口收敛到一个当前 claim 写入器，复用既有字段构造器和 `EMAIL_NUMERIC_SCOPE`；只更新原 job 的 token。没有新身份、DDL、兼容读取或回退；领取条件、事务、CAS、租约、token 生成、外部执行、Audit、授权和重试规则不变。独立审查穷举当前唯一索引，确认对有效 meeting claim 冲突语义相同。

新增结构性 SQL 预算回归在旧代码上以 `14,154,431 < 1,000,000` 断言失败，修复后通过；流式读取 EXPLAIN，避免把完整字节码物化到内存。测试覆盖新 claim、两种领取入口、同一物理 owner 更新 token、其他 job 不变、schema 和 FK 不变。Meeting 全文件及新回归共 157 项通过，24.82 秒；SPEC / QUALITY 复核通过。

## 发布与收尾（已完成）

- 已安装精确提交 `1ccf8fe8b391b97c3dac0df2b769aa56d910671d`，包含原文修复 `8f341ef85` 和独立 SQL 性能修复。完整固定门禁 406 个文件：**7817 passed、2 skipped、1 deselected，345.91 秒**；10 分钟 / 2 GB、全部测试、标准安静窗口、备份和失败回滚均保留。
- 正式部署账本 `deploy-ad9b43ec-0cff-4c5e-8301-d55e6e02fa01` 为 succeeded；2026-10-29 17:27 PDT 完成安装。新 PID 2981、health 200；前端 stamp `395fce9b6e3ddd924881471c36fbb654ac6ef7dd` 与安装提交的 frontend tree 一致。
- 原文详情 API 200，6331 字符 / 6417 UTF-8 bytes，SHA-256 与部署前直接只读原文的摘要一致；生产页面详情区域已渲染，不再显示来源不可用。UI 自动 mark-read 在本轮独立标签中阻断；没有触发真实发送、补发或生产测试任务，标签已关闭。
- History API 主题查询总数 44，两个 10 条分页身份无重复；页面实际翻到 21–40 / 44。failed 交集为零，与三个已完成动作及保留的失败历史含义一致。
- schema 精确清单仍为 463 项，SHA-256 `f5cd6496df716bf9a997c57403310c98ab393500b2de75dc48b8fe9ba10c6a94`，FK 异常集合仍为空，没有 DDL 或迁移。JunHe MOVE 111699、FLAG 127062、READ 127063 仍 done，尝试次数仍 3 / 2 / 1，没有重放。
- 读回时 pending 3、processing 0、failed 26、Attention 2，与本轮发布前观测一致；这些计数是该时点的快照，不表示全库历史失败归零。
- 部署的 pre-upgrade 备份已由启动后的已验证日备份替换。最后一代日备份已在目录锁下核对 CEOB、integrity=ok、schema/FK、SHA、inode/mtime/size 和无打开引用后删除。3 个失败/中断门禁副本、4 个本轮空模板及本轮测试根目录共 8 个目录均已删除；成功门禁目录由标准流程自动删除。每个路径都已复查不存在，备份目录为空。
- 删除文件的逻辑大小合计约 971,400,052 bytes，不作为实际磁盘回收量；保留生产数据库、原生 trajectory 和提交的简洁验收证据。未触碰更早且不属于本轮的框架模板。此项是测试产物清理，不是数据库业务容量缩减。

机器可读记录：[验收 JSON](validation/2026-10-29-email-original-history-read.json)。本轮读取缺陷和必要发布性能问题均已收口；不宣称全库模型或所有查询性能已最优。
