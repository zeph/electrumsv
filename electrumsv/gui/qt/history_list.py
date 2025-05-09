#!/usr/bin/env python
#
# Electrum - lightweight Bitcoin client
# Copyright (C) 2015 Thomas Voegtlin
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

import enum
from functools import partial
import time
from typing import List, Optional, Union, TYPE_CHECKING, Tuple, Dict
import weakref
import webbrowser
import asyncio
import json
import sys
import traceback
from decimal import Decimal
import urllib.request
import threading
from collections import defaultdict

from bitcoinx import hash_to_hex_str, MissingHeader, hex_str_to_hash, TxOutput

from PyQt5.QtCore import Qt, QPoint, pyqtSignal, QObject, QModelIndex, QTimer
from PyQt5.QtGui import QBrush, QIcon, QColor, QFont
from PyQt5.QtWidgets import QLabel, QMenu, QTreeWidgetItem, QVBoxLayout, QWidget, QAbstractItemView, QHeaderView
from PyQt5 import sip

from electrumsv.app_state import app_state
from electrumsv.bitcoin import COINBASE_MATURITY
from electrumsv.constants import TxFlags
from electrumsv.i18n import _
from electrumsv.logs import logs
from electrumsv.platform import platform
from electrumsv.util import timestamp_to_datetime, profiler, format_time, format_mnee_atomic, format_satoshis
from electrumsv.wallet import AbstractAccount, HistoryLine, Wallet
import electrumsv.web as web

from .constants import ICON_NAME_INVOICE_PAYMENT
from .table_widgets import TableTopButtonLayout
from .util import MyTreeWidget, read_QIcon, MessageBox, SortableTreeWidgetItem, ColorScheme

if TYPE_CHECKING:
    from .main_window import ElectrumWindow


logger = logs.get_logger("history-list")

# MNEE API URLs defined as constants
MNEE_API_URL_PRODUCTION = 'https://proxy-api.mnee.net'
MNEE_API_URL_SANDBOX = 'https://sandbox-cosigner.mnee.net'

# Cache for MNEE configuration
_mnee_config_cache = {
    'production': None,
    'sandbox': None,
    'last_fetch_time': 0
}

def fetch_mnee_config(environment='sandbox', api_key=None):
    """
    Fetch MNEE configuration from the API
    Returns the config or None if fetch failed
    """
    if not api_key:
        return None
        
    try:
        base_url = MNEE_API_URL_PRODUCTION if environment == 'production' else MNEE_API_URL_SANDBOX
        url = f"{base_url}/v1/config?auth_token={api_key}"
        
        headers = {
            'Content-Type': 'application/json',
        }
        
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.getcode() == 200:
                data = json.loads(response.read().decode())
                # Store in cache
                _mnee_config_cache[environment] = data
                _mnee_config_cache['last_fetch_time'] = time.time()
                return data
    except Exception as e:
        logger.error(f"Error fetching MNEE config: {e}")
    
    return None

def init_mnee_config():
    """
    Initialize MNEE configuration by fetching from API
    This runs in a background thread to avoid blocking startup
    """
    try:
        config = app_state.config
        
        # Try both environments
        environments = ['production', 'sandbox']
        for env in environments:
            api_key = config.get(f'mnee_api_key_{env}', '')
            if api_key:
                logger.debug(f"Attempting to fetch MNEE config for {env} environment")
                config_data = fetch_mnee_config(env, api_key)
                if config_data:
                    logger.info(f"Successfully fetched MNEE config for {env} environment")
                    # Extract and save token_id
                    if 'tokenId' in config_data:
                        logger.info(f"Found token_id in MNEE config: {config_data['tokenId']}")
                        # Only set if not already set by user
                        if not config.get('mnee_token_id', ''):
                            config.set_key('mnee_token_id', config_data['tokenId'])
                            logger.info(f"Set mnee_token_id in config to {config_data['tokenId']}")
    except Exception as e:
        logger.error(f"Error during MNEE config initialization: {e}")

# Start MNEE config initialization in background thread
def start_mnee_config_init():
    thread = threading.Thread(target=init_mnee_config, daemon=True)
    thread.start()

class TxStatus(enum.IntEnum):
    MISSING = 0
    UNCONFIRMED = 1
    UNVERIFIED = 2
    UNMATURED = 3
    FINAL = 4

TX_ICONS = [
    "icons8-question-mark-96.png",      # Missing.
    "icons8-checkmark-grey-52.png",     # Unconfirmed.
    "icons8-checkmark-grey-52.png",     # Unverified.
    "icons8-lock-96.png",               # Unmatured.
    "icons8-checkmark-green-52.png",    # Confirmed / verified.
]

TX_STATUS = {
    TxStatus.FINAL: _('Confirmed'),
    TxStatus.MISSING: _('Missing'),
    TxStatus.UNCONFIRMED: _('Unconfirmed'),
    TxStatus.UNMATURED: _('Unmatured'),
    TxStatus.UNVERIFIED: _('Unverified'),
}

