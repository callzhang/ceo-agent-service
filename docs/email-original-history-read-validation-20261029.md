# 邮件原文详情与 History 主题搜索验收（2026-10-29）

## 已证实根因与当前实现

生产原文详情 `/api/console/email/classifications/949082524487675729` 返回 502 / `email_original_unavailable`。异常在 `locate_message`，尚未读取正文。当前来源指向法务；原始邮件确实位于 UID 60。现有 Message-ID 合同规范化域名为小写，但该服务器的 HEADER Message-ID 检索只在原始大小写的完整值下命中，规范值、本地部分及子串检索均为零。只读获取原始头信息确认是同一完整身份，不是邮件被删除。

修复替换现行原文重新定位算法：UID SEARCH ALL，按最多 50 UID 一批只读 FETCH Message-ID 头字段；复用既有规范身份全等、严格 UID literal 解析、唯一性和 UIDVALIDITY 合同后读取正文。没有 DWS/旧结构回退、标题猜测、正文缓存、读取自修补或 DDL。正常已知 UID 的直接读取不变。代价是重新定位需要遍历目标文件夹的头信息，大文件夹耗时尚未实测。

History 生产当前主题查询 `Quotation for On-Premise` 命中 44 条；加 failed 为零，当前全部 18 条失败也没有该主题。JunHe MOVE 111699、FLAG 127062、READ 127063 都已 done；保留的失败尝试不重新决定 Action 当前状态。本轮未证实当前搜索缺陷，不修改搜索 SQL。新增当前模型 API 回归覆盖主题大小写、failed/全状态、无命中、计数和翻页。

## 验证

- 原文协议回归在修复前 2 failed / 4 passed；修复后相关测试通过。
- 直接相关测试共 201 项通过；包含只读 IMAP、完整原文、来源版本、classification reader/API、History 搜索与分页。测试中的 provider 均为替身。两次 History 测试初始失败是新断言误用 API 的 id 表示，已按实际当前契约修正，没有改变产品期望。
- 独立 SPEC / QUALITY 审查通过；Ruff、差异检查通过。
- 修复代码经生产来源只读验证：法务 UID 60、稳定身份准确、完整正文 6417 UTF-8 bytes；摘要见 JSON。没有保存原文、修改邮箱或重放 JunHe 动作。
- 发布前 Store / Email schema 共 463 项，SHA-256 `f5cd6496df716bf9a997c57403310c98ab393500b2de75dc48b8fe9ba10c6a94`，FK 异常为空；已安装版本 `616e237ec28ad4a913ee09d2f5e3e3ef1193b727`。这是冻结时点的证据，不是滚动当前状态。

## 发布与收尾

标准 gate、部署、安装后 API/UI 读回及本轮备份/临时文件清理仍待执行。负责人已确认本轮由本任务单独发布，不并行 deploy。

机器可读记录：[验收 JSON](validation/2026-10-29-email-original-history-read.json)。不将本次读取修复描述为数据库容量缩减。
