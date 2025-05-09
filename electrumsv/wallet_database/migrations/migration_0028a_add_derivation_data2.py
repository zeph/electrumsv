import logging
import sqlite3

from ...constants import TxFlags
from ...types import PasswordTokenProtocol
from .. import migration
from ..migration import ProgressCallbacks

logger = logging.getLogger('migrations')

def execute(conn: sqlite3.Connection, password_token: PasswordTokenProtocol,
        callbacks: ProgressCallbacks) -> None:
    """
    This migration adds the derivation_data2 column to the KeyInstances table.
    
    This is needed because migration_0029_reference_server requires this column
    but it wasn't explicitly added in previous migrations.
    """
    migration.update_migration_version(conn, 28)

    # Add the derivation_data2 column to the KeyInstances table
    # Initially it will be NULL for all existing rows
    try:
        conn.execute("ALTER TABLE KeyInstances ADD COLUMN derivation_data2 BLOB DEFAULT NULL")
        logger.debug("Added derivation_data2 column to KeyInstances table")
    except sqlite3.OperationalError:
        # If the column already exists, this will fail with "duplicate column name"
        logger.debug("derivation_data2 column already exists in KeyInstances table")
        pass
        
    # Add the required indexes for the new column
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_keyinstance_derivation_data2 ON KeyInstances(derivation_data2)")
        logger.debug("Created index on derivation_data2 column")
    except sqlite3.OperationalError:
        logger.debug("Index on derivation_data2 column already exists")
        pass
    
    # Rename the original migration to migration_0029
    conn.execute("UPDATE WalletData SET value=29 WHERE key='migration_version' AND value=28") 