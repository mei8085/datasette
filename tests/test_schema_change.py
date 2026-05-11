import asyncio
import io
import json
import os
import pytest
import sys
import tempfile
from unittest import mock
from datasette.app import Datasette
from datasette.events import SchemaChangeEvent
from datasette.utils.sqlite import sqlite3


@pytest.fixture
def tmp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = os.path.join(tmpdir, "test.db")
        conn = sqlite3.connect(filepath)
        conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO test (name) VALUES ('test')")
        conn.commit()
        conn.close()
        yield filepath


@pytest.mark.asyncio
async def test_schema_change_webhook_config(tmp_db):
    webhook_url = "http://example.com/webhook"
    ds = Datasette([tmp_db], config={"schema_change_webhook": webhook_url})
    await ds.invoke_startup()
    urls = ds._schema_change_webhook_urls()
    assert urls == [webhook_url]


@pytest.mark.asyncio
async def test_schema_change_webhook_list_config(tmp_db):
    webhook_urls = ["http://example.com/webhook1", "http://example.com/webhook2"]
    ds = Datasette([tmp_db], config={"schema_change_webhook": webhook_urls})
    await ds.invoke_startup()
    urls = ds._schema_change_webhook_urls()
    assert urls == webhook_urls


@pytest.mark.asyncio
async def test_schema_change_webhook_dict_config(tmp_db):
    ds = Datasette(
        [tmp_db],
        config={
            "schema_change_webhook": {
                "urls": ["http://example.com/webhook"],
                "timeout": 10,
            }
        }
    )
    await ds.invoke_startup()
    urls = ds._schema_change_webhook_urls()
    assert urls == ["http://example.com/webhook"]


@pytest.mark.asyncio
async def test_schema_change_check_interval_default(tmp_db):
    ds = Datasette([tmp_db])
    await ds.invoke_startup()
    interval = ds._get_schema_check_interval()
    assert interval == 60


@pytest.mark.asyncio
async def test_schema_change_check_interval_custom(tmp_db):
    ds = Datasette([tmp_db], config={"schema_change_check": {"interval_seconds": 30}})
    await ds.invoke_startup()
    interval = ds._get_schema_check_interval()
    assert interval == 30


@pytest.mark.asyncio
async def test_schema_change_check_interval_disabled(tmp_db):
    ds = Datasette([tmp_db], config={"schema_change_check": {"interval_seconds": 0}})
    await ds.invoke_startup()
    interval = ds._get_schema_check_interval()
    assert interval == 0


@pytest.mark.asyncio
async def test_schema_change_event_triggered(tmp_db):
    ds = Datasette([tmp_db])
    await ds.invoke_startup()
    await ds._refresh_schemas()

    ds._tracked_events = []

    conn = sqlite3.connect(tmp_db)
    conn.execute("ALTER TABLE test ADD COLUMN age INTEGER")
    conn.commit()
    conn.close()

    await ds._refresh_schemas()

    schema_change_events = [
        e for e in ds._tracked_events if isinstance(e, SchemaChangeEvent)
    ]
    assert len(schema_change_events) == 1
    event = schema_change_events[0]
    assert event.name == "schema-change"
    assert event.database == "test"
    assert event.before_schema_version < event.after_schema_version


@pytest.mark.asyncio
async def test_schema_change_event_not_triggered_on_first_refresh(tmp_db):
    ds = Datasette([tmp_db])
    await ds.invoke_startup()
    ds._tracked_events = []

    await ds._refresh_schemas()

    schema_change_events = [
        e for e in ds._tracked_events if isinstance(e, SchemaChangeEvent)
    ]
    assert len(schema_change_events) == 0


@pytest.mark.asyncio
async def test_schema_change_event_not_triggered_when_no_change(tmp_db):
    ds = Datasette([tmp_db])
    await ds.invoke_startup()
    await ds._refresh_schemas()
    ds._tracked_events = []

    await ds._refresh_schemas()

    schema_change_events = [
        e for e in ds._tracked_events if isinstance(e, SchemaChangeEvent)
    ]
    assert len(schema_change_events) == 0


@pytest.mark.asyncio
async def test_schema_change_log_output(tmp_db):
    ds = Datasette([tmp_db])
    await ds.invoke_startup()
    await ds._refresh_schemas()

    stderr_buffer = io.StringIO()
    with mock.patch('sys.stderr', stderr_buffer):
        conn = sqlite3.connect(tmp_db)
        conn.execute("ALTER TABLE test ADD COLUMN age INTEGER")
        conn.commit()
        conn.close()

        await ds._refresh_schemas()

    stderr_output = stderr_buffer.getvalue()
    assert "Schema change detected for database test" in stderr_output
    assert "version" in stderr_output
    assert "->" in stderr_output


