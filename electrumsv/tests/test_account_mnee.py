#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Tests for MNEE functionality in the MneeAccount class.
"""

import tempfile
import os
import unittest
from unittest.mock import patch, MagicMock, Mock
from collections import defaultdict

from electrumsv.wallet import AccountRow, KeyInstanceRow, TransactionOutputRow
from electrumsv.mnee import MneeAccount, MNEE_COIN_TYPE, MNEE_PURPOSE, MNEE_ACCOUNT
from electrumsv.constants import DerivationType, ScriptType, KeyInstanceFlag

class TestMNEEDataHandling(unittest.TestCase):
    
    def setUp(self):
        # Create a mock wallet
        self.mock_wallet = MagicMock()
        self.mock_storage = MagicMock()
        self.mock_wallet.get_storage.return_value = self.mock_storage
        
        # Create a temporary folder for storage
        self.temp_dir = tempfile.TemporaryDirectory()
        
        # Set up the storage dictionary for MNEE data
        self.storage_data = {}
        def mock_get(key, default=None):
            return self.storage_data.get(key, default)
        def mock_put(key, value):
            self.storage_data[key] = value
            
        self.mock_storage.get.side_effect = mock_get
        self.mock_storage.put.side_effect = mock_put
        
        # Create a test account
        self.account_row = AccountRow(1, 1, ScriptType.P2PKH, "Test Account")
        
        # Set up key instances
        self.keyinstance_rows = [
            KeyInstanceRow(1, 1, 1, DerivationType.BIP32_SUBPATH, 
                          '{"subpath": [0, 0]}'.encode(), ScriptType.P2PKH, 
                          KeyInstanceFlag.IS_ACTIVE, None),
            KeyInstanceRow(2, 1, 1, DerivationType.BIP32_SUBPATH, 
                          '{"subpath": [0, 1]}'.encode(), ScriptType.P2PKH, 
                          KeyInstanceFlag.IS_ACTIVE, None),
        ]
        
        # Create account with mock data
        with patch('electrumsv.wallet.AbstractAccount._load_keys'), \
             patch('electrumsv.wallet.AbstractAccount._load_txos'), \
             patch('electrumsv.wallet.SimpleDeterministicAccount.__init__') as mock_init:
            mock_init.return_value = None
            self.account = MneeAccount(self.mock_wallet, self.account_row, 
                                     self.keyinstance_rows, [])
            # Manually set up the necessary attributes that would normally be set in parent constructors
            self.account._keyinstances = {k.keyinstance_id: k for k in self.keyinstance_rows}
            self.account._mnee_tx_count_per_key = defaultdict(int)
            self.account._mnee_balances_per_key = {}
            
            # Add get_wallet method to account
            self.account.get_wallet = MagicMock(return_value=self.mock_wallet)
            
            # Mock the network for fetch_mnee_transactions
            self.account._wallet._network = Mock()
    
    def tearDown(self):
        self.temp_dir.cleanup()
        
    def test_get_mnee_balance_for_keyid(self):
        """Test the get_mnee_balance_for_keyid method returns correct balance."""
        self.account._mnee_balances_per_key = {1: 100, 2: 200}
        self.assertEqual(self.account.get_mnee_balance_for_keyid(1), 100)
        self.assertEqual(self.account.get_mnee_balance_for_keyid(2), 200)
        self.assertEqual(self.account.get_mnee_balance_for_keyid(3), 0)  # Non-existent key
        
    def test_clear_mnee_data_for_key(self):
        """Test that clearing MNEE data for a key works correctly."""
        # Set up test data
        self.account._mnee_balances_per_key = {1: 100, 2: 200}
        self.account._mnee_tx_count_per_key = {1: 2, 2: 3}
        
        # Clear data for key 1
        self.account.clear_mnee_data_for_key(1)
        
        # Verify key 1 data is gone but key 2 remains
        self.assertNotIn(1, self.account._mnee_balances_per_key)
        self.assertNotIn(1, self.account._mnee_tx_count_per_key)
        self.assertEqual(self.account._mnee_balances_per_key.get(2), 200)
        self.assertEqual(self.account._mnee_tx_count_per_key.get(2), 3)
        
    def test_mnee_derivation_paths(self):
        """Test that MNEE uses correct BIP44 derivation paths."""
        # Test base path
        base_path = self.account.get_base_path()
        self.assertEqual(base_path, [MNEE_PURPOSE, MNEE_COIN_TYPE, MNEE_ACCOUNT])
        
        # Test full derivation path
        subpath = (0, 1)  # This would be change address #1
        full_path = self.account.get_full_derivation_path(subpath)
        expected_path = [MNEE_PURPOSE, MNEE_COIN_TYPE, MNEE_ACCOUNT, 0, 1]
        self.assertEqual(full_path, expected_path)
    
    @patch('electrumsv.mnee.MneeAccount.fetch_txids_from_mnee_api')
    @patch('electrumsv.mnee.MneeAccount._external_transaction_request')
    @patch('electrumsv.mnee.MneeAccount.decode_mnee_from_transaction')
    def test_fetch_mnee_transactions(self, mock_decode, mock_fetch_tx, mock_fetch_txids):
        """Test the fetch_mnee_transactions method fetches and processes transactions correctly."""
        # Set up mocks
        mock_fetch_txids.return_value = ['txid1', 'txid2']
        
        # Mock transaction objects
        mock_tx1 = MagicMock()
        mock_tx2 = MagicMock()
        
        # Set up transaction cache behavior
        self.account._wallet._transaction_cache.get_transaction.side_effect = [None, mock_tx2]
        
        # Set up external request mock to return tx1
        mock_fetch_tx.return_value = mock_tx1
        
        # Set up decode method to return token amount
        mock_decode.side_effect = [100, 200]
        
        # Call the method
        result = self.account.fetch_mnee_transactions(1)
        
        # Verify results
        self.assertEqual(len(result), 2)
        tx_hash1, amount1 = result[0]
        tx_hash2, amount2 = result[1]
        self.assertEqual(amount1, 100)
        self.assertEqual(amount2, 200)
        
        # Verify transaction count and balance were updated
        self.assertEqual(self.account._mnee_tx_count_per_key[1], 2)
        self.assertEqual(self.account._mnee_balances_per_key[1], 300)
        
        # Verify add_transaction was called for tx1 (tx2 was already in cache)
        self.account._wallet.add_transaction.assert_called_once()

if __name__ == '__main__':
    unittest.main() 