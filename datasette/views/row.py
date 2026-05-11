from datasette.utils.asgi import NotFound, Forbidden, Response
from datasette.database import QueryInterrupted
from datasette.events import UpdateRowEvent, DeleteRowEvent
from datasette.resources import TableResource
from .base import DataView, BaseView, _error
from datasette.utils import (
    await_me_maybe,
    CustomRow,
    make_slot_function,
    to_css_class,
    escape_sqlite,
    path_from_row_pks,
)
from datasette.plugins import pm
from datetime import datetime, timezone
import json
import markupsafe
import sqlite_utils
from .table import display_columns_and_rows, _get_extras


async def save_row_history(datasette, database_name, table_name, pk_values, row_dict, actor):
    internal_db = datasette.get_internal_database()
    pk_values_str = json.dumps(pk_values, default=str)
    row_data_str = json.dumps(row_dict, default=str)
    created_at = datetime.now(timezone.utc).isoformat()
    actor_str = json.dumps(actor, default=str) if actor else None
    await internal_db.execute_write(
        """
        INSERT INTO row_history (database_name, table_name, pk_values, row_data, created_at, actor)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [database_name, table_name, pk_values_str, row_data_str, created_at, actor_str],
    )


async def get_row_history(datasette, database_name, table_name, pk_values, limit=100):
    internal_db = datasette.get_internal_database()
    pk_values_str = json.dumps(pk_values, default=str)
    results = await internal_db.execute(
        """
        SELECT id, row_data, created_at, actor FROM row_history
        WHERE database_name = ? AND table_name = ? AND pk_values = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        [database_name, table_name, pk_values_str, limit],
    )
    history = []
    for row in results.rows:
        history.append(
            {
                "id": row["id"],
                "row_data": json.loads(row["row_data"]),
                "created_at": row["created_at"],
                "actor": json.loads(row["actor"]) if row["actor"] else None,
            }
        )
    return history


async def get_history_version(datasette, history_id):
    internal_db = datasette.get_internal_database()
    results = await internal_db.execute(
        "SELECT database_name, table_name, pk_values, row_data, created_at, actor FROM row_history WHERE id = ?",
        [history_id],
    )
    rows = results.rows
    if not rows:
        return None
    row = rows[0]
    return {
        "database_name": row["database_name"],
        "table_name": row["table_name"],
        "pk_values": json.loads(row["pk_values"]),
        "row_data": json.loads(row["row_data"]),
        "created_at": row["created_at"],
        "actor": json.loads(row["actor"]) if row["actor"] else None,
    }


def compare_rows(old_row, new_row):
    diff = {"added": {}, "removed": {}, "changed": {}, "unchanged": {}}
    all_keys = set(old_row.keys()) | set(new_row.keys())
    for key in all_keys:
        old_val = old_row.get(key)
        new_val = new_row.get(key)
        if key not in old_row:
            diff["added"][key] = new_val
        elif key not in new_row:
            diff["removed"][key] = old_val
        elif old_val != new_val:
            diff["changed"][key] = {"old": old_val, "new": new_val}
        else:
            diff["unchanged"][key] = old_val
    return diff


