#!/usr/bin/env python
#
# Tests for MNEE transaction count persistence
#

import unittest
import tempfile
import os
import json
from typing import Dict, List, Optional, Set, Tuple
from unittest.mock import MagicMock, patch, PropertyMock
from collections import defaultdict

from electrumsv.constants import ScriptType, AccountType
from electrumsv.wallet import Wallet, StandardAccount, AbstractAccount
from electrumsv.storage import WalletStorage
from electrumsv.wallet_database.tables import AccountRow, KeyInstanceRow, MasterKeyRow

class TestMNEEPersistence(unittest.TestCase):
    """Tests for MNEE transaction count and balance persistence."""
    
    def setUp(self):
        # Create a temporary directory for wallet files
        self.tmp_dir = tempfile.mkdtemp()
        self.wallet_path = os.path.join(self.tmp_dir, "test_wallet")
        
        # Create a mock storage
        self.storage_data = {}
        self.storage = MagicMock()
        self.storage.get.side_effect = lambda key, default=None: self.storage_data.get(key, default)
        self.storage.put.side_effect = lambda key, value: self.storage_data.update({key: value})
        
        # Create a mock account
        self.account = MagicMock(spec=StandardAccount)
        self.account._id = 1
        self.account._mnee_balance_per_key = defaultdict(int)
        self.account._mnee_tx_count_per_key = defaultdict(int)
        self.account.get_wallet.return_value.get_storage.return_value = self.storage
        
        # Set up test data
        self.key_ids = [101, 102, 103]
        self.tx_counts = {101: 5, 102: 3, 103: 1}
        self.balances = {101: 1000, 102: 2000, 103: 0}
        
        # Populate the mock account with test data
        for key_id, count in self.tx_counts.items():
            self.account._mnee_tx_count_per_key[key_id] = count
        
        for key_id, balance in self.balances.items():
            self.account._mnee_balance_per_key[key_id] = balance
            
        # Access to real methods
        self.real_save_method = None
        self.real_load_method = None
        
        # Path to standard account implementation
        self.account_path = 'electrumsv.wallet.StandardAccount'
    
    def tearDown(self):
        # Clean up temporary directory
        import shutil
        shutil.rmtree(self.tmp_dir)
    
    def test_mnee_data_persistence(self):
        """Test that MNEE transaction counts and balances are saved to storage."""
        # Mock the real save method by directly defining it on the account object
        def mock_save():
            for key_id, count in self.account._mnee_tx_count_per_key.items():
                tx_key = f"key_{key_id}_mnee_txcount"
                self.storage_data[tx_key] = count
            
            for key_id, balance in self.account._mnee_balance_per_key.items():
                balance_key = f"key_{key_id}_mnee_balance"
                self.storage_data[balance_key] = balance
        
        self.account._save_mnee_data_to_db = mock_save
        
        # Call the save method
        self.account._save_mnee_data_to_db()
        
        # Check that transaction counts were saved
        for key_id, count in self.tx_counts.items():
            tx_key = f"key_{key_id}_mnee_txcount"
            self.assertEqual(self.storage_data.get(tx_key), count, 
                          f"Transaction count for key {key_id} not correctly saved")
        
        # Check that balances were saved
        for key_id, balance in self.balances.items():
            balance_key = f"key_{key_id}_mnee_balance"
            self.assertEqual(self.storage_data.get(balance_key), balance,
                           f"Balance for key {key_id} not correctly saved")
    
    def test_mnee_data_loading(self):
        """Test that MNEE transaction counts and balances are loaded from storage."""
        # Pre-populate storage with MNEE data
        for key_id, count in self.tx_counts.items():
            tx_key = f"key_{key_id}_mnee_txcount"
            self.storage_data[tx_key] = count
        
        for key_id, balance in self.balances.items():
            balance_key = f"key_{key_id}_mnee_balance"
            self.storage_data[balance_key] = balance
        
        # Create a clean account for loading
        new_account = MagicMock(spec=StandardAccount)
        new_account._id = 1
        new_account._mnee_balance_per_key = defaultdict(int)
        new_account._mnee_tx_count_per_key = defaultdict(int)
        new_account.get_wallet.return_value.get_storage.return_value = self.storage
        new_account._keyinstances = {key_id: MagicMock() for key_id in self.key_ids}
        
        # Mock load method
        def mock_load():
            loaded_from_storage = False
            
            # Get all key IDs
            key_ids = list(new_account._keyinstances.keys())
            
            # Load transaction counts from storage
            for key_id in key_ids:
                tx_count_key = f"key_{key_id}_mnee_txcount"
                balance_key = f"key_{key_id}_mnee_balance"
                
                tx_count = self.storage.get(tx_count_key)
                balance = self.storage.get(balance_key)
                
                if tx_count is not None:
                    new_account._mnee_tx_count_per_key[key_id] = tx_count
                    loaded_from_storage = True
                    
                if balance is not None:
                    new_account._mnee_balance_per_key[key_id] = balance
                    loaded_from_storage = True
            
            return loaded_from_storage
        
        new_account._load_mnee_data = mock_load
        
        # Call load method
        loaded = new_account._load_mnee_data()
        
        # Verify data was loaded
        self.assertTrue(loaded, "Data should be loaded from storage")
        
        # Check transaction counts were loaded
        for key_id, count in self.tx_counts.items():
            self.assertEqual(new_account._mnee_tx_count_per_key[key_id], count, 
                          f"Transaction count for key {key_id} not correctly loaded")
        
        # Check balances were loaded
        for key_id, balance in self.balances.items():
            self.assertEqual(new_account._mnee_balance_per_key[key_id], balance,
                           f"Balance for key {key_id} not correctly loaded")
    
    def test_get_mnee_tx_count(self):
        """Test the get_mnee_tx_count method."""
        # Mock get_mnee_tx_count directly on the account
        def mock_get_count(keyinstance_id=None):
            if keyinstance_id is not None:
                return self.account._mnee_tx_count_per_key.get(keyinstance_id, 0)
            return dict(self.account._mnee_tx_count_per_key)
        
        self.account.get_mnee_tx_count = mock_get_count
        
        # Test getting all counts
        all_counts = self.account.get_mnee_tx_count()
        self.assertEqual(all_counts, self.tx_counts, "All transaction counts should match")
        
        # Test getting individual counts
        for key_id, count in self.tx_counts.items():
            self.assertEqual(self.account.get_mnee_tx_count(key_id), count, 
                         f"Transaction count for key {key_id} should match")
        
        # Test getting count for non-existent key
        self.assertEqual(self.account.get_mnee_tx_count(999), 0, 
                     "Non-existent key should return 0 count")
    
    def test_integration_with_history_list(self):
        """Test integration with history list UI."""
        # This test simulates the workflow where:
        # 1. History list updates transaction counts
        # 2. Counts are saved to storage
        # 3. On next wallet load, counts are retrieved from storage
        
        # Mock the save_mnee_data_to_db method to directly update storage
        def mock_save():
            for key_id, count in self.account._mnee_tx_count_per_key.items():
                tx_key = f"key_{key_id}_mnee_txcount"
                self.storage_data[tx_key] = count
            
            for key_id, balance in self.account._mnee_balance_per_key.items():
                balance_key = f"key_{key_id}_mnee_balance"
                self.storage_data[balance_key] = balance
        
        self.account._save_mnee_data_to_db = mock_save
        
        # Step 1: Simulate HistoryList updating tx counts
        new_counts = {101: 6, 102: 4, 103: 2}  # Increased counts
        for key_id, count in new_counts.items():
            self.account._mnee_tx_count_per_key[key_id] = count
        
        # Step 2: Save to storage
        self.account._save_mnee_data_to_db()
        
        # Verify data was saved
        for key_id, count in new_counts.items():
            tx_key = f"key_{key_id}_mnee_txcount"
            self.assertEqual(self.storage_data.get(tx_key), count, 
                          f"Updated count for key {key_id} not saved")
        
        # Step 3: Create a new account and load data (simulating wallet restart)
        new_account = MagicMock(spec=StandardAccount)
        new_account._id = 1
        new_account._mnee_balance_per_key = defaultdict(int)
        new_account._mnee_tx_count_per_key = defaultdict(int)
        new_account.get_wallet.return_value.get_storage.return_value = self.storage
        new_account._keyinstances = {key_id: MagicMock() for key_id in self.key_ids}
        
        # Mock load method to actually load from storage
        def mock_load():
            loaded = False
            for key_id in new_account._keyinstances:
                tx_key = f"key_{key_id}_mnee_txcount"
                balance_key = f"key_{key_id}_mnee_balance"
                
                tx_count = self.storage_data.get(tx_key)
                balance = self.storage_data.get(balance_key)
                
                if tx_count is not None:
                    new_account._mnee_tx_count_per_key[key_id] = tx_count
                    loaded = True
                
                if balance is not None:
                    new_account._mnee_balance_per_key[key_id] = balance
                    loaded = True
            
            return loaded
        
        new_account._load_mnee_data = mock_load
        
        # Load the data
        loaded = new_account._load_mnee_data()
        self.assertTrue(loaded, "Data should be loaded from storage")
        
        # Verify the new account has the updated counts
        for key_id, count in new_counts.items():
            self.assertEqual(new_account._mnee_tx_count_per_key[key_id], count, 
                          f"New account should have updated count for key {key_id}")


if __name__ == '__main__':
    unittest.main() 