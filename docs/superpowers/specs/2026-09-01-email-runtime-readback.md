# Email runtime readback

更新时间：2026-09-01

本记录是对已启用 Email 集成的当前运行时回读，不是模型效果报告，也不授权任何
邮箱写动作。

## 当前状态

- `/healthz`：`{"ok":true,"status":"ok"}`；
- `ceo-mail-review`：`enabled/ready`；
- Email learning：`active_model_id=null`、`pending_examples=0`、`models=[]`；
- Email worker：`waiting_configuration / missing_model`；
- 生产 SQLite `PRAGMA integrity_check`：`ok`；
- 唯一账号 `dingtalk_primary`：enabled，IMAP/SMTP 端口为 `993/465`。

## 生产库回读

以下 Email 相关表当前均为 `0` 行：

- `email_messages`
- `email_classifications`
- `email_action_plans`
- `email_actions`
- `email_action_attempts`
- `email_feedback_requests`
- `email_unsubscribe_claims`
- `email_unsubscribe_receipts`

因此本轮随机 readonly 实验没有把邮件、临时标注、分类结果或外部动作写入生产库。

## 决策

“启用”目前只表示 Email 配置、Skill 和运行时接线已经存在；由于没有 active model，
mailbox shadow、自动分类动作、自动回复和自动退订仍保持关闭。下一步模型效果实验
需要用户确认后的 gold feedback；assistant provisional annotation 不得直接晋升
为 active model，也不得触发 provider action。
