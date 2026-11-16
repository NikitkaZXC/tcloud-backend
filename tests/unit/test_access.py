import uuid

import pytest

from src.application.exception import ForbiddenError
from src.application.schemas import Actor, GrantServiceRead
from src.application.services.access import (
    allowed,
    can_see_storage,
    leads_to_grant,
    require,
    require_admin,
    require_root,
)
from src.infrastructure.enums import Permission, UserRole

SID = uuid.uuid4()
OTHER = uuid.uuid4()


def grant(folder: str, recursive: bool = True, **rights: bool) -> GrantServiceRead:
    return GrantServiceRead(
        id=uuid.uuid4(),
        storage_id=SID,
        folder=folder,
        path=f"tcloud/{SID}/{folder}",
        recursive=recursive,
        can_read=rights.get("read", True),
        can_upload=rights.get("upload", False),
        can_delete=rights.get("delete", False),
    )


def user(*grants: GrantServiceRead) -> Actor:
    return Actor(
        user_id=uuid.uuid4(), login="u", role=UserRole.USER, grants=list(grants)
    )


def test_recursive_grant_covers_subfolders():
    actor = user(grant("photos"))
    assert allowed(actor, SID, "photos", Permission.READ)
    assert allowed(actor, SID, "photos/2026/may", Permission.READ)
    assert not allowed(actor, SID, "photos-old", Permission.READ)
    assert not allowed(actor, SID, "", Permission.READ)
    assert not allowed(actor, OTHER, "photos", Permission.READ)


def test_folder_only_grant():
    actor = user(grant("docs", recursive=False, delete=True))
    assert allowed(actor, SID, "docs", Permission.READ)
    assert allowed(actor, SID, "docs", Permission.DELETE)
    assert not allowed(actor, SID, "docs/inner", Permission.READ)
    assert not allowed(actor, SID, "docs", Permission.DELETE, subtree=True)


def test_storage_root_grant_covers_everything():
    actor = user(grant(""))
    assert allowed(actor, SID, "", Permission.READ)
    assert allowed(actor, SID, "a/b/c", Permission.READ)


def test_rights_are_separate():
    actor = user(grant("in", upload=True, read=False))
    assert allowed(actor, SID, "in", Permission.UPLOAD)
    assert not allowed(actor, SID, "in", Permission.READ)
    with pytest.raises(ForbiddenError):
        require(actor, SID, "in", Permission.DELETE)


def test_rights_of_several_grants_add_up():
    actor = user(grant("a"), grant("a/b", upload=True))
    assert allowed(actor, SID, "a/b/c", Permission.UPLOAD)
    assert not allowed(actor, SID, "a", Permission.UPLOAD)


def test_ancestors_lead_to_grant():
    actor = user(grant("a/b/c"))
    assert leads_to_grant(actor, SID, "")
    assert leads_to_grant(actor, SID, "a/b")
    assert not leads_to_grant(actor, SID, "a/b/c")
    assert not leads_to_grant(actor, SID, "a/bc")
    assert can_see_storage(actor, SID) and not can_see_storage(actor, OTHER)


def test_admin_and_root():
    admin = Actor(user_id=uuid.uuid4(), login="a", role=UserRole.ADMIN)
    root = Actor(user_id=uuid.uuid4(), login="r", role=UserRole.ROOT)
    assert allowed(admin, SID, "x", Permission.DELETE, subtree=True)
    require_admin(admin)
    require_root(root)
    with pytest.raises(ForbiddenError):
        require_root(admin)
    with pytest.raises(ForbiddenError):
        require_admin(user())
