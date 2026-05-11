from typing import Any, Dict, Tuple
from datasette.utils.asgi import Forbidden
from datasette.permissions import Resource


class ActorContext:
    """
    A context object that encapsulates an actor and provides unified permission checking.

    This class centralizes actor-based permission checks, reducing code duplication
    across views, database operations, and saved queries.

    Usage:
        actor_ctx = ActorContext(datasette, request.actor)
        await actor_ctx.ensure_permission("view-table", resource=TableResource(...))
        can_view = await actor_ctx.allowed("view-table", resource=TableResource(...))
        visible, private = await actor_ctx.check_visibility("view-table", resource=TableResource(...))
    """

    def __init__(self, datasette, actor: Dict[str, Any] | None):
        self.datasette = datasette
        self.actor = actor

    async def allowed(
        self,
        action: str,
        resource: Resource = None,
    ) -> bool:
        """Check if actor can perform action on resource."""
        return await self.datasette.allowed(
            action=action,
            resource=resource,
            actor=self.actor,
        )

    async def ensure_permission(
        self,
        action: str,
        resource: Resource = None,
    ):
        """Ensure actor has permission, raise Forbidden if not."""
        await self.datasette.ensure_permission(
            action=action,
            resource=resource,
            actor=self.actor,
        )

    async def check_visibility(
        self,
        action: str,
        resource: Resource = None,
    ) -> Tuple[bool, bool]:
        """Check visibility and private flag for a resource."""
        return await self.datasette.check_visibility(
            self.actor,
            action=action,
            resource=resource,
        )

    async def allowed_resources(
        self,
        action: str,
        parent: str | None = None,
        include_is_private: bool = False,
        limit: int = 100,
    ):
        """Get all resources the actor can access for the given action."""
        return await self.datasette.allowed_resources(
            action=action,
            actor=self.actor,
            parent=parent,
            include_is_private=include_is_private,
            limit=limit,
        )
