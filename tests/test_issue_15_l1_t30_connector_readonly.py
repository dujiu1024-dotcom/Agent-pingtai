from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

from agent_platform.control_plane.data_connectors import ConnectorError
from agent_platform.connectors.providers import (
    MySQLSourceProvider,
    PostgresSourceProvider,
    _readonly_query,
)


class ReadOnlyQueryContractTests(unittest.TestCase):
    def test_accepts_select_and_read_only_cte(self):
        self.assertEqual(_readonly_query("SELECT id FROM users", provider="PostgreSQL"), "SELECT id FROM users")
        self.assertTrue(_readonly_query("WITH rows AS (SELECT 1) SELECT * FROM rows", provider="MySQL"))

    def test_rejects_write_and_ddl_statements(self):
        statements = [
            "INSERT INTO users(id) VALUES (1)",
            "UPDATE users SET name='x'",
            "DELETE FROM users",
            "CREATE TABLE x(id INT)",
            "ALTER TABLE users ADD COLUMN x INT",
            "DROP TABLE users",
            "TRUNCATE users",
            "WITH changed AS (DELETE FROM users RETURNING id) SELECT * FROM changed",
            "SELECT id FROM users INTO OUTFILE '/tmp/out'",
            "SELECT id FROM users FOR UPDATE",
        ]
        for sql in statements:
            with self.subTest(sql=sql), self.assertRaises(ConnectorError):
                _readonly_query(sql, provider="database")

    def test_rejects_multiple_statements_and_non_select(self):
        for sql in ("SELECT 1; DELETE FROM users", "SHOW TABLES", "CALL mutate()"):
            with self.subTest(sql=sql), self.assertRaises(ConnectorError):
                _readonly_query(sql, provider="database")


class _PgCursor:
    def __init__(self, connection):
        self.connection = connection

    async def fetchall(self):
        return [{"id": 1}]


class _PgConnection:
    def __init__(self):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return _PgCursor(self)


class _MySQLCursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, sql, params=None):
        self.connection.calls.append((sql, params))

    def fetchall(self):
        return [{"id": 1}]


class _MySQLConnection:
    def __init__(self, **_kwargs):
        self.calls = []

    def cursor(self):
        return _MySQLCursor(self)

    def close(self):
        return None


class ProviderReadOnlyTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_postgres_marks_transaction_read_only_before_select(self):
        connection = _PgConnection()

        class AsyncConnection:
            @staticmethod
            async def connect(**_kwargs):
                return connection

        psycopg = types.ModuleType("psycopg")
        psycopg.AsyncConnection = AsyncConnection
        rows = types.ModuleType("psycopg.rows")
        rows.dict_row = object()
        with patch.dict(sys.modules, {"psycopg": psycopg, "psycopg.rows": rows}):
            page = await PostgresSourceProvider().fetch_page(
                credential='{"dsn":"postgresql://readonly-test"}',
                scopes=("query:SELECT id FROM users",), cursor=None,
            )
        self.assertEqual([call[0] for call in connection.calls], [
            "SET TRANSACTION READ ONLY", "SELECT id FROM users LIMIT %s OFFSET %s",
        ])
        self.assertEqual(page.items[0]["id"], 1)

    async def test_mysql_starts_read_only_transaction_before_select(self):
        connection = _MySQLConnection()
        pymysql = types.ModuleType("pymysql")
        pymysql.connect = lambda **kwargs: connection
        pymysql.cursors = types.SimpleNamespace(DictCursor=object())
        with patch.dict(sys.modules, {"pymysql": pymysql}):
            page = await MySQLSourceProvider().fetch_page(
                credential='{"host":"localhost","database":"test","user":"writer"}',
                scopes=("query:SELECT id FROM users",), cursor=None,
            )
        self.assertEqual([call[0] for call in connection.calls], [
            "START TRANSACTION READ ONLY", "SELECT id FROM users LIMIT %s OFFSET %s",
        ])
        self.assertEqual(page.items[0]["id"], 1)

    async def test_both_providers_reject_write_before_opening_connection(self):
        pg = types.ModuleType("psycopg")
        pg.AsyncConnection = types.SimpleNamespace(connect=lambda **_kwargs: self.fail("must not connect"))
        pg_rows = types.ModuleType("psycopg.rows")
        pg_rows.dict_row = object()
        with patch.dict(sys.modules, {"psycopg": pg, "psycopg.rows": pg_rows}):
            with self.assertRaises(ConnectorError):
                await PostgresSourceProvider().fetch_page(
                    credential='{"dsn":"postgresql://writer"}',
                    scopes=("query:DELETE FROM users",), cursor=None,
                )

        mysql = types.ModuleType("pymysql")
        mysql.connect = lambda **_kwargs: self.fail("must not connect")
        mysql.cursors = types.SimpleNamespace(DictCursor=object())
        with patch.dict(sys.modules, {"pymysql": mysql}):
            with self.assertRaises(ConnectorError):
                await MySQLSourceProvider().fetch_page(
                    credential='{"host":"localhost","user":"writer"}',
                    scopes=("query:UPDATE users SET active=0",), cursor=None,
                )


if __name__ == "__main__":
    unittest.main()
