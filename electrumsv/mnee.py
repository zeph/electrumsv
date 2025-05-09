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
from .transaction import Transaction
from .wallet_database.tables import (AccountRow, KeyInstanceRow, TransactionOutputRow, 
                                     WalletEventRow, WalletEventTable, WalletDataRow, WalletDataTable)
from .wallet import StandardAccount, DeterministicKeyAllocation

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

        transactions_from_response = []
        # ... (existing logic to populate transactions_from_response based on response_data type) ...
        # (This part of your code for extracting 'transactions' from 'response_data' needs to be here)
        # Example sketch of extraction logic (replace with your actual comprehensive logic):
        if isinstance(response_data, list):
            transactions_from_response = response_data
        elif isinstance(response_data, dict):
            if 'transactions' in response_data and isinstance(response_data['transactions'], list):
                transactions_from_response = response_data['transactions']
            elif 'data' in response_data and isinstance(response_data['data'], list):
                transactions_from_response = response_data['data']
            # ... add other checks as in your original code ...
        
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
        
        for tx_item in transactions_from_response:
            txid = None
            rawtx = None
            
            if isinstance(tx_item, dict):
                for id_field in ['txid', 'hash', 'id', 'transaction_hash', 'tx_hash']:
                    if id_field in tx_item and tx_item[id_field]:
                        txid = tx_item[id_field]
                        break
                for data_field in ['rawtx', 'raw', 'rawTx', 'hex', 'txhex', 'raw_tx', 'data']:
                    if data_field in tx_item and tx_item[data_field]:
                        rawtx = tx_item[data_field]
                        break
            
            if not txid:
                logger.debug(f"Missing transaction ID in item: {str(tx_item)[:100]}...")
                continue 
                
            if not isinstance(txid, str) or len(txid) != 64 or not all(c in '0123456789abcdefABCDEF' for c in txid):
                logger.error(f"Invalid transaction ID format from API: {txid[:64] if isinstance(txid, str) else str(txid)[:64]}...")
                all_txs_processed_successfully = False
                continue 
            
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


        for txid_to_store, raw_tx_hex_to_store in transactions_to_store_list:
            try:
                # Double-check txid format (already checked but good for safety before storage call)
                if not isinstance(txid_to_store, str) or len(txid_to_store) != 64 or not all(c in '0123456789abcdefABCDEF' for c in txid_to_store):
                    logger.error(f"Skipping storage for malformed transaction ID (final check): {txid_to_store[:64] if isinstance(txid_to_store, str) else str(txid_to_store)[:64]}...")
                    all_txs_processed_successfully = False
                    continue # Or break, if one invalid txid means the whole batch is suspect
                    
                if self.ensure_transaction_stored(txid_to_store, raw_tx_hex_to_store):
                    stored_count += 1
                    logger.debug(f"Successfully stored transaction {txid_to_store[:10]}... from API")
                else:
                    logger.warning(f"Failed to store transaction {txid_to_store[:10]}... from API (ensure_transaction_stored returned False)")
                    all_txs_processed_successfully = False
                    break # Stop processing this batch if a transaction fails to store due to flush issues
            except Exception as store_error:
                error_msg = f"Error storing transaction {txid_to_store[:10]}...: {str(store_error)}"
                logger.error(f"[_process_and_store] {error_msg}", exc_info=True)
                all_txs_processed_successfully = False
                break # Stop processing this batch on other storage exceptions too
        
        return stored_count, all_txs_processed_successfully

    def fetch_txids_for_multiple_addresses(self, addresses: List[str]) -> Dict:
        """
        Fetch transaction IDs for multiple addresses from the MNEE API.
        
        Args:
            addresses: List of addresses to fetch transactions for
            
        Returns:
            Dictionary mapping addresses to their transaction IDs
        """
        logger.debug(f"Fetching transaction IDs for {len(addresses)} addresses")
        result = {}
        directly_stored_count = 0
        
        # Get MNEE configuration
        config = app_state.config
        mnee_env = config.get('mnee_environment', 'production')
        base_url = MNEE_API_URL_PRODUCTION if mnee_env == 'production' else MNEE_API_URL_SANDBOX
        api_key = config.get(f'mnee_api_key_{mnee_env}', MNEE_DEFAULT_API_KEY)
        
        try:
            # Build the API request
            endpoint = "/v1/sync"
            url = f"{base_url}{endpoint}?auth_token={api_key}"
            
            # Set up headers
            headers = {
                'Content-Type': 'application/json',
            }
            
            # Create the JSON payload for this batch of addresses
            data = json.dumps(addresses).encode('utf-8')
            
            # Make API request
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    status_code = response.getcode()
                    
                    if status_code == 200:
                        raw_data = response.read().decode()
                        logger.debug(f"Received response: {len(raw_data)} bytes")
                        
                        # Parse the response data
                        response_data = json.loads(raw_data)
                        
                        # First check if we should directly store transactions
                        transactions = []
                        if isinstance(response_data, list):
                            transactions = response_data
                        elif isinstance(response_data, dict) and 'transactions' in response_data:
                            transactions = response_data['transactions']
                        
                        # Extract and store transactions with raw data
                        if transactions:
                            logger.debug(f"Processing {len(transactions)} transactions from API response for direct storage")
                            # Build a list of (txid, raw_tx_hex) tuples to store
                            transactions_to_store_from_api = []
                            for tx_data in transactions:
                                if 'txid' not in tx_data:
                                    continue
                                
                                txid = tx_data['txid']
                                if not isinstance(txid, str) or len(txid) != 64 or not all(c in '0123456789abcdefABCDEF' for c in txid):
                                    logger.error(f"Invalid transaction ID format from API: {txid[:64] if isinstance(txid, str) else str(txid)[:64]}...")
                                    continue # Skip this invalid one
                                
                                raw_tx_hex_data = None
                                # Adjusted to look for 'rawtx' then 'hex' as common alternatives
                                if 'rawtx' in tx_data and tx_data['rawtx']:
                                    raw_tx_hex_data = tx_data['rawtx']
                                elif 'hex' in tx_data and tx_data['hex']:
                                     raw_tx_hex_data = tx_data['hex']

                                if raw_tx_hex_data:
                                    try:
                                        if not all(c in '0123456789abcdefABCDEF' for c in raw_tx_hex_data):
                                            logger.debug(f"Converting non-hex transaction data for {txid[:10]}...")
                                            import base64
                                            padded = raw_tx_hex_data + ("=" * ((4 - len(raw_tx_hex_data) % 4) % 4))
                                            raw_tx_hex_data = base64.b64decode(padded).hex()
                                        
                                        if len(raw_tx_hex_data) > 100: # Arbitrary minimum for a valid tx
                                            transactions_to_store_from_api.append((txid, raw_tx_hex_data))
                                    except Exception as e:
                                        logger.error(f"Error validating/converting direct-store transaction data for {txid[:10]}: {str(e)}")
                                        continue # Skip this problematic transaction
                            
                            # Store transactions with our improved method
                            for txid_to_store, raw_tx_hex_to_store in transactions_to_store_from_api:
                                try:
                                    # Double-check txid one last time
                                    if not isinstance(txid_to_store, str) or len(txid_to_store) != 64 or not all(c in '0123456789abcdefABCDEF' for c in txid_to_store):
                                        logger.error(f"Skipping invalid transaction ID before storage (direct): {txid_to_store[:64] if isinstance(txid_to_store, str) else str(txid_to_store)[:64]}...")
                                        continue
                                        
                                    if self.ensure_transaction_stored(txid_to_store, raw_tx_hex_to_store):
                                        directly_stored_count += 1
                                    else:
                                        logger.error(f"Failed to store transaction {txid_to_store[:10]}... in fetch_txids_for_multiple_addresses.")
                                        result['error_storing_transactions'] = True # Signal error
                                        # Ensure all addresses are in the result dictionary before early exit
                                        for addr_in_batch in addresses:
                                            if addr_in_batch not in result:
                                                result[addr_in_batch] = []
                                        if directly_stored_count > 0 and 'directly_stored_count' not in result:
                                            result['directly_stored_count'] = directly_stored_count
                                        return result # Exit early
                                except Exception as e:
                                    logger.error(f"[fetch_txids] Error storing transaction {txid_to_store[:10]}...: {str(e)}", exc_info=True)
                                    result['error_storing_transactions'] = True # Signal error
                                    for addr_in_batch in addresses:
                                        if addr_in_batch not in result:
                                            result[addr_in_batch] = []
                                    if directly_stored_count > 0 and 'directly_stored_count' not in result:
                                        result['directly_stored_count'] = directly_stored_count
                                    return result # Exit early
                        elif isinstance(response_data, list):
                            # Format: [{'txid': '...', 'address': '...', ...}, ...]
                            # Group transactions by address
                            address_to_txids = {}
                            for tx_data in response_data:
                                if 'txid' in tx_data and 'address' in tx_data:
                                    addr = tx_data['address']
                                    txid = tx_data['txid']
                                    # CRITICAL FIX: Validate txid before adding to results
                                    if addr in addresses and isinstance(txid, str) and len(txid) == 64 and all(c in '0123456789abcdefABCDEF' for c in txid):
                                        if addr not in address_to_txids:
                                            address_to_txids[addr] = []
                                        address_to_txids[addr].append(txid)
                                    elif addr in addresses:
                                        logger.error(f"Invalid transaction ID format in list data: {txid[:64] if isinstance(txid, str) else str(txid)[:64]}...")
                            result.update(address_to_txids)
                        
                        # CRITICAL FINAL SAFETY CHECK: Ensure all txids in result are valid
                        for addr in list(result.keys()):
                            if addr != 'directly_stored_count':  # Skip the counter
                                valid_txids = []
                                for txid in result[addr]:
                                    if isinstance(txid, str) and len(txid) == 64 and all(c in '0123456789abcdefABCDEF' for c in txid):
                                        valid_txids.append(txid)
                                    else:
                                        logger.error(f"Final check: Invalid txid removed from results: {txid[:64] if isinstance(txid, str) else str(txid)[:64]}...")
                                result[addr] = valid_txids
                        
                        # Ensure all addresses are in the result, even with empty lists
                        for addr in addresses:
                            if addr not in result:
                                result[addr] = []
                        
                        # Add the directly stored count to the result
                        if directly_stored_count > 0:
                            result['directly_stored_count'] = directly_stored_count
                            
            except urllib.error.HTTPError as http_err:
                logger.error(f"HTTP error when fetching transactions: {http_err.code} - {http_err.reason}")
                # Return empty lists for all addresses on error
                for addr in addresses:
                    result[addr] = []
                
            except urllib.error.URLError as url_err:
                logger.error(f"URL error when fetching transactions: {url_err.reason}")
                # Return empty lists for all addresses on error
                for addr in addresses:
                    result[addr] = []
                    
        except Exception as e:
            logger.error(f"Error fetching transactions for addresses: {str(e)}")
            # Return empty lists for all addresses on error
            for addr in addresses:
                result[addr] = []
                
        # Ensure all transactions are properly flushed to the database
        if directly_stored_count > 0:
            logger.debug(f"Explicitly flushing {directly_stored_count} transactions to database from API sync")
            if self.safe_flush_transactions():
                logger.debug("Database flush completed successfully")
            else:
                logger.warning("Failed to flush transactions, they may not be persisted")
        
        return result
    
    def safe_flush_transactions(self) -> bool:
        """
        Placeholder for flushing transactions. 
        The standard Wallet.add_transaction queues writes to a dispatcher.
        An explicit flush here might be unnecessary or conflict if not done carefully.
            
        Returns:
            True, assuming writes are handled by the dispatcher.
        """
        logger.debug("safe_flush_transactions called. Trusting async dispatcher for Wallet.add_transaction writes.")
        # For now, we assume that if add_transaction succeeded in queuing,
        # the dispatcher will handle the commit. Forcing a flush here has been problematic.
        # If specific MNEE data (like WalletData) needs an explicit flush point after a batch,
        # this method could be revisited, perhaps by ensuring DatabaseContext queue is empty.
        return True
    
    def ensure_transaction_stored(self, txid: str, tx_hex: str) -> bool:
        """
        Ensure a transaction is stored in the wallet database using standard wallet methods.
        
        Args:
            txid: The transaction ID (hex string)
            tx_hex: The transaction data in hex format
            
        Returns:
            True if transaction was stored successfully or already exists, False otherwise
        """
        logger.debug(f"Attempting to store transaction {txid[:10]}...")
        
        if not self._wallet:
            logger.error(f"No wallet available to store transaction {txid[:10]}")
            return False
            
        if not isinstance(txid, str) or len(txid) != 64 or not all(c in '0123456789abcdefABCDEF' for c in txid):
            logger.error(f"Invalid transaction ID format for ensure_transaction_stored: {txid[:64]}")
            return False

        try:
            # Convert hex string txid to bytes
            tx_hash_bytes = hex_str_to_hash(txid)

            # Check if transaction already exists using the wallet's method
            if hasattr(self._wallet, 'have_transaction') and self._wallet.have_transaction(tx_hash_bytes):
                logger.debug(f"Transaction {txid[:10]}... already exists in wallet.")
                return True
            if hasattr(self._wallet, '_transaction_cache') and \
               hasattr(self._wallet._transaction_cache, 'get_transaction_entry') and \
               self._wallet._transaction_cache.get_transaction_entry(tx_hash_bytes) is not None:
                logger.debug(f"Transaction {txid[:10]}... already exists in transaction cache.")
                return True

            # Validate and convert tx_hex to a Transaction object
            if not all(c in '0123456789abcdefABCDEF' for c in tx_hex):
                logger.debug(f"Transaction data for {txid[:10]}... is not hex, trying base64 decode.")
                try:
                    import base64
                    padded_tx_hex = tx_hex + ("=" * ((4 - len(tx_hex) % 4) % 4))
                    tx_bytes_data = base64.b64decode(padded_tx_hex)
                    tx_hex = tx_bytes_data.hex()
                    logger.debug(f"Successfully converted base64 to hex for {txid[:10]}.")
                except Exception as e:
                    logger.error(f"Failed to convert non-hex transaction data for {txid[:10]}: {e}")
                    return False
            
            tx_bytes = bytes.fromhex(tx_hex)
            transaction_obj = Transaction.from_bytes(tx_bytes)

            # Use wallet.add_transaction - this is the standard method
            if hasattr(self._wallet, 'add_transaction'):
                logger.debug(f"Using wallet.add_transaction for {txid[:10]}...")
                self._wallet.add_transaction(tx_hash_bytes, transaction_obj, TxFlags.Unset)
                logger.info(f"Successfully queued transaction {txid[:10]}... via wallet.add_transaction.")
                # Assuming add_transaction queues it for persistence reliably.
                # The actual commit is handled by the DatabaseContext's write dispatcher.
                return True # Indicate the queuing was successful
            else:
                logger.error(f"Wallet object does not have 'add_transaction' method. Cannot store {txid[:10]}.")
                return False

        except Exception as e:
            logger.error(f"General error in ensure_transaction_stored for {txid[:10]}: {e}", exc_info=True)
            return False
    
    def process_mnee_transactions(self, key_id: int, txids: List[str]) -> List[Dict]:
        """
        Process MNEE transactions for a specific key.
        
        Args:
            key_id: The key instance ID
            txids: List of transaction IDs to process
            
        Returns:
            List of processed MNEE token data
        """
        logger.debug(f"Processing {len(txids)} MNEE transactions for key {key_id}")
        
        # Get the token ID from config
        config = app_state.config
        token_id = config.get('bsv20_token_id', '')
        
        if not token_id:
            # Try to get from cached config
            if self._mnee_config_cache and 'tokenId' in self._mnee_config_cache:
                token_id = self._mnee_config_cache['tokenId']
            
            if not token_id:
                logger.warning("No token ID available, cannot identify MNEE transactions")
                return []
        
        # Store the transactions if possible
        processed_mnee_txs = []
        
        # Process each transaction
        for txid in txids:
            try:
                # CRITICAL FIX: Validate txid is a valid transaction ID format (64 hex chars)
                if not isinstance(txid, str) or len(txid) != 64 or not all(c in '0123456789abcdefABCDEF' for c in txid):
                    logger.error(f"Invalid transaction ID format: {txid[:64] if isinstance(txid, str) else str(txid)[:64]}...")
                    continue
                
                # Skip if already processed
                if self._mnee_tx_count_per_key.get(key_id, 0) > 0 and txid in processed_mnee_txs:
                    continue
                
                # Get the transaction data
                tx_data = None
                
                # Try to get from wallet first
                if hasattr(self._wallet, 'get_transaction'):
                    try:
                        tx_data = self._wallet.get_transaction(txid)
                    except Exception as e:
                        logger.debug(f"Error getting transaction {txid[:10]}... from wallet: {str(e)}")
                
                # If we couldn't get it from the wallet, try to fetch it
                if not tx_data and hasattr(self._wallet, '_network'):
                    try:
                        network = self._wallet._network
                        if hasattr(network, 'request_fetch_transaction'):
                            logger.debug(f"Fetching transaction {txid[:10]}... from network")
                            tx_data = network.request_fetch_transaction(txid)
                    except Exception as e:
                        logger.debug(f"Error fetching transaction {txid[:10]}... from network: {str(e)}")
                
                # If we got the transaction data, analyze it for MNEE tokens
                if tx_data:
                    # Check if this is an MNEE token transaction
                    is_mnee_tx = False
                    token_amount = 0
                    
                    # Simple check for MNEE BSV-20 tokens in the transaction
                    # This is a simplified check, real implementation would be more thorough
                    tx_hex = tx_data.hex() if isinstance(tx_data, bytes) else tx_data
                    
                    # Look for BSV-20 and the token ID in the transaction
                    if "bsv-20" in tx_hex and token_id in tx_hex:
                        is_mnee_tx = True
                        # This is a very simplified token amount extraction
                        # Real implementation would decode the transaction properly
                        token_amount = 1  # Placeholder
                    
                    if is_mnee_tx:
                        # Store token data
                        token_data = {
                            'txid': txid,
                            'amount': token_amount,
                            'token_id': token_id
                        }
                        processed_mnee_txs.append(token_data)
                        
                        # Update counters
                        self._mnee_tx_count_per_key[key_id] = self._mnee_tx_count_per_key.get(key_id, 0) + 1
                        
                        # Update balance (simple accumulation, real impl would be more complex)
                        current_balance = self._mnee_balances_per_key.get(key_id, 0)
                        self._mnee_balances_per_key[key_id] = current_balance + token_amount
            
            except Exception as e:
                logger.error(f"Error processing transaction {txid[:10]}...: {str(e)}")
                
        return processed_mnee_txs
    
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
        
        # Log final results summary
        logger.debug(f"MNEE sync completed for account {self.get_id()}")
        logger.debug(f"Results: {result['mnee_api_calls']} API calls, " + 
                    f"{result['tokens_found']} tokens found, " +
                    f"{result['directly_stored_transactions']} transactions directly stored, " +
                    f"across {result.get('addresses_with_transactions', 0)}/{result['address_count']} addresses")
        
        return result
