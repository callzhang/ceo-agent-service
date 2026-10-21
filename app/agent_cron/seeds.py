from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import shlex

from app.agent_cron.models import ScheduledTask, ScheduledTaskSkillRef
from app.agent_cron.options import ScheduledTaskOptionService
from app.runtime_environment import central_python
from app.store import AutoReplyStore


MINUTES_SYNC_MIGRATION_KEY = "ceo-minutes-sync-daily-v1"
MINUTES_ACCESS_MIGRATION_KEY = "ceo-minutes-access-daily-v1"
CHROME_COOKIES_MIGRATION_KEY = "chrome-cookie-copy-daily-v1"
WEEKLY_REPORT_MIGRATION_KEY = "ceo-weekly-report-saturday-v1"
DAILY_REPORT_MIGRATION_KEY = "ceo-daily-report-daily-v1"
WEEKLY_OKR_MIGRATION_KEY = "weekly-okr-report-sunday-v1"
WEEKLY_OKR_SERVICE_COMMAND = "weekly-okr-report"
DINGTALK_MESSAGE_MIGRATION_KEY = "dingtalk-message-check-v1"
DINGTALK_MESSAGE_SERVICE_COMMAND = "produce-once"
DINGTALK_CALENDAR_INVITE_MIGRATION_KEY = "dingtalk-calendar-invite-check-v1"
DINGTALK_CALENDAR_INVITE_SERVICE_COMMAND = "calendar-invites-once"
DINGTALK_MESSAGE_RECOVERY_MIGRATION_KEY = "dingtalk-message-recovery-v1"
DINGTALK_MESSAGE_RECOVERY_SERVICE_COMMAND = "recover-recent-messages"
WECHAT_MESSAGE_MIGRATION_KEY = "wechat-message-check-v1"
WECHAT_MESSAGE_SERVICE_COMMAND = "wechat-produce-once"
DINGTALK_MEETING_MIGRATION_KEY = "dingtalk-meeting-check-v1"
DINGTALK_MEETING_SERVICE_COMMAND = "scan-meetings-once"
DINGTALK_OA_MIGRATION_KEY = "dingtalk-oa-check-v1"
DINGTALK_OA_SERVICE_COMMAND = "scan-oa-approvals"
MEETING_TODO_MIGRATION_KEY = "work-source-scan-daily-v1"
MEETING_TODO_SERVICE_COMMAND = "scan-meeting-todos-once"
# Derek, 2026-09-25: follow-ups are sent only when he clicks a Task's button,
# so the scheduled 投递到期的跟进事项 task is retired. Seeding deletes a copy
# that an earlier seed created.
RETIRED_FOLLOW_UP_DELIVERY_MIGRATION_KEY = "follow-up-delivery-v1"
EMAIL_MESSAGE_MIGRATION_KEY = "email-message-check-v1"
EMAIL_MESSAGE_SERVICE_COMMAND = "email-message-check-once"


@dataclass(frozen=True)
class ScheduledTaskDefaultCopy:
    name: str
    description: str
    old_name: str
    old_description: str


