"""
Saved queries with shortlink aliases for Datasette.

This module provides functionality to save SQL queries with human-readable
shortlink aliases, allowing visitors to run saved queries via short URLs.
Permissions are integrated with the existing canned query permission system.
"""

from __future__ import annotations

import json
import re
import secrets
import string
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from datasette.app import Datasette

from datasette import hookimpl
from datasette.resources import QueryResource

SHORTLINK_CHARS = string.ascii_lowercase + string.ascii_uppercase + string.digits
SHORTLINK_LENGTH = 8
SLUG_PATTERN = re.compile(r"^[^\/\.]+$")


async def _ensure_saved_queries_table(internal_db):
    """Ensure the saved_queries table exists."""
    await internal_db.execute_write_script(
        """
        CREATE TABLE IF NOT EXISTS saved_queries (
            slug TEXT PRIMARY KEY,
            database_name TEXT NOT NULL,
            sql TEXT NOT NULL,
            params TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )


def generate_shortlink(length: int = SHORTLINK_LENGTH) -> str:
    """Generate a random shortlink alias."""
    return "".join(secrets.choice(SHORTLINK_CHARS) for _ in range(length))


async def save_query(
    datasette: "Datasette",
    database_name: str,
    sql: str,
    params: Optional[Dict[str, Any]] = None,
    slug: Optional[str] = None,
) -> str:
    """
    Save a query with a shortlink alias.

    Args:
        datasette: The Datasette instance
        database_name: Name of the database
        sql: SQL query string
        params: Optional query parameters
        slug: Optional custom shortlink alias (auto-generated if not provided)

    Returns:
        The shortlink alias (slug)
    """
    internal_db = datasette.get_internal_database()
    await _ensure_saved_queries_table(internal_db)

    if slug is not None:
        slug = slug.strip()
        if not slug:
            raise ValueError("Shortlink alias cannot be empty")

        if not SLUG_PATTERN.match(slug):
            raise ValueError(
                f"Shortlink alias '{slug}' is invalid. Aliases cannot contain '.' or '/' characters."
            )

        if await _slug_exists(internal_db, slug):
            raise ValueError(f"Shortlink alias '{slug}' already exists")

        if await _canned_query_exists(datasette, database_name, slug):
            raise ValueError(
                f"Shortlink alias '{slug}' conflicts with an existing canned query"
            )
    else:
        slug = generate_shortlink()
        while await _slug_exists(internal_db, slug) or await _canned_query_exists(
            datasette, database_name, slug
        ):
            slug = generate_shortlink()

    params_json = json.dumps(params) if params else None

    await internal_db.execute_write(
        """
        INSERT INTO saved_queries (slug, database_name, sql, params)
        VALUES (?, ?, ?, ?)
        """,
        [slug, database_name, sql, params_json],
    )

    return slug


async def get_saved_query(
    datasette: "Datasette", slug: str
) -> Optional[Dict[str, Any]]:
    """
    Get a saved query by its shortlink alias.

    Args:
        datasette: The Datasette instance
        slug: The shortlink alias

    Returns:
        Dictionary with database_name, sql, and params, or None if not found
    """
    internal_db = datasette.get_internal_database()
    await _ensure_saved_queries_table(internal_db)
    result = await internal_db.execute(
        """
        SELECT slug, database_name, sql, params
        FROM saved_queries
        WHERE slug = ?
        """,
        [slug],
    )

    rows = list(result.rows)
    if not rows:
        return None

    row = rows[0]
    params = json.loads(row["params"]) if row["params"] else None

    return {
        "slug": row["slug"],
        "database_name": row["database_name"],
        "sql": row["sql"],
        "params": params,
    }


async def get_saved_queries(
    datasette: "Datasette", database_name: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Get all saved queries, optionally filtered by database.

    Args:
        datasette: The Datasette instance
        database_name: Optional database name filter

    Returns:
        List of saved query dictionaries
    """
    internal_db = datasette.get_internal_database()
    await _ensure_saved_queries_table(internal_db)

    if database_name:
        result = await internal_db.execute(
            """
            SELECT slug, database_name, sql, params, created_at
            FROM saved_queries
            WHERE database_name = ?
            ORDER BY created_at DESC
            """,
            [database_name],
        )
    else:
        result = await internal_db.execute(
            """
            SELECT slug, database_name, sql, params, created_at
            FROM saved_queries
            ORDER BY created_at DESC
            """
        )

    queries = []
    for row in result.rows:
        params = json.loads(row["params"]) if row["params"] else None
        queries.append(
            {
                "slug": row["slug"],
                "database_name": row["database_name"],
                "sql": row["sql"],
                "params": params,
                "created_at": row["created_at"],
            }
        )

    return queries


async def delete_saved_query(datasette: "Datasette", slug: str) -> bool:
    """
    Delete a saved query.

    Args:
        datasette: The Datasette instance
        slug: The shortlink alias

    Returns:
        True if deleted, False if not found
    """
    internal_db = datasette.get_internal_database()
    await _ensure_saved_queries_table(internal_db)
    result = await internal_db.execute_write(
        "DELETE FROM saved_queries WHERE slug = ?",
        [slug],
    )
    return result.rowcount > 0


async def _slug_exists(internal_db, slug: str) -> bool:
    """Check if a slug already exists."""
    await _ensure_saved_queries_table(internal_db)
    result = await internal_db.execute(
        "SELECT 1 FROM saved_queries WHERE slug = ?",
        [slug],
    )
    return len(list(result.rows)) > 0


async def _canned_query_exists(
    datasette: "Datasette", database_name: str, slug: str
) -> bool:
    """Check if a canned query with the same name exists."""
    canned_queries = await datasette.get_canned_queries(database_name, actor=None)
    return slug in canned_queries


@hookimpl
def canned_queries(datasette: "Datasette", database: str, actor):
    """
    Expose saved queries as canned queries for the permission system.

    This allows saved queries to use the same permission system as
    regular canned queries defined in datasette.yaml.
    """

    async def get_queries():
        queries = {}
        try:
            saved_queries = await get_saved_queries(datasette, database)
            for sq in saved_queries:
                queries[sq["slug"]] = {
                    "name": sq["slug"],
                    "sql": sq["sql"],
                    "params": sq["params"] if sq["params"] else {},
                }
        except Exception:
            pass
        return queries

    return get_queries()
