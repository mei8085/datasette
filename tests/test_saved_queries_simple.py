"""Simplified tests for saved queries with shortlink aliases."""

import pytest
import asyncio
from datasette.app import Datasette


@pytest.mark.asyncio
async def test_save_query_basic():
    """Test basic query saving functionality."""
    ds = Datasette([])
    from datasette.default_permissions import save_query, get_saved_query
    
    slug = await save_query(ds, "test_db", "SELECT 1 as result")
    assert slug is not None
    
    saved = await get_saved_query(ds, slug)
    assert saved is not None
    assert saved["sql"] == "SELECT 1 as result"


@pytest.mark.asyncio
async def test_save_query_with_custom_slug():
    """Test saving a query with a custom slug."""
    ds = Datasette([])
    from datasette.default_permissions import save_query, get_saved_query
    
    slug = await save_query(ds, "test_db", "SELECT 1", slug="my_custom_query")
    assert slug == "my_custom_query"
    
    saved = await get_saved_query(ds, "my_custom_query")
    assert saved is not None


@pytest.mark.asyncio
async def test_save_query_duplicate_slug():
    """Test that duplicate slugs are rejected."""
    ds = Datasette([])
    from datasette.default_permissions import save_query
    
    await save_query(ds, "test_db", "SELECT 1", slug="duplicate_test")
    
    with pytest.raises(ValueError, match="already exists"):
        await save_query(ds, "test_db", "SELECT 2", slug="duplicate_test")


@pytest.mark.asyncio
async def test_get_saved_query_not_found():
    """Test that retrieving a non-existent query returns None."""
    ds = Datasette([])
    from datasette.default_permissions import get_saved_query
    
    saved = await get_saved_query(ds, "nonexistent")
    assert saved is None


@pytest.mark.asyncio
async def test_get_all_saved_queries():
    """Test retrieving all saved queries."""
    ds = Datasette([])
    from datasette.default_permissions import save_query, get_saved_queries
    
    await save_query(ds, "test_db", "SELECT 1", slug="query1")
    await save_query(ds, "test_db", "SELECT 2", slug="query2")
    
    all_queries = await get_saved_queries(ds, "test_db")
    assert len(all_queries) == 2
    slugs = {q["slug"] for q in all_queries}
    assert "query1" in slugs
    assert "query2" in slugs


@pytest.mark.asyncio
async def test_delete_saved_query():
    """Test deleting a saved query."""
    ds = Datasette([])
    from datasette.default_permissions import save_query, get_saved_query, delete_saved_query
    
    await save_query(ds, "test_db", "SELECT 1", slug="delete_test")
    
    saved = await get_saved_query(ds, "delete_test")
    assert saved is not None
    
    deleted = await delete_saved_query(ds, "delete_test")
    assert deleted is True
    
    saved = await get_saved_query(ds, "delete_test")
    assert saved is None


@pytest.mark.asyncio
async def test_delete_nonexistent_saved_query():
    """Test that deleting a non-existent query returns False."""
    ds = Datasette([])
    from datasette.default_permissions import delete_saved_query
    
    deleted = await delete_saved_query(ds, "nonexistent")
    assert deleted is False


@pytest.mark.asyncio
async def test_canned_queries_integration():
    """Test that saved queries are exposed as canned queries."""
    ds = Datasette([])
    from datasette.default_permissions import save_query
    
    await save_query(ds, "test_db", "SELECT 1 as result", slug="canned_test")
    
    # Check if it's in canned queries
    canned = await ds.get_canned_queries("test_db", actor=None)
    assert "canned_test" in canned
    assert canned["canned_test"]["sql"] == "SELECT 1 as result"


@pytest.mark.asyncio
async def test_table_auto_creation():
    """Test that the table is created automatically when needed."""
    ds = Datasette([])
    from datasette.default_permissions import get_saved_query, get_saved_queries, delete_saved_query
    
    # These should not raise errors even if table doesn't exist
    saved = await get_saved_query(ds, "nonexistent")
    assert saved is None
    
    all_queries = await get_saved_queries(ds)
    assert all_queries == []
    
    deleted = await delete_saved_query(ds, "nonexistent")
    assert deleted is False
