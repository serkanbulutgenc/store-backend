from __future__ import annotations

from django.conf import settings
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
