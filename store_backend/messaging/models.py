from __future__ import annotations

import uuid

from django.db import models


class MessageStatus(models.TextChoices):
    PENDING = "pending", "Bekliyor"
    SENT = "sent", "Gönderildi"
    FAILED = "failed", "Gönderilemedi"
    BOUNCED = "bounced", "Ulaşmadı"


class Message(models.Model):
    """One outgoing email: the outbox row and, once sent, the kept log entry.

    Written in the same transaction as the change that caused it, rendered at that
    moment so the log shows exactly what the Shopper was (or would have been) sent.
    Never purged in phase 1.
    """

    order = models.ForeignKey(
        "order.Order",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="messages",
    )
    recipient = models.EmailField()
    template = models.CharField(max_length=60)
    subject = models.CharField(max_length=200)
    body_text = models.TextField()
    body_html = models.TextField()
    status = models.CharField(
        max_length=10,
        choices=MessageStatus,
        default=MessageStatus.PENDING,
        db_index=True,
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(db_index=True)
    last_error = models.CharField(max_length=500, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    bounced_at = models.DateTimeField(null=True, blank=True)
    bounce_reason = models.CharField(max_length=500, blank=True)
    # The SMTP Message-ID; bounce webhooks refer to a message by it.
    message_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    resent_from = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="resends",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.template} to {self.recipient} ({self.status})"