class RowView(DataView):
    name = "row"

    async def data(self, request, default_labels=False):
        resolved = await self.ds.resolve_row(request)
        db = resolved.db
        database = db.name
        table = resolved.table
        pk_values = resolved.pk_values

        # Ensure user has permission to view this row
        visible, private = await self.ds.check_visibility(
            request.actor,
            action="view-table",
            resource=TableResource(database=database, table=table),
        )
        if not visible:
            raise Forbidden("You do not have permission to view this table")

        results = await resolved.db.execute(
            resolved.sql, resolved.params, truncate=True
        )
        columns = [r[0] for r in results.description]
        rows = list(results.rows)
        if not rows:
            raise NotFound(f"Record not found: {pk_values}")

        pks = resolved.pks

        async def template_data():
            # Reorder columns so primary keys come first
            pk_set = set(pks)
            pk_cols = [d for d in results.description if d[0] in pk_set]
            non_pk_cols = [d for d in results.description if d[0] not in pk_set]
            reordered_description = pk_cols + non_pk_cols
            reordered_columns = [d[0] for d in reordered_description]

            # Reorder row data to match
            reordered_rows = []
            for row in rows:
                new_row = CustomRow(reordered_columns)
                for col in reordered_columns:
                    new_row[col] = row[col]
                reordered_rows.append(new_row)

            # Expand foreign key columns into dicts so display_columns_and_rows
            # renders them as hyperlinks, matching the table view behavior
            expanded_rows = reordered_rows
            for fk in await db.foreign_keys_for_table(table):
                column = fk["column"]
                if column not in reordered_columns:
                    continue
                column_index = reordered_columns.index(column)
                values = [row[column_index] for row in expanded_rows]
                expanded_labels = await self.ds.expand_foreign_keys(
                    request.actor, database, table, column, values
                )
                if expanded_labels:
                    new_rows = []
                    for row in expanded_rows:
                        new_row = CustomRow(reordered_columns)
                        for col in reordered_columns:
                            value = row[col]
                            if (
                                col == column
                                and (col, value) in expanded_labels
                                and value is not None
                            ):
                                new_row[col] = {
                                    "value": value,
                                    "label": expanded_labels[(col, value)],
                                }
                            else:
                                new_row[col] = value
                        new_rows.append(new_row)
                    expanded_rows = new_rows

            display_columns, display_rows = await display_columns_and_rows(
                self.ds,
                database,
                table,
                reordered_description,
                expanded_rows,
                link_column=False,
                truncate_cells=0,
                request=request,
            )
            for column in display_columns:
                column["sortable"] = False

            # Bold primary key cell values
            for row in display_rows:
                for cell in row:
                    if cell["column"] in pk_set:
                        cell["value"] = markupsafe.Markup(
                            "<strong>{}</strong>".format(cell["value"])
                        )

            row_actions = []
            for hook in pm.hook.row_actions(
                datasette=self.ds,
                actor=request.actor,
                request=request,
                database=database,
                table=table,
                row=rows[0],
            ):
                extra_links = await await_me_maybe(hook)
                if extra_links:
                    row_actions.extend(extra_links)

            row_dict = dict(rows[0])
            history = await get_row_history(
                self.ds, database, table, pk_values
            )
            original_pks = await db.primary_keys(table)
            use_rowid = not original_pks
            pk_path = path_from_row_pks(rows[0], original_pks, use_rowid)
            return {
                "private": private,
                "columns": reordered_columns,
                "foreign_key_tables": await self.foreign_key_tables(
                    database, table, pk_values
                ),
                "database_color": db.color,
                "display_columns": display_columns,
                "display_rows": display_rows,
                "custom_table_templates": [
                    f"_table-{to_css_class(database)}-{to_css_class(table)}.html",
                    f"_table-row-{to_css_class(database)}-{to_css_class(table)}.html",
                    "_table.html",
                ],
                "row_actions": row_actions,
                "top_row": make_slot_function(
                    "top_row",
                    self.ds,
                    request,
                    database=resolved.db.name,
                    table=resolved.table,
                    row=rows[0],
                ),
                "metadata": {},
                "row_dict": row_dict,
                "row_history": history,
                "current_row": row_dict,
                "pk_path": pk_path,
            }

        data = {
            "ok": True,
            "database": database,
            "table": table,
            "rows": rows,
            "columns": columns,
            "primary_keys": resolved.pks,
            "primary_key_values": pk_values,
        }

        # Handle _extra parameter (new style)
        extras = _get_extras(request)

        # Also support legacy _extras parameter for backward compatibility
        if "foreign_key_tables" in (request.args.get("_extras") or "").split(","):
            extras.add("foreign_key_tables")

        # Process extras
        if "foreign_key_tables" in extras:
            data["foreign_key_tables"] = await self.foreign_key_tables(
                database, table, pk_values
            )

        if "render_cell" in extras:
            # Call render_cell plugin hook for each cell
            ct_map = await self.ds.get_column_types(database, table)
            rendered_rows = []
            for row in rows:
                rendered_row = {}
                for value, column in zip(row, columns):
                    ct = ct_map.get(column)
                    plugin_display_value = None
                    # Try column type render_cell first
                    if ct:
                        candidate = await ct.render_cell(
                            value=value,
                            column=column,
                            table=table,
                            database=database,
                            datasette=self.ds,
                            request=request,
                        )
                        if candidate is not None:
                            plugin_display_value = candidate
                    if plugin_display_value is None:
                        for candidate in pm.hook.render_cell(
                            row=row,
                            value=value,
                            column=column,
                            table=table,
                            pks=resolved.pks,
                            database=database,
                            datasette=self.ds,
                            request=request,
                            column_type=ct,
                        ):
                            candidate = await await_me_maybe(candidate)
                            if candidate is not None:
                                plugin_display_value = candidate
                                break
                    if plugin_display_value:
                        rendered_row[column] = str(plugin_display_value)
                rendered_rows.append(rendered_row)
            data["render_cell"] = rendered_rows

        return (
            data,
            template_data,
            (
                f"row-{to_css_class(database)}-{to_css_class(table)}.html",
                "row.html",
            ),
        )

    async def foreign_key_tables(self, database, table, pk_values):
        if len(pk_values) != 1:
            return []
        db = self.ds.databases[database]
        all_foreign_keys = await db.get_all_foreign_keys()
        foreign_keys = all_foreign_keys[table]["incoming"]
        if len(foreign_keys) == 0:
            return []

        sql = "select " + ", ".join(
            [
                "(select count(*) from {table} where {column}=:id)".format(
                    table=escape_sqlite(fk["other_table"]),
                    column=escape_sqlite(fk["other_column"]),
                )
                for fk in foreign_keys
            ]
        )
        try:
            rows = list(await db.execute(sql, {"id": pk_values[0]}))
        except QueryInterrupted:
            # Almost certainly hit the timeout
            return []

        foreign_table_counts = dict(
            zip(
                [(fk["other_table"], fk["other_column"]) for fk in foreign_keys],
                list(rows[0]),
            )
        )
        foreign_key_tables = []
        for fk in foreign_keys:
            count = (
                foreign_table_counts.get((fk["other_table"], fk["other_column"])) or 0
            )
            key = fk["other_column"]
            if key.startswith("_"):
                key += "__exact"
            link = "{}?{}={}".format(
                self.ds.urls.table(database, fk["other_table"]),
                key,
                ",".join(pk_values),
            )
            foreign_key_tables.append({**fk, **{"count": count, "link": link}})
        return foreign_key_tables