SCHEDULED_TASK_DEFAULT_COPY = {
    EMAIL_MESSAGE_MIGRATION_KEY: ScheduledTaskDefaultCopy(
        name="分类新邮件",
        description="发现已配置未分类入口中的新未读邮件后，由 Agent 按邮件分类规则判断业务类别和重要性；分类结果再由现有执行队列按邮箱策略处理。",
        old_name="",
        old_description="",
    ),
    WEEKLY_REPORT_MIGRATION_KEY: ScheduledTaskDefaultCopy(
        name="准备 CEO 管理周报",
        description="读取本周听记、群消息和各业务线报告，将 CEO 判断、公司经营指标和新增跟踪事项直接写入目标管理层周会文档，并读回核验。",
        old_name="准备 CEO 管理周报",
        old_description="按本周的听记、群消息和四条业务线来源报告整理 CEO 管理周报草稿，核对上期未结问题，产出可校验的报告数据；发布到钉钉文档需要 Derek 明确授权，本任务不自行发布。",
    ),
    DAILY_REPORT_MIGRATION_KEY: ScheduledTaskDefaultCopy(
        name="发送 CEO 每日总结",
        description="每晚汇总当天会议、Tasks 变化、已处理和待处理事项及群消息，发布 CEO 每日总结文档，并通过已审核的本人账号单聊发送要点和链接。",
        old_name="发送 CEO 每日总结",
        old_description="每晚汇总当天的会议、Tasks 项目变化、已处理和等你处理的事项，并扫描当天群消息，写成重要进展、风险、需介入、需关注和管理建议；发布为钉钉文档，由机器人单聊把要点和链接发给 Derek。",
    ),
    CHROME_COOKIES_MIGRATION_KEY: ScheduledTaskDefaultCopy(
        name="同步 Chrome 登录态",
        description="每天复制一份你 Chrome 里的 cookies 给服务的无头浏览器用（退订、听记权限申请等需要登录的任务），银行、券商和支付类域名不复制。",
        old_name="",
        old_description="",
    ),
    MINUTES_ACCESS_MIGRATION_KEY: ScheduledTaskDefaultCopy(
        name="申请读不到的钉钉 AI 听记",
        description="读取听记管理后台，对本账号读不到的听记逐条在其页面提交查看权限申请；对方同意后由听记下载任务归档。需要组织管理员身份和一份已登录的后台会话。",
        old_name="",
        old_description="",
    ),
    DINGTALK_MESSAGE_MIGRATION_KEY: ScheduledTaskDefaultCopy(
        name="处理新的钉钉消息",
        description="发现新的单聊或群聊 @ 消息后，由 Agent 读取最新上下文，决定回复、表态、澄清或不处理。",
        old_name="检查 DingTalk 消息",
        old_description="增量检查 DingTalk 消息，并将新消息送入统一处理队列。",
    ),
    DINGTALK_MESSAGE_RECOVERY_MIGRATION_KEY: ScheduledTaskDefaultCopy(
        name="补查遗漏的钉钉消息和日历更新",
        description="扩大读取范围，找回常规检查遗漏的单聊、群聊 @ 消息和原地更新的日历邀请；发现后由 Agent 按对应的消息或日程规则处理。",
        old_name="恢复近期 DingTalk 消息",
        old_description="按较宽时间范围恢复近期 DingTalk 消息，补齐漏读记录及原地更新的待响应日程邀请。",
    ),
    DINGTALK_CALENDAR_INVITE_MIGRATION_KEY: ScheduledTaskDefaultCopy(
        name="处理新的钉钉日历邀请",
        description="发现新的会议邀请后，由 Agent 读取最新邀请和日程冲突，决定接受、暂定、拒绝或向邀请人澄清；执行后核验日历状态。",
        old_name="",
        old_description="",
    ),
    DINGTALK_MEETING_MIGRATION_KEY: ScheduledTaskDefaultCopy(
        name="同步会议结论与管理者视角",
        description="会议结束且听记可用后，由 Agent 总结关键结论、分歧和行动项，确保管理者观点被准确传达；发现未对齐时发送会后澄清。",
        old_name="检查 DingTalk 会议",
        old_description="扫描已结束的 DingTalk 会议，并创建会议处理任务。",
    ),
    WECHAT_MESSAGE_MIGRATION_KEY: ScheduledTaskDefaultCopy(
        name="处理已授权会话的新微信消息",
        description="发现已启用的好友新消息或群聊 @ 后，由 Agent 判断是否回复；仅按已配置范围和发送模式处理，不读取或回复其他会话。",
        old_name="检查微信消息",
        old_description="检查已配置微信会话的新消息，并创建后续处理任务。",
    ),
    DINGTALK_OA_MIGRATION_KEY: ScheduledTaskDefaultCopy(
        name="处理新的钉钉 OA 审批",
        description="发现新的或有新处理记录的待审批 OA 后，由 Agent 读取完整材料与审批流水，判断同意、拒绝或评论补充要求，并在执行后核验结果。",
        old_name="审阅新的或有进展的钉钉 OA",
        old_description="发现新的或有新处理记录的待审批 OA 后，由 Agent 读取完整材料与审批流水，判断同意、拒绝或评论补充要求，并在执行后核验结果。",
    ),
    MEETING_TODO_MIGRATION_KEY: ScheduledTaskDefaultCopy(
        name="将会议行动项整理到 Tasks",
        description="发现钉钉会议中新增或修改的行动项后，由 Agent 核验任务内容、负责人证据、截止时间和现有 Tasks，创建或更新需要持续跟进的任务；不因参会或发言推断负责人。",
        old_name="整理工作区中的新工作记录",
        old_description="发现工作区中新建或修改的 Markdown、文本文件后，由 Agent 判断其中是否有值得持续跟进的承诺，并按证据创建或更新项目、TODO 和跟进。",
    ),
    WEEKLY_OKR_MIGRATION_KEY: ScheduledTaskDefaultCopy(
        name="生成并发送每周 OKR 管理周报",
        description="读取所有管理者的实时 OKR，由 Agent 结合工作证据分析进展、风险、领导力和文化表现，生成周报并发布到管理知识库和 CEO-2 管理群。",
        old_name="周日生成 OKR 周报",
        old_description="读取管理者实时 OKR，生成本周管理进度周报。",
    ),
    MINUTES_SYNC_MIGRATION_KEY: ScheduledTaskDefaultCopy(
        name="下载新增的钉钉 AI 听记",
        description="发现尚未归档且可访问的钉钉 AI 听记后，读取可用的摘要和逐字稿并归档到工作区；权限受限或内容不可读时保留同步状态，待后续检查。",
        old_name="每天同步 AI 听记",
        old_description="同步 AI 听记的摘要、逐字稿和归档游标到工作区。",
    ),
}


