import pytest
import pytest_asyncio
import json
from datasette.app import Datasette
from datasette.database import Database


@pytest_asyncio.fixture
async def ds_with_comments():
    ds = Datasette(memory=True, settings={"num_sql_threads": 1})
    db = ds.add_database(Database(ds, memory_name="test_db"), name="test_db")
    
    await db.execute_write("CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)")
    await db.execute_write("INSERT INTO test_table (id, name) VALUES (1, 'Row 1')")
    await db.execute_write("INSERT INTO test_table (id, name) VALUES (2, 'Row 2')")
    
    await ds.invoke_startup()
    return ds


@pytest_asyncio.fixture
async def ds_with_comments_prefix():
    ds = Datasette(memory=True, settings={"num_sql_threads": 1, "base_url": "/prefix/"})
    db = ds.add_database(Database(ds, memory_name="test_db"), name="test_db")
    
    await db.execute_write("CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)")
    await db.execute_write("INSERT INTO test_table (id, name) VALUES (1, 'Row 1')")
    await db.execute_write("INSERT INTO test_table (id, name) VALUES (2, 'Row 2')")
    
    await ds.invoke_startup()
    return ds


@pytest.mark.asyncio
async def test_get_comments_unauthenticated(ds_with_comments):
    client = ds_with_comments.client
    
    response = await client.get("/test_db/test_table/1/-/comments")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["database"] == "test_db"
    assert data["table"] == "test_table"
    assert data["pk_values"] == "1"
    assert data["comments"] == []


@pytest.mark.asyncio
async def test_post_comment_requires_authentication(ds_with_comments):
    client = ds_with_comments.client
    
    response = await client.post(
        "/test_db/test_table/1/-/comments",
        json={"comment_text": "Test comment"},
    )
    assert response.status_code == 401
    data = response.json()
    assert data["ok"] is False
    assert "Authentication required" in data["error"]


