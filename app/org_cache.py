from collections.abc import Iterable
from datetime import datetime

from app.dingtalk_models import DingTalkMessage
from app.dws_client import DwsError, DwsUserProfile
from app.store import AutoReplyStore, OrgUserProfile

ORG_PROFILE_FETCH_BATCH_SIZE = 20
ORG_CACHE_REFRESHED_DATE_STATE_KEY = "org_cache_refreshed_date"


class CachedOrgDirectory:
    def __init__(self, store: AutoReplyStore):
        self.store = store

    def resolve_message_sender(self, message: DingTalkMessage) -> str:
        if message.sender_user_id:
            return message.sender_user_id
        if message.sender_open_dingtalk_id:
            profile = self.store.find_org_user_by_open_dingtalk_id(
                message.sender_open_dingtalk_id
            )
            if profile is not None:
                return profile.user_id
        matches = self.store.find_org_users_by_name(message.sender_name)
        if len(matches) != 1:
            raise DwsError(
                f"org cache cannot resolve unique sender for {message.sender_name}"
            )
        return matches[0].user_id

    def is_hr_user(self, user_id: str) -> bool:
        profile = self._require_profile(user_id)
        hr_department_ids = self.store.get_hr_department_ids()
        if not hr_department_ids:
            raise DwsError("HR department cache is empty")
        return bool(profile.department_ids & hr_department_ids)

    def user_in_manager_chain(
        self, manager_user_id: str, subject_user_id: str, max_depth: int = 20
    ) -> bool:
        current_user_id = subject_user_id
        visited: set[str] = set()
        for _ in range(max_depth):
            if current_user_id in visited:
                raise DwsError("org cache manager chain contains a cycle")
            visited.add(current_user_id)
            profile = self._require_profile(current_user_id)
            if not profile.manager_user_id:
                raise DwsError(
                    f"org cache manager chain is incomplete for {current_user_id}"
                )
            if profile.manager_user_id == manager_user_id:
                return True
            current_user_id = profile.manager_user_id
        raise DwsError("org cache manager chain exceeded max depth")

    def get_user_department_ids(self, user_id: str) -> set[str]:
        profile = self._require_profile(user_id)
        if not profile.department_ids:
            raise DwsError(f"org cache department data is missing for {user_id}")
        return profile.department_ids

    def is_current_user_message(self, message: DingTalkMessage) -> bool:
        current_user_id = self.store.get_current_user_id()
        if not current_user_id:
            raise DwsError("current user cache is empty")
        if message.sender_user_id:
            return message.sender_user_id == current_user_id
        if message.sender_open_dingtalk_id:
            profile = self.store.find_org_user_by_open_dingtalk_id(
                message.sender_open_dingtalk_id
            )
            return profile is not None and profile.user_id == current_user_id
        return False

    def _require_profile(self, user_id: str) -> OrgUserProfile:
        profile = self.store.get_org_user_profile(user_id)
        if profile is None:
            raise DwsError(f"org cache is missing user profile for {user_id}")
        return profile