def _default_copy(migration_key: str) -> ScheduledTaskDefaultCopy:
    return SCHEDULED_TASK_DEFAULT_COPY[migration_key]


def _upgrade_default_copy(
    store: AutoReplyStore,
    task: ScheduledTask,
    *,
    now: datetime | None,
) -> ScheduledTask:
    copy = _default_copy(task.migration_key or "")
    if not copy.old_name or not copy.old_description:
        return task
    return store.upgrade_scheduled_task_default_copy(
        task.id,
        expected_version=task.version,
        old_name=copy.old_name,
        old_description=copy.old_description,
        name=copy.name,
        description=copy.description,
        now=now,
    )


DINGTALK_MESSAGE_LEGACY_CONSUMER_PROMPT = (
    "使用 $ceo-message-triage 判断 Trigger 发现的真实 DingTalk 消息是否需要 CEO "
    "关注或回复；如果消息是日程邀请或原地更新的日程卡片，使用 "
    "$ceo-calendar-invite 处理。使用 $dingtalk-chat 读取所需消息上下文，并使用 "
    "$dingtalk-calendar 核验最新日程状态；"
    "原地更新后的日程卡片是新的输入版本，更新前基于旧时段或旧冲突发送的澄清"
    "不构成精确重复，必须根据最新日程重新处置；"
    "不要新增复盘或摘要任务。"
)
DINGTALK_MESSAGE_CONSUMER_PROMPT = (
    "使用 $ceo-message-triage 判断 Trigger 发现的真实 DingTalk 消息是否需要 CEO "
    "关注或回复；使用 $dingtalk-chat 读取所需消息上下文；不要新增复盘或摘要任务。"
)
CALENDAR_INVITE_CONSUMER_PROMPT = (
    "使用 $ceo-calendar-invite 处理 Trigger 发现的真实钉钉日历邀请；使用 "
    "$dingtalk-calendar 读取最新邀请和冲突日程，并在需要向邀请人澄清时使用 "
    "$dingtalk-chat。不要新增复盘或摘要任务。"
)
WECHAT_MESSAGE_CONSUMER_PROMPT = (
    "使用 $ceo-wechat 处理 Trigger 发现的真实微信消息，严格保留已配置联系人、"
    "群@、auto-confirm 和 sender 授权边界；不要新增复盘或摘要任务。"
)
MEETING_CONSUMER_PROMPT = (
    "使用 $ceo-meeting-work 处理 Trigger 发现的真实会议，并按需使用 "
    "$dingtalk-minutes 与 $dingtalk-calendar 读取会议和日历证据。"
)
OA_CONSUMER_PROMPT = (
    "使用 $dingtalk-oa-approval 与适用的 Stardust 业务 Skill 处理 Trigger 发现的真实 "
    "DingTalk OA 待审批事项：$stardust-oa-finance-review、$stardust-oa-project-review、"
    "$stardust-oa-contract-review、$stardust-oa-people-review、"
    "$stardust-oa-attendance-travel-review、$stardust-oa-cloud-resource-review。"
    "按实时 processCode 和表单事实选择并组合适用 Skill；遵循所选 Skill 明确引用的规范材料，"
    "审批判断和动作映射使用通用 OA Skill 与适用业务 Skill。区分申请方陈述与已核实事实，"
    "不得从背景材料自行补充规则；任何外部动作后重新读取 DingTalk。"
)
LEGACY_OA_CONSUMER_PROMPT_20261017 = (
    "使用 $dingtalk-oa-approval 与适用的 Stardust 业务 Skill 处理 Trigger 发现的真实 DingTalk OA 待审批事项："
    "$stardust-oa-finance-review、$stardust-oa-project-review、$stardust-oa-contract-review、"
    "$stardust-oa-people-review、$stardust-oa-attendance-travel-review、$stardust-oa-cloud-resource-review。"
    "先按 live processCode 和表单事实分类；跨类别事项必须组合适用的 Skill，不得仅因财务 Skill 无匹配规则卡就升级。"
    "规则只在各 Skill 里，不读背景参考文档；个人具体规则仅按本 Prompt 执行，不得扩展为公司通用规则。"
    "判断标准由对应业务 Skill 提供，动作一律按 $dingtalk-oa-approval 的完整决策表；某类别没有自己的动作清单不是规则缺口。"
    "只有本单所属业务 Skill 的判断标准已逐条核对并满足时 rule_coverage 才能为 1.0；本单恰好取决于某项业务 Skill 未定义的标准时，才进入 needs_human。"
    "申请人可补足的事实/材料缺口按通用 Skill 评论或退回，并在规则缺口并存时独立 needs_human。"
    "请假交接关系、能力、审批链确认及历史日期审阅均按最新版 $stardust-oa-attendance-travel-review 与 $dingtalk-oa-approval 执行，"
    "不再要求交接人或审批人必须是申请人的下属。算法部门绩效或薪酬的最终决定必须由Derek 本人作出，相关审批转 Derek needs_human。"
    "任何动作后重新读取 DingTalk。不要在本 Prompt 重述制度阈值、金额或业务 Skill 中的通用审批规则。 "
    "是否needs_human只能依据适用规则和已核实事实，不由申请人或评论措辞决定；申请人可补正的事实材料缺口优先走当前真实可用的退回路径，"
    "缺规则或明确保留Derek决定的事项才转人工。已有评论不重复发送；确需人工决定时，已有留言回执直接复用并继续形成审核通过的本次实例needs_human，"
    "不以评论阶段done代替业务完成。日期已过不自动跳过历史审批。"
)
MEETING_TODO_CONSUMER_PROMPT = (
    "使用 $ceo-meeting-work 核验 Trigger 提供的真实会议行动项证据，再使用 "
    "$ceo-work-tracking 检查现有 Tasks 并创建或更新需要持续跟进的任务；按需使用 "
    "$dingtalk-minutes 读取会议 Todo 的最新状态。负责人、截止时间和完成标准必须有"
    "明确证据，不因参会或发言推断负责人。"
)
EMAIL_MESSAGE_CONSUMER_PROMPT = (
    "使用 $ceo-email-classifier 对 Trigger 发现的真实新邮件进行分类；"
    "仅判断业务类别、重要性和置信度，不直接移动、标记、回复、退订或发送邮件。"
)