@pytest.mark.asyncio
async def test_post_and_get_comments(ds_with_comments):
    client = ds_with_comments.client
    
    actor_cookie = client.actor_cookie({"id": "test_user", "name": "Test User"})
    
    response = await client.post(
        "/test_db/test_table/1/-/comments",
        json={"comment_text": "First comment"},
        cookies={"ds_actor": actor_cookie},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["ok"] is True
    assert data["comment"]["comment_text"] == "First comment"
    assert data["comment"]["actor_id"] == "test_user"
    assert data["comment"]["actor_name"] == "Test User"
    
    await client.post(
        "/test_db/test_table/1/-/comments",
        json={"comment_text": "Second comment"},
        cookies={"ds_actor": actor_cookie},
    )
    
    response = await client.get("/test_db/test_table/1/-/comments")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert len(data["comments"]) == 2
    assert data["comments"][0]["comment_text"] == "Second comment"
    assert data["comments"][1]["comment_text"] == "First comment"


@pytest.mark.asyncio
async def test_comments_are_isolated_by_row(ds_with_comments):
    client = ds_with_comments.client
    
    actor_cookie = client.actor_cookie({"id": "user1"})
    
    await client.post(
        "/test_db/test_table/1/-/comments",
        json={"comment_text": "Comment for row 1"},
        cookies={"ds_actor": actor_cookie},
    )
    
    await client.post(
        "/test_db/test_table/2/-/comments",
        json={"comment_text": "Comment for row 2"},
        cookies={"ds_actor": actor_cookie},
    )
    
    response1 = await client.get("/test_db/test_table/1/-/comments")
    response2 = await client.get("/test_db/test_table/2/-/comments")
    
    assert len(response1.json()["comments"]) == 1
    assert response1.json()["comments"][0]["comment_text"] == "Comment for row 1"
    
    assert len(response2.json()["comments"]) == 1
    assert response2.json()["comments"][0]["comment_text"] == "Comment for row 2"


@pytest.mark.asyncio
async def test_delete_own_comment(ds_with_comments):
    client = ds_with_comments.client
    
    actor_cookie = client.actor_cookie({"id": "user1"})
    
    response = await client.post(
        "/test_db/test_table/1/-/comments",
        json={"comment_text": "To be deleted"},
        cookies={"ds_actor": actor_cookie},
    )
    comment_id = response.json()["comment"]["id"]
    
    response = await client.delete(
        f"/-/comments/{comment_id}",
        cookies={"ds_actor": actor_cookie},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    
    response = await client.get("/test_db/test_table/1/-/comments")
    assert len(response.json()["comments"]) == 0


@pytest.mark.asyncio
async def test_cannot_delete_other_users_comment(ds_with_comments):
    client = ds_with_comments.client
    
    user1_cookie = client.actor_cookie({"id": "user1"})
    user2_cookie = client.actor_cookie({"id": "user2"})
    
    response = await client.post(
        "/test_db/test_table/1/-/comments",
        json={"comment_text": "User 1's comment"},
        cookies={"ds_actor": user1_cookie},
    )
    comment_id = response.json()["comment"]["id"]
    
    response = await client.delete(
        f"/-/comments/{comment_id}",
        cookies={"ds_actor": user2_cookie},
    )
    assert response.status_code == 403
    
    response = await client.get("/test_db/test_table/1/-/comments")
    assert len(response.json()["comments"]) == 1


@pytest.mark.asyncio
async def test_delete_nonexistent_comment(ds_with_comments):
    client = ds_with_comments.client
    
    actor_cookie = client.actor_cookie({"id": "user1"})
    
    response = await client.delete(
        "/-/comments/99999",
        cookies={"ds_actor": actor_cookie},
    )
    assert response.status_code == 404
    data = response.json()
    assert data["ok"] is False


@pytest.mark.asyncio
async def test_post_comment_empty_text(ds_with_comments):
    client = ds_with_comments.client
    
    actor_cookie = client.actor_cookie({"id": "user1"})
    
    response = await client.post(
        "/test_db/test_table/1/-/comments",
        json={"comment_text": "   "},
        cookies={"ds_actor": actor_cookie},
    )
    assert response.status_code == 400
    data = response.json()
    assert data["ok"] is False
    assert "required" in data["error"]


@pytest.mark.asyncio
async def test_post_comment_invalid_json(ds_with_comments):
    client = ds_with_comments.client
    
    actor_cookie = client.actor_cookie({"id": "user1"})
    
    response = await client.post(
        "/test_db/test_table/1/-/comments",
        data="not json",
        cookies={"ds_actor": actor_cookie},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_get_comments_nonexistent_table(ds_with_comments):
    client = ds_with_comments.client
    
    response = await client.get("/test_db/nonexistent_table/1/-/comments")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_requires_authentication(ds_with_comments):
    client = ds_with_comments.client
    
    actor_cookie = client.actor_cookie({"id": "user1"})
    response = await client.post(
        "/test_db/test_table/1/-/comments",
        json={"comment_text": "Test"},
        cookies={"ds_actor": actor_cookie},
    )
    comment_id = response.json()["comment"]["id"]
    
    response = await client.delete(f"/-/comments/{comment_id}")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_invalid_comment_id(ds_with_comments):
    client = ds_with_comments.client
    
    actor_cookie = client.actor_cookie({"id": "user1"})
    
    response = await client.delete(
        "/-/comments/abc",
        cookies={"ds_actor": actor_cookie},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_prefix_url_get_comments(ds_with_comments_prefix):
    client = ds_with_comments_prefix.client
    
    response = await client.get("/prefix/test_db/test_table/1/-/comments")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["database"] == "test_db"
    assert data["table"] == "test_table"
    assert data["pk_values"] == "1"


@pytest.mark.asyncio
async def test_prefix_url_post_and_get_comments(ds_with_comments_prefix):
    client = ds_with_comments_prefix.client
    
    actor_cookie = client.actor_cookie({"id": "prefix_user", "name": "Prefix User"})
    
    response = await client.post(
        "/prefix/test_db/test_table/1/-/comments",
        json={"comment_text": "Prefix comment"},
        cookies={"ds_actor": actor_cookie},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["ok"] is True
    assert data["comment"]["comment_text"] == "Prefix comment"
    assert data["comment"]["actor_id"] == "prefix_user"
    
    comment_id = data["comment"]["id"]
    
    response = await client.get("/prefix/test_db/test_table/1/-/comments")
    assert response.status_code == 200
    data = response.json()
    assert len(data["comments"]) == 1
    assert data["comments"][0]["comment_text"] == "Prefix comment"
    
    response = await client.delete(
        f"/prefix/-/comments/{comment_id}",
        cookies={"ds_actor": actor_cookie},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    
    response = await client.get("/prefix/test_db/test_table/1/-/comments")
    data = response.json()
    assert len(data["comments"]) == 0


@pytest.mark.asyncio
async def test_prefix_url_non_prefixed_url_fails(ds_with_comments_prefix):
    client = ds_with_comments_prefix.client
    
    response = await client.get("/test_db/test_table/1/-/comments")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_prefix_url_delete_requires_auth(ds_with_comments_prefix):
    client = ds_with_comments_prefix.client
    
    actor_cookie = client.actor_cookie({"id": "user1"})
    response = await client.post(
        "/prefix/test_db/test_table/1/-/comments",
        json={"comment_text": "Test"},
        cookies={"ds_actor": actor_cookie},
    )
    comment_id = response.json()["comment"]["id"]
    
    response = await client.delete(f"/prefix/-/comments/{comment_id}")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_prefix_url_post_requires_authentication(ds_with_comments_prefix):
    client = ds_with_comments_prefix.client
    
    response = await client.post(
        "/prefix/test_db/test_table/1/-/comments",
        json={"comment_text": "Test comment"},
    )
    assert response.status_code == 401
    data = response.json()
    assert data["ok"] is False
    assert "Authentication required" in data["error"]
