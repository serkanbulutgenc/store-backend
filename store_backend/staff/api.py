# ruff: noqa: TC003
"""Dashboard endpoints for the signed-in user and for Staff users."""

from datetime import date
from datetime import datetime
from typing import Literal

from ninja import Field
from ninja import Query
from ninja import Router
from ninja import Schema
from ninja.pagination import paginate

from store_backend.api.pagination import PageOffsetPagination
from store_backend.api.permissions import requires
from store_backend.services import accounts
from store_backend.services import audit
from store_backend.services import staff
from store_backend.staff.permissions import Perm

router = Router(tags=["staff"])


class MeOut(Schema):
    id: int
    email: str
    name: str
    role: Literal["owner", "staff"] | None
    permissions: list[str]
    mfa_required: bool
    mfa_enrolled: bool


class StaffUserOut(Schema):
    id: int
    email: str
    name: str
    role: Literal["owner", "staff"] | None
    is_active: bool
    last_login: str | None = None

    @staticmethod
    def resolve_role(obj) -> Literal["owner", "staff"] | None:
        return accounts.role(obj)

    @staticmethod
    def resolve_last_login(obj) -> str | None:
        return obj.last_login.isoformat() if obj.last_login else None


class AuditEntryOut(Schema):
    """What the order detail already shows, plus the target. No before/after."""

    id: int
    at: datetime
    actor: str
    actor_id: int | None
    action: str
    target_type: str
    target_id: str
    summary: str

    @staticmethod
    def resolve_at(obj) -> datetime:
        return obj.created_at

    @staticmethod
    def resolve_actor(obj) -> str:
        return obj.actor_email or "Sistem"


class AuditEntryDetail(AuditEntryOut):
    before: dict | None
    after: dict | None


class AuditLogQuery(Schema):
    actor: int | None = Field(None, description="User id of the actor.")
    action: str | None = Field(None, description="An audit action name.")
    date_from: date | None = Field(None, description="First day, inclusive.")
    date_to: date | None = Field(None, description="Last day, inclusive.")


class InviteIn(Schema):
    email: str
    name: str = ""


@router.get(
    "/me",
    auth=requires(allow_unenrolled_owner=True),
    operation_id="staff_get_me",
    response=MeOut,
)
def get_me(request):
    """The caller's role and permission list. The UI hides controls from it; the API
    still enforces every permission. An Owner without TOTP gets an empty list.
    """
    user = request.auth
    return {
        "id": user.pk,
        "email": user.email,
        "name": user.name,
        "role": accounts.role(user),
        "permissions": accounts.usable_permissions(user),
        "mfa_required": accounts.mfa_required(user),
        "mfa_enrolled": accounts.mfa_enrolled(user),
    }


@router.get(
    "/staff",
    auth=requires(Perm.MANAGE_STAFF_USERS),
    operation_id="staff_list_users",
    response=list[StaffUserOut],
)
@paginate(PageOffsetPagination)
def list_staff(request):
    return staff.staff_queryset()


@router.post(
    "/staff",
    auth=requires(Perm.MANAGE_STAFF_USERS),
    operation_id="staff_invite_user",
    response={201: StaffUserOut},
)
def invite_staff(request, data: InviteIn):
    return 201, staff.invite(request.auth, request, email=data.email, name=data.name)


@router.post(
    "/staff/{user_id}/deactivate",
    auth=requires(Perm.MANAGE_STAFF_USERS),
    operation_id="staff_deactivate_user",
    response=StaffUserOut,
)
def deactivate_staff(request, user_id: int):
    return staff.deactivate(request.auth, user_id)


@router.get(
    "/audit-log",
    auth=requires(Perm.VIEW_AUDIT_LOG),
    operation_id="audit_log_list",
    response=list[AuditEntryOut],
)
@paginate(PageOffsetPagination)
def list_audit_log(request, params: Query[AuditLogQuery]):
    """Newest first, Owner only."""
    return audit.search(
        actor=params.actor,
        action=params.action,
        date_from=params.date_from,
        date_to=params.date_to,
    )


@router.get(
    "/audit-log/{entry_id}",
    auth=requires(Perm.VIEW_AUDIT_LOG),
    operation_id="audit_log_get",
    response=AuditEntryDetail,
)
def get_audit_entry(request, entry_id: int):
    """One entry with its before and after. Owner only; the list leaves them out."""
    return audit.get(entry_id)