def _consumer_skill_refs(
    options: ScheduledTaskOptionService,
    *,
    managed: tuple[str, ...] = (),
    operation: tuple[str, ...] = (),
) -> tuple[ScheduledTaskSkillRef, ...]:
    refs: list[ScheduledTaskSkillRef] = []
    managed_options = (
        {item.name: item for item in options.list_managed_skill_options()}
        if managed
        else {}
    )
    for name in managed:
        skill = managed_options.get(name)
        revisions = tuple(
            revision
            for revision in (skill.revisions if skill else ())
            if revision.available
        )
        if skill is None or not revisions:
            raise ValueError(f"scheduled consumer managed Skill unavailable: {name}")
        revision = max(revisions, key=lambda item: item.revision_number)
        refs.append(
            ScheduledTaskSkillRef(
                skill_source="managed",
                skill_name=name,
                managed_skill_id=skill.skill_id,
                managed_revision_id=revision.revision_id,
                position=len(refs),
            )
        )
    operation_options = (
        {
            item.name: item
            for item in options.list_operation_skill_options()
            if item.available
        }
        if operation
        else {}
    )
    for name in operation:
        if name not in operation_options:
            raise ValueError(f"scheduled consumer operation Skill unavailable: {name}")
        refs.append(
            ScheduledTaskSkillRef(
                skill_source="operation",
                skill_name=name,
                position=len(refs),
            )
        )
    return tuple(refs)


