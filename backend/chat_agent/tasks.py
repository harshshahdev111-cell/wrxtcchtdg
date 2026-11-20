"""Thin Celery entry points; the follow-up rules live in the service layer."""

from celery import shared_task

from chat_agent.services.follow_up import process_provider_cancellations


@shared_task(ignore_result=True, soft_time_limit=30, time_limit=35)
def check_provider_cancellations():
    """Detect provider cancellations and deliver pending follow-ups without user input."""
    return process_provider_cancellations()
