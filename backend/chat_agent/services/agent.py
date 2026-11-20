import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta

import httpx
import requests
from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone
from integrations.factory import get_model, get_provider_client
from integrations.llm.schemas import validate_plan
from litellm.exceptions import Timeout as LiteLLMTimeout

from chat_agent.exceptions import BookingDetailsChanged, ExecutionInProgress, LimitExceeded
from chat_agent.models import Message, MockBooking, MockHold, Proposal, Trace
from chat_agent.selectors import current_preferences
from chat_agent.services.approvals import (
    claim_execution,
    fingerprint,
    release_execution,
    save_execution_result,
)
from chat_agent.services.booking import BookingRecovery
from chat_agent.services.booking_change import execute_booking_change
from chat_agent.services.context import bounded_context
from chat_agent.services.costs import estimate_cost
from chat_agent.services.tracing import finish_trace, sanitize_trace


class Agent:
    def __init__(self, conversation, model=None):
        """Start one bounded run for a saved conversation with an optional injected model."""
        self.conversation = conversation
        self.model = model
        self.tools = get_provider_client(scenario=conversation.scenario)
        self.run_id = uuid.uuid4()
        self.started = time.monotonic()
        self.calls = 0

    def trace(self, kind, name, data, latency_ms=0):
        """Persist one event for this run, including its data and measured latency."""
        return Trace.objects.create(
            conversation=self.conversation,
            run_id=self.run_id,
            kind=kind,
            name=name,
            data=sanitize_trace(data),
            latency_ms=latency_ms,
        )

    def remaining(self):
        """Return the remaining run time in seconds; raise LimitExceeded when it is exhausted."""
        remaining = settings.AGENT_MAX_SECONDS - (time.monotonic() - self.started)
        if remaining <= 0:
            raise LimitExceeded("Time limit reached")
        return remaining

    def tool(self, name, **arguments):
        """Call a provider tool within the run limits and trace its result or exception.

        Exceptions propagate to the workflow so it can reconcile an uncertain outcome.
        """
        self.remaining()
        if self.calls >= settings.AGENT_MAX_TOOL_CALLS:
            raise LimitExceeded("Tool call limit reached")
        self.calls += 1
        started = time.perf_counter()
        event = self.trace("tool", name, {"arguments": arguments, "status": "started"})
        try:
            result = getattr(self.tools, name)(**arguments)
        except Exception as exc:
            finish_trace(
                event,
                {"arguments": arguments, "error": type(exc).__name__, "status": "error"},
                started,
            )
            raise
        finish_trace(
            event,
            {"arguments": arguments, "result": result, "status": "success"},
            started,
        )
        # A late side effect remains traced, but cannot be reported as verified success.
        self.remaining()
        return result

    def transition(self, state):
        """Save the new conversation state and record the previous state in the trace."""
        before = self.conversation.state
        self.conversation.state = state
        self.conversation.save(update_fields=["state"])
        self.trace("state", "transition", {"from": before, "to": state})

    def reply(self, text, **extra):
        """Save an assistant message and return the standard API response with any extra fields."""
        Message.objects.create(conversation=self.conversation, role="assistant", content=text)
        self.trace("outcome", self.conversation.state, {"message": text})
        return {
            "conversation_id": str(self.conversation.id),
            "state": self.conversation.state,
            "message": text,
            "run_id": str(self.run_id),
            **extra,
        }

    def context(self, message):
        """Build a bounded model context using current facts and explicit preferences.

        Historical messages are excluded so corrected or forgotten facts cannot reappear.
        """
        today = timezone.localdate()
        upcoming_dates = {}
        for day_offset in range(7):
            local_date = today + timedelta(days=day_offset)
            upcoming_dates[local_date.strftime("%A")] = local_date.isoformat()
        context = {
            "current_date": today.isoformat(),
            "upcoming_dates": upcoming_dates,
            "timezone": settings.TIME_ZONE,
            "message": message,
            "facts": self.conversation.facts,
            "preferences": current_preferences(self.conversation.user_id),
        }
        encoded, self.context_usage = bounded_context(context)
        return encoded

    def start_model_call(self, context):
        """Create one traced model attempt before either the sync or async call begins."""
        model = self.model if self.model is not None else get_model()
        backend = getattr(type(model), "backend", settings.LLM_BACKEND)
        client = getattr(model, "client", None)
        model_name = getattr(client, "model", settings.LLM_MODEL)
        base_url = getattr(client, "base_url", settings.LLM_BASE_URL)
        if not isinstance(model_name, str):
            model_name = settings.LLM_MODEL
        if not isinstance(base_url, str):
            base_url = settings.LLM_BASE_URL
        timeout = self.remaining()
        data = {
            "backend": backend,
            "model": model_name,
            "arguments": {"context": json.loads(context), "timeout_seconds": timeout},
            "status": "started",
            "usage": None,
            "estimated_cost": None,
        }
        started = time.perf_counter()
        event = self.trace("model", "plan", data)
        return model, base_url, timeout, data, started, event

    def finish_model_call(self, call, plan, usage):
        """Validate one returned plan and save its reported usage, cost and latency."""
        model, base_url, timeout, data, started, event = call
        data["usage"] = usage or None
        if data["backend"] == "live":
            data["estimated_cost"] = estimate_cost(data["model"], usage, base_url)
        self.remaining()
        plan = validate_plan(plan)
        data.update(status="success", plan=plan)
        finish_trace(event, data, started)
        return plan

    def fail_model_call(self, call, exc):
        """Keep failure/cancellation evidence without persisting raw exception messages."""
        model, base_url, timeout, data, started, event = call
        if data["usage"] is None:
            data["usage"] = getattr(exc, "usage", None)
            if data["backend"] == "live":
                data["estimated_cost"] = estimate_cost(data["model"], data["usage"], base_url)
        data.update(status="error", error=type(exc).__name__)
        finish_trace(event, data, started)

    def model_plan(self, context):
        """Run the synchronous adapter for offline commands and existing domain scenarios."""
        call = self.start_model_call(context)
        model, _, timeout, _, _, _ = call
        try:
            plan, usage = model.plan(context, timeout)
            return self.finish_model_call(call, plan, usage)
        except Exception as exc:
            self.fail_model_call(call, exc)
            raise

    async def amodel_plan(self, context):
        """Await model I/O while keeping trace writes and validation in short sync sections."""
        call = await sync_to_async(self.start_model_call)(context)
        model, _, timeout, _, _, _ = call
        try:
            async with asyncio.timeout(self.remaining()):
                plan, usage = await model.aplan(context, timeout)
            return await sync_to_async(self.finish_model_call)(call, plan, usage)
        except (Exception, asyncio.CancelledError) as exc:
            await sync_to_async(self.fail_model_call)(call, exc)
            raise

    def start_chat(self, message):
        """Save the message and build current context, or return an unresolved-action response."""
        Message.objects.create(conversation=self.conversation, role="user", content=message)
        if self.conversation.proposals.filter(status="executing").exists():
            return None, self.reply(
                "A confirmed action is unresolved. Retry its confirmation to check the outcome."
            )
        context = self.context(message)
        self.trace("context", "model_input", {"context": json.loads(context), **self.context_usage})
        return context, None

    def apply_plan(self, plan):
        """Merge validated facts and prepare a proposal after rechecking current execution state."""
        self.remaining()
        self.conversation.refresh_from_db()
        if self.conversation.proposals.filter(status="executing").exists():
            return self.reply(
                "A confirmed action is unresolved. Retry its confirmation to check the outcome."
            )
        self.conversation.proposals.filter(status="pending").update(status="invalidated")
        facts = {**self.conversation.facts, **plan}
        self.conversation.facts = facts
        self.conversation.save(update_fields=["facts"])
        return self.prepare(facts)

    def chat_failure(self, exc):
        """Return the same clear failure outcome from synchronous and asynchronous chat paths."""
        if isinstance(exc, LimitExceeded):
            self.transition("limited")
            return self.reply(str(exc) + ". No new action was approved.")
        if isinstance(
            exc, (TimeoutError, requests.Timeout, httpx.TimeoutException, LiteLLMTimeout)
        ):
            self.trace("error", "model_timeout", {"type": type(exc).__name__})
            self.transition("limited")
            return self.reply(
                "The AI request timed out or is busy. Please retry; no booking was made."
            )
        self.trace("error", "chat_failed", {"type": type(exc).__name__})
        self.transition("error")
        return self.reply(
            "I could not prepare that request. Check the model configuration or try again; no booking was made."
        )

    def chat(self, message):
        """Run the synchronous domain entry point for commands and offline scenario tests."""
        try:
            context, response = self.start_chat(message)
            if response is not None:
                return response
            return self.apply_plan(self.model_plan(context))
        except Exception as exc:
            return self.chat_failure(exc)

    async def achat(self, message):
        """Handle web chat with async model waiting and synchronous database-safe stages."""
        try:
            context, response = await sync_to_async(self.start_chat)(message)
            if response is not None:
                return response
            plan = await self.amodel_plan(context)
            return await sync_to_async(self.apply_plan)(plan)
        except Exception as exc:
            return await sync_to_async(self.chat_failure)(exc)

    def prepare(self, facts):
        """Choose the preparation path for a new booking, cancellation, or reschedule."""
        action = facts.get("action", "book")
        if action in {"cancel", "reschedule"}:
            return self._prepare_booking_change(facts, action)
        return self._prepare_booking(facts)

    def _prepare_booking_change(self, facts, action):
        """Check ownership and collect the details needed to propose a booking change."""
        required_fields = ["booking_id"]
        if action == "reschedule":
            required_fields.append("slot")
        missing = [field for field in required_fields if not facts.get(field)]
        if missing:
            self.transition("collecting")
            return self.reply("Please provide: " + ", ".join(missing), missing=missing)
        booking = (
            MockBooking.objects.filter(pk=facts["booking_id"], user_id=self.conversation.user_id)
            .select_related("hold")
            .first()
        )
        if not booking or booking.status != "confirmed":
            return self.reply("That booking does not exist for this user or is no longer active.")
        details = {
            "action": action,
            "booking_id": str(booking.id),
            "provider_id": booking.hold.provider_id,
            "price": booking.hold.price,
            "previous_slot": booking.slot,
            "slot": facts.get("slot", booking.slot),
        }
        if action == "reschedule":
            parsed = datetime.fromisoformat(details["slot"])
            if parsed.tzinfo is None or parsed <= timezone.now():
                return self.reply("Please supply a future slot with a timezone.")
        expiry = timezone.now() + timedelta(minutes=10)
        return self._request_approval(action, details, expiry)

    def _prepare_booking(self, facts, excluded_providers=()):
        """Find eligible providers, hold the best matching slot, and request approval.

        Saved preferences fill missing fields. Provider price and time constraints are
        checked before selection, and the final held quote must remain within budget.
        """
        preferences = current_preferences(self.conversation.user_id)
        effective = {**preferences, **facts}
        missing = [
            field
            for field in ["service", "area", "date_range", "budget"]
            if not effective.get(field)
        ]
        if missing:
            self.transition("collecting")
            return self.reply("Please provide: " + ", ".join(missing), missing=missing)
        self.transition("searching")
        providers = self.tool(
            "search_providers",
            service=effective["service"],
            area=effective["area"],
            date_range=effective["date_range"],
        )
        choices = []
        for provider in providers:
            if provider["id"] in excluded_providers:
                continue
            if provider["price"] > effective["budget"]:
                continue
            matching_slots = []
            for slot in provider["slots"]:
                slot_time = slot[11:16]
                earliest_time = effective.get("earliest_time", "00:00")
                latest_time = effective.get("latest_time", "23:59")
                is_future = datetime.fromisoformat(slot) > timezone.now()
                fits_time_window = earliest_time <= slot_time <= latest_time
                if is_future and fits_time_window:
                    matching_slots.append(slot)
            if matching_slots:
                choices.append({**provider, "slots": matching_slots})
        choices.sort(
            key=lambda provider: (
                provider["id"] != effective.get("preferred_provider"),
                provider["price"],
                -provider["rating"],
            )
        )
        if not choices:
            self.transition("collecting")
            return self.reply(
                "No providers match your budget and time window. Please adjust a constraint."
            )
        chosen = choices[0]
        self.tool("get_provider", provider_id=chosen["id"])
        hold = self.tool("hold_slot", provider_id=chosen["id"], slot=chosen["slots"][0])
        if hold["price"] > effective["budget"]:
            self.transition("collecting")
            return self.reply(
                f"The held quote is ₹{hold['price']}, above your ₹{effective['budget']} budget. Please adjust your budget or request another provider."
            )
        MockHold.objects.filter(pk=hold["hold_id"]).update(
            user_id=self.conversation.user_id,
            service=effective["service"],
            request_details={"facts": facts, "effective": effective},
        )
        details = {
            "action": "book",
            **hold,
            "service": effective["service"],
            "area": effective["area"],
        }
        expiry = datetime.fromisoformat(hold["expires_at"])
        return self._request_approval("book", details, expiry, providers=choices)

    def _request_approval(self, action, details, expiry, providers=None):
        """Save the exact action details and expiry, then return the proposal for user review."""
        proposal = Proposal.objects.create(
            conversation=self.conversation,
            action=action,
            details=details,
            fingerprint=fingerprint(details),
            expires_at=expiry,
        )
        self.transition("awaiting_approval")
        return self.reply(
            "Review the exact action below and explicitly confirm this proposal to proceed.",
            proposal=proposal_payload(proposal),
            providers=providers if providers is not None else [],
        )

    def confirm(self, proposal_id, supplied_fingerprint):
        """Execute or reconcile a proposal after the API validates explicit user confirmation.

        Validate the fingerprint, expiry, and current status before persisting approval.
        Use the proposal ID as the durable idempotency key, verify booking success against
        the provider ledger, and leave uncertain operations available for reconciliation.
        """
        try:
            proposal, new_approval = claim_execution(
                self.conversation, proposal_id, supplied_fingerprint, self.run_id
            )
        except ExecutionInProgress:
            self.conversation.refresh_from_db()
            return self.reply(
                "This action is being processed. Retry this confirmation to check its outcome."
            )
        if proposal.status == "done":
            return self.reply("This action was already completed.", result=proposal.result)
        if proposal.status == "expired":
            return self.reply("This proposal expired. Send your request again for a fresh quote.")
        if new_approval:
            self.trace(
                "approval",
                "explicit_confirmation",
                {
                    "proposal_id": str(proposal.id),
                    "fingerprint": proposal.fingerprint,
                },
            )
        try:
            self.transition("executing")
            if proposal.action == "book":
                result = BookingRecovery(proposal, self.run_id, self.tool).execute()
            else:
                result = execute_booking_change(proposal, self.conversation.user_id, self.tool)
            expected_status = "confirmed"
            if proposal.action == "cancel":
                expected_status = "cancelled"
            successful = result.get("status") == expected_status

            if successful:
                proposal.status = "done"
                next_state = "completed"
                message = "Action verified and completed."
            else:
                proposal.status = "failed"
                next_state = "needs_attention"
                message = (
                    f"Action was not completed ({result.get('status')}). "
                    "Please request a fresh proposal."
                )
            save_execution_result(proposal, self.run_id, proposal.status, result)
            self.transition(next_state)
            return self.reply(message, result=result)
        except BookingDetailsChanged:
            save_execution_result(proposal, self.run_id, "invalidated", {})
            self.transition("needs_attention")
            return self.reply(
                "The provider, slot, or price changed. Request a fresh proposal and approve it again."
            )
        except ExecutionInProgress:
            return self.reply(
                "Another run owns this action. Retry this confirmation to check the saved outcome."
            )
        except Exception as exc:
            self.trace("error", "execution_unresolved", {"type": type(exc).__name__})
            self.transition("needs_attention")
            return self.reply(
                "The outcome is unresolved. Retry this exact confirmation to reconcile safely; do not create another booking."
            )
        finally:
            release_execution(proposal, self.run_id)


def proposal_payload(proposal):
    """Return the reviewable proposal details, fingerprint, and local expiry timestamp."""
    return {
        "id": str(proposal.id),
        "action": proposal.action,
        "details": proposal.details,
        "fingerprint": proposal.fingerprint,
        "expires_at": timezone.localtime(proposal.expires_at).isoformat(),
        "status": proposal.status,
    }
