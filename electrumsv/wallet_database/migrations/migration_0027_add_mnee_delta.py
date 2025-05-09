#!/usr/bin/env python
# ElectrumSV - lightweight Bitcoin SV client
# Copyright (C) 2024 The ElectrumSV Developers
#
# ElectrumSV is released under the MIT license. See LICENSE for more information.

"""
Add the 'mnee_delta' column to the 'TransactionDeltas' table.
"""

import sqlite3
import time
import json

from ...logs import logs


logger = logs.get_logger("db-migration[0027]")

def apply_migration(db: sqlite3.Connection, db_version: int) -> None:
    logger.info("applying.. (add mnee_delta column to TransactionDeltas)")
    cursor = db.cursor()
    try:
        # Add the new column, allowing NULL for existing rows, default 0 for new rows
        cursor.execute("ALTER TABLE TransactionDeltas ADD COLUMN mnee_delta INTEGER DEFAULT 0")
        # Optionally, update existing NULLs to 0 if DEFAULT didn't apply retroactively (SQLite behavior can vary)
        cursor.execute("UPDATE TransactionDeltas SET mnee_delta = 0 WHERE mnee_delta IS NULL")
        logger.info("applied.")
    except sqlite3.OperationalError as e:
        # Error code for "duplicate column name" can vary, but message usually contains it
        if "duplicate column name: mnee_delta" in str(e):
            logger.warning("column 'mnee_delta' already exists in TransactionDeltas table.")
        else:
            raise
    finally:
        cursor.close()

def execute(db: sqlite3.Connection) -> None:
    """Apply the migration and update the version number in the database."""
    logger.info("migrating the database to version 27")
    apply_migration(db, 26)
    db.execute("UPDATE WalletData SET value=? WHERE key='migration'", (json.dumps(27),))
    logger.info("migration complete") 