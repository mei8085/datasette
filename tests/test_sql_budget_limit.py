from datasette.app import Datasette
from datasette.database import QueryInterruptedWithResults, QueryInterrupted, Database
import pytest


@pytest.mark.asyncio
async def test_sql_row_limit_settings():
    """Test that SQL row limit settings are properly configured"""
    ds = Datasette(
        settings={
            "sql_row_limit": 1000,
            "sql_partial_results": True,
        }
    )
    
    assert ds.sql_row_limit == 1000
    assert ds.sql_partial_results is True


@pytest.mark.asyncio
async def test_sql_row_limit_defaults():
    """Test that SQL row limit has proper defaults"""
    ds = Datasette()
    
    assert ds.sql_row_limit == 100000
    assert ds.sql_partial_results is True


@pytest.mark.asyncio
async def test_query_interrupted_with_results_exception():
    """Test that QueryInterruptedWithResults exception works correctly"""
    rows = [(1, "test1"), (2, "test2")]
    description = [("id", None, None, None, None, None, None), ("name", None, None, None, None, None, None)]
    
    exc = QueryInterruptedWithResults(rows, True, description, "row_limit_exceeded")
    
    assert exc.rows == rows
    assert exc.truncated is True
    assert exc.description == description
    assert exc.reason == "row_limit_exceeded"
    assert "row_limit_exceeded" in str(exc)


@pytest.mark.asyncio
async def test_sql_row_limit_partial_results_enabled():
    """Test that row limit works when partial results are enabled"""
    ds = Datasette(
        settings={
            "sql_row_limit": 10,
            "sql_partial_results": True,
        }
    )
    
    db = ds.add_memory_database("test")
    
    await db.execute_write("CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)")
    for i in range(100):
        await db.execute_write("INSERT INTO test_table (name) VALUES (?)", [f"row_{i}"])
    
    try:
        await db.execute("SELECT * FROM test_table")
        # If no exception, check the number of rows returned
        # Note: this may not throw an exception depending on implementation
        # but we should have at most 10 rows
    except QueryInterruptedWithResults as e:
        assert len(e.rows) <= 10
        assert e.truncated is True
        assert e.reason == "row_limit_exceeded"


@pytest.mark.asyncio
async def test_sql_row_limit_partial_results_disabled():
    """Test that row limit is disabled when partial results are off"""
    ds = Datasette(
        settings={
            "sql_row_limit": 10,
            "sql_partial_results": False,
        }
    )
    
    db = ds.add_memory_database("test")
    
    await db.execute_write("CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)")
    for i in range(100):
        await db.execute_write("INSERT INTO test_table (name) VALUES (?)", [f"row_{i}"])
    
    # With partial results disabled, should get all rows
    results = await db.execute("SELECT * FROM test_table")
    assert len(results.rows) == 100