class CachedDwsClient:
    def __init__(self, dws, org_directory: CachedOrgDirectory):
        self.dws = dws
        self.org_directory = org_directory

    def list_unread_conversations(self, count: int):
        return self.dws.list_unread_conversations(count)

    def check_upgrade(self):
        return self.dws.check_upgrade()

    def upgrade(self):
        return self.dws.upgrade()

    def get_current_user_id(self) -> str:
        return self.dws.get_current_user_id()

    def search_department_ids(self, query: str) -> set[str]:
        return self.dws.search_department_ids(query)

    def list_department_member_profiles(
        self, department_ids: list[str]
    ) -> list[DwsUserProfile]:
        return self.dws.list_department_member_profiles(department_ids)

    def get_user_profiles(self, user_ids: list[str]) -> list[DwsUserProfile]:
        return self.dws.get_user_profiles(user_ids)

    def read_recent_messages(self, conversation, limit: int = 50):
        return self.dws.read_recent_messages(conversation, limit)

    def read_unread_messages(self, conversation):
        return self.dws.read_unread_messages(conversation)

    def read_mentioned_messages(
        self,
        conversation=None,
        limit: int = 50,
        cursor: str = "0",
        lookback_hours: int = 24,
    ):
        return self.dws.read_mentioned_messages(
            conversation,
            limit=limit,
            cursor=cursor,
            lookback_hours=lookback_hours,
        )

    def read_broadcast_messages(
        self,
        aliases: tuple[str, ...],
        limit: int = 100,
        lookback_hours: int = 24,
    ):
        return self.dws.read_broadcast_messages(
            aliases,
            limit=limit,
            lookback_hours=lookback_hours,
        )

    def read_doc(self, node: str):
        return self.dws.read_doc(node)

    def doc_info(self, node: str):
        return self.dws.doc_info(node)

    def get_aitable_base(self, base_id: str):
        return self.dws.get_aitable_base(base_id)

    def get_aitable_tables(self, base_id: str, table_ids: list[str] | None = None):
        return self.dws.get_aitable_tables(base_id, table_ids)

    def query_aitable_records(self, base_id: str, table_id: str, limit: int = 10):
        return self.dws.query_aitable_records(base_id, table_id, limit)

    def search_documents(self, query: str, page_size: int = 5):
        return self.dws.search_documents(query, page_size=page_size)

    def get_user_profile(self, user_id: str) -> DwsUserProfile:
        return self.dws.get_user_profile(user_id)

    def download_doc(self, node: str):
        return self.dws.download_doc(node)

    def get_resource_download_url(
        self,
        open_conversation_id: str,
        open_message_id: str,
        resource_id: str,
        resource_type: str,
    ):
        return self.dws.get_resource_download_url(
            open_conversation_id,
            open_message_id,
            resource_id,
            resource_type,
        )

    def download_robot_message_file(self, download_code: str):
        return self.dws.download_robot_message_file(download_code)

    def minutes_permission_request_from_message(self, message: DingTalkMessage):
        return self.dws.minutes_permission_request_from_message(message)

    def add_minutes_member_permission(self, request):
        return self.dws.add_minutes_member_permission(request)

    def calendar_invite_from_message(self, message: DingTalkMessage):
        return self.dws.calendar_invite_from_message(message)

    def list_calendar_events(self, start: str, end: str):
        return self.dws.list_calendar_events(start, end)

    def respond_calendar_event(self, event_id: str, response_status: str):
        return self.dws.respond_calendar_event(event_id, response_status)

    def execute_oa_approval_action(
        self,
        process_instance_id: str,
        task_id: str,
        action: str,
        remark: str,
    ):
        return self.dws.execute_oa_approval_action(
            process_instance_id,
            task_id,
            action,
            remark,
        )

    def list_pending_oa_approvals(self, page: int = 1, size: int = 30):
        return self.dws.list_pending_oa_approvals(page=page, size=size)

    def read_oa_approval_detail(self, process_instance_id: str):
        return self.dws.read_oa_approval_detail(process_instance_id)

    def read_oa_approval_records(self, process_instance_id: str):
        return self.dws.read_oa_approval_records(process_instance_id)

    def read_oa_approval_tasks(self, process_instance_id: str):
        return self.dws.read_oa_approval_tasks(process_instance_id)

    def read_oa_process_instance_openapi(self, process_instance_id: str):
        return self.dws.read_oa_process_instance_openapi(process_instance_id)

    def send_message(
        self,
        conversation_id: str | None,
        text: str,
        at_users: list[str] | None = None,
        user_id: str | None = None,
        open_dingtalk_id: str | None = None,
    ):
        return self.dws.send_message(
            conversation_id,
            text,
            at_users=at_users,
            user_id=user_id,
            open_dingtalk_id=open_dingtalk_id,
        )

    def reply_message(
        self,
        conversation_id: str,
        ref_message_id: str,
        ref_sender_open_dingtalk_id: str,
        text: str,
    ):
        return self.dws.reply_message(
            conversation_id,
            ref_message_id,
            ref_sender_open_dingtalk_id,
            text,
        )

    def recall_bot_message(self, conversation_id: str | None, process_query_key: str):
        return self.dws.recall_bot_message(conversation_id, process_query_key)

    def ding_self(self, text: str) -> None:
        current_user_id = self.org_directory.store.get_current_user_id()
        if not current_user_id:
            raise DwsError("current user cache is empty")
        self.dws.ding_user(current_user_id, text)

    def resolve_message_sender(self, message: DingTalkMessage) -> str:
        try:
            return self.org_directory.resolve_message_sender(message)
        except DwsError:
            user_id = self.dws.resolve_message_sender(message)
            profile = self.dws.get_user_profile(user_id)
            self.org_directory.store.upsert_org_user_profile(
                user_id=profile.user_id,
                name=profile.name or message.sender_name,
                title=profile.title,
                open_dingtalk_id=profile.open_dingtalk_id
                or message.sender_open_dingtalk_id,
                manager_user_id=profile.manager_user_id,
                manager_name=profile.manager_name,
                department_ids=profile.department_ids,
                department_names=profile.department_names,
                org_labels=profile.org_labels,
                has_subordinate=profile.has_subordinate,
            )
            return user_id

    def is_hr_user(self, user_id: str) -> bool:
        try:
            return self.org_directory.is_hr_user(user_id)
        except DwsError:
            profile = self.dws.get_user_profile(user_id)
            self.org_directory.store.upsert_org_user_profile(
                user_id=profile.user_id,
                name=profile.name,
                title=profile.title,
                open_dingtalk_id=profile.open_dingtalk_id,
                manager_user_id=profile.manager_user_id,
                manager_name=profile.manager_name,
                department_ids=profile.department_ids,
                department_names=profile.department_names,
                org_labels=profile.org_labels,
                has_subordinate=profile.has_subordinate,
            )
            return self.dws.is_hr_user(user_id)

    def user_in_manager_chain(
        self, manager_user_id: str, subject_user_id: str
    ) -> bool:
        return self.org_directory.user_in_manager_chain(manager_user_id, subject_user_id)

    def get_user_department_ids(self, user_id: str) -> set[str]:
        return self.org_directory.get_user_department_ids(user_id)

    def is_current_user_message(self, message: DingTalkMessage) -> bool:
        current_user_id = self.org_directory.store.get_current_user_id()
        if not current_user_id:
            raise DwsError("current user cache is empty")
        if message.sender_user_id:
            return message.sender_user_id == current_user_id
        if not message.sender_open_dingtalk_id:
            return False
        return self.resolve_message_sender(message) == current_user_id