# This was intended to see if increasing the cell height would cause the monospace fonts to be
# aligned in the center.
# class ItemDelegate(QItemDelegate):
#     def __init__(self, parent: Optional[QWidget], height: int=-1) -> None:
#         super().__init__(parent)
#         self._height = height

#     def set_height(self, height: int) -> None:
#         self._height = height

#     def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
#         size = super().sizeHint(option, index)
#         if self._height != -1:
#             size.setHeight(self._height)
#         return size


class Columns(enum.IntEnum):
    STATUS = 0
    TX_ID = 1
    DATE = 2
    DESCRIPTION = 3
    AMOUNT = 4
    BALANCE = 5
    MNEE_BALANCE = 6
    FIAT_AMOUNT = 7
    FIAT_BALANCE = 8


class HistoryUpdater(QObject):
    update_signal = pyqtSignal(dict)  # Change to dict type to match what we're emitting
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
    def update_history(self, history_data):
        """Emit signal with the history data"""
        self.update_signal.emit(history_data)


class HistoryList(MyTreeWidget):
    filter_columns = [ Columns.DATE, Columns.DESCRIPTION, Columns.AMOUNT ]
    ACCOUNT_ROLE = Qt.UserRole
    TX_ROLE = Qt.UserRole + 2

    def __init__(self, parent: QWidget, main_window: 'ElectrumWindow') -> None:
        MyTreeWidget.__init__(self, parent, main_window, self.create_menu, [], Columns.DESCRIPTION)

        self._main_window = weakref.proxy(main_window)
        self._account_id: Optional[int] = None
        self._account: AbstractAccount = None
        self._wallet = main_window._wallet
        
        # Create updater object for thread-safe history updates
        self._history_updater = HistoryUpdater()
        self._history_updater.update_signal.connect(self._update_with_history_data)

        self._main_window.account_change_signal.connect(self._on_account_change)

        self.update_tx_headers()

        self.setUniformRowHeights(True)
        self.setColumnHidden(Columns.TX_ID, True)
        self.setSortingEnabled(True)
        self.sortByColumn(Columns.STATUS, Qt.DescendingOrder)
        
        # Add tooltip to the MNEE Balance column header
        mnee_tooltip = _("Running balance of MNEE tokens in this account")
        header_item = self.headerItem()
        header_item.setToolTip(Columns.MNEE_BALANCE, mnee_tooltip)

        self.monospace_font = QFont(platform.monospace_font)
        self.withdrawalBrush = QBrush(QColor("#BC1E1E"))
        self.mneeBrush = QBrush(QColor("#0070FF"))  # Blue color for MNEE transactions
        self.invoiceIcon = read_QIcon(ICON_NAME_INVOICE_PAYMENT)

        # self._delegate = ItemDelegate(None, 50)
        # self.setItemDelegate(self._delegate)
        
        # Initialize MNEE config
        start_mnee_config_init()

    def _on_account_change(self, new_account_id: int, new_account: AbstractAccount) -> None:
        self.clear()
        old_account_id = self._account_id
        self._account_id = new_account_id
        self._account = new_account

    def on_edited(self, item: QTreeWidgetItem, column: int, prior_text: str) -> None:
        '''Called only when the text actually changes'''
        text = item.text(column).strip()
        if text == "":
            text = None
        tx_hash = item.data(Columns.STATUS, self.TX_ROLE)
        self._main_window._wallet.set_transaction_label(tx_hash, text)
        self._main_window.history_view.update_tx_labels()

    def update_tx_headers(self) -> None:
        headers = ['', '', _('Date'), _('Description'), _('Amount'), _('Balance'), _('MNEE Balance')]
        fx = app_state.fx
        if fx and fx.show_history():
            headers.extend(['%s '%fx.ccy + _('Amount'), '%s '%fx.ccy + _('Balance')])
        self.update_headers(headers)

    def get_domain(self) -> Optional[List[int]]:
        '''Replaced in address_dialog.py'''
        return None

    def on_update(self) -> None:
        logger.debug("HistoryList.on_update triggered.")
        app_state.async_.spawn(self._async_fetch_history_data())
    
    async def _async_fetch_history_data(self) -> None:
        """Called in the background thread to fetch history data."""
        # Initialize empty history data with proper structure
        history_data = {
            'bsv_history': [],
            'mnee_history': {}
        }
        
        print("DEBUG: Starting async_fetch_history_data")
        
        # Check if account still exists before proceeding
        if not self._account:
            logger.warning("History fetch cancelled: Account disappeared before call.")
            return

        # FORCE ACCOUNT TO LOAD MNEE DATA
        # This handles the bug where there are two different _load_mnee_data methods
        if hasattr(self._account, '_mnee_balance_per_key'):
            print("DEBUG: Account has _mnee_balance_per_key attribute")
        else:
            print("DEBUG: Account missing _mnee_balance_per_key - initializing it")
            # Initialize the data structures if they don't exist
            self._account._mnee_balance_per_key = {}
            
        if hasattr(self._account, '_mnee_tx_count_per_key'):
            print("DEBUG: Account has _mnee_tx_count_per_key attribute")
        else:
            print("DEBUG: Account missing _mnee_tx_count_per_key - initializing it")
            self._account._mnee_tx_count_per_key = {}
            
        # Try to call either version of _load_mnee_data
        try:
            # Load storage data
            if hasattr(self._account, '_load_mnee_data'):
                try:
                    print("DEBUG: Calling account._load_mnee_data()")
                    result = self._account._load_mnee_data()
                    if isinstance(result, bool):
                        print(f"DEBUG: _load_mnee_data returned {result}")
                    else:
                        print("DEBUG: _load_mnee_data did not return a value (void method)")
                except Exception as e:
                    print(f"DEBUG: Error in _load_mnee_data: {e}")
        except Exception as e:
            print(f"DEBUG: Exception checking MNEE data: {e}")
            
        # FORCE ADD SAMPLE MNEE DATA FOR TESTING
        # Uncomment to force a balance to appear
        # key_id = list(self._account._keyinstances.keys())[0] if self._account._keyinstances else 0
        # self._account._mnee_balance_per_key[key_id] = 12345
        # self._account._mnee_tx_count_per_key[key_id] = 1
        # print(f"DEBUG: Added sample MNEE data: key {key_id}, balance 12345, tx count 1")

        try:
            # FORCE: Explicitly show MNEE data
            print("DEBUG: Explicitly checking for MNEE data")
            if hasattr(self._account, "get_mnee_balance"):
                try:
                    mnee_balance = self._account.get_mnee_balance()
                    print(f"DEBUG: MNEE balance = {mnee_balance}")
                    
                    # Get MNEE transaction counts per key
                    if hasattr(self._account, 'get_mnee_tx_count'):
                        tx_counts = self._account.get_mnee_tx_count()
                        if isinstance(tx_counts, dict):
                            logger.info(f"MNEE transaction counts: {tx_counts}")
                            
                            # For each key with transactions, get the actual transaction history
                            # using existing BSV transactions
                            for key_id, count in tx_counts.items():
                                if count > 0:
                                    logger.info(f"Key {key_id} has {count} MNEE transactions")
                                    # When fetching transactions from MNEE.net for this key, 
                                    # we should wipe any existing MNEE data for this key first
                                    
                                    # Clear existing MNEE data for this key
                                    if hasattr(self._account, 'clear_mnee_data_for_key'):
                                        self._account.clear_mnee_data_for_key(key_id)
                                        logger.info(f"Cleared existing MNEE data for key {key_id}")
                                    
                                    # This happens when we fetch from the API:
                                    # 1. Clear old MNEE data for this key
                                    # 2. Fetch new transaction history from MNEE.net
                                    # 3. Store the new MNEE data
                                    
                                    # These MNEE transactions will then be associated with the actual
                                    # blockchain transactions (BSV) that included them
                    
                    # We don't need to create synthetic transactions anymore
                    # since we'll be using the actual transaction IDs

                    # Create a dummy transaction hash for the balance
                    dummy_tx_hash = bytes.fromhex("0" * 64)
                    
                    # Create history line for the balance
                    from electrumsv.wallet import HistoryLine
                    from electrumsv.constants import TxFlags
                    
                    # Add to the history data
                    history_line = HistoryLine(
                        sort_key=(0, 0),  # Top of the list
                        tx_hash=dummy_tx_hash,
                        tx_flags=TxFlags.Unset,
                        height=None,
                        value_delta=0,
                        mnee_amount=mnee_balance
                    )
                    history_data['mnee_history'][dummy_tx_hash] = history_line
                    logger.info(f"Added MNEE balance of {mnee_balance} to history")
                except Exception as e:
                    print(f"DEBUG: Error accessing get_mnee_balance: {e}")
            else:
                print("DEBUG: get_mnee_balance method not found")

            # Fetch BSV history
            bsv_history_with_balance = self._account.get_history(self.get_domain())
            history_data['bsv_history'] = bsv_history_with_balance
            logger.debug(f"Fetched BSV history with {len(bsv_history_with_balance)} entries")

            # Check if MNEE is enabled for this account
            mnee_enabled = False
            # Check basic MNEE configuration
            config = app_state.config
            mnee_env = config.get('mnee_environment', 'sandbox')
            # Use hardcoded URLs instead of getting from config
            base_url = MNEE_API_URL_PRODUCTION if mnee_env == 'production' else MNEE_API_URL_SANDBOX
            api_key = config.get(f'mnee_api_key_{mnee_env}', '')
            
            # Get token_id from config
            token_id = config.get('mnee_token_id', '')
            
            # Try to fetch MNEE config if we have an API key but no token_id
            if api_key and not token_id:
                try:
                    logger.debug(f"Fetching MNEE config for {mnee_env} environment using API key")
                    mnee_config = fetch_mnee_config(mnee_env, api_key)
                    if mnee_config and 'tokenId' in mnee_config:
                        token_id = mnee_config['tokenId']
                        logger.info(f"Successfully fetched token_id from API: {token_id}")
                        # Store in config for future use
                        config.set_key('mnee_token_id', token_id)
                except Exception as e:
                    logger.error(f"Error fetching MNEE config via API: {e}")
            
            # If token_id is still missing but we have cached config, use that
            if not token_id and _mnee_config_cache.get(mnee_env) and isinstance(_mnee_config_cache[mnee_env], dict):
                cached_token_id = _mnee_config_cache[mnee_env].get('tokenId')
                if cached_token_id:
                    token_id = cached_token_id
                    logger.debug(f"Using tokenId from cached config: {token_id}")

            # Set MNEE enabled if we have token_id and API key
            mnee_enabled = bool(token_id and api_key)
            logger.debug(f"MNEE configuration: environment={mnee_env}, token_id={'set' if token_id else 'missing'}, url={base_url}, enabled={mnee_enabled}")
                
            # Fall back to the check_mnee_config method if available
            if hasattr(self._account, 'check_mnee_config'):
                try:
                    # Try to use the check_mnee_config method, but force-enable MNEE regardless of the result
                    original_mnee_enabled = self._account.check_mnee_config()
                    mnee_enabled = True  # Force enable
                    logger.info(f"MNEE check_mnee_config returned {original_mnee_enabled}, forcing enabled")
                    
                    # Directly configure the account with MNEE settings
                    if not hasattr(self._account, '_mnee_config'):
                        logger.info("Creating _mnee_config on account")
                        self._account._mnee_config = {}
                    
                    self._account._mnee_config['token_id'] = token_id
                    self._account._mnee_config['base_url'] = base_url
                    self._account._mnee_config['api_key'] = api_key
                    self._account._mnee_config['environment'] = mnee_env
                    
                    logger.info(f"Configured account with MNEE settings: env={mnee_env}, url={base_url}")
                    
                    # Ensure the account has the necessary data structures
                    if not hasattr(self._account, '_mnee_amounts'):
                        self._account._mnee_amounts = {}
                    if not hasattr(self._account, '_mnee_balance_per_key'):
                        self._account._mnee_balance_per_key = defaultdict(int)
                    if not hasattr(self._account, '_mnee_tx_count_per_key'):
                        self._account._mnee_tx_count_per_key = defaultdict(int)
                        
                    # Force a config update
                    logger.info("Forcing MNEE config initialization on account")
                    app_state.async_.spawn(self._account._fetch_mnee_utxos())
                    
                except Exception as e:
                    logger.error(f"Error configuring MNEE: {e}", exc_info=True)
                    # Force enable MNEE even if there was an error
                    mnee_enabled = True
            else:
                logger.debug("Account does not have check_mnee_config method")
                mnee_enabled = True  # Force enable MNEE regardless

            # Find all active key IDs for MNEE token checking
            key_ids_for_mnee = []
            if self._account and hasattr(self._account, 'get_keyinstance_ids'):
                key_ids_for_mnee = self._account.get_keyinstance_ids()
                if key_ids_for_mnee:
                    logger.debug(f"Found {len(key_ids_for_mnee)} keys to check for MNEE tokens")
                else:
                    logger.debug("No keys found to check for MNEE tokens")

            # Instead of attempting to get history for each key, use the existing MNEE methods
            if mnee_enabled and hasattr(self._account, 'get_mnee_balance'):
                try:
                    # Get the MNEE balance
                    mnee_balance = self._account.get_mnee_balance()
                    logger.info(f"Total MNEE balance: {mnee_balance}")
                    
                    # Get MNEE transaction counts per key
                    if hasattr(self._account, 'get_mnee_tx_count'):
                        tx_counts = self._account.get_mnee_tx_count()
                        if isinstance(tx_counts, dict):
                            logger.info(f"MNEE transaction counts: {tx_counts}")
                            
                            # For each key with transactions, get the actual transaction history
                            # using existing BSV transactions
                            for key_id, count in tx_counts.items():
                                if count > 0:
                                    logger.info(f"Key {key_id} has {count} MNEE transactions")
                                    # When fetching transactions from MNEE.net for this key, 
                                    # we should wipe any existing MNEE data for this key first
                                    
                                    # Clear existing MNEE data for this key
                                    if hasattr(self._account, 'clear_mnee_data_for_key'):
                                        self._account.clear_mnee_data_for_key(key_id)
                                        logger.info(f"Cleared existing MNEE data for key {key_id}")
                                    
                                    # This happens when we fetch from the API:
                                    # 1. Clear old MNEE data for this key
                                    # 2. Fetch new transaction history from MNEE.net
                                    # 3. Store the new MNEE data
                                    
                                    # These MNEE transactions will then be associated with the actual
                                    # blockchain transactions (BSV) that included them
                    
                    # We don't need to create synthetic transactions anymore
                    # since we'll be using the actual transaction IDs

                except Exception as e:
                    logger.error(f"Error getting MNEE data: {e}", exc_info=True)

            # Check if updater still exists before emitting signal
            if not self._history_updater or sip.isdeleted(self._history_updater):
                 logger.warning("History fetch complete, but updater is deleted. Skipping UI update.")
                 return
                 
            # Log summary of what we're sending to the UI
            logger.debug(f"Sending history data with {len(history_data['bsv_history'])} BSV entries and " 
                         f"{len(history_data['mnee_history'])} MNEE entries to UI")
            self._history_updater.update_history(history_data)

        except (asyncio.CancelledError, GeneratorExit):
            logger.warning("History fetch task cancelled.")
        except Exception as e:
            logger.error(f"Error fetching history data: {e}", exc_info=True)
    
    def _update_with_history_data(self, history_data):
        """Update the UI with history data (called in UI thread via signal)"""
        self.clear()
        if self._account is None:
            return
        
        # print(f"DEBUG: _update_with_history_data called with history data {history_data}")
        
        bsv_history = history_data.get('bsv_history', [])
        
        logger.debug(f"Processing {len(bsv_history)} BSV entries")
        
        # Get current item to restore selection
        item = self.currentItem()
        current_tx_hash = item.data(Columns.STATUS, self.TX_ROLE) if item else None
        
        # Track transaction counts by keyinstance_id for archiving logic
        tx_counts_by_key = {}
        
        # Start with existing counts from the account
        if hasattr(self._account, 'get_mnee_tx_count'):
            # Get existing counts as a baseline
            existing_counts = self._account.get_mnee_tx_count()
            if isinstance(existing_counts, dict):
                tx_counts_by_key = existing_counts.copy()
                logger.debug(f"Starting with {len(tx_counts_by_key)} existing MNEE transaction counts from storage")
                
                # Log the first few entries for debugging
                count = 0
                for key_id, tx_count in tx_counts_by_key.items():
                    if count < 5:  # Just show the first 5
                        logger.debug(f"Existing MNEE count for key {key_id}: {tx_count}")
                        count += 1
            else:
                logger.debug(f"get_mnee_tx_count() returned {existing_counts}, not a dictionary")
        else:
            logger.debug("Account doesn't have get_mnee_tx_count method")
        
        # --- Combine histories ---
        combined_history = {}
        for line, balance in bsv_history:
            combined_history[line.tx_hash] = (line, balance)
            
            # Update transaction count for this key
            keyinstance_id = self._account.get_keyinstance_for_txo(line.tx_hash)
            if keyinstance_id is not None:
                tx_counts_by_key[keyinstance_id] = tx_counts_by_key.get(keyinstance_id, 0) + 1
            
        logger.debug(f"Added {len(combined_history)} BSV transactions to combined history")

        last_bsv_balance = 0
        if bsv_history:
            last_bsv_balance = bsv_history[-1][1]
        
        # Update MNEE transaction counts in wallet data
        for keyinstance_id, count in tx_counts_by_key.items():
            if count > 0:
                logger.debug(f"Updating MNEE transaction count for key {keyinstance_id}: {count}")
                # Store the count for this keyinstance_id
                if hasattr(self._account, '_mnee_tx_count_per_key'):
                    self._account._mnee_tx_count_per_key[keyinstance_id] = count
                else:
                    logger.warning("Account doesn't have _mnee_tx_count_per_key attribute. MNEE transaction counting won't work.")
                
        # If this is a MNEE account, persist transaction counts to database
        if hasattr(self._account, '_save_mnee_data_to_db'):
            try:
                self._account._save_mnee_data_to_db()
            except Exception as e:
                logger.warning(f"Error saving MNEE transaction data: {str(e)}")
        else:
            logger.debug("Account doesn't have _save_mnee_data_to_db method. MNEE transaction counts won't be persisted.")
                
        # Check for keys with no UTXOs but with transactions - candidates for archiving
        for keyinstance_id, count in tx_counts_by_key.items():
            if hasattr(self._account, 'get_key_utxos') and count >= 2:
                utxos = self._account.get_key_utxos({keyinstance_id})
                if not utxos:
                    logger.info(f"Key {keyinstance_id} has {count} transactions but no UTXOs - candidate for archiving")
                    # The wallet's standard archiving mechanism should handle this

        # No history to display
        if not combined_history:
            logger.debug("No history entries to display")
            return
            
        combined_list = sorted(combined_history.values(), key=lambda item: item[0].sort_key)
        logger.debug(f"Sorted {len(combined_list)} combined history entries")

        # --- Create TreeWidgetItems ---
        fx = app_state.fx
        if fx:
            fx.history_used_spot = False
        local_height = self._wallet.get_local_height()
        server_height = self._main_window.network.get_server_height() if self._main_window.network else 0
        header_at_height = app_state.headers.header_at_height
        chain = app_state.headers.longest_chain()
        missing_header_heights = []
        items = []

        # Also check transaction counts which might exist separately
        if hasattr(self._account, '_mnee_tx_count_per_key'):
            tx_counts = self._account._mnee_tx_count_per_key
            print(f"DEBUG: Found _mnee_tx_count_per_key with {len(tx_counts)} entries")
            
            # Print the actual transaction counts
            for key_id, count in tx_counts.items():
                print(f"DEBUG: Key {key_id} has {count} MNEE transactions")

        # Iterate through the sorted, combined list
        print(f"DEBUG: Processing {len(combined_list)} history entries for display")
        for line, balance in combined_list:
            tx_id = hash_to_hex_str(line.tx_hash)
            # Fix for None height values in MNEE transactions
            conf = 0
            if line.height is not None:
                conf = 0 if line.height <= 0 else max(local_height - line.height + 1, 0)
                
            timestamp = False
            if line.height is not None and line.height > 0:
                try:
                    timestamp = header_at_height(chain, line.height).timestamp
                except MissingHeader:
                    if line.height <= server_height:
                        missing_header_heights.append(line.height)

            # Amount String Logic
            v_str = "--"
            fiat_amount_str = ""
            
            # Get the key_id this transaction relates to
            key_id = self._account.get_keyinstance_for_txo(line.tx_hash)
            
            # Check if this transaction has MNEE value
            is_mnee_tx = line.mnee_amount is not None 
            has_tx_count = key_id is not None and key_id in tx_counts_by_key and tx_counts_by_key[key_id] > 0
            
            # Status string
            status = get_tx_status(self._account, line.tx_hash, line.height, conf, timestamp)
            status_str = get_tx_desc(status, timestamp)
            if is_mnee_tx or has_tx_count:
                status_str = f"MNEE + BSV ({status_str})"
            
            # Format the amount string
            if is_mnee_tx and line.mnee_amount is not None:
                mnee_amount_str = format_mnee_atomic(line.mnee_amount, 5, "")
                bsv_str = app_state.format_amount(line.value_delta, True, whitespaces=True)
                v_str = f"{bsv_str} + {mnee_amount_str} MNEE"
                fiat_amount_str = "--"
            else:
                v_str = app_state.format_amount(line.value_delta, True, whitespaces=True)
                if fx and fx.show_history():
                    date = timestamp_to_datetime(time.time() if conf <= 0 else timestamp)
                    fiat_amount_str = fx.historical_value_str(line.value_delta, date)

            # Balance string logic
            balance_str = app_state.format_amount(balance, whitespaces=True)
            
            # MNEE balance string
            mnee_balance_str = "--"
            if line.mnee_amount is not None:
                mnee_balance_str = format_mnee_atomic(line.mnee_amount, 5, "MNEE")
            
            # Fiat strings
            fiat_balance_str = ""
            if fx and fx.show_history():
                date = timestamp_to_datetime(time.time() if conf <= 0 else timestamp)
                fiat_balance_str = fx.historical_value_str(balance, date)
            
            # Fetch the label
            label = self._wallet.get_transaction_label(line.tx_hash)
            
            # If this is a transaction with MNEE tokens, enhance the label
            if is_mnee_tx or has_tx_count:
                if not label:
                    label = "MNEE token transaction"
                elif "MNEE" not in label:
                    label = f"MNEE: {label}"
            
            # Create entry list
            entry = [None, tx_id, status_str, label, v_str, balance_str, mnee_balance_str]
            if fx and fx.show_history():
                entry.extend([fiat_amount_str, fiat_balance_str])

            item = SortableTreeWidgetItem(entry)
            item.setIcon(Columns.STATUS, get_tx_icon(status))
            item.setToolTip(Columns.STATUS, get_tx_tooltip(status, conf))

            for i in range(len(entry)):
                if i > Columns.DESCRIPTION:
                    item.setTextAlignment(i, Qt.AlignRight | Qt.AlignVCenter)
                else:
                    item.setTextAlignment(i, Qt.AlignLeft | Qt.AlignVCenter)
                if i != Columns.DATE:
                    item.setFont(i, self.monospace_font)
                    
            # Set text color based on transaction type
            if is_mnee_tx or has_tx_count:
                item.setForeground(Columns.DESCRIPTION, self.mneeBrush)
                item.setForeground(Columns.AMOUNT, self.mneeBrush)
                item.setForeground(Columns.MNEE_BALANCE, self.mneeBrush)
            elif line.value_delta and line.value_delta < 0:
                item.setForeground(Columns.DESCRIPTION, self.withdrawalBrush)
                item.setForeground(Columns.AMOUNT, self.withdrawalBrush)

            # Modify sort data for Amount column
            sort_amount = line.value_delta
            item.setData(Columns.AMOUNT, SortableTreeWidgetItem.DataRole, sort_amount)
            
            # Set other sort data
            item.setData(Columns.STATUS, SortableTreeWidgetItem.DataRole, line.sort_key)
            item.setData(Columns.DATE, SortableTreeWidgetItem.DataRole, line.sort_key)
            item.setData(Columns.BALANCE, SortableTreeWidgetItem.DataRole, balance)
            
            item.setData(Columns.STATUS, self.ACCOUNT_ROLE, self._account_id)
            item.setData(Columns.STATUS, self.TX_ROLE, line.tx_hash)

            if current_tx_hash == line.tx_hash:
                self.setCurrentItem(item)

            items.append(item)

        self.addTopLevelItems(items)

        if len(missing_header_heights) and self._main_window.network:
            # Request missing headers needed for timestamps
            self._main_window.network.backfill_headers_at_heights(missing_header_heights)

    def on_doubleclick(self, item: QTreeWidgetItem, column: int) -> None:
        if self.permit_edit(item, column):
            super(HistoryList, self).on_doubleclick(item, column)
        else:
            account_id = item.data(Columns.STATUS, self.ACCOUNT_ROLE)
            tx_hash = item.data(Columns.STATUS, self.TX_ROLE)

            account = self._wallet.get_account(account_id)
            tx = account.get_transaction(tx_hash)
            if tx is not None:
                self._main_window.show_transaction(account, tx)
            else:
                MessageBox.show_error(_("The full transaction is not yet present in your wallet."+
                    " Please try again when it has been obtained from the network."))

    def update_tx_labels(self) -> None:
        root = self.invisibleRootItem()
        child_count = root.childCount()
        for i in range(child_count):
            item = root.child(i)
            tx_hash = item.data(Columns.STATUS, self.TX_ROLE)
            label = self._wallet.get_transaction_label(tx_hash)
            item.setText(Columns.DESCRIPTION, label)

    # From the wallet 'verified' event.
    def update_tx_item(self, tx_hash: bytes, height: int, conf: int, timestamp: int) -> None:
        # External event may be called before the UI element has an account.
        if self._account is None:
            return

        status = get_tx_status(self._account, tx_hash, height, conf, timestamp)
        tx_id = hash_to_hex_str(tx_hash)
        items = self.findItems(tx_id, self.TX_ROLE | Qt.MatchContains | Qt.MatchRecursive,
            column=Columns.TX_ID)
        if items:
            item = items[0]
            item.setIcon(Columns.STATUS, get_tx_icon(status))
            item.setText(Columns.DATE, get_tx_desc(status, timestamp))
            item.setToolTip(Columns.STATUS, get_tx_tooltip(status, conf))

            # NOTE: This is a damned if you do and damned if you don't situation.
            # - If we update the now verified row then it is not guaranteed to be in final order.
            #   It will have been in order of date added to wallet before the update, and after
            #   the naive display update above will still be in that order. But on the next list
            #   update it will be in block order (height, position). CURRENT PROBLEM
            # - If we update the sorting information without correcting all the balances, then
            #   the balances will be out of order. WORSE PROBLEM.

            # NOTE: Update the balances and then update the sorting keys and it is correct?
            #       Manually updating the balances could be painful as we need to preserve the
            #       value_delta and regenerate the balance, the fiat value (if applicable) and
            #       the fiat balance (if applicable). And we need to do it in the order of sorting.
            #       At this point, the painfulness of this, seems to suggest we would be better off
            #       rewriting this whole thing to be paging based.

            # # Consistent sorting.
            # metadata = self._wallet._transaction_cache.get_metadata(tx_hash)
            # sort_key = height, metadata.position
            # item.setData(Columns.STATUS, SortableTreeWidgetItem.DataRole, sort_key)
            # item.setData(Columns.DATE, SortableTreeWidgetItem.DataRole, sort_key)

    def create_menu(self, position: QPoint) -> None:
        self.selectedIndexes()
        item = self.currentItem()
        if not item:
            return
        column = self.currentColumn()
        account_id = item.data(Columns.STATUS, self.ACCOUNT_ROLE)
        tx_hash = item.data(Columns.STATUS, self.TX_ROLE)
        if account_id is None or tx_hash is None:
            return
        if column == 0:
            column_title = "ID"
            column_data = hash_to_hex_str(tx_hash)
        else:
            column_title = self.headerItem().text(column)
            column_data = item.text(column).strip()

        account = self._wallet.get_account(account_id)

        tx_id = hash_to_hex_str(tx_hash)
        tx_URL = web.BE_URL(self.config, 'tx', tx_id)
        height, _conf, _timestamp = self._wallet.get_tx_height(tx_hash)
        tx = account.get_transaction(tx_hash)
        if not tx: return # this happens sometimes on account synch when first starting up.
        is_unconfirmed = height <= 0

        menu = QMenu()
        menu.addAction(_("Copy {}").format(column_title),
            lambda: self._main_window.app.clipboard().setText(column_data))
        if column in self.editable_columns:
            # We grab a fresh reference to the current item, as it has been deleted in a
            # reported issue.
            menu.addAction(_("Edit {}").format(column_title),
                lambda: self.currentItem() and self.editItem(self.currentItem(), column))
        menu.addAction(_("Details"), lambda: self._main_window.show_transaction(account, tx))
        if is_unconfirmed and tx:
            child_tx = account.cpfp(tx, 0)
            if child_tx:
                menu.addAction(_("Child pays for parent"),
                    lambda: self._main_window.cpfp(account, tx, child_tx))
        entry = self._account.get_transaction_entry(tx_hash)
        if entry.flags & TxFlags.PaysInvoice:
            invoice_row = self._account.invoices.get_invoice_for_tx_hash(tx_hash)
            invoice_id = invoice_row.invoice_id if invoice_row is not None else None
            action = menu.addAction(read_QIcon(ICON_NAME_INVOICE_PAYMENT), _("View invoice"),
                    partial(self._show_invoice_window, invoice_id))
            action.setEnabled(invoice_id is not None)

        if tx_URL:
            menu.addAction(_("View on block explorer"), lambda: webbrowser.open(tx_URL))
        menu.exec_(self.viewport().mapToGlobal(position))

    def _show_invoice_window(self, invoice_id: int) -> None:
        row = self._account.invoices.get_invoice_for_id(invoice_id)
        if row is None:
            self._main_window.show_error(_("The invoice for the transaction has been deleted."))
            return
        self._main_window.show_invoice(self._account, row)


