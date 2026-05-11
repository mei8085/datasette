"""Tests for saved queries with shortlink aliases."""

import pytest
import pytest_asyncio
from datasette.app import Datasette
from datasette.database import Database
import secrets


@pytest_asyncio.fixture
async def saved_queries_ds():
    """Create a Datasette instance with a test database for saved queries tests."""
    from .fixtures import CONFIG, METADATA, PLUGINS_DIR

    ds = Datasette(
        metadata=METADATA,
        config=CONFIG,
        plugins_dir=PLUGINS_DIR,
        settings={
            "default_page_size": 50,
            "max_returned_rows": 100,
            "sql_time_limit_ms": 200,
            "facet_suggest_time_limit_ms": 200,
            "num_sql_threads": 1,
        },
    )

    # Create a test database with items table
    unique_memory_name = f"test_{secrets.token_hex(8)}"
    db = ds.add_database(Database(ds, memory_name=unique_memory_name), name="test")
    ds.remove_database("_memory")

    def prepare(conn):
        conn.execute("create table items (id int, name text)")
        conn.execute("insert into items values (1, 'one')")
        conn.execute("insert into items values (2, 'two')")

    await db.execute_write_fn(prepare)
    await ds.invoke_startup()
    return ds


@pytest.mark.asyncio
async def test_save_query_basic(saved_queries_ds):
    """Test basic query saving functionality."""
    client = saved_queries_ds.client

    response = await client.post(
        "/test/-/save-query",
        data={"sql": "select * from items"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "slug" in data
    assert "url" in data
    assert data["url"].startswith("/-/q/")


@pytest.mark.asyncio
async def test_save_query_with_custom_slug(saved_queries_ds):
    """Test saving a query with a custom slug."""
    client = saved_queries_ds.client

    response = await client.post(
        "/test/-/save-query",
        data={"sql": "select * from items", "slug": "my_custom_query"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["slug"] == "my_custom_query"
    assert data["url"] == "/-/q/my_custom_query"


@pytest.mark.asyncio
async def test_save_query_duplicate_slug(saved_queries_ds):
    """Test that duplicate slugs are rejected."""
    client = saved_queries_ds.client

    # First save
    await client.post(
        "/test/-/save-query",
        data={"sql": "select * from items", "slug": "duplicate_test"},
    )

    # Second save with same slug
    response = await client.post(
        "/test/-/save-query",
        data={"sql": "select * from items", "slug": "duplicate_test"},
    )
    assert response.status_code == 400
    data = response.json()
    assert data["ok"] is False
    assert "already exists" in data["error"]


@pytest.mark.asyncio
async def test_save_query_missing_sql(saved_queries_ds):
    """Test that missing SQL returns an error."""
    client = saved_queries_ds.client

    response = await client.post(
        "/test/-/save-query",
        data={"sql": ""},
    )
    assert response.status_code == 400
    data = response.json()
    assert data["ok"] is False


@pytest.mark.asyncio
async def test_shortlink_redirect(saved_queries_ds):
    """Test that shortlink redirects to the canned query path."""
    client = saved_queries_ds.client

    save_response = await client.post(
        "/test/-/save-query",
        data={"sql": "select * from items", "slug": "redirect_test"},
    )
    assert save_response.status_code == 200
    slug = save_response.json()["slug"]

    response = await client.get(f"/-/q/{slug}")
    assert response.status_code == 302
    assert response.headers["Location"] == f"/test/{slug}"


@pytest.mark.asyncio
async def test_shortlink_not_found(saved_queries_ds):
    """Test that non-existent shortlinks return 404."""
    client = saved_queries_ds.client

    response = await client.get("/-/q/nonexistent_slug")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_saved_query_as_canned_query(saved_queries_ds):
    """Test that saved queries are accessible as canned queries."""
    client = saved_queries_ds.client

    await client.post(
        "/test/-/save-query",
        data={"sql": "select 1 as result", "slug": "canned_test"},
    )

    response = await client.get("/test/canned_test.json")
    assert response.status_code == 200
    data = response.json()
    assert "rows" in data
    assert len(data["rows"]) == 1


@pytest.mark.asyncio
async def test_saved_query_permissions(saved_queries_ds):
    """Test that saved queries use the same permission system as canned queries."""
    client = saved_queries_ds.client

    await client.post(
        "/test/-/save-query",
        data={"sql": "select 1 as result", "slug": "perm_test"},
    )

    response = await client.get("/test/perm_test")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_saved_query_by_slug(saved_queries_ds):
    """Test retrieving a saved query by slug."""
    from datasette.default_permissions import get_saved_query

    client = saved_queries_ds.client

    await client.post(
        "/test/-/save-query",
        data={"sql": "select * from items", "slug": "get_test"},
    )

    saved_query = await get_saved_query(saved_queries_ds, "get_test")
    assert saved_query is not None
    assert saved_query["slug"] == "get_test"
    assert saved_query["database_name"] == "test"
    assert saved_query["sql"] == "select * from items"


@pytest.mark.asyncio
async def test_get_nonexistent_saved_query(saved_queries_ds):
    """Test that retrieving a non-existent query returns None."""
    from datasette.default_permissions import get_saved_query

    saved_query = await get_saved_query(saved_queries_ds, "nonexistent")
    assert saved_query is None


@pytest.mark.asyncio
async def test_get_all_saved_queries(saved_queries_ds):
    """Test retrieving all saved queries."""
    from datasette.default_permissions import get_saved_queries

    client = saved_queries_ds.client

    await client.post(
        "/test/-/save-query",
        data={"sql": "select 1", "slug": "query1"},
    )
    await client.post(
        "/test/-/save-query",
        data={"sql": "select 2", "slug": "query2"},
    )

    all_queries = await get_saved_queries(saved_queries_ds, "test")
    assert len(all_queries) == 2
    slugs = {q["slug"] for q in all_queries}
    assert "query1" in slugs
    assert "query2" in slugs


@pytest.mark.asyncio
async def test_delete_saved_query(saved_queries_ds):
    """Test deleting a saved query."""
    from datasette.default_permissions import delete_saved_query, get_saved_query

    client = saved_queries_ds.client

    await client.post(
        "/test/-/save-query",
        data={"sql": "select 1", "slug": "delete_test"},
    )

    saved_query = await get_saved_query(saved_queries_ds, "delete_test")
    assert saved_query is not None

    deleted = await delete_saved_query(saved_queries_ds, "delete_test")
    assert deleted is True

    saved_query = await get_saved_query(saved_queries_ds, "delete_test")
    assert saved_query is None


@pytest.mark.asyncio
async def test_delete_nonexistent_saved_query(saved_queries_ds):
    """Test that deleting a non-existent query returns False."""
    from datasette.default_permissions import delete_saved_query

    deleted = await delete_saved_query(saved_queries_ds, "nonexistent")
    assert deleted is False


# New tests for slug validation and canned query conflict
@pytest.mark.asyncio
async def test_save_query_invalid_slug_dot(saved_queries_ds):
    """Test that slugs with dots are rejected."""
    client = saved_queries_ds.client

    response = await client.post(
        "/test/-/save-query",
        data={"sql": "select 1", "slug": "invalid.slug"},
    )
    assert response.status_code == 400
    data = response.json()
    assert data["ok"] is False
    assert "invalid" in data["error"].lower()


@pytest.mark.asyncio
async def test_save_query_invalid_slug_slash(saved_queries_ds):
    """Test that slugs with slashes are rejected."""
    client = saved_queries_ds.client

    response = await client.post(
        "/test/-/save-query",
        data={"sql": "select 1", "slug": "invalid/slug"},
    )
    assert response.status_code == 400
    data = response.json()
    assert data["ok"] is False
    assert "invalid" in data["error"].lower()


@pytest.mark.asyncio
async def test_save_query_empty_slug(saved_queries_ds):
    """Test that empty slugs are rejected."""
    client = saved_queries_ds.client

    response = await client.post(
        "/test/-/save-query",
        data={"sql": "select 1", "slug": ""},
    )
    # Empty slug should be treated as no slug, which will auto-generate
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True


@pytest.mark.asyncio
async def test_save_query_whitespace_slug(saved_queries_ds):
    """Test that whitespace-only slugs are rejected."""
    client = saved_queries_ds.client

    response = await client.post(
        "/test/-/save-query",
        data={"sql": "select 1", "slug": "   "},
    )
    # Whitespace slug should be trimmed and treated as empty, which will auto-generate
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True


@pytest.mark.asyncio
async def test_save_query_valid_slugs(saved_queries_ds):
    """Test that valid slugs are accepted."""
    client = saved_queries_ds.client

    valid_slugs = [
        "simple",
        "with-dash",
        "with_underscore",
        "mixedCase123",
        "query-1",
        "test_2",
    ]

    for slug in valid_slugs:
        response = await client.post(
            "/test/-/save-query",
            data={"sql": f"select '{slug}'", "slug": slug},
        )
        assert response.status_code == 200, f"Slug '{slug}' should be valid"
        data = response.json()
        assert data["ok"] is True
        assert data["slug"] == slug


@pytest.mark.asyncio
async def test_save_query_canned_query_conflict():
    """Test that saving a query with a name that conflicts with an existing canned query is rejected."""
    from .fixtures import CONFIG, METADATA, PLUGINS_DIR

    # Create a Datasette instance with a canned query defined in config
    ds = Datasette(
        metadata=METADATA,
        config={
            "databases": {
                "_memory": {
                    "queries": {
                        "existing_canned": {
                            "sql": "SELECT 1 as canned",
                        }
                    }
                }
            }
        },
        plugins_dir=PLUGINS_DIR,
        settings={
            "default_page_size": 50,
            "max_returned_rows": 100,
            "sql_time_limit_ms": 200,
            "num_sql_threads": 1,
        },
    )
    await ds.invoke_startup()

    # Verify the canned query exists
    from datasette.default_permissions import save_query

    # Try to save a query with the same name as the existing canned query
    with pytest.raises(ValueError, match="conflicts with an existing canned query"):
        await save_query(ds, "_memory", "SELECT 2", slug="existing_canned")


@pytest.mark.asyncio
async def test_save_query_duplicate_saved_query(saved_queries_ds):
    """Test that saving a query with a name that conflicts with an existing saved query is rejected."""
    client = saved_queries_ds.client

    # First, save a query
    await client.post(
        "/test/-/save-query",
        data={"sql": "SELECT 1", "slug": "duplicate_test2"},
    )

    # Now try to save another query with the same name via API
    response = await client.post(
        "/test/-/save-query",
        data={"sql": "SELECT 2", "slug": "duplicate_test2"},
    )
    assert response.status_code == 400
    data = response.json()
    assert data["ok"] is False
    assert "already exists" in data["error"]
