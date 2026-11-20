from django.urls import path

from .views import (
    BookingListView,
    ChatView,
    ConfirmView,
    ConversationDetailView,
    ConversationListView,
    HealthView,
    MemoryDetailView,
    MemoryView,
    TraceView,
)

urlpatterns = [
    path("health/", HealthView.as_view()),
    path("conversations/", ConversationListView.as_view()),
    path("conversations/<uuid:conversation_id>/", ConversationDetailView.as_view()),
    path("conversations/<uuid:conversation_id>/messages/", ChatView.as_view()),
    path("conversations/<uuid:conversation_id>/confirm/", ConfirmView.as_view()),
    path("conversations/<uuid:conversation_id>/traces/", TraceView.as_view()),
    path("users/<str:user_id>/memories/", MemoryView.as_view()),
    path("users/<str:user_id>/memories/<str:key>/", MemoryDetailView.as_view()),
    path("users/<str:user_id>/bookings/", BookingListView.as_view()),
]
