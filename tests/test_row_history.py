from datasette.app import Datasette
from datasette.utils import sqlite3
import pytest
import time


@pytest.fixture
def ds_write(tmp_path_factory):
    db_directory = tmp_path_factory.mktemp("dbs")
    db_path = str(db_directory / "data.db")
    db1 = sqlite3.connect(str(db_path))
    db1.execute(
        "create table docs (id integer primary key, title text, score float, age integer)"
    )
    db1.close()
    ds = Datasette([db_path])
    ds.root_enabled = True
    yield ds
    ds.close()


def write_token(ds, actor_id="root", permissions=None):
    to_sign = {"a": actor_id, "token": "dstok", "t": int(time.time())}
    if permissions:
        to_sign["_r"] = {"a": permissions}
    return "dstok_{}".format(ds.sign(to_sign, namespace="token"))


def _headers(token):
    return {
        "Authorization": "Bearer {}".format(token),
        "Content-Type": "application/json",
    }


async def _insert_row(ds):
    insert_response = await ds.client.post(
        "/data/docs/-/insert",
        json={"row": {"title": "Row one", "score": 1.2, "age": 5}, "return": True},
        headers=_headers(write_token(ds)),
    )
    assert insert_response.status_code == 201
    return insert_response.json()["rows"][0]["id"]


@pytest.mark.asyncio
async def test_update_row_creates_history(ds_write):
    pk = await _insert_row(ds_write)

    update_response = await ds_write.client.post(
        "/data/docs/{}/-/update".format(pk),
        json={"update": {"title": "Updated title", "score": 2.0}, "return": True},
        headers=_headers(write_token(ds_write)),
    )
    assert update_response.status_code == 200
    assert update_response.json()["ok"] is True

    history_response = await ds_write.client.get(
        "/data/docs/{}/-/history".format(pk),
        headers=_headers(write_token(ds_write, permissions=["vi", "vt"])),
    )
    assert history_response.status_code == 200
    history_data = history_response.json()
    assert history_data["ok"] is True
    assert len(history_data["history"]) == 1
    assert history_data["history"][0]["row_data"]["title"] == "Row one"
    assert history_data["history"][0]["row_data"]["score"] == 1.2


@pytest.mark.asyncio
async def test_multiple_updates_create_multiple_history_entries(ds_write):
    pk = await _insert_row(ds_write)

    for i in range(3):
        update_response = await ds_write.client.post(
            "/data/docs/{}/-/update".format(pk),
            json={"update": {"title": "Version {}".format(i + 1)}, "return": True},
            headers=_headers(write_token(ds_write)),
        )
        assert update_response.status_code == 200

    history_response = await ds_write.client.get(
        "/data/docs/{}/-/history".format(pk),
        headers=_headers(write_token(ds_write, permissions=["vi", "vt"])),
    )
    history_data = history_response.json()
    assert len(history_data["history"]) == 3

    titles = [h["row_data"]["title"] for h in history_data["history"]]
    assert "Version 2" in titles
    assert "Version 1" in titles
    assert "Row one" in titles


@pytest.mark.asyncio
async def test_history_diff_api(ds_write):
    pk = await _insert_row(ds_write)

    update1 = await ds_write.client.post(
        "/data/docs/{}/-/update".format(pk),
        json={"update": {"title": "First update", "age": 10}, "return": True},
        headers=_headers(write_token(ds_write)),
    )
    assert update1.status_code == 200

    history_response = await ds_write.client.get(
        "/data/docs/{}/-/history".format(pk),
        headers=_headers(write_token(ds_write, permissions=["vi", "vt"])),
    )
    history_data = history_response.json()
    history_id = history_data["history"][0]["id"]

    diff_response = await ds_write.client.get(
        "/data/docs/{}/-/history-diff?history_id={}".format(pk, history_id),
        headers=_headers(write_token(ds_write, permissions=["vi", "vt"])),
    )
    assert diff_response.status_code == 200
    diff_data = diff_response.json()
    assert diff_data["ok"] is True

    diff = diff_data["diff"]
    assert "changed" in diff
    assert "title" in diff["changed"]
    assert "age" in diff["changed"]
    assert diff["changed"]["title"]["old"] == "Row one"
    assert diff["changed"]["title"]["new"] == "First update"
    assert diff["changed"]["age"]["old"] == 5
    assert diff["changed"]["age"]["new"] == 10


@pytest.mark.asyncio
async def test_revert_to_history_version(ds_write):
    pk = await _insert_row(ds_write)

    update1 = await ds_write.client.post(
        "/data/docs/{}/-/update".format(pk),
        json={"update": {"title": "First update"}, "return": True},
        headers=_headers(write_token(ds_write)),
    )
    assert update1.status_code == 200

    update2 = await ds_write.client.post(
        "/data/docs/{}/-/update".format(pk),
        json={"update": {"title": "Second update"}, "return": True},
        headers=_headers(write_token(ds_write)),
    )
    assert update2.status_code == 200

    history_response = await ds_write.client.get(
        "/data/docs/{}/-/history".format(pk),
        headers=_headers(write_token(ds_write, permissions=["vi", "vt"])),
    )
    history_data = history_response.json()
    first_update_history_id = history_data["history"][0]["id"]

    revert_response = await ds_write.client.post(
        "/data/docs/{}/-/revert".format(pk),
        json={"history_id": first_update_history_id, "return": True},
        headers=_headers(write_token(ds_write)),
    )
    assert revert_response.status_code == 200
    revert_data = revert_response.json()
    assert revert_data["ok"] is True
    assert revert_data["row"]["title"] == "First update"

    row_response = await ds_write.client.get(
        "/data/docs/{}.json?_shape=array".format(pk),
    )
    assert row_response.status_code == 200
    current_row = row_response.json()[0]
    assert current_row["title"] == "First update"
    assert current_row["score"] == 1.2
    assert current_row["age"] == 5