def get_tx_status(account: AbstractAccount, tx_hash: bytes, height: int, conf: int,
        timestamp: Union[bool, int]) -> TxStatus:
    if not account.have_transaction_data(tx_hash):
        return TxStatus.MISSING

    metadata = account.get_transaction_metadata(tx_hash)
    if metadata.position == 0:
        if height + COINBASE_MATURITY > account._wallet.get_local_height():
            return TxStatus.UNMATURED
    elif conf == 0:
        if height > 0:
            return TxStatus.UNVERIFIED
        return TxStatus.UNCONFIRMED

    return TxStatus.FINAL

def get_tx_desc(status: TxStatus, timestamp: Union[bool, int]) -> str:
    if status in [ TxStatus.UNCONFIRMED, TxStatus.MISSING ]:
        return TX_STATUS[status]
    return format_time(timestamp, _("unknown")) if timestamp else _("unknown")

def get_tx_tooltip(status: TxStatus, conf: int) -> str:
    text = str(conf) + " confirmation" + ("s" if conf != 1 else "")
    if status == TxStatus.UNMATURED:
        text = text + "\n" + _("Unmatured")
    elif status in TX_STATUS:
        text = text + "\n"+ TX_STATUS[status]
    return text

def get_tx_icon(status: TxStatus) -> QIcon:
    return read_QIcon(TX_ICONS[status])