class RowError(Exception):
    def __init__(self, error):
        self.error = error


async def _resolve_row_and_check_permission(datasette, request, permission):
    from datasette.app import DatabaseNotFound, TableNotFound, RowNotFound

    try:
        resolved = await datasette.resolve_row(request)
    except DatabaseNotFound as e:
        return False, _error(["Database not found: {}".format(e.database_name)], 404)
    except TableNotFound as e:
        return False, _error(["Table not found: {}".format(e.table)], 404)
    except RowNotFound as e:
        return False, _error(["Record not found: {}".format(e.pk_values)], 404)

    # Ensure user has permission to delete this row
    if not await datasette.allowed(
        action=permission,
        resource=TableResource(database=resolved.db.name, table=resolved.table),
        actor=request.actor,
    ):
        return False, _error(["Permission denied"], 403)

    return True, resolved


class RowDeleteView(BaseView):
    name = "row-delete"

    def __init__(self, datasette):
        self.ds = datasette

    async def post(self, request):
        ok, resolved = await _resolve_row_and_check_permission(
            self.ds, request, "delete-row"
        )
        if not ok:
            return resolved

        # Delete table
        def delete_row(conn):
            sqlite_utils.Database(conn)[resolved.table].delete(resolved.pk_values)

        try:
            await resolved.db.execute_write_fn(delete_row, request=request)
        except Exception as e:
            return _error([str(e)], 500)

        await self.ds.track_event(
            DeleteRowEvent(
                actor=request.actor,
                database=resolved.db.name,
                table=resolved.table,
                pks=resolved.pk_values,
            )
        )

        return Response.json({"ok": True}, status=200)