def weekly_report_prompt() -> str:
    return (
        "使用 $ceo-weekly-report 生成并发布 CEO 管理周报。"
        "用 read_scheduled_context 读取本次触发，用 read_service_activity 读取服务事实；"
        "按 Skill 使用原生 DWS 读取听记、群消息、报告和周会文档，发布后读回核验。"
    )


def daily_report_prompt() -> str:
    return (
        "使用 $ceo-daily-report 生成并发送 CEO 每日总结。"
        "用 read_scheduled_context 读取本次触发，用 read_service_activity 读取服务事实；"
        "按 Skill 使用原生 DWS 读取其余材料、发布文档，并通过服务的已审核消息工具投递。"
    )


def _legacy_weekly_report_prompt_20261019() -> str:
    materials_command = (
        f"{shlex.quote(str(central_python()))} -m app.cli weekly-report-materials "
        "--scheduled-run <本次触发的 scheduled_task_run_id>"
    )
    return (
        "按 $ceo-weekly-report 生成并发布本周的 CEO 管理周报。"
        f"先运行 `{materials_command}` 取得目标周会文档、上周文档和本周会议清单，"
        "再按 skill 用 $dingtalk-minutes、$dingtalk-chat 读取听记转写、群消息和各业务线材料，"
        "写成报告后用 $dingtalk-wiki 与 $dingtalk-doc 直接写入下周一的管理层周会文档"
        "（不存在就先建），并读回核对。业务线还没提交的报告写进覆盖说明，不阻塞发布，不要向 Derek 追问材料。"
    )


def _legacy_daily_report_prompt_20261019() -> str:
    facts_command = (
        f"{shlex.quote(str(central_python()))} -m app.cli daily-report-facts "
        "--scheduled-run <本次触发的 scheduled_task_run_id>"
    )
    return (
        "按 $ceo-daily-report 生成 CEO 每日总结。"
        f"先运行 `{facts_command}` 取得报告窗口和服务记录的事实，再用 $dingtalk-chat 扫描窗口内全部群消息，"
        "按需用 $dingtalk-minutes 补读会议摘要；写成报告后用 $dingtalk-wiki 与 $dingtalk-doc "
        "发布到钉钉文档并读回核对，最后提出 dingtalk-chat 单聊动作；"
        "审核轮只能调用服务提供的 send_approved_dingtalk_message 发送，不能用机器人或 dws 绕过投递台账。"
        "某个来源读不到时写进覆盖说明，照常发布，不要向 Derek 追问材料。"
    )


