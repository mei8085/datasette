import json
from datetime import datetime
from datasette.utils.asgi import BadRequest, Forbidden, NotFound, Response
from datasette.views.base import BaseView
from datasette.resources import TableResource


class RowCommentsView(BaseView):
    name = "row-comments"

    async def get(self, request):
        try:
            resolved = await self.ds.resolve_table(request)
        except NotFound as e:
            return Response.json({"ok": False, "error": str(e)}, status=404)
        
        database_name = resolved.db.name
        table_name = resolved.table
        
        pk_values = request.url_vars.get("pks")
        if not pk_values:
            return Response.json({"ok": False, "error": "Missing row primary key"}, status=400)
        
        if not await self.ds.allowed(
            action="view-table",
            resource=TableResource(database=database_name, table=table_name),
            actor=request.actor,
        ):
            return Response.json({"ok": False, "error": "Permission denied"}, status=403)
        
        internal_db = self.ds.get_internal_database()
        comments = await internal_db.execute(
            """
            SELECT id, database_name, table_name, pk_values, actor_id, actor_name, 
                   comment_text, created_at, updated_at
            FROM row_comments 
            WHERE database_name = ? AND table_name = ? AND pk_values = ?
            ORDER BY created_at DESC
            """,
            [database_name, table_name, pk_values]
        )
        
        comments_list = []
        for row in comments.rows:
            comments_list.append({
                "id": row["id"],
                "actor_id": row["actor_id"],
                "actor_name": row["actor_name"],
                "comment_text": row["comment_text"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"]
            })
        
        return Response.json({
            "ok": True,
            "database": database_name,
            "table": table_name,
            "pk_values": pk_values,
            "comments": comments_list
        })

    async def post(self, request):
        if request.actor is None:
            return Response.json({"ok": False, "error": "Authentication required"}, status=401)
        
        try:
            resolved = await self.ds.resolve_table(request)
        except NotFound as e:
            return Response.json({"ok": False, "error": str(e)}, status=404)
        
        database_name = resolved.db.name
        table_name = resolved.table
        
        pk_values = request.url_vars.get("pks")
        if not pk_values:
            return Response.json({"ok": False, "error": "Missing row primary key"}, status=400)
        
        if not await self.ds.allowed(
            action="view-table",
            resource=TableResource(database=database_name, table=table_name),
            actor=request.actor,
        ):
            return Response.json({"ok": False, "error": "Permission denied"}, status=403)
        
        content_type = request.headers.get("content-type") or ""
        if not content_type.startswith("application/json"):
            return Response.json({"ok": False, "error": "Content-type must be application/json"}, status=400)
        
        try:
            body = await request.post_body()
            data = json.loads(body)
        except json.JSONDecodeError:
            return Response.json({"ok": False, "error": "Invalid JSON"}, status=400)
        
        comment_text = data.get("comment_text")
        if not comment_text or not comment_text.strip():
            return Response.json({"ok": False, "error": "Comment text is required"}, status=400)
        
        actor_id = request.actor.get("id")
        actor_name = request.actor.get("name") or str(actor_id) if actor_id else "Anonymous"
        
        now = datetime.utcnow().isoformat()
        
        internal_db = self.ds.get_internal_database()
        result = await internal_db.execute_write(
            """
            INSERT INTO row_comments (database_name, table_name, pk_values, actor_id, actor_name, comment_text, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [database_name, table_name, pk_values, actor_id, actor_name, comment_text.strip(), now, now]
        )
        
        comment_id = result.lastrowid
        
        return Response.json({
            "ok": True,
            "comment": {
                "id": comment_id,
                "actor_id": actor_id,
                "actor_name": actor_name,
                "comment_text": comment_text.strip(),
                "created_at": now,
                "updated_at": now
            }
        }, status=201)


class RowCommentDeleteView(BaseView):
    name = "row-comment-delete"

    async def delete(self, request):
        if request.actor is None:
            return Response.json({"ok": False, "error": "Authentication required"}, status=401)
        
        comment_id = request.url_vars.get("comment_id")
        if not comment_id or not comment_id.isdigit():
            return Response.json({"ok": False, "error": "Invalid comment ID"}, status=400)
        
        comment_id = int(comment_id)
        
        internal_db = self.ds.get_internal_database()
        comments = await internal_db.execute(
            """
            SELECT id, database_name, table_name, pk_values, actor_id
            FROM row_comments WHERE id = ?
            """,
            [comment_id]
        )
        
        if len(comments.rows) == 0:
            return Response.json({"ok": False, "error": "Comment not found"}, status=404)
        
        comment = comments.rows[0]
        
        is_owner = comment["actor_id"] == request.actor.get("id")
        
        if not is_owner:
            if not await self.ds.allowed(
                action="delete-row-comment",
                resource=TableResource(database=comment["database_name"], table=comment["table_name"]),
                actor=request.actor,
            ):
                return Response.json({"ok": False, "error": "Permission denied"}, status=403)
        
        await internal_db.execute_write(
            "DELETE FROM row_comments WHERE id = ?",
            [comment_id]
        )
        
        return Response.json({"ok": True, "message": "Comment deleted"})