class RowUpdateView(BaseView):
    name = "row-update"

    def __init__(self, datasette):
        self.ds = datasette

    async def post(self, request):
        ok, resolved = await _resolve_row_and_check_permission(
            self.ds, request, "update-row"
        )
        if not ok:
            return resolved

        body = await request.post_body()
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            return _error(["Invalid JSON: {}".format(e)])

        if not isinstance(data, dict):
            return _error(["JSON must be a dictionary"])
        if "update" not in data or not isinstance(data["update"], dict):
            return _error(["JSON must contain an update dictionary"])

        invalid_keys = set(data.keys()) - {"update", "return", "alter"}
        if invalid_keys:
            return _error(["Invalid keys: {}".format(", ".join(invalid_keys))])

        update = data["update"]

        # Validate column types
        from datasette.views.table import _validate_column_types

        ct_errors = await _validate_column_types(
            self.ds, resolved.db.name, resolved.table, [update]
        )
        if ct_errors:
            return _error(ct_errors, 400)

        alter = data.get("alter")
        if alter and not await self.ds.allowed(
            action="alter-table",
            resource=TableResource(database=resolved.db.name, table=resolved.table),
            actor=request.actor,
        ):
            return _error(["Permission denied for alter-table"], 403)

        # Save current state to history before updating
        current_results = await resolved.db.execute(
            resolved.sql, resolved.params, truncate=True
        )
        current_row = current_results.dicts()[0]
        await save_row_history(
            self.ds,
            resolved.db.name,
            resolved.table,
            resolved.pk_values,
            current_row,
            request.actor,
        )

        def update_row(conn):
            sqlite_utils.Database(conn)[resolved.table].update(
                resolved.pk_values, update, alter=alter
            )

        try:
            await resolved.db.execute_write_fn(update_row, request=request)
        except Exception as e:
            return _error([str(e)], 400)

        result = {"ok": True}
        if data.get("return"):
            results = await resolved.db.execute(
                resolved.sql, resolved.params, truncate=True
            )
            result["row"] = results.dicts()[0]

        await self.ds.track_event(
            UpdateRowEvent(
                actor=request.actor,
                database=resolved.db.name,
                table=resolved.table,
                pks=resolved.pk_values,
            )
        )

        return Response.json(result, status=200)


class RowHistoryView(BaseView):
    name = "row-history"

    def __init__(self, datasette):
        self.ds = datasette

    async def get(self, request):
        ok, resolved = await _resolve_row_and_check_permission(
            self.ds, request, "view-table"
        )
        if not ok:
            return resolved

        history = await get_row_history(
            self.ds, resolved.db.name, resolved.table, resolved.pk_values
        )

        # Get current row
        current_results = await resolved.db.execute(
            resolved.sql, resolved.params, truncate=True
        )
        current_row = current_results.dicts()[0]

        result = {
            "ok": True,
            "database": resolved.db.name,
            "table": resolved.table,
            "primary_keys": resolved.pks,
            "primary_key_values": resolved.pk_values,
            "current_row": current_row,
            "history": history,
        }
        return Response.json(result, status=200)


