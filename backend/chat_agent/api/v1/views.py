from adrf.views import APIView as AsyncAPIView
from asgiref.sync import sync_to_async
from django.conf import settings
from django.shortcuts import get_object_or_404
from integrations.factory import get_provider_client
from rest_framework.response import Response
from rest_framework.views import APIView

from chat_agent.models import Conversation, Memory
from chat_agent.services.agent import Agent, proposal_payload

from .serializers import (
    ChatInput,
    ConfirmationInput,
    ConversationInput,
    MemoryInput,
    MessageOutput,
    TraceOutput,
)


class HealthView(APIView):
    def get(self, request):
        """Return backend health, model mode, and the local-mock indicator."""
        return Response(
            {"status": "ok", "model_backend": settings.LLM_BACKEND, "local_mocks": True}
        )


class ConversationListView(APIView):
    def get(self, request):
        """Return the latest one hundred conversations, newest first."""
        return Response(
            list(
                Conversation.objects.order_by("-created_at").values(
                    "id", "user_id", "state", "scenario"
                )[:100]
            )
        )

    def post(self, request):
        """Validate the requested user and scenario, then create a conversation."""
        data = ConversationInput(data=request.data)
        data.is_valid(raise_exception=True)
        conversation = Conversation.objects.create(**data.validated_data)
        return Response({"id": str(conversation.id), "state": conversation.state}, status=201)


class ConversationDetailView(APIView):
    def get(self, request, conversation_id):
        """Return conversation facts, proposals, and messages with local timestamps."""
        conversation = get_object_or_404(Conversation, pk=conversation_id)
        return Response(
            {
                "id": str(conversation.id),
                "state": conversation.state,
                "facts": conversation.facts,
                "messages": MessageOutput(conversation.messages.order_by("id"), many=True).data,
                "proposals": [
                    proposal_payload(proposal)
                    for proposal in conversation.proposals.order_by("created_at")
                ],
            }
        )


class ChatView(AsyncAPIView):
    async def post(self, request, conversation_id):
        """Validate the user message and pass it to a new agent run for this conversation."""
        data = ChatInput(data=request.data)
        data.is_valid(raise_exception=True)
        conversation = await sync_to_async(get_object_or_404)(Conversation, pk=conversation_id)
        return Response(await Agent(conversation).achat(data.validated_data["message"]))


class ConfirmView(APIView):
    def post(self, request, conversation_id):
        """Require explicit confirmation before executing the matching proposal."""
        data = ConfirmationInput(data=request.data)
        data.is_valid(raise_exception=True)
        conversation = get_object_or_404(Conversation, pk=conversation_id)
        return Response(
            Agent(conversation).confirm(
                data.validated_data["proposal_id"], data.validated_data["fingerprint"]
            )
        )


class TraceView(APIView):
    def get(self, request, conversation_id):
        """Return the saved trace events in order with timestamps in the configured timezone."""
        conversation = get_object_or_404(Conversation, pk=conversation_id)
        events = conversation.traces.order_by("id")
        return Response(TraceOutput(events, many=True).data)


class MemoryView(APIView):
    def get(self, request, user_id):
        """Return the user's stored preferences, including their explicit-confirmation status."""
        return Response(
            list(Memory.objects.filter(user_id=user_id).values("key", "value", "explicit"))
        )

    def put(self, request, user_id):
        """Validate and save a user-supplied preference as explicitly confirmed."""
        data = MemoryInput(data=request.data)
        data.is_valid(raise_exception=True)
        memory, _ = Memory.objects.update_or_create(
            user_id=user_id,
            key=data.validated_data["key"],
            defaults={"value": data.validated_data["value"], "explicit": True},
        )
        return Response({"key": memory.key, "value": memory.value, "explicit": True})


class MemoryDetailView(APIView):
    def delete(self, request, user_id, key):
        """Forget the named preference and return an empty success response."""
        Memory.objects.filter(user_id=user_id, key=key).delete()
        return Response(status=204)


class BookingListView(APIView):
    def get(self, request, user_id):
        """Read the user's mock bookings through the configured provider factory."""
        return Response(get_provider_client().list_bookings(user_id))