def refresh_org_cache(
    store: AutoReplyStore,
    dws,
    user_ids: Iterable[str] | None = None,
    hr_query: str = "人力资源",
) -> int:
    requested_user_ids = set(user_ids or set()) | set(store.list_org_user_ids())
    current_user_id = dws.get_current_user_id()
    store.set_current_user_id(current_user_id)
    requested_user_ids.add(current_user_id)

    hr_department_ids = dws.search_department_ids(hr_query)
    if not hr_department_ids:
        raise DwsError("HR department query returned no departments")
    store.set_hr_department_ids(hr_department_ids)

    refreshed = 0
    hr_profiles = dws.list_department_member_profiles(sorted(hr_department_ids))
    refreshed += _cache_profiles(store, hr_profiles)

    pending = set(requested_user_ids)
    seen: set[str] = set()
    while pending:
        batch = sorted(pending - seen)[:ORG_PROFILE_FETCH_BATCH_SIZE]
        if not batch:
            break
        seen.update(batch)
        profiles = dws.get_user_profiles(batch)
        refreshed += _cache_profiles(store, profiles)
        for profile in profiles:
            if profile.manager_user_id and profile.manager_user_id not in seen:
                pending.add(profile.manager_user_id)
    store.set_service_state(
        ORG_CACHE_REFRESHED_DATE_STATE_KEY,
        datetime.now().astimezone().date().isoformat(),
    )
    return refreshed


def _cache_profiles(store: AutoReplyStore, profiles: Iterable[DwsUserProfile]) -> int:
    count = 0
    for profile in profiles:
        store.upsert_org_user_profile(
            user_id=profile.user_id,
            name=profile.name,
            title=profile.title,
            open_dingtalk_id=profile.open_dingtalk_id,
            manager_user_id=profile.manager_user_id,
            manager_name=profile.manager_name,
            department_ids=profile.department_ids,
            department_names=profile.department_names,
            org_labels=profile.org_labels,
            has_subordinate=profile.has_subordinate,
        )
        count += 1
    return count
