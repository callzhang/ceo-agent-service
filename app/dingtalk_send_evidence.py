"""Require a provider receipt before Audit may report a DingTalk send executed.

``executed`` asserts that an external action happened.  The only thing that can
support that assertion is something the service wrote down itself: the receipt
a provider returned when it accepted the effect.  Everything the model types --
the message id, the delivery key, the readback -- is authored by the same turn
that is making the claim, and two of those values are handed to it in its own
prompt, so echoing them proves nothing.

What this rejects, seen live: a turn that ran no provider call at all and
reported the *trigger* message id as the id of the message it had just sent.
The task closed as done, the delivery ledger stayed empty, and the question it
was supposed to ask was never asked by anyone.

What this deliberately does not reject: a turn that sent, then identified its
own message by reading the conversation back instead of querying the send
status.  The send is real and the receipt is there; which id the turn then
reports is bookkeeping, not evidence.  Blocking those would force a retry, and
a retry of a delivered message sends it twice.
"""

from __future__ import annotations

import json

from app.agent_effect_guard import provider_receipts
from app.consumer_agent import dingtalk_outgoing_text_key
from app.store import AgentRole, AutoReplyStore, ReplyTask


class DingTalkSendEvidenceDriver:
    """Answer whether one Audit run really reached a provider for its sends."""

    def __init__(self, store: AutoReplyStore) -> None:
        self.store = store

    def audit_run_has_execution_evidence(
        self, task: ReplyTask, *, audit_run_id: int
    ) -> bool:
        if task.channel != "dingtalk":
            return True
        run = self.store.get_agent_run(audit_run_id)
        if run is None:
            return False
        if not self._claims_a_chat_send(task, run.proposal_revision):
            # Calendar responses, approvals and reactions carry their own
            # provider identities, which this receipt shape does not describe.
            # They keep the contract they have until each one has a receipt of
            # its own; answering for them here would block honest work.
            return True
        return bool(provider_receipts(run.tool_events))

    def execution_evidence_requirement(self) -> str:
        return (
            "external_result: executed requires the provider receipt returned "
            "when the send was accepted. Send through the reviewed CLI and "
            "report what it returned; a message id taken from the conversation "
            "or from this prompt is not a receipt."
        )

    def _claims_a_chat_send(self, task: ReplyTask, proposal_revision: int) -> bool:
        """Whether the accepted proposal carries a chat message to send.

        Deliberately wider than the actions the service prepared a body for:
        an action naming no resolvable target is one the service could not
        prepare, but a turn can still report having sent it, and that report
        is exactly what needs a receipt behind it.
        """
        runs = self.store.list_agent_runs_for_task_generation(
            task.id, task.execution_generation
        )
        consumer = next(
            (
                run
                for run in reversed(runs)
                if run.role is AgentRole.CONSUMER
                and run.proposal_revision == proposal_revision
                and run.final_result_json
            ),
            None,
        )
        if consumer is None:
            return False
        # Read the two fields this question needs rather than validating the
        # whole result: the scoring fields a Consumer result carries have
        # changed shape over time and have nothing to do with whether the
        # proposal sends a message.
        proposal = json.loads(consumer.final_result_json).get("proposal")
        if not isinstance(proposal, dict):
            return False
        actions = proposal.get("actions")
        if not isinstance(actions, list):
            return False
        return any(
            isinstance(action, dict)
            and action.get("capability") == "dingtalk-chat"
            and isinstance(action.get("payload"), dict)
            and dingtalk_outgoing_text_key(action["payload"]) is not None
            for action in actions
        )
