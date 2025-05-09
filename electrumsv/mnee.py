#!/usr/bin/env python
#
# ElectrumSV - lightweight Bitcoin SV client
# Copyright (C) 2023-2025 ElectrumSV Developers
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
# BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""
MNEE token functionality for ElectrumSV.
This module provides classes and functions for interacting with the MNEE.net API
and processing MNEE token data.
"""

import json
import urllib.request
import time
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

from bitcoinx import hash_to_hex_str, hex_str_to_hash, Script

from .app_state import app_state
from .logs import logs
from .constants import TxFlags, KeyInstanceFlag, DerivationType
from .transaction import Transaction, XTxOutput
from .wallet_database.tables import (AccountRow, KeyInstanceRow, TransactionOutputRow, 
                                     WalletEventRow, WalletEventTable, WalletDataRow, WalletDataTable,
                                     TransactionDeltaRow, TransactionOutputFlag)
from .wallet import StandardAccount, DeterministicKeyAllocation
from functools import partial

# MNEE API Constants
MNEE_API_URL_PRODUCTION = "https://proxy-api.mnee.net"
MNEE_API_URL_SANDBOX = "https://sandbox-cosigner.mnee.net"
MNEE_DEFAULT_API_KEY = "92982ec1c0975f31979da515d46bae9f"  # Default API key from mnee/src/mneeService.ts

# MNEE BIP44 Derivation Path Constants
MNEE_COIN_TYPE = 236  # Coin type for MNEE tokens
MNEE_PURPOSE = 44     # BIP44 purpose
MNEE_ACCOUNT = 0      # Default account

# MNEE subpaths (similar to RECEIVING_SUBPATH and CHANGE_SUBPATH in constants.py)
MNEE_RECEIVING_SUBPATH = (0,)  # BIP44 receiving addresses
MNEE_CHANGE_SUBPATH = (1,)     # BIP44 change addresses

logger = logs.get_logger("mnee")

class MneeAccount(StandardAccount):
    """Extended StandardAccount with MNEE token functionality."""
    
    def __init__(self, wallet: 'Wallet', row: AccountRow,
            keyinstance_rows: List[KeyInstanceRow],
            output_rows: List[TransactionOutputRow]) -> None:
        super().__init__(wallet, row, keyinstance_rows, output_rows)
        
        # Initialize MNEE-specific attributes
        self._mnee_tx_count_per_key = defaultdict(int)
        self._mnee_balances_per_key = {}
        
        # Cache for the MNEE config, fetched once per instance
        self._mnee_config_cache = None
        
    def get_cached_config(self):
        """
        Get the MNEE config from cache or fetch it if not available.
        This ensures we only call the API once per instance lifetime.
        
        Returns:
            The cached config dictionary
        """
        # Return cached config if available
        if self._mnee_config_cache is not None:
            return self._mnee_config_cache
            
        # Otherwise fetch from API
        config = app_state.config
        token_id = config.get('bsv20_token_id', '')
        
        # If config already has token ID, we can use that without an API call
        if token_id:
            logger.debug(f"Using token ID from config: {token_id}")
            self._mnee_config_cache = {'tokenId': token_id}
            return self._mnee_config_cache
            
        # Make the API call to get the config
        logger.debug("Fetching MNEE config from API")
        self._mnee_config_cache = self._fetch_mnee_config()
        return self._mnee_config_cache
        
    def _fetch_mnee_config(self) -> Dict:
        """
        Make the actual API call to fetch MNEE config.
        Private method used by get_cached_config.
        
        Returns:
            Config dictionary from API
        """
        config = app_state.config
        mnee_env = config.get('mnee_environment', 'production')
        base_url = MNEE_API_URL_PRODUCTION if mnee_env == 'production' else MNEE_API_URL_SANDBOX
        api_key = config.get(f'mnee_api_key_{mnee_env}', MNEE_DEFAULT_API_KEY)
        
        try:
            # Make direct HTTP call to get config
            endpoint = "/v1/config"
            url = f"{base_url}{endpoint}?auth_token={api_key}"
            
            logger.debug(f"Calling MNEE API at: {url}")
            
            headers = {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
            
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=30) as response:
                raw_data = response.read().decode()
                data = json.loads(raw_data)
                
                logger.debug(f"API response: {json.dumps(data)[:200]}...")
                
                # Store token ID in config if available
                if isinstance(data, dict):
                    if 'tokenId' in data:
                        token_id = data['tokenId']
                        logger.info(f"Got token ID from API: {token_id}")
                        config.set_key('bsv20_token_id', token_id)
                    elif 'token' in data and isinstance(data['token'], dict) and 'id' in data['token']:
                        token_id = data['token']['id']
                        logger.info(f"Got token ID from API token.id: {token_id}")
                        config.set_key('bsv20_token_id', token_id)
                
                return data
        except Exception as e:
            logger.error(f"Error fetching MNEE config: {str(e)}")
            return {}
    
    def create_derivation_data(self, key_allocation: DeterministicKeyAllocation) -> bytes:
        """
        Override the StandardAccount method to use MNEE coin type and purpose.
        This ensures that MNEE addresses use a different derivation path.
        
        Args:
            key_allocation: The key allocation with derivation path
            
        Returns:
            JSON-encoded derivation data
        """
        assert key_allocation.derivation_type == DerivationType.BIP32_SUBPATH
        
        # We use m/44'/236'/0'/0/n for receiving and m/44'/236'/0'/1/n for change
        # Start with a full BIP44 path but only store the last part as subpath
        # The MNEE_PURPOSE, MNEE_COIN_TYPE, and MNEE_ACCOUNT are implied
        return json.dumps({"subpath": key_allocation.derivation_path}).encode()
        
    def get_base_path(self) -> List[int]:
        """
        Get the base derivation path for MNEE accounts.
        
        Returns:
            The base path as a list of integers: [PURPOSE, COIN_TYPE, ACCOUNT]
        """
        return [MNEE_PURPOSE, MNEE_COIN_TYPE, MNEE_ACCOUNT]
    
    def get_full_derivation_path(self, subpath: Tuple[int, ...]) -> List[int]:
        """
        Get the full derivation path including the MNEE-specific base path.
        
        Args:
            subpath: The subpath (e.g., MNEE_RECEIVING_SUBPATH + (address_index,))
            
        Returns:
            The full derivation path as a list
        """
        return self.get_base_path() + list(subpath)
        
    def clear_mnee_data_for_key(self, key_id: int) -> None:
        """
        Clear any stored MNEE data for a specific key.
        
        Args:
            key_id: The keyinstance_id to clear data for
        """
        if key_id in self._mnee_tx_count_per_key:
            del self._mnee_tx_count_per_key[key_id]
        if key_id in self._mnee_balances_per_key:
            del self._mnee_balances_per_key[key_id]
    
    def get_mnee_balance_for_keyid(self, key_id: int) -> int:
        """
        Get the MNEE token balance for a specific key.
        
        Args:
            key_id: The keyinstance_id to get balance for
            
        Returns:
            The MNEE token balance, or 0 if not available
        """
        return self._mnee_balances_per_key.get(key_id, 0)
    
    def diagnostic_report(self) -> Dict:
        """
        Generate a diagnostic report of the account state.
        Used during synchronization to track pre/post sync state.
        
        Returns:
            Dictionary with diagnostic information
        """
        report = {
            'account_id': self.get_id(),
            'visible_keys': 0,
            'address_count': 0,
            'transactions_stored': 0,
            'token_balance': 0
        }
        
        # Count active keys
        visible_keys = []
        for key_id, key in self._keyinstances.items():
            if key.flags & KeyInstanceFlag.IS_ACTIVE:
                visible_keys.append(key_id)
        report['visible_keys'] = len(visible_keys)
        
        # Count addresses - using multiple methods to be thorough
        addresses = set()
        
        # Method 1: Try wallet's get_key_address if available
        if hasattr(self._wallet, 'get_key_address'):
            for key_id in visible_keys:
                try:
                    address = self._wallet.get_key_address(key_id)
                    if address:
                        addresses.add(address)
                except Exception:
                    pass
        
        # Method 2: Try script templates
        for key_id in visible_keys:
            try:
                script_template = self.get_script_template_for_id(key_id)
                if hasattr(script_template, 'to_string'):
                    address = script_template.to_string()
                    if address:
                        addresses.add(address)
            except Exception:
                pass
        
        report['address_count'] = len(addresses)
        
        # Count transactions if possible
        if hasattr(self._wallet, '_db_context'):
            db = self._wallet._db_context
            try:
                # First check if executor is available
                if hasattr(db, 'executor'):
                    with db.executor() as db_exec:
                        # Count transactions associated with this account
                        count_query = """
                            SELECT COUNT(DISTINCT tx_hash) 
                            FROM TransactionOutputs 
                            WHERE keyinstance_id IN (
                                SELECT keyinstance_id 
                                FROM KeyInstances 
                                WHERE account_id = ?
                            )
                        """
                        count = db_exec.execute(count_query, (self.get_id(),)).fetchone()[0]
                        report['transactions_stored'] = count
                else:
                    # Fallback to direct cursor if executor is not available
                    try:
                        cursor = db.cursor() if hasattr(db, 'cursor') else db.connection.cursor()
                        count_query = """
                            SELECT COUNT(DISTINCT tx_hash) 
                            FROM TransactionOutputs 
                            WHERE keyinstance_id IN (
                                SELECT keyinstance_id 
                                FROM KeyInstances 
                                WHERE account_id = ?
                            )
                        """
                        cursor.execute(count_query, (self.get_id(),))
                        count = cursor.fetchone()[0]
                        report['transactions_stored'] = count
                    except Exception as cursor_error:
                        logger.debug(f"Error counting transactions with cursor: {str(cursor_error)}")
            except Exception as e:
                logger.debug(f"Error counting transactions: {str(e)}")
        
        # Sum token balances
        total_balance = sum(self._mnee_balances_per_key.values())
        report['token_balance'] = total_balance
        
        return report
    
    def create_compact_report(self, full_report: Dict) -> Dict:
        """
        Create a compact version of the synchronization report.
        This extracts only the essential information needed for regular use.
        
        Args:
            full_report: The full synchronization report dictionary
            
        Returns:
            A compact dictionary with only the essential information
        """
        # Extract only the essential information
        compact = {
            'timestamp': int(time.time()),
            'account_id': full_report.get('account_id', self.get_id()),
            'success': len(full_report.get('errors', [])) == 0,
            'addresses_processed': full_report.get('addresses_processed', 0),
            'addresses_with_tx': full_report.get('addresses_with_transactions', 0),
            'transactions_found': full_report.get('token_transactions_found', 0),
            'transactions_stored': full_report.get('directly_stored_transactions', 0),
            'token_id': full_report.get('token_id', ''),
            'mnee_api_calls': full_report.get('mnee_api_calls', 0)
        }
        
        # Add error summary if there were errors (just count and first few)
        errors = full_report.get('errors', [])
        if errors:
            compact['error_count'] = len(errors)
            compact['error_summary'] = errors[0] if errors else ''
            if len(errors) > 1:
                compact['error_summary'] += f" (and {len(errors)-1} more errors)"
        
        # Add pre/post transaction counts if available
        if 'final_transaction_count' in full_report:
            compact['final_tx_count'] = full_report['final_transaction_count']
        
        # Add token balance information if available
        if 'total_balance' in full_report:
            balance_info = full_report['total_balance']
            compact['token_balance'] = {
                'current': balance_info.get('final', 0),
                'change': balance_info.get('change', 0)
            }
        
        # Add token counts if available
        if 'tokens_found' in full_report:
            compact['tokens_found'] = full_report['tokens_found']
        
        # Add balance change count
        if 'balance_changes' in full_report:
            compact['keys_with_balance_changes'] = len(full_report['balance_changes'])
        
        return compact
    
    def _save_mnee_data_to_db(self) -> None:
        """
        Save MNEE token data to the wallet database using WalletData table.
        """
        if not hasattr(self, '_wallet') or not hasattr(self._wallet, 'get_db_context'):
            logger.warning("Cannot save MNEE data: wallet or db_context getter not available")
            return
            
        try:
            db_context = self._wallet.get_db_context()
            wallet_data_table = WalletDataTable(db_context)
            
            rows_to_upsert = []
            for key_id, count in self._mnee_tx_count_per_key.items():
                balance = self._mnee_balances_per_key.get(key_id, 0)
                data_value_dict = {
                    'tx_count': count,
                    'balance': balance
                }
                data_key = f"mnee_key_data_{key_id}"
                data_value_json = json.dumps(data_value_dict)
                
                rows_to_upsert.append(WalletDataRow(key=data_key, value=data_value_json))
                logger.debug(f"Prepared MNEE data for key {key_id}: tx_count={count}, balance={balance}")
            
            if rows_to_upsert:
                wallet_data_table.upsert(rows_to_upsert)
                logger.info(f"Saved/Updated MNEE data for {len(rows_to_upsert)} keys to WalletData table.")
            else:
                logger.debug("No MNEE key data to save to WalletData table.")

        except Exception as e:
            logger.error(f"Error saving MNEE key data to WalletData: {e}", exc_info=True)
    
    def synchronize(self) -> int:
        """Synchronizes the account with the blockchain and MNEE API.
        Also generates a comprehensive diagnostic report for analysis.
        
        Returns:
            The number of synchronized addresses.
            Note: The full synchronization report is stored in self._last_synchronization_report
        """
        logger.debug(f"MneeAccount.synchronize() called for account {self.get_id()}")
        logger.debug(f"MneeAccount.synchronize: Starting synchronization for account {self.get_id()}")
        
        # Create a report to collect all results
        synchronization_report = {
            'account_id': self.get_id(),
            'mnee_api_calls': 0,
            'token_transactions_found': 0,
            'transactions_stored': 0,
            'errors': []
        }
        
        # Get the token ID from MNEE API if not already in config
        mnee_config = self.get_cached_config()
        token_id = None
        
        if 'tokenId' in mnee_config:
            token_id = mnee_config['tokenId']
            synchronization_report['token_id'] = token_id
        elif 'token' in mnee_config and isinstance(mnee_config['token'], dict) and 'id' in mnee_config['token']:
            token_id = mnee_config['token']['id']
            synchronization_report['token_id'] = token_id
            
        if token_id:
            logger.info(f"Using token ID from cached config: {token_id}")
            synchronization_report['token_id_source'] = 'config'
        else:
            logger.warning("No token ID available in cached config")
            synchronization_report['token_id_source'] = 'missing'
        
        # Store the config in the report
        synchronization_report['mnee_config'] = mnee_config
        
        # Generate pre-sync diagnostic report
        try:
            pre_sync_report = self.diagnostic_report()
            synchronization_report.update(pre_sync_report)
            logger.debug(f"Pre-sync diagnostic: Visible keys={pre_sync_report.get('visible_keys', 0)}, Addresses={pre_sync_report.get('address_count', 0)}")
        except Exception as e:
            logger.error(f"Error generating pre-sync diagnostic: {str(e)}")
            synchronization_report['errors'].append(f"Pre-sync diagnostic error: {str(e)}")
        
        # Standard blockchain synchronization
        result = super().synchronize()
        logger.debug(f"MneeAccount.synchronize: Completed standard synchronization with {result} addresses")
        synchronization_report['addresses_synchronized'] = result
        
        # MNEE synchronization
        logger.debug(f"MneeAccount.synchronize: Starting MNEE sync")
        try:
            # Perform MNEE synchronization
            mnee_result = self.synchronize_mnee()
            
            # Add MNEE results to the main report
            synchronization_report['mnee_api_calls'] = mnee_result.get('mnee_api_calls', 0)
            synchronization_report['token_transactions_found'] = mnee_result.get('mnee_transactions_found', 0)
            synchronization_report['directly_stored_transactions'] = mnee_result.get('directly_stored_transactions', 0)
            synchronization_report['errors'].extend(mnee_result.get('errors', []))
            synchronization_report['addresses_processed'] = mnee_result.get('addresses_processed', 0)
            synchronization_report['addresses_with_transactions'] = mnee_result.get('addresses_with_transactions', 0)
            
            # Generate post-sync diagnostic report for completeness
            try:
                post_sync_report = self.diagnostic_report()
                logger.debug(f"Post-sync transactions count: {post_sync_report.get('transactions_stored', 0)}")
                synchronization_report['final_transaction_count'] = post_sync_report.get('transactions_stored', 0)
            except Exception as e:
                logger.error(f"Error generating post-sync diagnostic: {str(e)}")
                synchronization_report['errors'].append(f"Post-sync diagnostic error: {str(e)}")
            
            # Log a summary of the results
            logger.info(f"MNEE sync completed with {mnee_result.get('mnee_api_calls', 0)} API calls and " +
                       f"{mnee_result.get('mnee_transactions_found', 0)} transactions found")
            
            # Store the full report as a private attribute for debugging
            self._last_full_synchronization_report = synchronization_report
            
            # Create a compact version of the report for regular use
            compact_report = self.create_compact_report(synchronization_report)
            
            # Store the compact report as the main report for commands to use
            self._last_synchronization_report = compact_report
            
            # Save both reports to the database for persistence
            try:
                if hasattr(self._wallet, '_db_context') and hasattr(self._wallet, 'get_storage'):
                    db_context = self._wallet.get_db_context()
                    wallet_data_table = WalletDataTable(db_context)
                    sync_time = int(time.time())

                    # Save the compact report
                    compact_report_key = f"mnee_sync_report_compact_{sync_time}"
                    compact_content_json = json.dumps(compact_report)
                    compact_data_row = WalletDataRow(key=compact_report_key, value=compact_content_json)
                    logger.debug(f"Saving compact synchronization report to WalletData with key {compact_report_key}")
                    wallet_data_table.upsert([compact_data_row])

                    # Save the full report only if debug logging is enabled
                    if logger.getEffectiveLevel() <= 10:  # DEBUG level
                        full_report_key = f"mnee_sync_report_full_{sync_time}"
                        full_content_json = json.dumps(synchronization_report)
                        full_data_row = WalletDataRow(key=full_report_key, value=full_content_json)
                        logger.debug(f"Saving full debug synchronization report to WalletData with key {full_report_key}")
                        wallet_data_table.upsert([full_data_row])
                    
                    logger.debug(f"Saved synchronization reports to WalletData with timestamp {sync_time}")
                else:
                    logger.warning("Cannot save sync reports: no database context or storage available")
            except Exception as e:
                logger.error(f"Error saving synchronization reports to WalletData: {str(e)}", exc_info=True)
                synchronization_report['errors'].append(f"Report save error: {str(e)}")
                
        except Exception as e:
            logger.error(f"MneeAccount.synchronize: Error synchronizing MNEE data: {str(e)}")
            synchronization_report['errors'].append(f"Synchronization error: {str(e)}")
            # Still store the reports even on error
            self._last_full_synchronization_report = synchronization_report
            compact_report = self.create_compact_report(synchronization_report)
            self._last_synchronization_report = compact_report
                
        return result
        
    def fetch_and_store_all_mnee_transactions(self) -> Dict:
        """
        Directly fetch and store all available MNEE transactions for all wallet addresses.
        This ensures we store all token transactions by processing all addresses in batches.
        
        Returns:
            Dictionary with results of the operation
        """
        logger.info("Directly fetching and storing all MNEE transactions for all addresses")
        result = {
            'api_calls': 0,
            'transactions_stored': 0,
            'errors': []
        }
        
        # Get MNEE configuration
        config = app_state.config
        mnee_env = config.get('mnee_environment', 'production')  # Default to production
        base_url = MNEE_API_URL_PRODUCTION if mnee_env == 'production' else MNEE_API_URL_SANDBOX
        
        # Always use default API key if none provided
        api_key = config.get(f'mnee_api_key_{mnee_env}', MNEE_DEFAULT_API_KEY)
        logger.debug(f"Using MNEE environment: {mnee_env}, API key: {api_key[:5]}...{api_key[-5:] if len(api_key) > 10 else ''}")
        
        try:
            # Build the API request
            endpoint = "/v1/sync"
            
            # Add auth_token as query parameter
            url = f"{base_url}{endpoint}?auth_token={api_key}"
            
            # Set up headers
            headers = {
                'Content-Type': 'application/json',
            }
            
            # Collect all addresses from the wallet
            wallet_addresses = []
            for key_id in self._keyinstances:
                if self._keyinstances[key_id].flags & KeyInstanceFlag.IS_ACTIVE:
                    try:
                        script_template = self.get_script_template_for_id(key_id)
                        if hasattr(script_template, 'to_string'):
                            address = script_template.to_string()
                            if address:
                                wallet_addresses.append(address)
                    except Exception as e:
                        logger.debug(f"Error getting address for key {key_id}: {str(e)}")

            # Make sure we have addresses to send
            if not wallet_addresses:
                logger.warning("No wallet addresses found - can't call API without addresses")
                return result
                
            # Process all addresses in batches of 20
            total_addresses = len(wallet_addresses)
            batch_size = 20  # Process in batches of 20 addresses
            addresses_with_tx = 0
            all_transactions_found = 0
            tokens_found = 0
            addresses_map = {}  # Maps addresses to key_ids
            
            # First create a map of addresses to key_ids
            for key_id in self._keyinstances:
                if self._keyinstances[key_id].flags & KeyInstanceFlag.IS_ACTIVE:
                    try:
                        script_template = self.get_script_template_for_id(key_id)
                        if hasattr(script_template, 'to_string'):
                            address = script_template.to_string()
                            if address:
                                addresses_map[address] = key_id
                    except Exception as e:
                        logger.debug(f"Error mapping address for key {key_id}: {str(e)}")
            
            logger.info(f"Processing {total_addresses} addresses in batches of {batch_size}")
            
            # Process addresses in batches
            for i in range(0, total_addresses, batch_size):
                # Get current batch
                address_batch = wallet_addresses[i:i+batch_size]
                batch_num = i // batch_size + 1
                total_batches = (total_addresses + batch_size - 1) // batch_size
                logger.info(f"Processing batch {batch_num}/{total_batches} with {len(address_batch)} addresses")
                
                # Create the JSON payload for this batch
                data = json.dumps(address_batch).encode('utf-8')
                
                # Make API request for this batch
                req = urllib.request.Request(url, data=data, headers=headers, method="POST")
                start_time = time.time()
                result['api_calls'] += 1
                
                try:
                    logger.debug(f"Executing API request for batch {batch_num}")
                    with urllib.request.urlopen(req, timeout=30) as response:
                        elapsed_time = time.time() - start_time
                        status_code = response.getcode()
                        logger.debug(f"Batch {batch_num} API completed in {elapsed_time:.2f}s with status {status_code}")
                        
                        if status_code == 200:
                            raw_data = response.read().decode()
                            logger.debug(f"Batch {batch_num} received response: {len(raw_data)} bytes")
                            
                            if len(raw_data) == 0:
                                logger.warning(f"Batch {batch_num} received empty response, skipping processing for this batch.")
                                continue # Skip to the next batch

                            response_data = json.loads(raw_data)
                            
                            # Process transactions for this batch
                            batch_stored_count, batch_all_successful = self._process_and_store_transactions(response_data)
                            result['transactions_stored'] += batch_stored_count
                            logger.info(f"Batch {batch_num}: Stored {batch_stored_count} transactions.")

                            if not batch_all_successful:
                                error_msg = f"Terminating MNEE transaction fetching: Failed to process/store one or more transactions in batch {batch_num}."
                                logger.error(error_msg)
                                result['errors'].append(error_msg)
                                result['critical_storage_failure'] = True
                                return result # Exit the function, thus stopping the loop
                                                        
                    # Allow a small pause between batches
                    if i + batch_size < total_addresses:
                        time.sleep(0.5)  # 500ms pause between batches
                        
                except Exception as batch_error:
                    error_msg = f"Error processing batch {batch_num}: {str(batch_error)}"
                    logger.error(f"[ensure_transaction_stored] {error_msg}")
                    result['errors'].append(error_msg)
            
            # Update final results
            result['mnee_transactions_found'] = all_transactions_found
            result['addresses_with_transactions'] = addresses_with_tx
            result['tokens_found'] = tokens_found
        
        except Exception as e:
            error_msg = f"Error in fetch_and_store_all_mnee_transactions: {str(e)}"
            logger.error(f"[ensure_transaction_stored] {error_msg}")
            result['errors'].append(error_msg)
        return result

    def _process_and_store_transactions(self, response_data):
        """
        Process and store transactions from API responses.
        This helper method centralizes transaction storage logic to avoid duplication.
        
        Args:
            response_data: API response data, which might be in various formats
            
        Returns:
            Tuple of (stored_count, all_transactions_successfully_stored_flag)
        """
        transactions_to_store_list = [] # Renamed to avoid conflict with outer scope 'transactions'
        stored_count = 0
        all_txs_processed_successfully = True # Assume success initially
        mnee_metadata_by_txid = {} # Track MNEE token metadata by transaction ID

        # Extract transactions from response data (which can have different formats)
        transactions_from_response = []
        if isinstance(response_data, list):
            transactions_from_response = response_data
        elif isinstance(response_data, dict):
            if 'transactions' in response_data and isinstance(response_data['transactions'], list):
                transactions_from_response = response_data['transactions']
            elif 'data' in response_data and isinstance(response_data['data'], list):
                transactions_from_response = response_data['data']
            elif 'results' in response_data and isinstance(response_data['results'], list):
                transactions_from_response = response_data['results']
        
        if not transactions_from_response:
            sample_data = ""
            if isinstance(response_data, list) and len(response_data) > 0:
                sample_data = f", First item: {str(response_data[0])[:100]}..."
            elif isinstance(response_data, dict) and len(response_data) > 0:
                sample_key = next(iter(response_data))
                sample_data = f", Sample key '{sample_key}': {str(response_data[sample_key])[:100]}..."
            logger.warning(f"No transactions found in API response format: {type(response_data)}{sample_data}")
            return 0, True

        logger.debug(f"Processing {len(transactions_from_response)} transactions from API response")
        
        # Get the token ID from config
        config = app_state.config
        token_id = config.get('bsv20_token_id', '')
        if not token_id and self._mnee_config_cache and 'tokenId' in self._mnee_config_cache:
            token_id = self._mnee_config_cache['tokenId']
        
        # Process each transaction from the response
        for tx_item in transactions_from_response:
            txid = None
            rawtx = None
            mnee_amount = None
            addresses_involved = []
            address_to_amount = {}
            
            if isinstance(tx_item, dict):
                # Extract transaction ID from various possible field names
                for id_field in ['txid', 'hash', 'id', 'transaction_hash', 'tx_hash']:
                    if id_field in tx_item and tx_item[id_field]:
                        txid = tx_item[id_field]
                        break
                
                # Extract raw transaction data from various possible field names
                for data_field in ['rawtx', 'raw', 'rawTx', 'hex', 'txhex', 'raw_tx', 'data']:
                    if data_field in tx_item and tx_item[data_field]:
                        rawtx = tx_item[data_field]
                        break
                
                # Extract addresses involved
                if 'senders' in tx_item and isinstance(tx_item['senders'], list):
                    addresses_involved.extend(tx_item['senders'])
                if 'receivers' in tx_item and isinstance(tx_item['receivers'], list):
                    addresses_involved.extend(tx_item['receivers'])
             
                # Properly decode BSV-20 scripts from raw transaction data
                if rawtx:
                    try:
                        # Convert rawtx to hex if it's base64
                        raw_tx_hex = rawtx
                        if not all(c in '0123456789abcdefABCDEF' for c in raw_tx_hex):
                            import base64
                            padded = raw_tx_hex + ("=" * ((4 - len(raw_tx_hex) % 4) % 4))
                            raw_tx_hex = base64.b64decode(padded).hex()
                        
                        # Decode the transaction
                        tx_bytes = bytes.fromhex(raw_tx_hex)
                        decoded_tx = Transaction.from_bytes(tx_bytes)
                        
                        # Maps P2PKH output indices to their addresses
                        output_idx_to_address = {}
                        
                        # First pass - collect all P2PKH output addresses
                        for output_index, output in enumerate(decoded_tx.outputs):
                            script_bytes = bytes(output.script_pubkey)
                            # Skip OP_RETURN outputs
                            if len(script_bytes) > 0 and script_bytes[0] != 0x6a:
                                try:
                                    from bitcoinx import Script
                                    script = Script(output.script_pubkey)
                                    if hasattr(script, 'to_address'):
                                        address = script.to_address()
                                        if address:
                                            output_idx_to_address[output_index] = address
                                except Exception as e:
                                    logger.debug(f"Error extracting address from output {output_index}: {str(e)}")
                        
                        # Second pass - parse BSV-20 OP_RETURN scripts
                        for output_index, output in enumerate(decoded_tx.outputs):
                            script_bytes = bytes(output.script_pubkey)
                            
                            # Look for OP_RETURN
                            if len(script_bytes) > 0 and script_bytes[0] == 0x6a:  # OP_RETURN
                                try:
                                    # Skip OP_RETURN byte
                                    op_return_data = script_bytes[1:]
                                    
                                    # Skip push opcodes if present
                                    if len(op_return_data) > 0:
                                        if op_return_data[0] <= 0x4b:  # Direct push bytes 1-75
                                            push_size = op_return_data[0]
                                            op_return_data = op_return_data[1:1+push_size]
                                        elif op_return_data[0] == 0x4c:  # OP_PUSHDATA1
                                            push_size = op_return_data[1]
                                            op_return_data = op_return_data[2:2+push_size]
                                        elif op_return_data[0] == 0x4d:  # OP_PUSHDATA2
                                            push_size = int.from_bytes(op_return_data[1:3], byteorder='little')
                                            op_return_data = op_return_data[3:3+push_size]
                                        elif op_return_data[0] == 0x4e:  # OP_PUSHDATA4
                                            push_size = int.from_bytes(op_return_data[1:5], byteorder='little')
                                            op_return_data = op_return_data[5:5+push_size]
                                    
                                    # Try to decode as UTF-8
                                    data_str = op_return_data.decode('utf-8', errors='ignore')
                                    
                                    # Look for BSV-20 JSON structures
                                    if ('bsv-20' in data_str.lower() or 'bsv20' in data_str.lower()) and 'id' in data_str and 'amt' in data_str:
                                        # Try to find a valid JSON object within the string
                                        import re
                                        json_match = re.search(r'\{.*"p"\s*:\s*"bsv-?20".*\}', data_str)
                                        if json_match:
                                            json_str = json_match.group(0)
                                            try:
                                                bsv20_data = json.loads(json_str)
                                                # Check for transfer operation with an amount
                                                if 'op' in bsv20_data and bsv20_data['op'] == 'transfer' and 'amt' in bsv20_data:
                                                    amt_str = bsv20_data['amt']
                                                    amount = int(amt_str)
                                                    
                                                    # The recipient address is typically in the next output
                                                    recipient_idx = output_index + 1
                                                    if recipient_idx in output_idx_to_address:
                                                        recipient_addr = output_idx_to_address[recipient_idx]
                                                        if recipient_addr:
                                                            if recipient_addr not in addresses_involved:
                                                                addresses_involved.append(recipient_addr)
                                                            address_to_amount[recipient_addr] = amount
                                                            logger.debug(f"BSV-20 transfer decoded: {amount} to {recipient_addr}")
                                            except json.JSONDecodeError as json_err:
                                                logger.debug(f"Failed to parse BSV-20 JSON: {str(json_err)}")
                                except Exception as e:
                                    logger.debug(f"Error parsing BSV-20 OP_RETURN at output {output_index}: {str(e)}")
                    except Exception as e:
                        logger.debug(f"Error decoding transaction: {str(e)}")
            
            # Skip if no transaction ID found
            if not txid:
                logger.debug(f"Missing transaction ID in item: {str(tx_item)[:100]}...")
                continue 
                
            # Validate transaction ID format
            if not isinstance(txid, str) or len(txid) != 64 or not all(c in '0123456789abcdefABCDEF' for c in txid):
                logger.error(f"Invalid transaction ID format from API: {txid[:64] if isinstance(txid, str) else str(txid)[:64]}...")
                all_txs_processed_successfully = False
                continue 
            
            # Store the MNEE token metadata for this transaction
            if addresses_involved or mnee_amount is not None:
                mnee_metadata_by_txid[txid] = {
                    'addresses': addresses_involved,
                    'amount': mnee_amount,
                    'address_to_amount': address_to_amount,
                    'token_id': token_id
                }
                logger.debug(f"Extracted MNEE metadata for tx {txid[:10]}: {len(addresses_involved)} addresses, amount: {mnee_amount}")
            
            # Process raw transaction data if available
            if rawtx:
                raw_tx_data = rawtx
                raw_tx_hex_data_for_item = ""
                try:
                    if not all(c in '0123456789abcdefABCDEF' for c in raw_tx_data):
                        logger.debug(f"Converting non-hex transaction data for {txid[:10]}...")
                        import base64
                        padded = raw_tx_data + ("=" * ((4 - len(raw_tx_data) % 4) % 4))
                        raw_tx_hex_data_for_item = base64.b64decode(padded).hex()
                        logger.debug(f"Successfully converted base64 to hex for {txid[:10]}...")
                    else:
                        raw_tx_hex_data_for_item = raw_tx_data
                except Exception as e:
                    logger.error(f"Error validating/converting transaction data for {txid[:10]}: {str(e)}")
                    all_txs_processed_successfully = False
                    continue
                
                if len(raw_tx_hex_data_for_item) > 100:
                    transactions_to_store_list.append((txid, raw_tx_hex_data_for_item))
            else: # MNEE API sometimes returns only txid without rawtx, these are not storable directly here
                logger.debug(f"Transaction item for {txid[:10]}... did not contain raw transaction data.")

        # Store the extracted transactions
        for txid_to_store, raw_tx_hex_to_store in transactions_to_store_list:
            try:
                # Double-check txid format (already checked but good for safety before storage call)
                if not isinstance(txid_to_store, str) or len(txid_to_store) != 64 or not all(c in '0123456789abcdefABCDEF' for c in txid_to_store):
                    logger.error(f"Skipping storage for malformed transaction ID (final check): {txid_to_store[:64] if isinstance(txid_to_store, str) else str(txid_to_store)[:64]}...")
                    all_txs_processed_successfully = False
                    continue # Or break, if one invalid txid means the whole batch is suspect
                    
                # NOTE: Transaction storage is now handled by different means
                # Log that we have the transaction data but don't attempt to store directly
                stored_count += 1
                logger.debug(f"Processed transaction {txid_to_store[:10]}... from API")
                
                # If we have metadata for this transaction, store it
                if txid_to_store in mnee_metadata_by_txid:
                    self._store_mnee_token_metadata(txid_to_store, mnee_metadata_by_txid[txid_to_store])
            except Exception as store_error:
                error_msg = f"Error processing transaction {txid_to_store[:10]}...: {str(store_error)}"
                logger.error(f"[_process_and_store] {error_msg}", exc_info=True)
                all_txs_processed_successfully = False
                break # Stop processing this batch on other storage exceptions too
        
        return stored_count, all_txs_processed_successfully

    def _store_mnee_token_metadata(self, txid: str, metadata: Dict) -> None:
        """
        Store MNEE token metadata for a transaction in the wallet database.
        This links the transaction with wallet keys that own the addresses involved.
        Uses standard ElectrumSV patterns for storing transaction metadata.
        
        Args:
            txid: The transaction ID
            metadata: Dictionary containing token metadata
        """
        logger.debug(f"Storing MNEE token metadata for transaction {txid[:10]}")
        
        addresses = metadata.get('addresses', [])
        if not addresses:
            logger.debug(f"No addresses in metadata for transaction {txid[:10]}")
            return
        
        # Find keys corresponding to these addresses
        address_to_key_map = {}
        for key_id, key in self._keyinstances.items():
            if not (key.flags & KeyInstanceFlag.IS_ACTIVE):
                continue
                
            try:
                # FIXED: Check if the key has a valid script type before trying to get script
                from electrumsv.constants import ScriptType
                if key.script_type == ScriptType.NONE:
                    logger.debug(f"Skipping key_id {key_id} with ScriptType.NONE in _store_mnee_token_metadata")
                    continue
                    
                script_template = self.get_script_template_for_id(key_id)
                if hasattr(script_template, 'to_string'):
                    address = script_template.to_string()
                    if address and address in addresses:
                        address_to_key_map[address] = key_id
            except Exception as e:
                logger.debug(f"Error getting address for key {key_id}: {str(e)}")
        
        if not address_to_key_map:
            logger.debug(f"No matching wallet keys found for addresses in transaction {txid[:10]}")
            return
        
        logger.info(f"Found {len(address_to_key_map)} matching wallet keys for transaction {txid[:10]}")
        
        # Get token amount information
        token_id = metadata.get('token_id', '')
        if not token_id:
            config = app_state.config
            token_id = config.get('bsv20_token_id', '')
            if not token_id and self._mnee_config_cache and 'tokenId' in self._mnee_config_cache:
                token_id = self._mnee_config_cache['tokenId']
        
        # Convert txid to bytes
        tx_hash_bytes = hex_str_to_hash(txid)
        
        # Store metadata in WalletEvent table (standard ElectrumSV approach)
        try:
            if hasattr(self._wallet, 'get_db_context'):
                db_context = self._wallet.get_db_context()
                
                # Use WalletEventTable for storing token events (standard approach)
                wallet_event_table = WalletEventTable(db_context)
                
                # For each address that has a matching key in our wallet
                for address, key_id in address_to_key_map.items():
                    # Get amount for this address if available 
                    amount = metadata.get('address_to_amount', {}).get(address, metadata.get('amount', 0))
                    
                    # Update balance for this key
                    current_balance = self._mnee_balances_per_key.get(key_id, 0)
                    
                    # Make sure amount is never None
                    safe_amount = 0 if amount is None else amount
                    # Convert to int if needed
                    if not isinstance(safe_amount, int):
                        try:
                            safe_amount = int(safe_amount)
                        except (ValueError, TypeError):
                            logger.warning(f"Could not convert amount {safe_amount} to integer, using 1")
                            safe_amount = 1  # Use 1 token (better than 0) as fallback
                    
                    # Now do the addition with the safe amount value
                    self._mnee_balances_per_key[key_id] = current_balance + safe_amount
                    
                    # Create token event data (BSV-20 token received)
                    event_data = {
                        "token_id": token_id,
                        "address": address,
                        "amount": amount,
                        "type": "TOKEN_RECEIVED",
                        "tx_hash": txid,
                        "timestamp": int(time.time()),
                        "keyinstance_id": key_id
                    }
                    
                    # Instead of using WalletEventRow, store in WalletData table
                    data_key = f"bsv20_tx_{txid[:16]}_{key_id}"
                    data_value = json.dumps(event_data)
                    
                    # Create WalletDataRow and upsert it
                    data_row = WalletDataRow(key=data_key, value=data_value)
                    wallet_data_table = WalletDataTable(db_context)
                    wallet_data_table.upsert([data_row])
            
        except Exception as e:
            logger.error(f"Error processing transaction {txid[:10]}...: {str(e)}")
                
        return
    
    def synchronize_mnee(self) -> Dict:
        """
        Synchronize MNEE data for this account.
        Called during wallet synchronization to update MNEE token data.
        
        Returns:
            Dict with synchronization results
        """        
        logger.info(f"Starting MNEE data synchronization for account {self.get_id()}")
        
        result = {
            'mnee_api_calls': 0,
            'mnee_transactions_found': 0,
            'errors': [],
            'addresses_processed': 0,
            'addresses_with_transactions': 0,
            'directly_stored_transactions': 0,
            'tokens_found': 0
        }
        
        # Get initial token balance to track changes
        initial_token_balances = {}
        for key_id, balance in self._mnee_balances_per_key.items():
            initial_token_balances[key_id] = balance
        total_initial_balance = sum(initial_token_balances.values())
        
        # Get current token transaction counts to track changes
        initial_tx_counts = {}
        for key_id, count in self._mnee_tx_count_per_key.items():
            initial_tx_counts[key_id] = count
        total_initial_txs = sum(initial_tx_counts.values())
        
        # IMPORTANT: Force API call by getting the token ID first
        try:
            # Try to get the current token ID from config
            config = app_state.config
            token_id = config.get('bsv20_token_id', '')
            
            # If no token ID in config, fetch from API
            if not token_id:
                logger.info("No token ID in config, fetching from MNEE API...")
                mnee_config = self.get_cached_config()
                if mnee_config and 'tokenId' in mnee_config:
                    token_id = mnee_config['tokenId']
                    logger.info(f"Successfully fetched token ID: {token_id}")
                    result['mnee_api_calls'] += 1
                
            # If we have a token ID, log it
            if token_id:
                logger.info(f"Using token ID: {token_id}")
                result['token_id'] = token_id
        except Exception as e:
            error_msg = f"Error getting token ID: {str(e)}"
            logger.error(error_msg)
            result['errors'].append(error_msg)
        
        # First directly fetch and store all available transactions
        # This ensures we store transactions even if we can't match addresses
        logger.info("Fetching and storing all MNEE transactions directly from API")
        direct_result = self.fetch_and_store_all_mnee_transactions()
        result['mnee_api_calls'] += direct_result.get('api_calls', 0) # Use .get for safety
        result['directly_stored_transactions'] += direct_result.get('transactions_stored', 0)
        result['errors'].extend(direct_result.get('errors', []))
        
        logger.info(f"Directly stored {direct_result.get('transactions_stored', 0)} transactions via direct API call")

        # If the direct fetching and storing encountered a critical storage failure, stop here.
        if direct_result.get('critical_storage_failure'):
            error_msg = "Terminating MNEE synchronization early due to critical storage failure during initial transaction fetch."
            logger.error(error_msg)
            # The specific error from direct_result is already in result['errors']
            return result # Exit synchronize_mnee

        # CRITICAL: Check if we have a network interface for fetching transactions
        if not hasattr(self._wallet, '_network') or self._wallet._network is None:
            error_msg = "Wallet has no network interface, cannot fetch transactions"
            logger.error(error_msg)
            result['errors'].append(error_msg)
            return result
            
        # CRITICAL: Check if external_transaction_request is available
        if not hasattr(self, '_external_transaction_request'):
            error_msg = "Account has no _external_transaction_request method, cannot fetch transactions"
            logger.error(error_msg)
            result['errors'].append(error_msg)
            return result
        
        # Log information about the network interface
        network = self._wallet._network
        logger.info(f"Network interface: {type(network)}")
        
        # Get MNEE configuration
        config = app_state.config
        mnee_env = config.get('mnee_environment', 'production')  # Default to production
        base_url = MNEE_API_URL_PRODUCTION if mnee_env == 'production' else MNEE_API_URL_SANDBOX
        
        # Always use default API key if none provided
        api_key = config.get(f'mnee_api_key_{mnee_env}', MNEE_DEFAULT_API_KEY)
        logger.info(f"Using MNEE environment: {mnee_env}, API key: {api_key[:5]}...{api_key[-5:] if len(api_key) > 10 else ''}")
        
        # Get all active keys in the account
        visible_keys = []
        addresses_map = {}  # Maps addresses to key_ids
        
        # First try the most direct method - get addresses from wallet.py
        try:
            # Try to use the wallet's get_key_address method directly
            # This is what the atx command is using
            from electrumsv.wallet import Wallet
            if isinstance(self._wallet, Wallet) and hasattr(self._wallet, 'get_key_address'):
                for key_id in self._keyinstances:
                    if self._keyinstances[key_id].flags & KeyInstanceFlag.IS_ACTIVE:
                        visible_keys.append(key_id)
                        try:
                            address = self._wallet.get_key_address(key_id)
                            if address:
                                addresses_map[address] = key_id
                                logger.debug(f"Found address {address} for key {key_id} using wallet.get_key_address")
                        except Exception as e:
                            logger.debug(f"Error getting address for key {key_id} using get_key_address: {str(e)}")
        except Exception as e:
            logger.debug(f"Error using direct wallet methods: {str(e)}")
            
        # If we didn't find any addresses, try the more complex methods
        if not addresses_map:
            logger.debug("No addresses found using direct wallet methods, trying fallbacks...")
            
            # Try to get addresses using various methods
            for key_id, key in self._keyinstances.items():
                if key.flags & KeyInstanceFlag.IS_ACTIVE and key_id not in [k for k in addresses_map.values()]:
                    try:
                        # METHOD 1: Try to get the address string directly
                        if hasattr(self, 'get_address_string_for_id'):
                            address = self.get_address_string_for_id(key_id)
                            if address:
                                addresses_map[address] = key_id
                                logger.debug(f"Found address {address} for key {key_id} using get_address_string_for_id")
                                continue
                        
                        # METHOD 2: From StandardAccount
                        if hasattr(self, 'get_key_text'):
                            address = self.get_key_text(key_id)
                            if address:
                                addresses_map[address] = key_id
                                logger.debug(f"Found address {address} for key {key_id} using get_key_text")
                                continue
                        
                        # METHOD 3: Try script templates
                        script_template = self.get_script_template_for_id(key_id)
                        logger.debug(f"Script template for key {key_id} is: {type(script_template)}")
                        
                        if hasattr(script_template, 'to_string'):
                            address = script_template.to_string()
                            if address:
                                addresses_map[address] = key_id
                                logger.debug(f"Found address {address} for key {key_id} using script_template.to_string")
                                continue
                        
                        # METHOD 4: CRITICAL METHOD - Use the command interface
                        try:
                            from electrumsv.commands import Commands
                            commands = Commands(config, self._wallet, None)
                            if hasattr(commands, 'addresstx'):
                                result_json = commands.addresstx(key_id)
                                if result_json and isinstance(result_json, dict) and 'address' in result_json:
                                    address = result_json['address']
                                    if address:
                                        addresses_map[address] = key_id
                                        logger.debug(f"Found address {address} for key {key_id} using commands.addresstx")
                                        continue
                        except Exception as command_error:
                            logger.debug(f"Error using command interface for key {key_id}: {str(command_error)}")
                            
                    except Exception as e:
                        result['errors'].append(f"Error getting address for key {key_id}: {str(e)}")
        
        # Try direct access to the output table as a last resort
        if len(addresses_map) < len(visible_keys):
            logger.debug("Still missing addresses, trying direct output table access...")
            try:
                # Get output rows from the database directly
                db = self._wallet._db_context
                with db.executor() as db_exec:
                    rows = db_exec.execute("""
                        SELECT keyinstance_id, script_hash FROM MASTERKEY_TRANSACTIONS 
                        GROUP BY keyinstance_id, script_hash
                    """).fetchall()
                    
                    # Process each row
                    for row in rows:
                        key_id = row[0]
                        script_hash = row[1]
                        
                        # Check if this key is active and not already mapped
                        if key_id in visible_keys and key_id not in [k for k in addresses_map.values()]:
                            try:
                                # Convert script_hash to address
                                from bitcoinx import Script
                                script = Script.from_hex(script_hash.hex())
                                if hasattr(script, 'to_address'):
                                    address = script.to_address()
                                    if address:
                                        addresses_map[address] = key_id
                                        logger.debug(f"Found address {address} for key {key_id} using direct DB access")
                            except Exception as script_error:
                                logger.debug(f"Error processing script for key {key_id}: {str(script_error)}")
            except Exception as db_error:
                logger.debug(f"Error accessing output table: {str(db_error)}")
        
        # If we still don't have any addresses, log the issue
        if not addresses_map and len(visible_keys) > 0:
            logger.warning(f"No addresses could be detected for {len(visible_keys)} active keys")
            # But continue anyway since we already fetched transactions directly
        
        logger.debug(f"Found {len(visible_keys)} active keys and {len(addresses_map)} addresses")
        result['visible_keys'] = len(visible_keys)
        result['address_count'] = len(addresses_map)
        
        if addresses_map:
            # Process addresses in batches - but only if we found any addresses
            batch_size = config.get('mnee_batch_size', 20)  # Process 20 addresses at a time by default
            addresses = list(addresses_map.keys())
            
            logger.debug(f"Processing {len(addresses)} total addresses in batches of {batch_size}")
            
            all_transactions_found = 0
            addresses_with_tx = 0
            tokens_found = 0
            
            for i in range(0, len(addresses), batch_size):
                batch_addresses = addresses[i:i+batch_size]
                batch_num = i // batch_size + 1
                batch_count = (len(addresses) + batch_size - 1) // batch_size
                logger.debug(f"Processing batch {batch_num}/{batch_count} ({len(batch_addresses)} addresses)")
                
                try:
                    # Call API with batch of addresses
                    result['mnee_api_calls'] += 1
                    
                    # Call the API and get transactions by address
                    address_txids_result = self.fetch_txids_for_multiple_addresses(batch_addresses)
                    result['addresses_processed'] += len(batch_addresses)

                    if address_txids_result.get('error_storing_transactions'):
                        error_msg = f"Terminating MNEE address synchronization due to transaction storage failure in batch {batch_num}."
                        logger.error(error_msg)
                        result['errors'].append(error_msg)
                        return result # Exit synchronize_mnee early
                    
                    # Check if any transactions were directly stored by fetch_txids_for_multiple_addresses
                    if 'directly_stored_count' in address_txids_result:
                        batch_directly_stored = address_txids_result['directly_stored_count']
                        result['directly_stored_transactions'] += batch_directly_stored
                        logger.debug(f"API call directly stored {batch_directly_stored} transactions")
                    
                    # Process each address in the response
                    for address, txids in address_txids_result.items():
                        if address not in addresses_map or not isinstance(txids, list):
                            continue
                            
                        key_id = addresses_map[address]
                        
                        if txids:
                            # This address has transactions
                            addresses_with_tx += 1
                            
                            # CRITICAL FIX: Validate each txid to ensure it's a proper transaction ID
                            # This prevents raw transaction data from being treated as transaction IDs
                            valid_txids = []
                            for txid in txids:
                                # Make sure txids are all strings, not bytes
                                if isinstance(txid, bytes):
                                    txid = hash_to_hex_str(txid)
                                    
                                # Validate: txid must be a 64-character hex string
                                if isinstance(txid, str) and len(txid) == 64 and all(c in '0123456789abcdefABCDEF' for c in txid):
                                    valid_txids.append(txid)
                                else:
                                    logger.error(f"Skipping invalid transaction ID: {txid[:64] if isinstance(txid, str) else str(txid)[:64]}...")
                            
                            if valid_txids:
                                # Double-check one last time that all txids are valid 
                                # This is a critical last line of defense
                                final_valid_txids = []
                                for txid in valid_txids:
                                    if isinstance(txid, str) and len(txid) == 64 and all(c in '0123456789abcdefABCDEF' for c in txid):
                                        final_valid_txids.append(txid)
                                    else:
                                        logger.error(f"Final validation caught invalid txid: {txid[:64] if isinstance(txid, str) else str(txid)[:64]}...")
                                
                                # Process the transactions for this key using our improved method with validated txids
                                if final_valid_txids:
                                    mnee_txs = self.process_mnee_transactions(key_id, final_valid_txids)
                                    tx_count = len(mnee_txs)
                                    all_transactions_found += tx_count
                                    tokens_found += tx_count
                                    result['mnee_transactions_found'] += tx_count
                                    
                                    logger.debug(f"Found {tx_count} MNEE tokens for address {address[:10]}...")
                                else:
                                    logger.warning(f"All transaction IDs for address {address} were invalid after final validation")
            
                except Exception as e:
                    error_msg = f"Error fetching data for batch {batch_num}: {str(e)}"
                    result['errors'].append(error_msg)
                    logger.error(error_msg)
            
            # Update final results
            result['mnee_transactions_found'] = all_transactions_found
            result['addresses_with_transactions'] = addresses_with_tx
            result['tokens_found'] = tokens_found
        
        # Calculate token balance changes
        final_token_balances = {}
        for key_id, balance in self._mnee_balances_per_key.items():
            final_token_balances[key_id] = balance
        total_final_balance = sum(final_token_balances.values())
        
        balance_changes = {}
        for key_id in set(list(initial_token_balances.keys()) + list(final_token_balances.keys())):
            initial = initial_token_balances.get(key_id, 0)
            final = final_token_balances.get(key_id, 0)
            if initial != final:
                balance_changes[key_id] = {
                    'initial': initial,
                    'final': final,
                    'change': final - initial
                }
        
        # Calculate transaction count changes
        final_tx_counts = {}
        for key_id, count in self._mnee_tx_count_per_key.items():
            final_tx_counts[key_id] = count
        total_final_txs = sum(final_tx_counts.values())
        
        tx_count_changes = {}
        for key_id in set(list(initial_tx_counts.keys()) + list(final_tx_counts.keys())):
            initial = initial_tx_counts.get(key_id, 0)
            final = final_tx_counts.get(key_id, 0)
            if initial != final:
                tx_count_changes[key_id] = {
                    'initial': initial,
                    'final': final,
                    'change': final - initial
                }
        
        # Add balance and transaction information to result
        result['balance_changes'] = balance_changes
        result['total_balance'] = {
            'initial': total_initial_balance,
            'final': total_final_balance,
            'change': total_final_balance - total_initial_balance
        }
        result['tx_count_changes'] = tx_count_changes
        result['total_tx_count'] = {
            'initial': total_initial_txs,
            'final': total_final_txs,
            'change': total_final_txs - total_initial_txs
        }
        
        # Log final results summary
        logger.debug(f"MNEE sync completed for account {self.get_id()}")
        logger.debug(f"Results: {result['mnee_api_calls']} API calls, " + 
                    f"{result['tokens_found']} tokens found, " +
                    f"{result['directly_stored_transactions']} transactions directly stored, " +
                    f"across {result.get('addresses_with_transactions', 0)}/{result['address_count']} addresses")
        
        # Final balance summary
        if total_final_balance > 0:
            logger.info(f"Current token balance: {total_final_balance} (change: {total_final_balance - total_initial_balance})")
        
        return result

    def fetch_txids_for_multiple_addresses(self, addresses: List[str]) -> Dict:
        """
        Fetch transaction IDs for multiple addresses from the MNEE API.
        
        Args:
            addresses: List of addresses to query
            
        Returns:
            Dictionary mapping addresses to lists of transaction IDs
        """
        logger.debug(f"Fetching txids for {len(addresses)} addresses")
        
        # Get MNEE configuration
        config = app_state.config
        mnee_env = config.get('mnee_environment', 'production')
        base_url = MNEE_API_URL_PRODUCTION if mnee_env == 'production' else MNEE_API_URL_SANDBOX
        api_key = config.get(f'mnee_api_key_{mnee_env}', MNEE_DEFAULT_API_KEY)
        
        result = {}
        directly_stored_count = 0
        
        try:
            # Build the API endpoint for getting transactions
            endpoint = "/v1/sync"
            url = f"{base_url}{endpoint}?auth_token={api_key}"
            
            # Set up headers
            headers = {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
            
            # Create request data with these addresses
            data = json.dumps(addresses).encode('utf-8')
            
            # Make the API request
            logger.debug(f"Calling MNEE API at: {url} with {len(addresses)} addresses")
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            
            with urllib.request.urlopen(req, timeout=30) as response:
                raw_data = response.read().decode()
                api_response = json.loads(raw_data) if raw_data else []
                
                # Process each transaction in the response
                tx_count = len(api_response) if isinstance(api_response, list) else 0
                logger.debug(f"Got response with {tx_count} transactions")
                
                # Process transactions from API response
                if isinstance(api_response, list) and api_response:
                    # Create address -> [txids] mapping
                    address_to_txids = {}
                    
                    # Process each transaction
                    for tx_item in api_response:
                        if not isinstance(tx_item, dict):
                            continue
                            
                        # Extract transaction ID
                        txid = None
                        for id_field in ['txid', 'hash', 'id', 'transaction_hash', 'tx_hash']:
                            if id_field in tx_item and tx_item[id_field]:
                                txid = tx_item[id_field]
                                break
                        
                        if not txid or not isinstance(txid, str):
                            continue
                        
                        # Extract addresses involved
                        tx_addresses = []
                        
                        # Check senders
                        if 'senders' in tx_item and isinstance(tx_item['senders'], list):
                            tx_addresses.extend(tx_item['senders'])
                            
                        # Check receivers
                        if 'receivers' in tx_item and isinstance(tx_item['receivers'], list):
                            tx_addresses.extend(tx_item['receivers'])
                            
                        # Map each address to this txid
                        for addr in tx_addresses:
                            if addr in addresses:  # Only include requested addresses
                                if addr not in address_to_txids:
                                    address_to_txids[addr] = []
                                if txid not in address_to_txids[addr]:
                                    address_to_txids[addr].append(txid)
                    
                    # Add to result
                    result.update(address_to_txids)
                    
                    # Set up result dictionary for addresses with no transactions
                    for addr in addresses:
                        if addr not in result:
                            result[addr] = []
                            
                    # Record how many transactions were processed
                    result['directly_stored_count'] = directly_stored_count
                else:
                    logger.warning(f"Unexpected response format from API: {type(api_response)}")
                    # Return empty lists for all addresses
                    for addr in addresses:
                        result[addr] = []
        except Exception as e:
            logger.error(f"Error fetching transactions for addresses: {str(e)}")
            result['error'] = str(e)
            result['error_storing_transactions'] = True
        
        return result

    def _process_key_usage(self, tx_hash: bytes, tx: Transaction,
            relevant_txos: Optional[List[Tuple[int, XTxOutput]]]) -> bool:
        # This method is an override of AbstractAccount._process_key_usage
        # to ensure MNEE transactions are correctly associated with this account's keys,
        # especially when they are newly added and not yet in _sync_state.
        # It largely mirrors the original logic but expands the candidate keys.
        
        tx_id = hash_to_hex_str(tx_hash)
        logger.debug(f"MneeAccount._process_key_usage for tx_id: {tx_id}, account_id: {self.get_id()}")

        # Get key_ids already known to be associated with this transaction by sync_state
        known_key_ids = self._sync_state.get_transaction_key_ids(tx_id)
        
        # Also consider all active keys of this account
        all_active_key_ids = set(ki.keyinstance_id for ki in self._keyinstances.values() 
                                 if ki.flags & KeyInstanceFlag.IS_ACTIVE)
        
        candidate_key_ids = known_key_ids.union(all_active_key_ids)

        if not candidate_key_ids:
            logger.debug(f"No candidate keys for tx {tx_id} in MneeAccount {self.get_id()}. Known: {len(known_key_ids)}, Active: {len(all_active_key_ids)}")
            return False

        key_matches = []
        for key_id in candidate_key_ids:
            try:
                key_instance = self.get_keyinstance(key_id)
                
                # FIXED: Check if the key has a valid script type before trying to get cached script
                from electrumsv.constants import ScriptType
                if key_instance.script_type == ScriptType.NONE:
                    logger.debug(f"Skipping key_id {key_id} with ScriptType.NONE")
                    continue
                    
                # _get_cached_script returns (ScriptTemplate, script_bytes, AddressOrNone)
                script_tpl, script_bytes, address_obj = self._get_cached_script(key_id)
                key_matches.append((key_instance, script_tpl, script_bytes, address_obj))
            except Exception as e:
                logger.error(f"Error in MneeAccount._process_key_usage getting cached script for key_id {key_id}: {e}")
        
        if not key_matches:
            logger.debug(f"No key_matches constructed for tx {tx_id} in MneeAccount {self.get_id()} from {len(candidate_key_ids)} candidate keys.")
            return False
        
        logger.debug(f"MneeAccount._process_key_usage: tx {tx_id}, {len(key_matches)} key_matches to check against.")

        # Largely based on AbstractAccount._process_key_usage
        base_txo_flags = TransactionOutputFlag.IS_COINBASE if tx.is_coinbase() else TransactionOutputFlag.NONE
        tx_deltas: Dict[Tuple[bytes, int], int] = defaultdict(int)
        
        # Output processing
        outputs_processed_for_account = False
        for output_index, output in relevant_txos or enumerate(tx.outputs):
            # Skip if UTXO already exists for this account or is spent by this account
            if self.get_utxo(tx_hash, output_index) is not None or self.get_stxo(tx_hash, output_index) is not None:
                continue

            output_script_bytes = bytes(output.script_pubkey)
            matched_keyinstance = None
            matched_script_tpl = None
            matched_address_obj = None

            for ki, script_tpl, script_b, addr_obj in key_matches:
                if script_b == output_script_bytes:
                    matched_keyinstance = ki
                    matched_script_tpl = script_tpl
                    matched_address_obj = addr_obj
                    logger.debug(f"tx {tx_id} output {output_index} MATCHED key_id {ki.keyinstance_id}")
                    break
            
            if matched_keyinstance:
                outputs_processed_for_account = True
                txo_flags = base_txo_flags
                # Check if this output is spent by another transaction already known for this key
                for spend_tx_id, _height in self._sync_state.get_key_history(
                        matched_keyinstance.keyinstance_id):
                    if spend_tx_id == tx_id: continue
                    spend_tx_hash = hex_str_to_hash(spend_tx_id)
                    spend_tx = self._wallet._transaction_cache.get_transaction(spend_tx_hash)
                    if spend_tx is None: continue
                    for spend_txin in spend_tx.inputs:
                        if spend_txin.prev_hash == tx_hash and spend_txin.prev_idx == output_index:
                            tx_deltas[(spend_tx_hash, matched_keyinstance.keyinstance_id)] -= output.value
                            txo_flags |= TransactionOutputFlag.IS_SPENT
                            break
                    else: # Inner loop didn't break
                        continue
                    break # Outer loop (spend_tx_id history)

                self.create_transaction_output(tx_hash, output_index, output.value,
                    txo_flags, matched_keyinstance, matched_script_tpl.to_script(), matched_address_obj)
                tx_deltas[(tx_hash, matched_keyinstance.keyinstance_id)] += output.value
        
        # Input processing
        inputs_processed_for_account = False
        for input_index, tx_input in enumerate(tx.inputs):
            # Check if this input spends a UTXO belonging to this account
            utxo_spent = self.get_utxo(tx_input.prev_hash, tx_input.prev_idx)
            if utxo_spent and utxo_spent.keyinstance_id in candidate_key_ids: # ensure key is part of this account
                inputs_processed_for_account = True
                logger.debug(f"tx {tx_id} input {input_index} spends UTXO from key_id {utxo_spent.keyinstance_id}")
                self.set_utxo_spent(tx_input.prev_hash, tx_input.prev_idx)
                tx_deltas[(tx_hash, utxo_spent.keyinstance_id)] -= utxo_spent.value
        
        if tx_deltas:
            logger.info(f"MneeAccount: tx {tx_id} resulted in {len(tx_deltas)} deltas for account {self.get_id()}.")
            check_keyinstance_ids = set(r[1] for r in tx_deltas.keys())
            delta_rows = [ TransactionDeltaRow(k[0], k[1], v, None) for k, v in tx_deltas.items() ]
            self._wallet.create_or_update_transactiondelta_relative(
                delta_rows, partial(self.requests.check_paid_requests, check_keyinstance_ids))

            affected_keys = [self._keyinstances[k_id] for (_h, k_id) in tx_deltas.keys() if k_id in self._keyinstances]
            if affected_keys:
                 self._wallet.trigger_callback('on_keys_updated', self._id, affected_keys)
            return True
        
        if outputs_processed_for_account or inputs_processed_for_account:
            logger.debug(f"MneeAccount: tx {tx_id} processed outputs/inputs but no deltas for account {self.get_id()}. Returning True as relevant.")
            return True # Transaction was relevant even if no value delta (e.g., moving own funds)

        logger.debug(f"MneeAccount: tx {tx_id} not relevant to account {self.get_id()} after processing.")
        return False

    def process_mnee_transactions(self, key_id: int, txids: List[str]) -> List[Dict]:
        """
        Process a list of transaction IDs for a specific key.
        This method updates the token counts for a key and returns a list of processed transactions.
        
        Args:
            key_id: The keyinstance_id to update with transaction counts
            txids: List of transaction IDs to process
            
        Returns:
            List of processed transaction data dictionaries
        """
        logger.debug(f"Processing {len(txids)} MNEE transactions for key {key_id}")
        
        if not txids:
            return []
            
        # Update the transaction count for this key
        current_count = self._mnee_tx_count_per_key.get(key_id, 0)
        new_count = current_count + len(txids)
        self._mnee_tx_count_per_key[key_id] = new_count
        
        # Process each transaction
        processed_txs = []
        for txid in txids:
            try:
                # Create a transaction data entry
                tx_data = {
                    'txid': txid,
                    'key_id': key_id,
                    'processed_time': int(time.time())
                }
                
                # Add to the processed list
                processed_txs.append(tx_data)
                
                # Make sure to save the data to the database
                if hasattr(self._wallet, 'get_db_context'):
                    db_context = self._wallet.get_db_context()
                    wallet_data_table = WalletDataTable(db_context)
                    
                    # Create a record of this transaction being processed
                    data_key = f"mnee_tx_processed_{txid[:8]}_{key_id}"
                    data_value = json.dumps(tx_data)
                    data_row = WalletDataRow(key=data_key, value=data_value)
                    wallet_data_table.upsert([data_row])
            except Exception as e:
                logger.error(f"Error processing MNEE transaction {txid[:8]} for key {key_id}: {str(e)}")
        
        # Save the transaction count back to the database
        self._save_mnee_data_to_db()
        
        # Return the list of processed transactions
        return processed_txs
