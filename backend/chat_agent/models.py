import uuid

from django.db import models


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.CharField(max_length=80, default="local-user")
    state = models.CharField(max_length=32, default="collecting")
    facts = models.JSONField(default=dict)
    scenario = models.CharField(max_length=32, default="success")
    created_at = models.DateTimeField(auto_now_add=True)


class Message(models.Model):
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    role = models.CharField(max_length=16)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)


class Proposal(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="proposals"
    )
    action = models.CharField(max_length=16, default="book")
    details = models.JSONField()
    fingerprint = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    status = models.CharField(max_length=16, default="pending")
    result = models.JSONField(default=dict)
    approved_at = models.DateTimeField(null=True, blank=True)
    execution_token = models.UUIDField(null=True, blank=True)
    execution_expires_at = models.DateTimeField(null=True, blank=True)
    booking_attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)


class Memory(models.Model):
    user_id = models.CharField(max_length=80)
    key = models.CharField(max_length=80)
    value = models.JSONField()
    explicit = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user_id", "key"], name="unique_user_memory")
        ]


class Trace(models.Model):
    run_id = models.UUIDField(default=uuid.uuid4)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="traces")
    kind = models.CharField(max_length=32)
    name = models.CharField(max_length=80)
    data = models.JSONField(default=dict)
    latency_ms = models.FloatField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)


class MockHold(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider_id = models.CharField(max_length=40)
    slot = models.CharField(max_length=80)
    price = models.PositiveIntegerField()
    user_id = models.CharField(max_length=80, default="local-user")
    service = models.CharField(max_length=80, default="")
    request_details = models.JSONField(default=dict)
    expires_at = models.DateTimeField()


class MockBooking(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    hold = models.OneToOneField(MockHold, on_delete=models.PROTECT)
    idempotency_key = models.CharField(max_length=100, unique=True)
    user_id = models.CharField(max_length=80, default="local-user")
    service = models.CharField(max_length=80, default="")
    status = models.CharField(max_length=32, default="confirmed")
    cancellation_source = models.CharField(max_length=16, default="")
    slot = models.CharField(max_length=80)


class MockOperation(models.Model):
    key = models.CharField(max_length=120, unique=True)
    result = models.JSONField(default=dict)
    attempts = models.PositiveIntegerField(default=0)


class CancellationFollowUp(models.Model):
    booking = models.OneToOneField(MockBooking, on_delete=models.CASCADE)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE)
    status = models.CharField(max_length=24, default="pending")
    proposal = models.OneToOneField(Proposal, null=True, blank=True, on_delete=models.SET_NULL)
    notified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
