from uuid import UUID

from src.application.exception import ForbiddenError
from src.application.schemas import Actor, GrantServiceRead
from src.infrastructure.enums import Permission, UserRole


def covers(
    grant: GrantServiceRead, storage_id: UUID, folder: str, subtree: bool
) -> bool:
    if grant.storage_id != storage_id:
        return False
    if folder == grant.folder:
        return grant.recursive or not subtree
    return grant.recursive and (
        grant.folder == "" or folder.startswith(grant.folder + "/")
    )


def allowed(
    actor: Actor,
    storage_id: UUID,
    folder: str,
    permission: Permission,
    subtree: bool = False,
) -> bool:
    if actor.is_admin:
        return True
    return any(
        covers(grant, storage_id, folder, subtree)
        and getattr(grant, f"can_{permission}")
        for grant in actor.grants
    )


def leads_to_grant(actor: Actor, storage_id: UUID, folder: str) -> bool:
    return any(
        grant.storage_id == storage_id
        and grant.can_read
        and (folder == "" or grant.folder.startswith(folder + "/"))
        and grant.folder != folder
        for grant in actor.grants
    )


def require(
    actor: Actor,
    storage_id: UUID,
    folder: str,
    permission: Permission,
    subtree: bool = False,
) -> None:
    if not allowed(actor, storage_id, folder, permission, subtree):
        raise ForbiddenError(f"no {permission} access to this folder")


def can_see_storage(actor: Actor, storage_id: UUID) -> bool:
    return actor.is_admin or any(
        grant.storage_id == storage_id for grant in actor.grants
    )


def require_admin(actor: Actor) -> None:
    if not actor.is_admin:
        raise ForbiddenError("only admin and root can do this")


def require_root(actor: Actor) -> None:
    if actor.role != UserRole.ROOT:
        raise ForbiddenError("only root can do this")