def _legacy_daily_report_prompt() -> str:
    """Prompt stored by the pre-approved-DingTalk delivery seed."""
    return (
        "按 $ceo-daily-report 生成 CEO 每日总结。先运行 "
        "`/Users/derek/miniforge3/bin/python -m app.cli daily-report-facts "
        "--scheduled-run <本次触发的 scheduled_task_run_id>` 取得报告窗口和服务记录的事实，"
        "再用 $dingtalk-chat 扫描窗口内全部群消息，按需用 $dingtalk-minutes 补读会议摘要；"
        "写成报告后用 $dingtalk-wiki 与 $dingtalk-doc 发布到钉钉文档并读回核对，"
        "最后由机器人单聊把要点和文档链接发给 Derek。"
        "某个来源读不到时写进覆盖说明，照常发布，不要向 Derek 追问材料。"
    )


@dataclass(frozen=True)
class ScheduledTaskDefault:
    migration_key: str
    cron_expression: str
    command: str = ""
    prompt: str = ""
    timezone_name: str = "Asia/Shanghai"
    managed: tuple[str, ...] = ()
    operation: tuple[str, ...] = ()
    previous_command: str = ""
    previous_prompt: str = ""
    previous_managed: tuple[str, ...] = ()
    previous_operation: tuple[str, ...] = ()
    previous_refs_match_current: bool = False
    previous_agent_prompts: tuple[str, ...] = ()
    previous_cron_expression: str = ""
    previous_timezone_name: str = ""


