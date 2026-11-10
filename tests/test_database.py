"""Tests for storage/database.py (schema v1).

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import sqlite3

import pytest

from storage.database import get_connection, init_db

TABLES = {"patients", "vitals", "appointments", "fetal_kicks", "lab_results"}


def _tables(path) -> set[str]:
    with sqlite3.connect(path) as c:
        return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_init_db_idempotent_and_tables(tmp_path):
    path = tmp_path / "p.db"
    init_db(path)
    init_db(path)  # second call must not fail
    tables = _tables(path)
    assert TABLES <= tables
    assert "chat_sessions" not in tables


def test_wal_mode_and_edd_column(tmp_path):
    path = init_db(tmp_path / "p.db")
    conn = get_connection(path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    cols = {r[1] for r in conn.execute("PRAGMA table_info(patients)")}
    assert "edd" in cols
    conn.close()


def test_glucose_requires_unit(tmp_path):
    conn = get_connection(init_db(tmp_path / "p.db"))
    conn.execute("INSERT INTO patients(patient_id, display_name, edd) VALUES ('P001', 'Demo', '2027-02-01')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO vitals(patient_id, blood_glucose) VALUES ('P001', 95)")
    conn.close()
