"""
Tests for SQL query budget limits feature.

This module tests the dual budget system (row count and time limit) for SQL queries,
ensuring that queries can return partial results when they exceed their configured limits.
"""

import pytest
import uuid
from datasette.app import Datasette
from datasette.database import (
    Database,
    QueryInterrupted,
    QueryInterruptedWithResults,
    Results,
)


def _unique_name():
    """Generate a unique name to avoid test interference."""
    return f"test_{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
async def test_sql_row_limit_settings():
    """Test that SQL row limit settings are properly configured."""
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
    """Test that SQL row limit has proper defaults."""
    ds = Datasette()
    
    assert ds.sql_row_limit == 100000
    assert ds.sql_partial_results is True


@pytest.mark.asyncio
async def test_query_interrupted_with_results_exception():
    """Test that QueryInterruptedWithResults exception works correctly."""
    rows = [(1, "test1"), (2, "test2")]
    description = [("id", None, None, None, None, None, None), ("name", None, None, None, None, None, None)]
    
    exc = QueryInterruptedWithResults(rows, True, description, "row_limit_exceeded")
    
    assert exc.rows == rows
    assert exc.truncated is True
    assert exc.description == description
    assert exc.reason == "row_limit_exceeded"
    assert "row_limit_exceeded" in str(exc)


@pytest.mark.asyncio
async def test_sql_row_limit_exact_match():
    """Test that queries returning exactly the row limit work correctly."""
    ds = Datasette(
        settings={
            "sql_row_limit": 10,
            "sql_partial_results": True,
        }
    )
    
    db = ds.add_memory_database(_unique_name())
    
    await db.execute_write("CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)")
    for i in range(10):
        await db.execute_write("INSERT INTO test_table (name) VALUES (?)", [f"row_{i}"])
    
    # Should return all 10 rows without truncation
    results = await db.execute("SELECT * FROM test_table")
    assert len(results.rows) == 10


@pytest.mark.asyncio
async def test_sql_row_limit_exceeded_returns_partial():
    """Test that queries exceeding row limit return exactly the limit."""
    ds = Datasette(
        settings={
            "sql_row_limit": 10,
            "sql_partial_results": True,
        }
    )
    
    db = ds.add_memory_database(_unique_name())
    
    await db.execute_write("CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)")
    for i in range(100):
        await db.execute_write("INSERT INTO test_table (name) VALUES (?)", [f"row_{i}"])
    
    try:
        results = await db.execute("SELECT * FROM test_table")
        # If no exception, results should be truncated to exactly 10 rows
        assert len(results.rows) == 10
    except QueryInterruptedWithResults as e:
        # If exception, partial results should be exactly 10 rows
        assert len(e.rows) == 10
        assert e.truncated is True
        assert e.reason == "row_limit_exceeded"


@pytest.mark.asyncio
async def test_sql_row_limit_partial_results_disabled():
    """Test that row limit is disabled when partial results are off."""
    ds = Datasette(
        settings={
            "sql_row_limit": 10,
            "sql_partial_results": False,
        }
    )
    
    db = ds.add_memory_database(_unique_name())
    
    await db.execute_write("CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)")
    for i in range(100):
        await db.execute_write("INSERT INTO test_table (name) VALUES (?)", [f"row_{i}"])
    
    # With partial results disabled, should get all rows
    results = await db.execute("SELECT * FROM test_table")
    assert len(results.rows) == 100


@pytest.mark.asyncio
async def test_sql_row_limit_with_order_by():
    """Test that row limit works correctly with ORDER BY."""
    ds = Datasette(
        settings={
            "sql_row_limit": 5,
            "sql_partial_results": True,
        }
    )
    
    db = ds.add_memory_database(_unique_name())
    
    await db.execute_write("CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)")
    for i in range(20):
        await db.execute_write("INSERT INTO test_table (name) VALUES (?)", [f"row_{i}"])
    
    try:
        results = await db.execute("SELECT * FROM test_table ORDER BY id DESC")
        assert len(results.rows) == 5
        # Check that we got the first 5 results (largest IDs)
        ids = [row["id"] for row in results.rows]
        assert ids == [19, 18, 17, 16, 15]
    except QueryInterruptedWithResults as e:
        assert len(e.rows) == 5
        assert e.truncated is True


@pytest.mark.asyncio
async def test_sql_row_limit_zero_disables():
    """Test that setting row limit to 0 disables the feature."""
    ds = Datasette(
        settings={
            "sql_row_limit": 0,
            "sql_partial_results": True,
        }
    )
    
    db = ds.add_memory_database(_unique_name())
    
    await db.execute_write("CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)")
    for i in range(100):
        await db.execute_write("INSERT INTO test_table (name) VALUES (?)", [f"row_{i}"])
    
    # With row_limit=0, should get all rows
    results = await db.execute("SELECT * FROM test_table")
    assert len(results.rows) == 100


@pytest.mark.asyncio
async def test_sql_row_limit_smaller_than_batch():
    """Test row limit when limit is smaller than default batch size."""
    ds = Datasette(
        settings={
            "sql_row_limit": 3,
            "sql_partial_results": True,
        }
    )
    
    db = ds.add_memory_database(_unique_name())
    
    await db.execute_write("CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)")
    for i in range(10):
        await db.execute_write("INSERT INTO test_table (name) VALUES (?)", [f"row_{i}"])
    
    try:
        results = await db.execute("SELECT * FROM test_table")
        assert len(results.rows) == 3
    except QueryInterruptedWithResults as e:
        assert len(e.rows) == 3
        assert e.truncated is True
        assert e.reason == "row_limit_exceeded"


@pytest.mark.asyncio
async def test_sql_row_limit_with_truncate():
    """Test that row limit works correctly with truncate parameter."""
    ds = Datasette(
        settings={
            "sql_row_limit": 20,
            "max_returned_rows": 10,
            "sql_partial_results": True,
        }
    )
    
    db = ds.add_memory_database(_unique_name())
    
    await db.execute_write("CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)")
    for i in range(100):
        await db.execute_write("INSERT INTO test_table (name) VALUES (?)", [f"row_{i}"])
    
    # With truncate=True, should use the smaller of row_limit and max_returned_rows
    try:
        results = await db.execute("SELECT * FROM test_table", truncate=True)
        assert len(results.rows) <= 10
    except QueryInterruptedWithResults as e:
        assert len(e.rows) <= 10


@pytest.mark.asyncio
async def test_sql_row_limit_returns_correct_data():
    """Test that partial results contain the correct data."""
    ds = Datasette(
        settings={
            "sql_row_limit": 5,
            "sql_partial_results": True,
        }
    )
    
    db = ds.add_memory_database(_unique_name())
    
    await db.execute_write("CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)")
    for i in range(20):
        await db.execute_write("INSERT INTO test_table (name) VALUES (?)", [f"row_{i}"])
    
    try:
        results = await db.execute("SELECT * FROM test_table WHERE id < 10 ORDER BY id")
        # Should return first 5 rows
        assert len(results.rows) == 5
        ids = [row["id"] for row in results.rows]
        assert ids == [0, 1, 2, 3, 4]
    except QueryInterruptedWithResults as e:
        assert len(e.rows) == 5
        # Check that data is correct
        ids = [row[0] if isinstance(row, tuple) else row["id"] for row in e.rows]
        assert ids == [0, 1, 2, 3, 4]
