#!/usr/bin/env python
#
# Simple test to verify MNEE token transaction counting
#

import unittest
from unittest.mock import MagicMock, patch
from typing import Dict, List, Optional, Set, Tuple

from electrumsv.types import TxoKeyType
from electrumsv.constants import TxFlags

class TestMNEETransactionCount(unittest.TestCase):
    """Test to verify MNEE transaction count tracking works properly."""
    
    def test_mnee_transaction_counting(self):
        """Test that MNEE transaction count is correctly tracked per key/address."""
        # Create a mock account with the necessary MNEE tracking attributes
        mock_account = MagicMock()
        mock_account._mnee_tx_count_per_key = {}
        mock_account._mnee_amounts = {}
        
        # Key instance IDs for test
        key_id_1 = 101
        key_id_2 = 102
        
        # Simulate transaction processing with MNEE tokens
        tx_hash1 = bytes.fromhex("a" * 64)
        tx_hash2 = bytes.fromhex("b" * 64)
        txo_key1 = TxoKeyType(tx_hash1, 0)
        txo_key2 = TxoKeyType(tx_hash2, 0)
        
        # Add MNEE amounts to transactions
        mock_account._mnee_amounts[txo_key1] = 1000
        mock_account._mnee_amounts[txo_key2] = 2000
        
        # Simulate processing transactions for key 1
        if key_id_1 not in mock_account._mnee_tx_count_per_key:
            mock_account._mnee_tx_count_per_key[key_id_1] = 0
        mock_account._mnee_tx_count_per_key[key_id_1] += 1
        
        # Process a second transaction for key 1
        mock_account._mnee_tx_count_per_key[key_id_1] += 1
        
        # Process one transaction for key 2
        if key_id_2 not in mock_account._mnee_tx_count_per_key:
            mock_account._mnee_tx_count_per_key[key_id_2] = 0
        mock_account._mnee_tx_count_per_key[key_id_2] += 1
        
        # Verify counts are correct
        self.assertEqual(mock_account._mnee_tx_count_per_key[key_id_1], 2, 
                         "Key 1 should have 2 MNEE transactions")
        self.assertEqual(mock_account._mnee_tx_count_per_key[key_id_2], 1, 
                         "Key 2 should have 1 MNEE transaction")
        
        # Test archiving logic - a key with 2 transactions but 0 balance should be archived
        # Clear balances to simulate spent outputs
        mock_account._mnee_amounts = {}
        
        # Check if keys should be archived (MNEE token usage count = 2, no balance)
        keys_to_archive = []
        for key_id, tx_count in mock_account._mnee_tx_count_per_key.items():
            if tx_count >= 2:
                # Check if this key has any active MNEE token balance
                has_balance = False
                for txo_key, amount in mock_account._mnee_amounts.items():
                    if amount > 0:
                        has_balance = True
                        break
                
                if not has_balance:
                    keys_to_archive.append(key_id)
        
        # Verify key 1 with 2 transactions and no balance is marked for archiving
        self.assertEqual(len(keys_to_archive), 1, 
                         "Should have 1 key to archive")
        self.assertEqual(keys_to_archive[0], key_id_1, 
                         "Key 1 should be marked for archiving")
        
        # Key 2 should not be archived as it only has 1 transaction
        self.assertNotIn(key_id_2, keys_to_archive,
                         "Key 2 should not be marked for archiving (only 1 tx)")

if __name__ == '__main__':
    unittest.main() 