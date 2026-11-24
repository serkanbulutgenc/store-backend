from __future__ import annotations

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from store_backend.staff.permissions import Perm


class Dashboard(models.Model):
    """Anchor for the Dashboard's named permissions; it has no table."""

    class Meta:
        managed = False
        default_permissions = ()
        permissions = [(p.value, p.label) for p in Perm]

    def __str__(self) -> str:
        return "Dashboard"


class AppendOnlyError(Exception):
    """Audit entries are written once and never changed or removed."""


class AuditQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AppendOnlyError

    def delete(self):
        raise AppendOnlyError

    def bulk_update(self, objs, fields, batch_size=None):
        raise AppendOnlyError


class AuditEntry(models.Model):
    """One business event: who did what to which record, with a short before/after.

    Append-only: enforced here and by a database trigger (see migration 0002).
    Write entries through `store_backend.services.audit.record`.
    """

    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="Empty for system actions such as the 48 h auto-cancel.",
    )
    actor_email = models.CharField(max_length=254, blank=True)
    action = models.CharField(max_length=50, db_index=True)
    target_type = models.CharField(max_length=100)
    target_id = models.CharField(max_length=64)
    summary = models.CharField(max_length=255, blank=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)

    objects = AuditQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["target_type", "target_id"])]

    def __str__(self) -> str:
        return f"{self.action} {self.target_type}:{self.target_id}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AppendOnlyError
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError


class ApprovalStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PENDING = "pending_approval", "Pending approval"
    PUBLISHED = "published", "Published"
    UNPUBLISHED = "unpublished", "Unpublished"


class ApprovalFields(models.Model):
    """The state every content type under the approval rule carries (phase 4, section
    2): Staff draft, the Owner approves. Mix this into the model; the rules are in
    `store_backend.services.approval`."""

    status = models.CharField(
        max_length=16,
        choices=ApprovalStatus,
        default=ApprovalStatus.DRAFT,
    )
    published_at = models.DateTimeField(null=True, blank=True)
    # Set while pending approval; kept after a rejection so the submitter sees why.
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=500, blank=True)

    class Meta:
        abstract = True


class PendingChange(models.Model):
    """A Staff edit of a published item, held until the Owner decides (ADR 0008). The
    live row is untouched meanwhile. At most one per item; a second edit amends it."""

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    payload = models.JSONField(encoder=DjangoJSONEncoder)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["content_type", "object_id"],
                name="staff_pending_change_one_per_item",
            ),
        ]

    def __str__(self) -> str:
        return f"pending change {self.content_type_id}:{self.object_id}"


class ApprovedVersion(models.Model):
    """One approved text of an item, kept as its history. Written when the item is
    published or a pending change is approved; never changed."""

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    version = models.PositiveIntegerField()
    payload = models.JSONField(encoder=DjangoJSONEncoder)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    approved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["content_type", "object_id", "version"],
                name="staff_approved_version_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.content_type_id}:{self.object_id} v{self.version}"


class HomeLayout(models.Model):
    """The Ana sayfa Widgets one Dashboard user turned on, in the order they chose.

    No row means the user never configured it and gets their role's default. Keys are
    Widget keys (see `store_backend.services.home_layout`); unknown or no longer
    permitted keys are dropped when the layout is read, never trusted from here.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="home_layout",
    )
    widgets = models.JSONField(default=list)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Ana sayfa of user {self.user_id}"
