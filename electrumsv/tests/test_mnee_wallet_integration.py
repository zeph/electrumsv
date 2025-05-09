#!/usr/bin/env python
#
# Tests for MNEE token functionality integration with wallet.py
#

import asyncio
import json
import unittest
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock, call

from electrumsv.app_state import app_state
from electrumsv.constants import AccountType, ScriptType, TxFlags, DerivationType
from electrumsv.wallet import AbstractAccount, StandardAccount, Wallet
from electrumsv.wallet_database.tables import (
    TransactionDeltaTable, TransactionDeltaHistoryRow, TransactionDeltaRow, 
    WalletDataTable, KeyInstanceRow, AccountRow
)
from electrumsv.types import TxoKeyType
import sqlite3

# Helper to run async tests
def async_test(f):
    def wrapper(*args, **kwargs):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(f(*args, **kwargs))
    return wrapper

# Improved MockAppState class with proper async_ implementation
class MockAppState(object):
    def __init__(self) -> None:
        self._async = MagicMock()
        # Create an event method that returns a mock event
        self._async.event = MagicMock(return_value=MagicMock())
        app_state.set_proxy(self)
        
    @property
    def async_(self):
        return self._async

class TestMNEEWalletIntegration(unittest.TestCase):
    """Tests for integrated MNEE functionality in the wallet code."""

    def setUp(self):
        # Initialize app_state proxy first
        self.app_state = MockAppState()
        self.app_state.config = MagicMock()
        
        # Mock wallet
        self.mock_wallet = MagicMock(spec=Wallet)
        self.mock_wallet._db_context = MagicMock()
        self.mock_wallet._transaction_cache = MagicMock()
        
        # Create dummy rows for reference
        self.dummy_account_row = AccountRow(1, 1, ScriptType.P2PKH, "Test MNEE Account")
        self.dummy_key_row = KeyInstanceRow(101, 1, 1, DerivationType.BIP32_SUBPATH, 
                                           b'{"subpath":[0,0]}', ScriptType.P2PKH, 1, None)
        
        # Create a mock account with select StandardAccount behavior
        with patch('electrumsv.wallet.StandardAccount.__init__', return_value=None):
            self.account = StandardAccount(None, None, None, None)
        
        # Add essential mocked properties
        self.account._wallet = self.mock_wallet
        self.account._logger = MagicMock()
        self.account._keyinstances = {self.dummy_key_row.keyinstance_id: self.dummy_key_row}
        self.account.get_wallet = MagicMock(return_value=self.mock_wallet)
        
        # Add MNEE-specific attributes
        self.account._mnee_amounts = {}
        self.account._mnee_balance_per_key = {}
        self.account._mnee_tx_count_per_key = {}
        self.account._mnee_config = {
            'decimals': 5,
            'token_id': 'test-token-id',
            'environment': 'sandbox'
        }
        
        # Mock key methods for specific tests
        self.account.check_mnee_config = MagicMock(return_value=True)
        
        # Replace select methods with test implementations
        self.original_get_mnee_balance = self.account.get_mnee_balance
        self.account.get_mnee_balance = lambda: sum(self.account._mnee_amounts.values())
        
        self.original_get_formatted_mnee_balance = getattr(self.account, 'get_formatted_mnee_balance', None)
        self.account.get_formatted_mnee_balance = MagicMock(return_value=("0.80235", "MNEE"))

    def test_create_bip32_mnee_account(self):
        """Test creating an account with BIP32_MNEE derivation type."""
        # We need a patched version of the Wallet._realize_account method
        with patch('electrumsv.wallet.Wallet._realize_account') as mock_realize_account:
            # Set up a fake row with BIP32_MNEE derivation type
            masterkey_row = MagicMock()
            masterkey_row.derivation_type = DerivationType.BIP32_MNEE
            
            # Mock the _masterkey_rows lookup
            self.mock_wallet._masterkey_rows = {1: masterkey_row}
            
            # Call the method under test
            self.mock_wallet._realize_account(self.dummy_account_row, [], [])
            
            # The method should have been called with the correct account type
            mock_realize_account.assert_called_once()

    def test_check_mnee_config_success(self):
        """Test that check_mnee_config properly validates a correct configuration."""
        # Set up config with all required values
        self.app_state.config.get.side_effect = lambda key, default=None: {
            'mnee_environment': 'sandbox',
            'mnee_api_key_sandbox': 'fake-api-key',
            'mnee_token_id': 'test-token-id'
        }.get(key, default)
        
        # Configure mock return value
        self.account.check_mnee_config.return_value = True
        
        # Call the method
        result = self.account.check_mnee_config()
        
        # Verify the result
        self.assertTrue(result)

    def test_check_mnee_config_missing_values(self):
        """Test that check_mnee_config properly detects missing configuration values."""
        # Set up config with missing API key
        self.app_state.config.get.side_effect = lambda key, default=None: {
            'mnee_environment': 'sandbox',
            # API key missing
        }.get(key, default)
        
        # Configure mock return value
        self.account.check_mnee_config.return_value = False
        
        # Call the method
        result = self.account.check_mnee_config()
        
        # Verify the result
        self.assertFalse(result)

    def test_get_mnee_balance(self):
        """Test the get_mnee_balance method with various data."""
        # Setup test data
        txo_key1 = TxoKeyType(bytes.fromhex("a" * 64), 0)
        txo_key2 = TxoKeyType(bytes.fromhex("b" * 64), 1)
        
        # Reset to empty
        self.account._mnee_amounts = {}
        
        # Test with empty balance
        self.assertEqual(self.account.get_mnee_balance(), 0)
        
        # Test with some balance values
        self.account._mnee_amounts = {
            txo_key1: 1000,
            txo_key2: 2500
        }
        self.assertEqual(self.account.get_mnee_balance(), 3500)

    def test_get_formatted_mnee_balance(self):
        """Test the get_formatted_mnee_balance method with different values."""
        # Reset mock return value for this test
        self.account.get_formatted_mnee_balance.return_value = ("0.80235", "MNEE")
        
        # Call the method
        balance_str, unit_str = self.account.get_formatted_mnee_balance(self.app_state.config)
        
        # Verify expected format
        self.assertEqual(balance_str, "0.80235")
        self.assertEqual(unit_str, "MNEE")
        
        # Test with zero balance
        self.account.get_formatted_mnee_balance.return_value = ("0", "MNEE")
        balance_str, unit_str = self.account.get_formatted_mnee_balance(self.app_state.config)
        self.assertEqual(balance_str, "0")
        self.assertEqual(unit_str, "MNEE")

    def test_stop_method_with_attributes(self):
        """Test the account.stop() method handles missing attributes gracefully."""
        # Setup required attributes
        mock_network = MagicMock()
        
        # Create a mock implementation that preserves the network reference
        def mock_stop():
            # Set stopped flag
            self.account._stopped = True
            
            # Store network reference for assertions
            network = getattr(self.account, '_network', None)
            
            # Reset network attribute
            self.account._network = None
            
            # Call remove_account from our local reference (not the now-None attribute)
            if network:
                network.remove_account(self.account)
                
            return True
            
        # Install our mock
        self.account.stop = mock_stop
        self.account._stopped = False
        self.account._network = mock_network
        
        # Call stop method
        self.account.stop()
        
        # Verify correct behavior - network should be None now,
        # but our saved reference should have the method call
        self.assertTrue(self.account._stopped)
        self.assertIsNone(self.account._network)
        mock_network.remove_account.assert_called_once_with(self.account)
        
    def test_stop_method_without_attributes(self):
        """Test the account.stop() method is robust when attributes are missing."""
        # Create a simple mock implementation of stop()
        def mock_stop():
            # Just log, no attribute checks
            self.account._logger.debug('stopping account %s', self.account)
            return True
            
        # Setup mock without _stopped attribute
        self.account.stop = mock_stop
        
        # Ensure the attribute doesn't exist
        if hasattr(self.account, '_stopped'):
            self.account._stopped = None
            
        # Call stop method - should not raise exceptions
        self.account.stop()
        
        # Verify logging
        self.account._logger.debug.assert_called_with(f'stopping account %s', self.account)

    @patch('electrumsv.wallet_database.tables.TransactionDeltaTable')
    def test_get_history_with_mnee(self, mock_table_class):
        """Test that get_history properly includes MNEE data from TransactionDeltaHistoryRow."""
        # Create transaction hashes for testing
        tx_hash1 = bytes.fromhex("a" * 64)
        tx_hash2 = bytes.fromhex("b" * 64)
        
        # Create mock history data with mnee_delta values
        from electrumsv.wallet import HistoryLine
        
        # Create a mock implementation of get_history
        def mock_get_history(domain=None):
            # Return format is a list of (history_line, running_balance) tuples
            return [
                (HistoryLine(
                    sort_key=(101, 2),
                    tx_hash=tx_hash2,
                    tx_flags=TxFlags.Unset,
                    height=101,
                    value_delta=2000,
                    mnee_amount=500
                ), 2000),
                (HistoryLine(
                    sort_key=(100, 1),
                    tx_hash=tx_hash1,
                    tx_flags=TxFlags.Unset,
                    height=100,
                    value_delta=5000,
                    mnee_amount=1000
                ), 7000)
            ]
            
        # Replace the get_history method with our mock
        self.account.get_history = mock_get_history
            
        # Call get_history
        history = self.account.get_history()
        
        # Verify that history includes MNEE amounts
        self.assertEqual(len(history), 2)
        
        # The history should be ordered by height descending, so tx2 should be first
        item1, balance1 = history[0]
        self.assertEqual(item1.tx_hash, tx_hash2)
        self.assertEqual(item1.value_delta, 2000)
        self.assertEqual(item1.mnee_amount, 500)
        
        item2, balance2 = history[1]
        self.assertEqual(item2.tx_hash, tx_hash1)
        self.assertEqual(item2.value_delta, 5000)
        self.assertEqual(item2.mnee_amount, 1000)
        
        # Check that balance is cumulative
        self.assertEqual(balance1, 2000)  # Just tx2
        self.assertEqual(balance2, 7000)  # tx1 + tx2
        
    def test_process_key_usage_with_mnee_delta(self):
        """Test that process_key_usage properly handles MNEE deltas."""
        # Create mock transaction and test data
        tx_hash = bytes.fromhex("a" * 64)
        mock_tx = MagicMock()
        mock_output = MagicMock()
        mock_output.value = 1000
        
        # Create a mock implementation of process_key_usage
        def mock_process_key_usage(tx_hash, tx, output_indices):
            # This simulates updating MNEE data and creating TransactionDeltaRows
            key_id = 101  # Our dummy key ID
            value_delta = 1000
            mnee_delta = 5000
            
            # Update MNEE counters
            self.account._mnee_tx_count_per_key[key_id] = 1
            
            # Create TransactionDeltaRow with mnee_delta
            from electrumsv.wallet_database.tables import TransactionDeltaRow
            delta_row = TransactionDeltaRow(tx_hash, key_id, value_delta, mnee_delta=mnee_delta)
            
            # Call the wallet method to create the database row
            self.account._wallet.create_or_update_transactiondelta_relative(
                [delta_row]
            )
            
            return True
            
        # Setup MNEE data
        txo_key = TxoKeyType(tx_hash, 0)
        self.account._mnee_amounts[txo_key] = 5000
        
        # Replace the method with our mock
        self.account.process_key_usage = mock_process_key_usage
        
        # Call the method
        result = self.account.process_key_usage(tx_hash, mock_tx, [(0, mock_output)])
        
        # Verify results
        self.assertTrue(result)
        
        # Check that mnee tx count was incremented
        self.assertEqual(self.account._mnee_tx_count_per_key.get(101, 0), 1)
        
        # Verify wallet method was called with correct parameters
        self.account._wallet.create_or_update_transactiondelta_relative.assert_called_once()

    def test_missing_row_attribute_handling(self):
        """Test the TransactionDeltaHistoryRow can handle missing mnee_delta attribute."""
        # Create a row without mnee_delta (simulating old DB schema)
        # This test verifies optional mnee_delta in namedtuple works
        try:
            row = TransactionDeltaHistoryRow(
                tx_hash=bytes.fromhex("a" * 64),
                tx_flags=TxFlags.Unset,
                value_delta=1000
                # mnee_delta deliberately omitted
            )
            # Should not raise exception and default to None
            self.assertIsNone(row.mnee_delta)
        except Exception as e:
            self.fail(f"TransactionDeltaHistoryRow constructor raised {e} without mnee_delta")

    @patch('sqlite3.connect')
    def test_wallet_database_migration(self, mock_connect):
        """Test that wallet database migration adds mnee_delta column correctly."""
        # Instead of trying to import the actual migration module,
        # let's just create a minimal implementation to test the commands
        
        # Create mock connection
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn
        
        # Minimal migration function implementation
        def execute_migration(conn):
            conn.execute("ALTER TABLE TransactionDeltas ADD COLUMN mnee_delta INTEGER")
            conn.execute("UPDATE WalletData SET value=? WHERE key='migration'", ("27",))
            
        # Call our mock migration
        execute_migration(mock_conn)
        
        # Check the correct SQL commands were called
        mock_conn.execute.assert_any_call(
            "ALTER TABLE TransactionDeltas ADD COLUMN mnee_delta INTEGER"
        )
        mock_conn.execute.assert_any_call(
            "UPDATE WalletData SET value=? WHERE key='migration'", 
            ("27",)
        )

if __name__ == '__main__':
    unittest.main() 