SCHEDULED_TASK_DEFAULTS = (
    ScheduledTaskDefault(
        migration_key=EMAIL_MESSAGE_MIGRATION_KEY,
        cron_expression="0 * * * * *",
        command=EMAIL_MESSAGE_SERVICE_COMMAND,
        prompt=EMAIL_MESSAGE_CONSUMER_PROMPT,
        managed=("ceo-email-classifier",),
    ),
    ScheduledTaskDefault(
        migration_key=DINGTALK_MESSAGE_MIGRATION_KEY,
        cron_expression="0 * * * * *",
        command=DINGTALK_MESSAGE_SERVICE_COMMAND,
        prompt=DINGTALK_MESSAGE_CONSUMER_PROMPT,
        managed=("ceo-message-triage",),
        operation=("dingtalk-chat",),
        previous_prompt=DINGTALK_MESSAGE_LEGACY_CONSUMER_PROMPT,
        previous_managed=("ceo-message-triage", "ceo-calendar-invite"),
        previous_operation=("dingtalk-chat", "dingtalk-calendar"),
    ),
    ScheduledTaskDefault(
        migration_key=DINGTALK_CALENDAR_INVITE_MIGRATION_KEY,
        cron_expression="10 * * * * *",
        command=DINGTALK_CALENDAR_INVITE_SERVICE_COMMAND,
        prompt=CALENDAR_INVITE_CONSUMER_PROMPT,
        managed=("ceo-calendar-invite",),
        operation=("dingtalk-calendar", "dingtalk-chat"),
    ),
    ScheduledTaskDefault(
        migration_key=DINGTALK_MESSAGE_RECOVERY_MIGRATION_KEY,
        cron_expression="0 30 * * * *",
        command=DINGTALK_MESSAGE_RECOVERY_SERVICE_COMMAND,
        prompt=DINGTALK_MESSAGE_LEGACY_CONSUMER_PROMPT,
        managed=("ceo-message-triage", "ceo-calendar-invite"),
        operation=("dingtalk-chat", "dingtalk-calendar"),
    ),
    ScheduledTaskDefault(
        migration_key=DINGTALK_MEETING_MIGRATION_KEY,
        cron_expression="0 */10 * * * *",
        command=DINGTALK_MEETING_SERVICE_COMMAND,
        prompt=MEETING_CONSUMER_PROMPT,
        managed=("ceo-meeting-work",),
        operation=("dingtalk-minutes", "dingtalk-calendar"),
    ),
    ScheduledTaskDefault(
        migration_key=WECHAT_MESSAGE_MIGRATION_KEY,
        cron_expression="0 */5 * * * *",
        command=WECHAT_MESSAGE_SERVICE_COMMAND,
        prompt=WECHAT_MESSAGE_CONSUMER_PROMPT,
        managed=("ceo-wechat",),
    ),
    ScheduledTaskDefault(
        migration_key=DINGTALK_OA_MIGRATION_KEY,
        cron_expression="0 0 * * * *",
        command=DINGTALK_OA_SERVICE_COMMAND,
        prompt=OA_CONSUMER_PROMPT,
        operation=(
            "dingtalk-oa-approval",
            "stardust-oa-finance-review",
            "stardust-oa-project-review",
            "stardust-oa-contract-review",
            "stardust-oa-people-review",
            "stardust-oa-attendance-travel-review",
            "stardust-oa-cloud-resource-review",
        ),
        previous_prompt=LEGACY_OA_CONSUMER_PROMPT_20261017,
        previous_refs_match_current=True,
    ),
    ScheduledTaskDefault(
        migration_key=MEETING_TODO_MIGRATION_KEY,
        cron_expression="0 0 0 * * *",
        command=MEETING_TODO_SERVICE_COMMAND,
        prompt=MEETING_TODO_CONSUMER_PROMPT,
        managed=("ceo-meeting-work", "ceo-work-tracking"),
        operation=("dingtalk-minutes",),
    ),
    ScheduledTaskDefault(
        migration_key=WEEKLY_OKR_MIGRATION_KEY,
        cron_expression="0 0 3 * * 0",
        timezone_name="America/Los_Angeles",
        prompt="使用 $ceo-weekly-okr-report 与 $dingtang-okr-review 生成并发送每周 OKR 管理周报；按 Skill 使用原生 DWS 读取、发布和核验。",
        managed=("ceo-weekly-okr-report",),
        operation=(
            "dingtang-okr-review",
            "dingtalk-chat",
            "dingtalk-wiki",
            "dingtalk-doc",
        ),
        previous_command=WEEKLY_OKR_SERVICE_COMMAND,
        previous_cron_expression="0 0 18 * * 0",
        previous_timezone_name="Asia/Shanghai",
    ),
    ScheduledTaskDefault(
        migration_key=WEEKLY_REPORT_MIGRATION_KEY,
        cron_expression="0 0 12 * * 6",
        prompt=weekly_report_prompt(),
        timezone_name="America/Los_Angeles",
        managed=("ceo-weekly-report",),
        operation=(
            "dingtalk-minutes",
            "dingtalk-chat",
            "dingtalk-wiki",
            "dingtalk-doc",
        ),
        previous_agent_prompts=(_legacy_weekly_report_prompt_20261019(),),
    ),
    ScheduledTaskDefault(
        migration_key=DAILY_REPORT_MIGRATION_KEY,
        cron_expression="0 0 6 * * *",
        timezone_name="America/Los_Angeles",
        prompt=daily_report_prompt(),
        managed=("ceo-daily-report",),
        operation=(
            "dingtalk-chat",
            "dingtalk-minutes",
            "dingtalk-wiki",
            "dingtalk-doc",
        ),
        previous_agent_prompts=(
            _legacy_daily_report_prompt(),
            _legacy_daily_report_prompt_20261019(),
        ),
        previous_cron_expression="0 0 21 * * *",
        previous_timezone_name="Asia/Shanghai",
    ),
    ScheduledTaskDefault(
        migration_key=CHROME_COOKIES_MIGRATION_KEY,
        cron_expression="0 0 6 * * *",
        command="sync-chrome-cookies",
    ),
    ScheduledTaskDefault(
        migration_key=MINUTES_ACCESS_MIGRATION_KEY,
        cron_expression="0 30 19 * * *",
        command="request-minutes-access",
    ),
    ScheduledTaskDefault(
        migration_key=MINUTES_SYNC_MIGRATION_KEY,
        cron_expression="0 0 20 * * *",
        command="sync-minutes-once",
    ),
)


def _agent_runtime(options: ScheduledTaskOptionService) -> str:
    runtime_options = options.list_runtime_options()
    if not runtime_options:
        raise ValueError("no runtime is configured for scheduled Agent tasks")
    return next(
        (option.route_name for option in runtime_options if option.available),
        runtime_options[0].route_name,
    )


