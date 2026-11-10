"""SQLite patient-records database, schema v1 (Phase 1 creates the schema only).

Purpose
    Phase 2 tools (vitals, kicks, appointments, labs) save and read patient data here. Phase 1
    only creates the tables so Phase 2 can start immediately. There are no save/read functions
    yet (Member 3 adds them in Phase 2).

Design notes
    - WAL journal mode: readers (the dashboard) do not block the writer (the agent).
      It reduces lock errors; it does not remove them, hence timeout=10.
    - check_same_thread=False: Streamlit may use the connection from different threads.
    - patients.edd (estimated due date) is stored, never a fixed week number: the week is
      computed from the EDD at query time, so it is never out of date.
    - No chat_sessions table: in Phase 2 LangGraph's SqliteSaver keeps its own tables in its
      own file (db/checkpoints.db).
    - Pseudonymous IDs (P001); this is pseudonymization, not anonymization.

Public interface (stable): init_db(path=None) -> Path, get_connection(path=None)

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from config import PATIENT_DB_PATH

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS patients (
    patient_id   TEXT PRIMARY KEY CHECK (patient_id GLOB 'P[0-9][0-9][0-9]*'),
    display_name TEXT NOT NULL,
    edd          DATE,                       -- estimated due date (ISO YYYY-MM-DD)
    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS vitals (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id       TEXT NOT NULL REFERENCES patients(patient_id),
    measured_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    systolic_bp      INTEGER CHECK (systolic_bp IS NULL OR systolic_bp BETWEEN 40 AND 300),
    diastolic_bp     INTEGER CHECK (diastolic_bp IS NULL OR diastolic_bp BETWEEN 20 AND 200),
    blood_glucose    REAL,
    glucose_unit     TEXT CHECK (glucose_unit IS NULL OR glucose_unit IN ('mg/dL', 'mmol/L')),
    fasting_status   TEXT CHECK (fasting_status IS NULL OR fasting_status IN ('fasting', 'post_meal', 'random')),
    symptoms         TEXT,
    gestational_week INTEGER,
    status           TEXT NOT NULL DEFAULT 'UNKNOWN'
                     CHECK (status IN ('NORMAL', 'FLAGGED', 'EMERGENCY', 'UNKNOWN')),
    CHECK (blood_glucose IS NULL OR glucose_unit IS NOT NULL)   -- a glucose value needs a unit
);

CREATE TABLE IF NOT EXISTS appointments (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id       TEXT NOT NULL REFERENCES patients(patient_id),
    appointment_date DATE NOT NULL,
    appointment_time TEXT NOT NULL,
    reason           TEXT,
    status           TEXT NOT NULL DEFAULT 'BOOKED' CHECK (status IN ('BOOKED', 'CANCELLED', 'COMPLETED')),
    created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (appointment_date, appointment_time)
);

CREATE TABLE IF NOT EXISTS fetal_kicks (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id       TEXT NOT NULL REFERENCES patients(patient_id),
    session_start    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    kicks_count      INTEGER NOT NULL CHECK (kicks_count >= 0),
    duration_minutes INTEGER NOT NULL CHECK (duration_minutes > 0),
    gestational_week INTEGER,
    alert_status     TEXT NOT NULL DEFAULT 'NONE' CHECK (alert_status IN ('NONE', 'INFO', 'ALERT', 'URGENT'))
);

CREATE TABLE IF NOT EXISTS lab_results (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id       TEXT NOT NULL REFERENCES patients(patient_id),
    recorded_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    test_name        TEXT NOT NULL,
    test_value       REAL NOT NULL,
    test_unit        TEXT NOT NULL,
    reference_range  TEXT,
    gestational_week INTEGER,
    alert_status     TEXT NOT NULL DEFAULT 'NORMAL' CHECK (alert_status IN ('NORMAL', 'FLAGGED', 'UNKNOWN'))
);

CREATE INDEX IF NOT EXISTS idx_vitals_patient ON vitals(patient_id, measured_at);
CREATE INDEX IF NOT EXISTS idx_kicks_patient ON fetal_kicks(patient_id, session_start);
CREATE INDEX IF NOT EXISTS idx_labs_patient ON lab_results(patient_id, recorded_at);
"""


def get_connection(path: Optional[Path] = None) -> sqlite3.Connection:
    """Open the database with WAL, foreign keys on and a 10-second busy timeout."""
    path = path or PATIENT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: Optional[Path] = None) -> Path:
    """Create all tables if they do not exist. Safe to call many times (idempotent)."""
    path = path or PATIENT_DB_PATH
    conn = get_connection(path)
    try:
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()
    finally:
        conn.close()
    return path
