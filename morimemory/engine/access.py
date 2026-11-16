"""Backend-enforced memory space permissions.

Two access modes exist:

* ``manage`` — the MoriBot management UI. The local administrator may browse
  their user spaces, their conversations and character spaces.
* ``recall`` — reply-time retrieval. A character only sees its own private
  space, its own conversations, and spaces explicitly granted to that
  character. User-level spaces are never shared implicitly, so two characters
  can never read each other's memories.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import ConfigManager
from ..db.models import Memory, MemorySpace
from ..db.repository import MemoryRepository
from ..domain import AccessMode, MemorySpaceKind, PrincipalRole
from .types import Principal


@dataclass(slots=True)
class AccessScope:
    mode: str
    space_ids: list[str] = field(default_factory=list)
    writable_space_ids: list[str] = field(default_factory=list)
    granted_memory_ids: list[str] = field(default_factory=list)
    spaces: list[MemorySpace] = field(default_factory=list)

    def can_read_space(self, space_id: str) -> bool:
        return space_id in self.space_ids

    def can_write_space(self, space_id: str) -> bool:
        return space_id in self.writable_space_ids

    def can_read_memory(self, memory: Memory) -> bool:
        return memory.space_id in self.space_ids or memory.id in self.granted_memory_ids


class SpaceAccessService:
    def __init__(self, config: ConfigManager) -> None:
        self.config = config

    async def resolve(
        self,
        repository: MemoryRepository,
        principal: Principal,
        mode: str = AccessMode.RECALL,
    ) -> AccessScope:
        is_admin = principal.role == PrincipalRole.ADMIN
        spaces = await repository.list_spaces(statuses=["active"])
        readable: list[MemorySpace] = []
        writable: list[MemorySpace] = []
        granted_memory_ids: list[str] = []

        for space in spaces:
            if mode == AccessMode.MANAGE and is_admin:
                if self._manage_readable(space, principal):
                    readable.append(space)
                    if self._manage_writable(space, principal):
                        writable.append(space)
                continue

            if self._recall_readable(space, principal):
                readable.append(space)
                if self._recall_writable(space, principal):
                    writable.append(space)

        if principal.character_id:
            granted_space_ids = set(await repository.granted_space_ids(principal.character_id))
            for space in spaces:
                if space.id in granted_space_ids and space.id not in {item.id for item in readable}:
                    readable.append(space)
            granted_memory_ids = await repository.granted_memory_ids(principal.character_id)

        return AccessScope(
            mode=mode,
            space_ids=list(dict.fromkeys(space.id for space in readable)),
            writable_space_ids=list(dict.fromkeys(space.id for space in writable)),
            granted_memory_ids=granted_memory_ids,
            spaces=readable,
        )

    def _manage_readable(self, space: MemorySpace, principal: Principal) -> bool:
        if space.kind == MemorySpaceKind.USER:
            return space.user_id == principal.user_id
        if space.kind == MemorySpaceKind.CONVERSATION:
            return (
                space.conversation_id == principal.conversation_id
                or space.user_id == principal.user_id
                or bool(space.user_id is None and principal.role == PrincipalRole.ADMIN)
            )
        if space.kind == MemorySpaceKind.CHARACTER:
            if not bool(self.config.get("security.allow_character_management", True)):
                return False
            return True
        return False

    def _manage_writable(self, space: MemorySpace, principal: Principal) -> bool:
        if space.kind == MemorySpaceKind.USER:
            return space.user_id == principal.user_id
        if space.kind == MemorySpaceKind.CONVERSATION:
            return (
                space.user_id == principal.user_id
                or space.conversation_id == principal.conversation_id
            )
        if space.kind == MemorySpaceKind.CHARACTER:
            return bool(self.config.get("security.allow_character_management", True))
        return False

    def _recall_readable(self, space: MemorySpace, principal: Principal) -> bool:
        if space.kind == MemorySpaceKind.USER:
            if space.user_id != principal.user_id:
                return False
            # A character conversation must not read the shared user space:
            # doing so would leak facts learned by one character into another.
            # Explicit grants are resolved separately in ``resolve``.
            return principal.character_id is None
        if space.kind == MemorySpaceKind.CONVERSATION:
            if principal.conversation_id and space.conversation_id == principal.conversation_id:
                return True
            # The same character may recall events from its own past
            # conversations, but never from another character's conversations.
            return bool(principal.character_id) and space.character_id == principal.character_id
        if space.kind == MemorySpaceKind.CHARACTER:
            return bool(principal.character_id) and space.character_id == principal.character_id
        return False

    def _recall_writable(self, space: MemorySpace, principal: Principal) -> bool:
        return self._recall_readable(space, principal)

    async def require_readable_memory(
        self,
        repository: MemoryRepository,
        principal: Principal,
        memory_id: str,
        mode: str = AccessMode.RECALL,
    ) -> tuple[Memory | None, AccessScope]:
        scope = await self.resolve(repository, principal, mode)
        memory = await repository.get_memory(memory_id)
        if memory is None or not scope.can_read_memory(memory):
            return None, scope
        return memory, scope

    @staticmethod
    def describe(scope: AccessScope) -> dict:
        return {
            "mode": scope.mode,
            "space_ids": scope.space_ids,
            "writable_space_ids": scope.writable_space_ids,
            "granted_memory_ids": scope.granted_memory_ids,
            "space_count": len(scope.space_ids),
        }