class HistoryView(QWidget):
    def __init__(self, parent: QWidget, main_window: 'ElectrumWindow') -> None:
        super().__init__(parent)

        self._main_window = weakref.proxy(main_window)

        self._account_id: Optional[int] = None
        self._account: AbstractAccount = None

        # The history view is created before the transactions view, so this will be updated by
        # an event from the transactions view. If this ordering changes you may see that it is
        # not updated.
        self._local_count = 0
        self._local_value = 0
        label = self._local_summary_label = QLabel()
        label.setAlignment(Qt.AlignCenter)
        label.setToolTip(_("The account balance shown in the status bar does "
            "not include any coins allocated and used by transactions in the Transactions tab."
            "<br/><br/>"
            "This summary indicates the current balance of the Transactions tab."))
        label.setContentsMargins(5, 5, 5, 5)
        label.setVisible(False)

        self.list = HistoryList(parent, main_window)
        self._top_button_layout = TableTopButtonLayout()
        self._top_button_layout.refresh_signal.connect(self._main_window.refresh_wallet_display)
        self._top_button_layout.filter_signal.connect(self.filter_tx_list)
        self._top_button_layout.add_button("icons8-export-32-windows.png",
            self._main_window.export_history_dialog, _("Export history as.."))

        vbox = QVBoxLayout()
        vbox.setSpacing(0)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.addWidget(label)
        vbox.addLayout(self._top_button_layout)
        vbox.addWidget(self.list, 1)
        self.setLayout(vbox)

        self._update_transactions_tab_summary()

        main_window.account_change_signal.connect(self._on_account_changed)

    def _on_account_changed(self, new_account_id: int, new_account: AbstractAccount) -> None:
        self._account_id = new_account_id
        self._account = new_account

        self._update_transactions_tab_summary()

    def on_transaction_view_changed(self, account_id: int) -> None:
        if self._account_id == account_id:
            self._update_transactions_tab_summary()

    def _update_transactions_tab_summary(self) -> None:
        local_count = 0
        local_value = 0

        if self._account_id is not None:
            wallet = self._account.get_wallet()
            with wallet.get_transaction_delta_table() as table:
                _account_id, local_value, local_count = table.read_balance(self._account_id,
                    mask=TxFlags.STATE_UNCLEARED_MASK)

        if local_count == 0:
            self._local_summary_label.setVisible(False)
            return

        value_text = app_state.format_amount(local_value) +" "+ app_state.base_unit()
        if local_count == 1:
            text = _("The Transactions tab has <b>1</b> transaction containing <b>{balance}</b> "
                "in allocated coins.").format(balance=value_text)
        else:
            text = _("The Transactions tab has <b>{count}</b> transactions containing "
                "<b>{balance}</b> in allocated coins.").format(count=local_count,
                balance=value_text)
        self._local_summary_label.setText(text)
        self._local_summary_label.setVisible(True)

    def update_tx_labels(self) -> None:
        self.list.update_tx_labels()

    def update_tx_headers(self) -> None:
        self.list.update_tx_headers()

    # From the wallet 'verified' event.
    def update_tx_item(self, tx_hash: bytes, height: int, conf: int, timestamp: int) -> None:
        self.list.update_tx_item(tx_hash, height, conf, timestamp)

    def update_tx_list(self) -> None:
        self.list.update()

    # Called externally via the Find menu option.
    def on_search_toggled(self) -> None:
        self._top_button_layout.on_toggle_filter()

    def filter_tx_list(self, text: str) -> None:
        self.list.filter(text)