class RowHistoryDiffView(BaseView):
    name = "row-history-diff"

    def __init__(self, datasette):
        self.ds = datasette

    async def get(self, request):
        ok, resolved = await _resolve_row_and_check_permission(
            self.ds, request, "view-table"
        )
        if not ok:
            return resolved

        history_id = request.args.get("history_id")
        if not history_id:
            return _error(["history_id is required"], 400)

        try:
            history_id = int(history_id)
        except ValueError:
            return _error(["history_id must be an integer"], 400)

        version = await get_history_version(self.ds, history_id)
        if not version:
            return _error(["History version not found"], 404)

        # Verify this version belongs to the same row
        if (
            version["database_name"] != resolved.db.name
            or version["table_name"] != resolved.table
            or version["pk_values"] != resolved.pk_values
        ):
            return _error(["History version does not match this row"], 400)

        # Get current row
        current_results = await resolved.db.execute(
            resolved.sql, resolved.params, truncate=True
        )
        current_row = current_results.dicts()[0]

        # Compare with previous version or current
        history = await get_row_history(
            self.ds, resolved.db.name, resolved.table, resolved.pk_values
        )
        previous_version = None
        for i, h in enumerate(history):
            if h["id"] == history_id:
                if i + 1 < len(history):
                    previous_version = history[i + 1]
                break

        if previous_version:
            diff = compare_rows(previous_version["row_data"], version["row_data"])
            comparison_with = previous_version
        else:
            diff = compare_rows(version["row_data"], current_row)
            comparison_with = {
                "id": None,
                "row_data": current_row,
                "created_at": None,
                "actor": None,
            }

        result = {
            "ok": True,
            "database": resolved.db.name,
            "table": resolved.table,
            "primary_keys": resolved.pks,
            "primary_key_values": resolved.pk_values,
            "current_row": current_row,
            "version": version,
            "comparison_with": comparison_with,
            "diff": diff,
        }
        return Response.json(result, status=200)


class RowRevertView(BaseView):
    name = "row-revert"

    def __init__(self, datasette):
        self.ds = datasette

    async def post(self, request):
        ok, resolved = await _resolve_row_and_check_permission(
            self.ds, request, "update-row"
        )
        if not ok:
            return resolved

        body = await request.post_body()
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            return _error(["Invalid JSON: {}".format(e)])

        if "history_id" not in data:
            return _error(["history_id is required"], 400)

        try:
            history_id = int(data["history_id"])
        except ValueError:
            return _error(["history_id must be an integer"], 400)

        version = await get_history_version(self.ds, history_id)
        if not version:
            return _error(["History version not found"], 404)

        # Verify this version belongs to the same row
        if (
            version["database_name"] != resolved.db.name
            or version["table_name"] != resolved.table
            or version["pk_values"] != resolved.pk_values
        ):
            return _error(["History version does not match this row"], 400)

        # Save current state before reverting
        current_results = await resolved.db.execute(
            resolved.sql, resolved.params, truncate=True
        )
        current_row = current_results.dicts()[0]
        await save_row_history(
            self.ds,
            resolved.db.name,
            resolved.table,
            resolved.pk_values,
            current_row,
            request.actor,
        )

        # Prepare update data (exclude primary keys as they shouldn't change)
        pks = set(resolved.pks)
        update_data = {
            k: v for k, v in version["row_data"].items() if k not in pks
        }

        def update_row(conn):
            sqlite_utils.Database(conn)[resolved.table].update(
                resolved.pk_values, update_data
            )

        try:
            await resolved.db.execute_write_fn(update_row, request=request)
        except Exception as e:
            return _error([str(e)], 400)

        result = {"ok": True}
        if data.get("return"):
            results = await resolved.db.execute(
                resolved.sql, resolved.params, truncate=True
            )
            result["row"] = results.dicts()[0]

        await self.ds.track_event(
            UpdateRowEvent(
                actor=request.actor,
                database=resolved.db.name,
                table=resolved.table,
                pks=resolved.pk_values,
            )
        )

        return Response.json(result, status=200)
