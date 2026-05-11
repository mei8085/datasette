"""Tests for saved queries with shortlink aliases."""

import pytest
from .fixtures import make_app_client


@pytest.fixture
def saved_queries_client():
    with make_app_client(
        extra_databases={"test.db": "create table items (id int, name text)"},
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_save_query_basic(saved_queries_client):
    """Test basic query saving functionality."""
    response = await saved_queries_client.post(
        "/test/-/save-query",
        {"sql": "select * from items"},
        csrftoken_from=True,
    )
    assert response.status == 200
    data = response.json
    assert data["ok"] is True
    assert "slug" in data
    assert "url" in data
    assert data["url"].startswith("/-/q/")


@pytest.mark.asyncio
async def test_save_query_with_custom_slug(saved_queries_client):
    """Test saving a query with a custom slug."""
    response = await saved_queries_client.post(
        "/test/-/save-query",
        {"sql": "select * from items", "slug": "my_custom_query"},
        csrftoken_from=True,
    )
    assert response.status == 200
    data = response.json
    assert data["ok"] is True
    assert data["slug"] == "my_custom_query"
    assert data["url"] == "/-/q/my_custom_query"


@pytest.mark.asyncio
async def test_save_query_duplicate_slug(saved_queries_client):
    """Test that duplicate slugs are rejected."""
    await saved_queries_client.post(
        "/test/-/save-query",
        {"sql": "select * from items", "slug": "duplicate_test"},
        csrftoken_from=True,
    )
    response = await saved_queries_client.post(
        "/test/-/save-query",
        {"sql": "select * from items", "slug": "duplicate_test"},
        csrftoken_from=True,
    )
    assert response.status == 400
    assert response.json["ok"] is False
    assert "already exists" in response.json["error"]


@pytest.mark.asyncio
async def test_save_query_missing_sql(saved_queries_client):
    """Test that missing SQL returns an error."""
    response = await saved_queries_client.post(
        "/test/-/save-query",
        {"sql": ""},
        csrftoken_from=True,
    )
    assert response.status == 400
    assert response.json["ok"] is False


@pytest.mark.asyncio
async def test_shortlink_redirect(saved_queries_client):
    """Test that shortlink redirects to the canned query path."""
    save_response = await saved_queries_client.post(
        "/test/-/save-query",
        {"sql": "select * from items", "slug": "redirect_test"},
        csrftoken_from=True,
    )
    assert save_response.status == 200
    slug = save_response.json["slug"]

    response = await saved_queries_client.get(f"/-/q/{slug}")
    assert response.status == 302
    assert response.headers["Location"] == f"/test/{slug}"


@pytest.mark.asyncio
async def test_shortlink_not_found(saved_queries_client):
    """Test that non-existent shortlinks return 404."""
    response = await saved_queries_client.get("/-/q/nonexistent_slug")
    assert response.status == 404


@pytest.mark.asyncio
async def test_saved_query_as_canned_query(saved_queries_client):
    """Test that saved queries are accessible as canned queries."""
    await saved_queries_client.post(
        "/test/-/save-query",
        {"sql": "select 1 as result", "slug": "canned_test"},
        csrftoken_from=True,
    )

    response = await saved_queries_client.get("/test/canned_test.json")
    assert response.status == 200
    data = response.json
    assert "rows" in data
    assert len(data["rows"]) == 1


@pytest.mark.asyncio
async def test_saved_query_permissions(saved_queries_client):
    """Test that saved queries use the same permission system as canned queries."""
    await saved_queries_client.post(
        "/test/-/save-query",
        {"sql": "select 1 as result", "slug": "perm_test"},
        csrftoken_from=True,
    )

    response = await saved_queries_client.get("/test/perm_test")
    assert response.status == 200


@pytest.mark.asyncio
async def test_get_saved_query_by_slug(saved_queries_client):
    """Test retrieving a saved query by slug."""
    from datasette.default_permissions import get_saved_query

    await saved_queries_client.post(
        "/test/-/save-query",
        {"sql": "select * from items", "slug": "get_test"},
        csrftoken_from=True,
    )

    saved_query = await get_saved_query(saved_queries_client.ds, "get_test")
    assert saved_query is not None
    assert saved_query["slug"] == "get_test"
    assert saved_query["database_name"] == "test"
    assert saved_query["sql"] == "select * from items"


@pytest.mark.asyncio
async def test_get_nonexistent_saved_query(saved_queries_client):
    """Test that retrieving a non-existent query returns None."""
    from datasette.default_permissions import get_saved_query

    saved_query = await get_saved_query(saved_queries_client.ds, "nonexistent")
    assert saved_query is None


@pytest.mark.asyncio
async def test_get_all_saved_queries(saved_queries_client):
    """Test retrieving all saved queries."""
    from datasette.default_permissions import get_saved_queries

    await saved_queries_client.post(
        "/test/-/save-query",
        {"sql": "select 1", "slug": "query1"},
        csrftoken_from=True,
    )
    await saved_queries_client.post(
        "/test/-/save-query",
        {"sql": "select 2", "slug": "query2"},
        csrftoken_from=True,
    )

    all_queries = await get_saved_queries(saved_queries_client.ds, "test")
    assert len(all_queries) == 2
    slugs = {q["slug"] for q in all_queries}
    assert "query1" in slugs
    assert "query2" in slugs


@pytest.mark.asyncio
async def test_delete_saved_query(saved_queries_client):
    """Test deleting a saved query."""
    from datasette.default_permissions import delete_saved_query, get_saved_query

    await saved_queries_client.post(
        "/test/-/save-query",
        {"sql": "select 1", "slug": "delete_test"},
        csrftoken_from=True,
    )

    saved_query = await get_saved_query(saved_queries_client.ds, "delete_test")
    assert saved_query is not None

    deleted = await delete_saved_query(saved_queries_client.ds, "delete_test")
    assert deleted is True

    saved_query = await get_saved_query(saved_queries_client.ds, "delete_test")
    assert saved_query is None


@pytest.mark.asyncio
async def test_delete_nonexistent_saved_query(saved_queries_client):
    """Test that deleting a non-existent query returns False."""
    from datasette.default_permissions import delete_saved_query

    deleted = await delete_saved_query(saved_queries_client.ds, "nonexistent")
    assert deleted is False