@pytest.mark.asyncio
async def test_revert_creates_new_history_entry(ds_write):
    pk = await _insert_row(ds_write)

    update1 = await ds_write.client.post(
        "/data/docs/{}/-/update".format(pk),
        json={"update": {"title": "First update"}, "return": True},
        headers=_headers(write_token(ds_write)),
    )
    assert update1.status_code == 200

    history_response = await ds_write.client.get(
        "/data/docs/{}/-/history".format(pk),
        headers=_headers(write_token(ds_write, permissions=["vi", "vt"])),
    )
    history_data = history_response.json()
    initial_count = len(history_data["history"])
    first_update_history_id = history_data["history"][0]["id"]

    revert_response = await ds_write.client.post(
        "/data/docs/{}/-/revert".format(pk),
        json={"history_id": first_update_history_id, "return": True},
        headers=_headers(write_token(ds_write)),
    )
    assert revert_response.status_code == 200

    history_response2 = await ds_write.client.get(
        "/data/docs/{}/-/history".format(pk),
        headers=_headers(write_token(ds_write, permissions=["vi", "vt"])),
    )
    history_data2 = history_response2.json()
    assert len(history_data2["history"]) == initial_count + 1


@pytest.mark.asyncio
async def test_history_permissions(ds_write):
    pk = await _insert_row(ds_write)

    update1 = await ds_write.client.post(
        "/data/docs/{}/-/update".format(pk),
        json={"update": {"title": "First update"}, "return": True},
        headers=_headers(write_token(ds_write)),
    )
    assert update1.status_code == 200

    no_perm_token = write_token(ds_write, actor_id="not-root")

    history_response = await ds_write.client.get(
        "/data/docs/{}/-/history".format(pk),
        headers=_headers(no_perm_token),
    )
    assert history_response.status_code == 200

    history_response2 = await ds_write.client.get(
        "/data/docs/{}/-/history-diff?history_id=1".format(pk),
        headers=_headers(no_perm_token),
    )
    assert history_response2.status_code == 200

    revert_response = await ds_write.client.post(
        "/data/docs/{}/-/revert".format(pk),
        json={"history_id": 1, "return": True},
        headers=_headers(no_perm_token),
    )
    assert revert_response.status_code == 403


@pytest.mark.asyncio
async def test_compare_rows_added_removed_changed():
    from datasette.views.row import compare_rows

    old_row = {"id": 1, "title": "Old", "score": 1.0, "age": 5}
    new_row = {"id": 1, "title": "New", "score": 1.0, "extra": "added"}

    diff = compare_rows(old_row, new_row)

    assert "title" in diff["changed"]
    assert diff["changed"]["title"]["old"] == "Old"
    assert diff["changed"]["title"]["new"] == "New"

    assert "extra" in diff["added"]
    assert diff["added"]["extra"] == "added"

    assert "age" in diff["removed"]
    assert diff["removed"]["age"] == 5

    assert "id" in diff["unchanged"]
    assert "score" in diff["unchanged"]


@pytest.mark.asyncio
async def test_history_diff_invalid_history_id(ds_write):
    pk = await _insert_row(ds_write)

    update1 = await ds_write.client.post(
        "/data/docs/{}/-/update".format(pk),
        json={"update": {"title": "First update"}, "return": True},
        headers=_headers(write_token(ds_write)),
    )
    assert update1.status_code == 200

    diff_response = await ds_write.client.get(
        "/data/docs/{}/-/history-diff?history_id=99999".format(pk),
        headers=_headers(write_token(ds_write, permissions=["vi", "vt"])),
    )
    assert diff_response.status_code == 404


@pytest.mark.asyncio
async def test_history_diff_missing_history_id(ds_write):
    pk = await _insert_row(ds_write)

    diff_response = await ds_write.client.get(
        "/data/docs/{}/-/history-diff".format(pk),
        headers=_headers(write_token(ds_write, permissions=["vi", "vt"])),
    )
    assert diff_response.status_code == 400


@pytest.mark.asyncio
async def test_revert_invalid_history_id(ds_write):
    pk = await _insert_row(ds_write)

    revert_response = await ds_write.client.post(
        "/data/docs/{}/-/revert".format(pk),
        json={"history_id": 99999, "return": True},
        headers=_headers(write_token(ds_write)),
    )
    assert revert_response.status_code == 404


@pytest.mark.asyncio
async def test_revert_missing_history_id(ds_write):
    pk = await _insert_row(ds_write)

    revert_response = await ds_write.client.post(
        "/data/docs/{}/-/revert".format(pk),
        json={"return": True},
        headers=_headers(write_token(ds_write)),
    )
    assert revert_response.status_code == 400