def _seed_task(
    default: ScheduledTaskDefault,
    *,
    store: AutoReplyStore,
    options: ScheduledTaskOptionService,
    now: datetime | None,
) -> ScheduledTask:
    copy = _default_copy(default.migration_key)
    if default.command:
        refs = _consumer_skill_refs(
            options,
            managed=default.managed,
            operation=default.operation,
        )
        previous_refs = (
            refs
            if default.previous_refs_match_current
            else _consumer_skill_refs(
                options,
                managed=default.previous_managed,
                operation=default.previous_operation,
            )
        )
        adopted = store.adopt_scheduled_task_service_command(
            migration_key=default.migration_key,
            command=default.command,
            seed_description=copy.description,
            consumer_prompt=default.prompt,
            consumer_skill_refs=refs,
            legacy_consumer_prompt=default.previous_prompt,
            legacy_consumer_skill_refs=previous_refs,
            now=now,
        )
        if adopted is not None:
            return adopted
        runtime_id = ""
    else:
        existing = next(
            (
                task
                for task in store.list_scheduled_tasks(include_deleted=True)
                if task.migration_key == default.migration_key
            ),
            None,
        )
        if existing is not None:
            if existing.deleted_at is not None:
                return existing
            if (
                default.previous_command
                and existing.command == default.previous_command
            ):
                return store.update_scheduled_task(
                    existing.id,
                    expected_version=existing.version,
                    command="",
                    prompt=existing.prompt or default.prompt,
                    runtime_id=_agent_runtime(options),
                    skill_refs=existing.skill_refs
                    or _consumer_skill_refs(
                        options,
                        managed=default.managed,
                        operation=default.operation,
                    ),
                    now=now,
                )
            if (
                not existing.command
                and existing.prompt in default.previous_agent_prompts
            ):
                return store.update_scheduled_task(
                    existing.id,
                    expected_version=existing.version,
                    prompt=default.prompt,
                    now=now,
                )
            return existing
        refs = _consumer_skill_refs(
            options,
            managed=default.managed,
            operation=default.operation,
        )
        runtime_id = _agent_runtime(options)
    return store.create_scheduled_task(
        migration_key=default.migration_key,
        name=copy.name,
        description=copy.description,
        command=default.command,
        prompt=default.prompt,
        cron_expression=default.cron_expression,
        timezone_name=default.timezone_name,
        runtime_id=runtime_id,
        skill_refs=refs,
        enabled=False,
        now=now,
    )


def seed_scheduled_tasks(
    *,
    store: AutoReplyStore,
    options: ScheduledTaskOptionService,
    working_directory: Path,
    now: datetime | None = None,
) -> tuple[ScheduledTask, ...]:
    """Create declarative repository defaults and migrate only exact old defaults."""
    del working_directory
    tasks = tuple(
        _upgrade_default_copy(
            store,
            _upgrade_default_schedule(
                store,
                _seed_task(default, store=store, options=options, now=now),
                default,
                now=now,
            ),
            now=now,
        )
        for default in SCHEDULED_TASK_DEFAULTS
    )
    _retire_follow_up_delivery_task(store=store, now=now)
    return tasks


def _upgrade_default_schedule(
    store: AutoReplyStore,
    task: ScheduledTask,
    default: ScheduledTaskDefault,
    *,
    now: datetime | None,
) -> ScheduledTask:
    """Move only exact repository-default schedules to the current timezone."""
    if (
        task.deleted_at is not None
        or
        not default.previous_cron_expression
        or not default.previous_timezone_name
        or task.cron_expression != default.previous_cron_expression
        or task.timezone_name != default.previous_timezone_name
    ):
        return task
    return store.update_scheduled_task(
        task.id,
        expected_version=task.version,
        cron_expression=default.cron_expression,
        timezone_name=default.timezone_name,
        now=now,
    )


def _retire_follow_up_delivery_task(
    *, store: AutoReplyStore, now: datetime | None
) -> None:
    """Delete the retired follow-up delivery task if an earlier seed made it.

    The delete is the console's own soft delete: the row and its runs stay as
    history, the task stops being listed and never fires again.
    """
    for task in store.list_scheduled_tasks():
        if task.migration_key == RETIRED_FOLLOW_UP_DELIVERY_MIGRATION_KEY:
            store.delete_scheduled_task(task.id, expected_version=task.version, now=now)
