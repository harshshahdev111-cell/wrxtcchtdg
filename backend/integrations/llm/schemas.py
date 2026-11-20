"""Validate and normalize model facts once, at the agent boundary."""

import json

from rest_framework import serializers


class PlanSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["book", "cancel", "reschedule"], required=False)
    service = serializers.CharField(max_length=80, required=False)
    area = serializers.CharField(max_length=120, required=False)
    date_range = serializers.ListField(
        child=serializers.DateField(), min_length=2, max_length=2, required=False
    )
    budget = serializers.IntegerField(min_value=1, max_value=1000000, required=False)
    earliest_time = serializers.TimeField(format="%H:%M", input_formats=["%H:%M"], required=False)
    latest_time = serializers.TimeField(format="%H:%M", input_formats=["%H:%M"], required=False)
    preferred_provider = serializers.CharField(max_length=40, required=False)
    booking_id = serializers.UUIDField(required=False)
    slot = serializers.DateTimeField(required=False)


def validate_plan(data):
    """Validate extracted facts and normalize date, time, and UUID values for JSON storage."""
    serializer = PlanSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    # Normalize DRF's date/time/UUID objects to JSON primitives.
    data = dict(serializer.validated_data)
    for key in ("earliest_time", "latest_time"):
        if key in data:
            data[key] = data[key].strftime("%H:%M")
    return json.loads(json.dumps(data, default=_serialize_plan_value))


def _serialize_plan_value(value):
    """Convert a date-like value to ISO text and other non-JSON values to strings."""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
