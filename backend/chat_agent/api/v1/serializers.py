from rest_framework import serializers

from chat_agent.constants import SCENARIOS
from chat_agent.models import Message, Trace
from chat_agent.services.tracing import sanitize_trace


class MessageOutput(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = ["role", "content", "created_at"]


class TraceOutput(serializers.ModelSerializer):
    data = serializers.SerializerMethodField()

    def get_data(self, instance):
        """Apply the same credential rules to previously saved traces when returning them."""
        return sanitize_trace(instance.data)

    class Meta:
        model = Trace
        fields = ["run_id", "kind", "name", "data", "latency_ms", "created_at"]


class ConversationInput(serializers.Serializer):
    user_id = serializers.CharField(max_length=80, default="local-user")
    scenario = serializers.ChoiceField(choices=SCENARIOS, default="success")


class ChatInput(serializers.Serializer):
    message = serializers.CharField(max_length=4000)


class ConfirmationInput(serializers.Serializer):
    proposal_id = serializers.UUIDField()
    fingerprint = serializers.RegexField(r"^[a-f0-9]{64}$")
    confirmed = serializers.BooleanField()

    def validate_confirmed(self, value):
        """Require an explicit true value before the API accepts a proposal confirmation."""
        if value is not True:
            raise serializers.ValidationError("Explicit confirmation is required")
        return value


class MemoryInput(serializers.Serializer):
    key = serializers.ChoiceField(choices=["area", "earliest_time", "preferred_provider"])
    value = serializers.CharField(max_length=120)

    def validate(self, attrs):
        """Normalize an earliest-time preference to HH:MM before it is stored."""
        if attrs["key"] == "earliest_time":
            field = serializers.TimeField(input_formats=["%H:%M"])
            attrs["value"] = field.run_validation(attrs["value"]).strftime("%H:%M")
        return attrs