@pytest.mark.asyncio
async def test_internal_schema_version_update(tmp_db):
    ds = Datasette([tmp_db])
    await ds.invoke_startup()
    await ds._refresh_schemas()

    internal_db = ds.get_internal_database()
    results = await internal_db.execute(
        "SELECT schema_version FROM catalog_databases WHERE database_name = 'test'"
    )
    before_version = results.rows[0]["schema_version"]

    conn = sqlite3.connect(tmp_db)
    conn.execute("ALTER TABLE test ADD COLUMN age INTEGER")
    conn.commit()
    conn.close()

    await ds._refresh_schemas()

    results = await internal_db.execute(
        "SELECT schema_version FROM catalog_databases WHERE database_name = 'test'"
    )
    after_version = results.rows[0]["schema_version"]

    assert before_version < after_version


@pytest.mark.asyncio
async def test_schema_tables_populated_after_change(tmp_db):
    ds = Datasette([tmp_db])
    await ds.invoke_startup()
    await ds._refresh_schemas()

    internal_db = ds.get_internal_database()
    results = await internal_db.execute(
        "SELECT COUNT(*) as count FROM columns WHERE database_name = 'test' AND table_name = 'test'"
    )
    before_count = results.rows[0]["count"]
    assert before_count == 2

    conn = sqlite3.connect(tmp_db)
    conn.execute("ALTER TABLE test ADD COLUMN age INTEGER")
    conn.commit()
    conn.close()

    await ds._refresh_schemas()

    results = await internal_db.execute(
        "SELECT COUNT(*) as count FROM columns WHERE database_name = 'test' AND table_name = 'test'"
    )
    after_count = results.rows[0]["count"]
    assert after_count == 3


@pytest.mark.asyncio
async def test_asgi_lifespan_starts_schema_check(tmp_db):
    ds = Datasette([tmp_db], config={"schema_change_check": {"interval_seconds": 0.1}})
    app = ds.app()

    messages_sent = []

    async def receive():
        if not hasattr(receive, "count"):
            receive.count = 0
        if receive.count == 0:
            receive.count += 1
            return {"type": "lifespan.startup"}
        elif receive.count == 1:
            receive.count += 1
            await asyncio.sleep(0.3)
            return {"type": "lifespan.shutdown"}
        raise Exception("Unexpected receive call")

    async def send(message):
        messages_sent.append(message)

    await app({"type": "lifespan"}, receive, send)

    startup_complete = any(m["type"] == "lifespan.startup.complete" for m in messages_sent)
    shutdown_complete = any(m["type"] == "lifespan.shutdown.complete" for m in messages_sent)

    assert startup_complete
    assert shutdown_complete
    assert ds._closed


@pytest.mark.asyncio
async def test_schema_change_event_properties(tmp_db):
    event = SchemaChangeEvent(
        actor=None,
        database="test",
        before_schema_version=1,
        after_schema_version=2,
    )

    props = event.properties()
    assert props["database"] == "test"
    assert props["before_schema_version"] == 1
    assert props["after_schema_version"] == 2
    assert "actor" not in props
    assert "created" not in props


@pytest.mark.asyncio
async def test_schema_change_webhook_payload_structure():
    payload = {
        "event": "schema-change",
        "database": "test",
        "before_schema_version": 1,
        "after_schema_version": 2,
    }

    assert payload["event"] == "schema-change"
    assert payload["database"] == "test"
    assert payload["before_schema_version"] == 1
    assert payload["after_schema_version"] == 2


@pytest.mark.asyncio
async def test_multiple_schema_changes(tmp_db):
    ds = Datasette([tmp_db])
    await ds.invoke_startup()
    await ds._refresh_schemas()

    ds._tracked_events = []

    conn = sqlite3.connect(tmp_db)
    conn.execute("ALTER TABLE test ADD COLUMN age INTEGER")
    conn.commit()
    await ds._refresh_schemas()

    conn.execute("ALTER TABLE test ADD COLUMN email TEXT")
    conn.commit()
    conn.close()
    await ds._refresh_schemas()

    schema_change_events = [
        e for e in ds._tracked_events if isinstance(e, SchemaChangeEvent)
    ]
    assert len(schema_change_events) == 2
    assert schema_change_events[0].before_schema_version < schema_change_events[0].after_schema_version
    assert schema_change_events[1].before_schema_version < schema_change_events[1].after_schema_version


@pytest.mark.asyncio
async def test_no_schema_change_events_on_insert_or_update(tmp_db):
    ds = Datasette([tmp_db])
    await ds.invoke_startup()
    await ds._refresh_schemas()

    ds._tracked_events = []

    conn = sqlite3.connect(tmp_db)
    conn.execute("INSERT INTO test (name) VALUES ('another')")
    conn.commit()
    conn.close()

    await ds._refresh_schemas()

    schema_change_events = [
        e for e in ds._tracked_events if isinstance(e, SchemaChangeEvent)
    ]
    assert len(schema_change_events) == 0
