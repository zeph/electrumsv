#!/usr/bin/env python
#
# Tests for MNEE database queries and table operations
#

import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, MagicMock, call

from electrumsv.app_state import app_state
from electrumsv.constants import TxFlags
from electrumsv.wallet_database.tables import (
    TransactionDeltaTable, TransactionDeltaHistoryRow, TransactionDeltaRow, 
    TransactionDeltaSumRow, DatabaseContext
)

class TestMNEEDatabaseQueries(unittest.TestCase):
    """Tests for database queries involving MNEE token data."""

    def setUp(self):
        # Create a temporary database file for testing
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_wallet.sqlite')
        
        # Create a test database with the correct schema including mnee_delta
        self.conn = sqlite3.connect(self.db_path)
        
        # Create minimal tables needed for testing TransactionDelta operations
        self.conn.executescript('''
            CREATE TABLE TransactionDeltas (
                tx_hash BLOB NOT NULL,
                keyinstance_id INTEGER NOT NULL,
                value_delta INTEGER NOT NULL,
                mnee_delta INTEGER,
                date_created INTEGER NOT NULL,
                date_updated INTEGER NOT NULL,
                description TEXT,
                PRIMARY KEY(tx_hash, keyinstance_id)
            );
            
            CREATE TABLE Transactions (
                tx_hash BLOB NOT NULL PRIMARY KEY,
                flags INTEGER NOT NULL
            );
            
            CREATE TABLE KeyInstances (
                keyinstance_id INTEGER NOT NULL PRIMARY KEY,
                account_id INTEGER NOT NULL,
                masterkey_id INTEGER,
                derivation_type INTEGER NOT NULL,
                derivation_data BLOB NOT NULL,
                script_type INTEGER NOT NULL,
                flags INTEGER NOT NULL,
                description TEXT
            );
        ''')
        self.conn.commit()
        
        # Create a mock DatabaseContext that uses our temp db
        self.db_context = MagicMock(spec=DatabaseContext)
        self.db_context.acquire_connection = MagicMock(return_value=self.conn)
        self.db_context.release_connection = MagicMock()
        
        # Insert some sample data
        self.tx_hash1 = b'0' * 32
        self.tx_hash2 = b'1' * 32
        
        # Insert a test account
        self.conn.execute('''
            INSERT INTO KeyInstances (keyinstance_id, account_id, masterkey_id, derivation_type, 
                                     derivation_data, script_type, flags, description)
            VALUES (101, 1, 1, 0, X'', 0, 1, NULL)
        ''')
        
        # Insert test transactions
        self.conn.execute(
            "INSERT INTO Transactions (tx_hash, flags) VALUES (?, ?)",
            (self.tx_hash1, TxFlags.HasHeight)
        )
        self.conn.execute(
            "INSERT INTO Transactions (tx_hash, flags) VALUES (?, ?)",
            (self.tx_hash2, TxFlags.HasHeight)
        )
        
        # Insert test transaction deltas with mnee_delta values
        self.conn.execute('''
            INSERT INTO TransactionDeltas 
            (tx_hash, keyinstance_id, value_delta, mnee_delta, date_created, date_updated)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (self.tx_hash1, 101, 1000, 5000, 1622565000, 1622565000))
        
        self.conn.execute('''
            INSERT INTO TransactionDeltas 
            (tx_hash, keyinstance_id, value_delta, mnee_delta, date_created, date_updated)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (self.tx_hash2, 101, 2000, 7500, 1622565000, 1622565000))
        
        self.conn.commit()
        
    def tearDown(self):
        # Close the database and remove temporary files
        if hasattr(self, 'conn') and self.conn:
            self.conn.close()
        shutil.rmtree(self.temp_dir)

    def test_read_history_with_mnee(self):
        """Test that read_history returns TransactionDeltaHistoryRow with mnee_delta."""
        # Create the table with our mock db_context
        table = TransactionDeltaTable(self.db_context)
        
        # Read history for account_id=1
        history_rows = table.read_history(1)
        
        # Verify we got rows back
        self.assertEqual(len(history_rows), 2, "Should have 2 history rows")
        
        # Check first row
        row1 = [row for row in history_rows if row.tx_hash == self.tx_hash1][0]
        self.assertEqual(row1.value_delta, 1000)
        self.assertEqual(row1.mnee_delta, 5000)
        
        # Check second row
        row2 = [row for row in history_rows if row.tx_hash == self.tx_hash2][0]
        self.assertEqual(row2.value_delta, 2000)
        self.assertEqual(row2.mnee_delta, 7500)

    def test_read_transaction_value_with_mnee(self):
        """Test that read_transaction_value returns TransactionDeltaSumRow with mnee_delta."""
        # Create the table with our mock db_context
        table = TransactionDeltaTable(self.db_context)
        
        # Read transaction value for tx_hash1
        sum_rows = table.read_transaction_value(self.tx_hash1)
        
        # Verify we got a row back
        self.assertEqual(len(sum_rows), 1, "Should have 1 sum row")
        
        # Check the row
        row = sum_rows[0]
        self.assertIsInstance(row, TransactionDeltaSumRow)
        self.assertEqual(row.value_delta, 1000)
        self.assertEqual(row.mnee_delta, 5000) 

    def test_create_or_update_relative_values_with_mnee(self):
        """Test creating or updating transaction deltas with mnee_delta values."""
        # Create the table with our mock db_context
        table = TransactionDeltaTable(self.db_context)
        
        # Create a new tx_hash for testing
        new_tx_hash = b'2' * 32
        
        # Create a new transaction delta row with mnee_delta
        delta_row = TransactionDeltaRow(
            tx_hash=new_tx_hash,
            keyinstance_id=101,
            value_delta=3000,
            mnee_delta=10000
        )
        
        # Insert the row
        table.create_or_update_relative_values([delta_row])
        
        # Read back and verify
        cursor = self.conn.execute(
            "SELECT value_delta, mnee_delta FROM TransactionDeltas WHERE tx_hash = ?",
            (new_tx_hash,)
        )
        row = cursor.fetchone()
        self.assertIsNotNone(row, "New row wasn't inserted")
        value_delta, mnee_delta = row
        self.assertEqual(value_delta, 3000)
        self.assertEqual(mnee_delta, 10000)
        
        # Now update the existing row by adding another delta
        update_row = TransactionDeltaRow(
            tx_hash=new_tx_hash,
            keyinstance_id=101,
            value_delta=1000,
            mnee_delta=2000
        )
        
        table.create_or_update_relative_values([update_row])
        
        # Read back and verify the update was applied relatively
        cursor = self.conn.execute(
            "SELECT value_delta, mnee_delta FROM TransactionDeltas WHERE tx_hash = ?",
            (new_tx_hash,)
        )
        row = cursor.fetchone()
        self.assertIsNotNone(row, "Updated row is missing")
        value_delta, mnee_delta = row
        self.assertEqual(value_delta, 4000)  # 3000 + 1000
        self.assertEqual(mnee_delta, 12000)  # 10000 + 2000

    def test_read_history_domain_with_mnee(self):
        """Test that read_history_domain correctly includes mnee_delta."""
        # Create the table with our mock db_context
        table = TransactionDeltaTable(self.db_context)
        
        # Read history for a specific domain (only include keyinstance_id=101)
        history_rows = table.read_history(1, domain={101})
        
        # Verify we got rows back
        self.assertEqual(len(history_rows), 2, "Should have 2 history rows")
        
        # Check that both rows have the mnee_delta field
        for row in history_rows:
            self.assertIsNotNone(row.mnee_delta, "mnee_delta should not be None")
            self.assertIn(row.mnee_delta, [5000, 7500])

    def test_update_relative_sql_with_mnee(self):
        """Test the UPDATE_RELATIVE_SQL query with mnee_delta."""
        # We're checking that the SQL statement in TransactionDeltaTable
        # has been updated to support mnee_delta
        
        # Get the SQL statement from the table class
        sql = TransactionDeltaTable.UPDATE_RELATIVE_SQL
        
        # Check that the SQL includes mnee_delta
        self.assertIn("mnee_delta=COALESCE(mnee_delta,0)+?", sql, 
                      "UPDATE_RELATIVE_SQL should update mnee_delta")

    def test_read_history_sql_with_mnee(self):
        """Test the READ_HISTORY_SQL query includes mnee_delta."""
        # We're checking that the SQL statement in TransactionDeltaTable
        # has been updated to include mnee_delta in the SELECT
        
        # Get the SQL statements from the table class
        read_sql = TransactionDeltaTable.READ_HISTORY_SQL
        read_domain_sql = TransactionDeltaTable.READ_HISTORY_DOMAIN_SQL
        
        # Check that both SQL queries include TOTAL(TD.mnee_delta)
        self.assertIn("TOTAL(TD.mnee_delta)", read_sql, 
                      "READ_HISTORY_SQL should select mnee_delta")
        self.assertIn("TOTAL(TD.mnee_delta)", read_domain_sql, 
                      "READ_HISTORY_DOMAIN_SQL should select mnee_delta")

if __name__ == '__main__':
    unittest.main() 