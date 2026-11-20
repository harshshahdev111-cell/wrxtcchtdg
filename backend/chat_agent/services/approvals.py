"""Short PostgreSQL transactions for proposal approval and execution ownership."""

import hashlib
import json
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from chat_agent.exceptions import ExecutionInProgress
from chat_agent.models import Proposal


def fingerprint(details):
    """Hash the exact action details that the user is asked to approve."""
    return hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest()


def claim_execution(conversation, proposal_id, supplied_fingerprint, owner):
    """Validate approval and claim execution under a short row lock.

    The transaction commits before any provider call. An expiring claim permits
    recovery after process death. The returned flag identifies a new approval.
    """
    with transaction.atomic():
        proposal = (
            Proposal.objects.select_for_update()
            .filter(pk=proposal_id, conversation=conversation)
            .first()
        )
        if proposal is None:
            raise ValidationError({"proposal_id": "Unknown proposal for this conversation"})
        if (
            supplied_fingerprint != proposal.fingerprint
            or fingerprint(proposal.details) != proposal.fingerprint
        ):
            raise ValidationError(
                {"fingerprint": "Proposal details changed; request a new proposal"}
            )
        if proposal.status == "done":
            return proposal, False
        if proposal.status not in {"pending", "executing"}:
            raise ValidationError({"proposal_id": "Proposal is no longer valid"})
        now = timezone.now()
        new_approval = proposal.status == "pending"
        if new_approval and proposal.expires_at <= now:
            proposal.status = "expired"
            proposal.save(update_fields=["status"])
            return proposal, False
        if (
            proposal.execution_token is not None
            and proposal.execution_expires_at is not None
            and proposal.execution_expires_at > now
        ):
            raise ExecutionInProgress("This action is already being processed")
        if new_approval:
            proposal.approved_at = now
        proposal.status = "executing"
        proposal.execution_token = owner
        proposal.execution_expires_at = now + timedelta(
            seconds=max(settings.AGENT_MAX_SECONDS, settings.GUNICORN_TIMEOUT, 1) + 5
        )
        proposal.save(
            update_fields=["approved_at", "status", "execution_token", "execution_expires_at"]
        )
        return proposal, new_approval


def save_execution_result(proposal, owner, status, result):
    """Save a result only if this run still owns the execution claim."""
    updated = Proposal.objects.filter(pk=proposal.pk, execution_token=owner).update(
        status=status,
        result=result,
    )
    if not updated:
        raise ExecutionInProgress("A newer run owns this action; retry to check its outcome")
    proposal.status = status
    proposal.result = result


def release_execution(proposal, owner):
    """Release only this run's claim; never clear a newer worker's ownership."""
    Proposal.objects.filter(pk=proposal.pk, execution_token=owner).update(
        execution_token=None,
        execution_expires_at=None,
    )
