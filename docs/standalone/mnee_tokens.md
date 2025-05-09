# MNEE Token Support

This document describes the MNEE token functionality in ElectrumSV, focusing on transaction counting and persistence.

## Overview

MNEE token support allows ElectrumSV to track and display MNEE token balances and transaction history alongside BSV transactions. Token transaction counts are maintained per address/key and persisted to the wallet storage, allowing for consistent display across wallet restarts.

## Implementation Details

### Key Components

1. **StandardAccount Extensions**:
   - `_mnee_balance_per_key`: Dictionary mapping keyinstance IDs to MNEE token balances
   - `_mnee_tx_count_per_key`: Dictionary mapping keyinstance IDs to transaction counts
   - Methods for saving/loading MNEE data to/from storage

2. **History List Integration**:
   - Displays combined BSV and MNEE transaction history
   - Updates transaction counts per key
   - Persists counts to storage

### Transaction Count Persistence

Transaction counts are persisted to the wallet's storage using the following pattern:

```python
# Save MNEE transaction counts to storage
for key_id, count in self._mnee_tx_count_per_key.items():
    key = f"key_{key_id}_mnee_txcount"
    storage.put(key, count)

# Save MNEE balances to storage
for key_id, balance in self._mnee_balance_per_key.items():
    key = f"key_{key_id}_mnee_balance"
    storage.put(key, balance)
```

When a wallet is loaded, MNEE data is restored from storage before calculating from transaction history:

```python
# First try to load from storage
for key_id in self._keyinstances.keys():
    tx_count_key = f"key_{key_id}_mnee_txcount"
    balance_key = f"key_{key_id}_mnee_balance"
    
    tx_count = storage.get(tx_count_key)
    balance = storage.get(balance_key)
    
    if tx_count is not None:
        self._mnee_tx_count_per_key[key_id] = tx_count
    
    if balance is not None:
        self._mnee_balance_per_key[key_id] = balance
```

This approach prevents the need to recalculate transaction counts from history each time the wallet is loaded.

## API Reference

### StandardAccount Methods

#### `get_mnee_balance()`
Returns the total MNEE balance across all keys.

#### `get_mnee_tx_count(keyinstance_id=None)`
Returns either the transaction count for a specific key or a dictionary of counts for all keys.

#### `get_formatted_mnee_balance(config)`
Returns a tuple of (formatted_balance_string, unit_name) for UI display.

#### `_save_mnee_data_to_db()`
Internal method to persist MNEE data to storage.

#### `_load_mnee_data()`
Internal method to load MNEE data from storage.

## Testing

You can test MNEE functionality with the included test suite:

```bash
# Run all MNEE tests
./run_mnee_tests.sh

# Run a specific MNEE test
python -m unittest electrumsv.tests.test_mnee_persistence
```

You can also use the diagnostic script to check MNEE data in a wallet:

```bash
python test_mnee_persistence.py /path/to/wallet
```

## Common Issues

1. **Missing Transaction Counts**: If transaction counts appear to be missing, check that the `_save_mnee_data_to_db()` method is called after counts are updated.

2. **UI Not Refreshing**: The UI may need to be refreshed after transaction counts are updated. Use the refresh button in the History view.

3. **Different Counts Between Wallets**: If different wallet files show different transaction counts for the same addresses, the counts may not be correctly synchronized with the blockchain.

## Future Improvements

- Store MNEE transaction data in the SQLite database alongside BSV transactions
- Add support for other token types using the same persistence mechanism
- Implement token transaction search and filtering in the UI 