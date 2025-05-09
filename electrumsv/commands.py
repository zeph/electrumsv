#!/usr/bin/env python
#
# Electrum - lightweight Bitcoin client
# Copyright (C) 2011 thomasv@gitorious
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

import argparse
from decimal import Decimal
from functools import wraps
import json
import os
import sys
from typing import Dict, Optional

from bitcoinx import hash_to_hex_str

from .bitcoin import COIN
from .i18n import _
from .logs import logs
from .app_state import app_state
from .constants import KeyInstanceFlag, CHANGE_SUBPATH, AccountType
from .mnee import MneeAccount

logger = logs.get_logger("commands")

known_commands: Dict[str, 'Command'] = {}


def satoshis(amount):
    # satoshi conversion must not be performed by the parser
    return int(COIN*Decimal(amount)) if amount not in ['!', None] else amount


class Command:
    def __init__(self, func, s: str) -> None:
        self.name = func.__name__
        self.description = func.__doc__
        self.help = self.description.split('.')[0] if self.description else None

        self.requires_network = 'n' in s
        self.requires_wallet = 'w' in s
        self.requires_password = 'p' in s

        varnames = func.__code__.co_varnames[1:func.__code__.co_argcount]
        self.defaults = func.__defaults__
        if self.defaults:
            n = len(self.defaults)
            self.params = list(varnames[:-n])
            self.options = list(varnames[-n:])
        else:
            self.params = list(varnames)
            self.options = []
            self.defaults = []

    def __repr__(self):
        return "<Command {}>".format(self)

    def __str__(self):
        return "{}({})".format(self.name, ", ".join(self.params +
            [ "{}={!r}".format(name, self.defaults[i]) for i, name in enumerate(self.options) ]))


def command(s: str):
    def decorator(func):
        global known_commands
        name = func.__name__
        known_commands[name] = Command(func, s)

        @wraps(func)
        def func_wrapper(*args, **kwargs):
            c = known_commands[func.__name__]
            wallet = args[0]._wallet
            network = args[0]._network
            password = kwargs.get('password')
            if c.requires_network and network is None:
                raise Exception("Daemon offline")  # Same wording as in daemon.py.
            if c.requires_wallet and wallet is None:
                raise Exception("Wallet not loaded. Use 'electrum-sv daemon load_wallet'")
            if (c.requires_password and password is None and not kwargs.get("unsigned")):
                return {'error': 'Password required' }
            return func(*args, **kwargs)
        return func_wrapper
    return decorator


class Commands:
    def __init__(self, config, wallet, network, callback = None):
        self.config = config
        self._wallet = wallet
        self._network = network
        self._callback = callback

    def _run(self, method_name: str, *args, password_getter=None, **kwargs):
        # this wrapper is called from the python console
        cmd = known_commands[method_name]
        if cmd.requires_password:
            password = password_getter()
            if password is None:
                return
        else:
            password = None

        f = getattr(self, method_name)
        if cmd.requires_password:
            kwargs.update(password=password)
        result = f(*args, **kwargs)

        if self._callback:
            self._callback()
        return result

    @command('')
    def commands(self) -> str:
        """List of commands"""
        return ' '.join(sorted(k for k in known_commands.keys()))

    @command('')
    def version(self) -> str:
        """Return the version of electrum-sv."""
        from .version import PACKAGE_VERSION
        return PACKAGE_VERSION

    @command('')
    def help(self):
        # for the python console
        return sorted(known_commands.keys())

    @command('')
    def create_wallet(self):
        """Create a new wallet"""
        raise Exception('Not a JSON-RPC command')

    @command('')
    def create_account(self):
        """Create a new account"""
        raise Exception('Not a JSON-RPC command')

    @command('')
    def listaddresses(self, show_all=False, account_id=None, id=None, address=None):
        """List wallet addresses. Shows active addresses by default."""
        # Normalize the address parameter if provided
        if address is not None:
            address = str(address).strip()
            if not address:  # If it's empty after stripping
                address = None
        
        # Check if running via daemon with a loaded wallet
        daemon = app_state.daemon
        if daemon and daemon.wallets:
            if len(daemon.wallets) > 1:
                # Multiple wallets loaded - need user to be more specific
                wallet_paths = list(daemon.wallets.keys())
                return {
                    'error': f'Multiple wallets loaded. Please specify which wallet to use with -w. Available wallets: {wallet_paths}'
                }
            
            # Get the single loaded wallet
            wallet_path = next(iter(daemon.wallets.keys()))
            wallet = daemon.wallets[wallet_path]
            
            # Check if account_id is specified
            if account_id is not None:
                # Find the account by ID
                try:
                    account_id = int(account_id)
                    account = None
                    for acc in wallet.get_accounts():
                        if acc.get_id() == account_id:
                            account = acc
                            break
                    
                    if account is None:
                        available_ids = [acc.get_id() for acc in wallet.get_accounts()]
                        return {
                            'error': f'Account ID {account_id} not found. Available account IDs: {available_ids}'
                        }
                except ValueError:
                    return {
                        'error': f'Invalid account ID: {account_id}. Account ID must be an integer.'
                    }
            else:
                # No account_id specified, check if there's only one account
                accounts = list(wallet.get_accounts())
                if len(accounts) > 1:
                    # Multiple accounts loaded - need user to be more specific
                    account_ids = [account.get_id() for account in accounts]
                    return {
                        'error': f'Multiple accounts found in wallet. Please use "--account_id ID" to specify which account to use. Available account IDs: {account_ids}'
                    }
                
                # Get the single account
                account = accounts[0]
            
            account_id = account.get_id()
            
            # If keyinstance_id is specified, just return that specific address
            if id is not None:
                try:
                    keyinstance_id = int(id)
                    # Check if the keyinstance_id exists
                    if keyinstance_id not in account.get_keyinstance_ids():
                        return {
                            'error': f'Key instance ID {keyinstance_id} not found in account {account_id}. Available key instance IDs: {list(account.get_keyinstance_ids())}'
                        }
                    
                    # Get the single keyinstance
                    keyinstance = account.get_keyinstance(keyinstance_id)
                    
                    # Check if we should include inactive keys
                    if not show_all and keyinstance.flags & KeyInstanceFlag.IS_ACTIVE != KeyInstanceFlag.IS_ACTIVE:
                        return {
                            'error': f'Key instance ID {keyinstance_id} is not active. Use --show_all to include inactive keys.'
                        }
                    
                    # Get script and script template
                    script_template = account.get_script_template_for_id(keyinstance_id)
                    
                    # Try different ways to get the address string
                    try:
                        if hasattr(script_template, 'to_string'):
                            address_str = script_template.to_string()
                        elif hasattr(script_template, '__str__'):
                            address_str = str(script_template)
                        else:
                            script = account.get_script_for_id(keyinstance_id)
                            address_str = str(script)
                    except Exception as e:
                        address_str = f"<unable to convert to address: {e}>"
                        
                    script_type = keyinstance.script_type.name
                    
                    # Include MNEE data if available
                    mnee_data = {}
                    if hasattr(account, 'get_mnee_balance_for_keyid'):
                        try:
                            mnee_balance = account.get_mnee_balance_for_keyid(keyinstance_id)
                            mnee_data['mnee_balance'] = mnee_balance
                        except:
                            pass
                        
                    address_data = {
                        'address': address_str,
                        'keyinstance_id': keyinstance_id,
                        'is_change': account.get_derivation_path(keyinstance_id) is not None and \
                                      len(account.get_derivation_path(keyinstance_id)) > 0 and \
                                      account.get_derivation_path(keyinstance_id)[0] == CHANGE_SUBPATH[0],
                        'script_type': script_type
                    }
                    
                    # Add MNEE data if available
                    if mnee_data:
                        address_data.update(mnee_data)
                    
                    return {f'account_{account_id}': [address_data]}
                    
                except ValueError:
                    return {
                        'error': f'Invalid key instance ID: {id}. Key instance ID must be an integer.'
                    }
            
            # Get all addresses for this account
            addresses = []
            for keyinstance_id in account.get_keyinstance_ids():
                keyinstance = account.get_keyinstance(keyinstance_id)
                if not show_all and keyinstance.flags & KeyInstanceFlag.IS_ACTIVE != KeyInstanceFlag.IS_ACTIVE:
                    continue
                    
                # Get script and script template
                script_template = account.get_script_template_for_id(keyinstance_id)
                
                # Try different ways to get the address string
                try:
                    if hasattr(script_template, 'to_string'):
                        address_str = script_template.to_string()
                    elif hasattr(script_template, '__str__'):
                        address_str = str(script_template)
                    else:
                        script = account.get_script_for_id(keyinstance_id)
                        address_str = str(script)
                except Exception as e:
                    address_str = f"<unable to convert to address: {e}>"
                    
                # Skip if filtering by address and this doesn't match
                if address is not None:
                    # Ensure address is treated as string for comparison
                    if str(address) != str(address_str):
                        continue
                    
                script_type = keyinstance.script_type.name
                
                # Include MNEE data if available
                mnee_data = {}
                if hasattr(account, 'get_mnee_balance_for_keyid'):
                    try:
                        mnee_balance = account.get_mnee_balance_for_keyid(keyinstance_id)
                        mnee_data['mnee_balance'] = mnee_balance
                    except:
                        pass
                    
                address_data = {
                    'address': address_str,
                    'keyinstance_id': keyinstance_id,
                    'is_change': account.get_derivation_path(keyinstance_id) is not None and \
                                  len(account.get_derivation_path(keyinstance_id)) > 0 and \
                                  account.get_derivation_path(keyinstance_id)[0] == CHANGE_SUBPATH[0],
                    'script_type': script_type
                }
                
                # Add MNEE data if available
                if mnee_data:
                    address_data.update(mnee_data)
                    
                addresses.append(address_data)
                
            # If filtering by address and none found, return an error
            if address is not None and not addresses:
                return {
                    'error': f'Address {address} not found in account {account_id}.'
                }
                
            return {f'account_{account_id}': addresses}
            
        # If no daemon or no wallets loaded, or using via file path
        elif self._wallet is not None:
            wallet = self._wallet
            
            # Check if account_id is specified
            if account_id is not None:
                # Find the account by ID
                try:
                    account_id = int(account_id)
                    account = None
                    for acc in wallet.get_accounts():
                        if acc.get_id() == account_id:
                            account = acc
                            break
                    
                    if account is None:
                        available_ids = [acc.get_id() for acc in wallet.get_accounts()]
                        return {
                            'error': f'Account ID {account_id} not found. Available account IDs: {available_ids}'
                        }
                except ValueError:
                    return {
                        'error': f'Invalid account ID: {account_id}. Account ID must be an integer.'
                    }
            else:
                # No account_id specified, check if there's only one account
                accounts = list(wallet.get_accounts())
                if len(accounts) > 1:
                    # Multiple accounts loaded - need user to be more specific
                    account_ids = [account.get_id() for account in accounts]
                    return {
                        'error': f'Multiple accounts found in wallet. Please use "--account_id ID" to specify which account to use. Available account IDs: {account_ids}'
                    }
                
                # Get the single account
                account = accounts[0]
            
            account_id = account.get_id()
            
            # If keyinstance_id is specified, just return that specific address
            if id is not None:
                try:
                    keyinstance_id = int(id)
                    # Check if the keyinstance_id exists
                    if keyinstance_id not in account.get_keyinstance_ids():
                        return {
                            'error': f'Key instance ID {keyinstance_id} not found in account {account_id}. Available key instance IDs: {list(account.get_keyinstance_ids())}'
                        }
                    
                    # Get the single keyinstance
                    keyinstance = account.get_keyinstance(keyinstance_id)
                    
                    # Check if we should include inactive keys
                    if not show_all and keyinstance.flags & KeyInstanceFlag.IS_ACTIVE != KeyInstanceFlag.IS_ACTIVE:
                        return {
                            'error': f'Key instance ID {keyinstance_id} is not active. Use --show_all to include inactive keys.'
                        }
                    
                    # Get script and script template
                    script_template = account.get_script_template_for_id(keyinstance_id)
                    
                    # Try different ways to get the address string
                    try:
                        if hasattr(script_template, 'to_string'):
                            address_str = script_template.to_string()
                        elif hasattr(script_template, '__str__'):
                            address_str = str(script_template)
                        else:
                            script = account.get_script_for_id(keyinstance_id)
                            address_str = str(script)
                    except Exception as e:
                        address_str = f"<unable to convert to address: {e}>"
                        
                    script_type = keyinstance.script_type.name
                    
                    # Include MNEE data if available
                    mnee_data = {}
                    if hasattr(account, 'get_mnee_balance_for_keyid'):
                        try:
                            mnee_balance = account.get_mnee_balance_for_keyid(keyinstance_id)
                            mnee_data['mnee_balance'] = mnee_balance
                        except:
                            pass
                        
                    address_data = {
                        'address': address_str,
                        'keyinstance_id': keyinstance_id,
                        'is_change': account.get_derivation_path(keyinstance_id) is not None and \
                                      len(account.get_derivation_path(keyinstance_id)) > 0 and \
                                      account.get_derivation_path(keyinstance_id)[0] == CHANGE_SUBPATH[0],
                        'script_type': script_type
                    }
                    
                    # Add MNEE data if available
                    if mnee_data:
                        address_data.update(mnee_data)
                    
                    return {f'account_{account_id}': [address_data]}
                    
                except ValueError:
                    return {
                        'error': f'Invalid key instance ID: {id}. Key instance ID must be an integer.'
                    }
            
            # Get all addresses for this account
            addresses = []
            for keyinstance_id in account.get_keyinstance_ids():
                keyinstance = account.get_keyinstance(keyinstance_id)
                if not show_all and keyinstance.flags & KeyInstanceFlag.IS_ACTIVE != KeyInstanceFlag.IS_ACTIVE:
                    continue
                    
                # Get script and script template
                script_template = account.get_script_template_for_id(keyinstance_id)
                
                # Try different ways to get the address string
                try:
                    if hasattr(script_template, 'to_string'):
                        address_str = script_template.to_string()
                    elif hasattr(script_template, '__str__'):
                        address_str = str(script_template)
                    else:
                        script = account.get_script_for_id(keyinstance_id)
                        address_str = str(script)
                except Exception as e:
                    address_str = f"<unable to convert to address: {e}>"
                    
                # Skip if filtering by address and this doesn't match
                if address is not None:
                    # Ensure address is treated as string for comparison
                    if str(address) != str(address_str):
                        continue
                    
                script_type = keyinstance.script_type.name
                
                # Include MNEE data if available
                mnee_data = {}
                if hasattr(account, 'get_mnee_balance_for_keyid'):
                    try:
                        mnee_balance = account.get_mnee_balance_for_keyid(keyinstance_id)
                        mnee_data['mnee_balance'] = mnee_balance
                    except:
                        pass
                    
                address_data = {
                    'address': address_str,
                    'keyinstance_id': keyinstance_id,
                    'is_change': account.get_derivation_path(keyinstance_id) is not None and \
                                  len(account.get_derivation_path(keyinstance_id)) > 0 and \
                                  account.get_derivation_path(keyinstance_id)[0] == CHANGE_SUBPATH[0],
                    'script_type': script_type
                }
                
                # Add MNEE data if available
                if mnee_data:
                    address_data.update(mnee_data)
                    
                addresses.append(address_data)
                
            # If filtering by address and none found, return an error
            if address is not None and not addresses:
                return {
                    'error': f'Address {address} not found in account {account_id}.'
                }
                
            return {f'account_{account_id}': addresses}
            
        else:
            # No wallet loaded in daemon, and no wallet provided
            return {'error': 'No wallet is currently loaded. Please run "electrum-sv daemon load_wallet -w YOUR_WALLET_PATH" first.'}

    @command('')
    def la(self, show_all=False, account_id=None, id=None, address=None):
        """List all wallet addresses (alias for listaddresses)"""
        return self.listaddresses(show_all, account_id, id, address)

    @command('')
    def addresstxs(self, id=None, address=None, account_id=None):
        """List transactions for a specific address"""
        # Normalize the address parameter if provided
        if address is not None:
            address = str(address).strip()
            if not address:  # If it's empty after stripping
                address = None
                
        # Check if running via daemon with a loaded wallet
        daemon = app_state.daemon
        if daemon and daemon.wallets:
            if len(daemon.wallets) > 1:
                # Multiple wallets loaded - need user to be more specific
                wallet_paths = list(daemon.wallets.keys())
                return {
                    'error': f'Multiple wallets loaded. Please specify which wallet to use with -w. Available wallets: {wallet_paths}'
                }
            
            # Get the single loaded wallet
            wallet_path = next(iter(daemon.wallets.keys()))
            wallet = daemon.wallets[wallet_path]
            
            # Handle account selection
            if account_id is not None:
                try:
                    account_id = int(account_id)
                    account = None
                    for acc in wallet.get_accounts():
                        if acc.get_id() == account_id:
                            account = acc
                            break
                    
                    if account is None:
                        available_ids = [acc.get_id() for acc in wallet.get_accounts()]
                        return {
                            'error': f'Account ID {account_id} not found. Available account IDs: {available_ids}'
                        }
                except ValueError:
                    return {
                        'error': f'Invalid account ID: {account_id}. Account ID must be an integer.'
                    }
            else:
                # No account_id specified, check if there's only one account
                accounts = list(wallet.get_accounts())
                if len(accounts) > 1:
                    # Multiple accounts loaded - need user to be more specific
                    account_ids = [account.get_id() for account in accounts]
                    return {
                        'error': f'Multiple accounts found in wallet. Please use "--account_id ID" to specify which account to use. Available account IDs: {account_ids}'
                    }
                
                # Get the single account
                account = accounts[0]
            
            account_id = account.get_id()
            
            # Determine the keyinstance_id
            keyinstance_id = None
            
            # If key ID is provided, use that directly
            if id is not None:
                try:
                    keyinstance_id = int(id)
                    # Check if the keyinstance_id exists
                    if keyinstance_id not in account.get_keyinstance_ids():
                        return {
                            'error': f'Key instance ID {keyinstance_id} not found in account {account_id}.'
                        }
                except ValueError:
                    return {
                        'error': f'Invalid key instance ID: {id}. Key instance ID must be an integer.'
                    }
            # If address is provided, find the corresponding key ID
            elif address is not None:
                found_key_id = None
                for key_id in account.get_keyinstance_ids():
                    script_template = account.get_script_template_for_id(key_id)
                    addr_str = str(script_template)
                    if addr_str == address:
                        found_key_id = key_id
                        break
                if found_key_id is None:
                    return {
                        'error': f'Address {address} not found in account {account_id}.'
                    }
                keyinstance_id = found_key_id
            else:
                return {
                    'error': 'You must specify either --id or --address'
                }
            
            # Now we have the keyinstance_id, we can get transactions for it
            try:
                # Create a domain containing just this key ID - this follows the same
                # pattern used in the UI's KeyDialog class
                domain = [keyinstance_id]
                
                # Get history for this domain (key ID)
                history_lines = account.get_history(domain)
                
                # Format the results
                address_history = []
                for history_line, balance in history_lines:
                    tx_hash_hex = hash_to_hex_str(history_line.tx_hash)
                    
                    # Get a timestamp if available
                    timestamp = None
                    # Try to get transaction to extract timestamp
                    tx = account.get_transaction(history_line.tx_hash)
                    if tx and hasattr(tx.context, 'timestamp'):
                        timestamp = tx.context.timestamp
                    # If height is available, try to get timestamp from headers
                    elif history_line.height is not None and history_line.height > 0:
                        try:
                            header = app_state.headers.header_at_height(app_state.headers.longest_chain(), 
                                                                       history_line.height)
                            if header:
                                timestamp = header.timestamp
                        except Exception:
                            pass
                    
                    # Add to our history list
                    tx_item = {
                        'txid': tx_hash_hex,
                        'height': history_line.height,
                        'value': str(history_line.value_delta) if history_line.value_delta is not None else '0',
                        'balance': str(balance) if balance is not None else '0',
                        'timestamp': timestamp,
                        'label': wallet.get_transaction_label(history_line.tx_hash) if hasattr(wallet, 'get_transaction_label') else None
                    }
                    
                    # Add MNEE amount if available from history_line
                    if hasattr(history_line, 'mnee_amount') and history_line.mnee_amount is not None:
                        tx_item['mnee_amount'] = str(history_line.mnee_amount)
                    
                    address_history.append(tx_item)
                
                script_template = account.get_script_template_for_id(keyinstance_id)
                address_str = str(script_template)
                
                # Prepare the response
                result = {
                    'address': address_str,
                    'key_id': keyinstance_id,
                    'transactions': address_history
                }
                
                return result
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                return {
                    'error': f'Error getting transaction history: {str(e)}',
                    'traceback': tb
                }
        
        # If no daemon or no wallets loaded, or using via file path
        elif self._wallet is not None:
            wallet = self._wallet
            
            # Handle account selection
            if account_id is not None:
                try:
                    account_id = int(account_id)
                    account = None
                    for acc in wallet.get_accounts():
                        if acc.get_id() == account_id:
                            account = acc
                            break
                    
                    if account is None:
                        available_ids = [acc.get_id() for acc in wallet.get_accounts()]
                        return {
                            'error': f'Account ID {account_id} not found. Available account IDs: {available_ids}'
                        }
                except ValueError:
                    return {
                        'error': f'Invalid account ID: {account_id}. Account ID must be an integer.'
                    }
            else:
                # No account_id specified, check if there's only one account
                accounts = list(wallet.get_accounts())
                if len(accounts) > 1:
                    # Multiple accounts loaded - need user to be more specific
                    account_ids = [account.get_id() for account in accounts]
                    return {
                        'error': f'Multiple accounts found in wallet. Please use "--account_id ID" to specify which account to use. Available account IDs: {account_ids}'
                    }
                
                # Get the single account
                account = accounts[0]
            
            account_id = account.get_id()
            
            # Same implementation as above
            return {
                'error': 'Direct wallet access not implemented. Please use daemon mode.'
            }
        
        else:
            # No wallet loaded in daemon, and no wallet provided
            return {'error': 'No wallet is currently loaded. Please run "electrum-sv daemon load_wallet -w YOUR_WALLET_PATH" first.'}

    @command('')
    def atx(self, id=None, address=None, account_id=None):
        """List transactions for a specific address (alias for addresstxs)"""
        return self.addresstxs(id, address, account_id)

    @command('')
    def synchronize(self, account_id=None) -> Dict:
        """Force synchronization of the wallet with blockchain.
        
        Args:
            account_id: Optional account ID to limit synchronization to a specific account
        
        Returns a dict with operation results.
        """
        logger.debug("commands.py synchronize() method called")
        # Check if running via daemon with a loaded wallet
        daemon = app_state.daemon
        if not self._wallet and daemon and daemon.wallets:
            if len(daemon.wallets) > 1:
                # Multiple wallets loaded - need user to be more specific
                wallet_paths = list(daemon.wallets.keys())
                return {
                    'error': f'Multiple wallets loaded. Please specify which wallet to use with -w. Available wallets: {wallet_paths}'
                }
                
            # Get the single loaded wallet
            wallet_path = next(iter(daemon.wallets.keys()))
            self._wallet = daemon.wallets[wallet_path]

        # Still no wallet after trying to get from daemon
        if not self._wallet:
            return {'error': 'No wallet is currently loaded. Please run "electrum-sv daemon load_wallet -w YOUR_WALLET_PATH" first.'}
            
        result = {
            'wallet_path': self._wallet.get_storage_path() if hasattr(self._wallet, 'get_storage_path') else 'unknown',
            'address_count': 0,
            'accounts': [],
            'mnee_api_calls': 0,
            'mnee_transactions_found': 0,
            'errors': [],
        }
        
        # Skip wallet-level synchronization since it doesn't exist
        # Just get the accounts directly
        accounts = list(self._wallet.get_accounts())

        # Initial synchronization phase
        account_results = []
        for account in accounts:
            account_result = {}
            try:
                # If that fails, try without parameters
                account.synchronize()
                
                # Debug information collection
                visible_keys = []
                # Use public methods instead of direct attribute access
                for key_id in account.get_keyinstance_ids():
                    key = account.get_keyinstance(key_id)
                    if key.flags & KeyInstanceFlag.IS_ACTIVE:
                        visible_keys.append(key_id)

                account_result['account_id'] = account.get_id()
                account_result['visible_keys'] = len(visible_keys)
                
                # Check if this is an MNEE account and get the report
                if isinstance(account, MneeAccount) and hasattr(account, '_last_synchronization_report'):
                    mnee_report = account._last_synchronization_report
                    account_result['mnee_report'] = mnee_report
                    
                    # Add key MNEE stats to the main result
                    if 'mnee_api_calls' in mnee_report:
                        result['mnee_api_calls'] += mnee_report['mnee_api_calls']
                    if 'token_transactions_found' in mnee_report:
                        result['mnee_transactions_found'] += mnee_report['token_transactions_found']
                    if 'errors' in mnee_report:
                        result['errors'].extend(mnee_report['errors'])
                    
                    # Add note about report access
                    account_result['mnee_reports_available'] = True
                
                account_results.append(account_result)
            except Exception as e:
                error_msg = f"Error synchronizing account {account.get_id()}: {str(e)}"
                result['errors'].append(error_msg)
                
        result['accounts'] = account_results
        
        # Add command info to access detailed reports
        result['reports_info'] = {
            'access_commands': {
                'latest_report': 'mneesync',
                'historical_reports': 'mneehistory'
            }
        }

        return result

    @command('')
    def sync(self, account_id=None):
        """Synchronize wallet/account with blockchain and MNEE data (alias for synchronize)"""
        logger.debug("commands.py sync() alias method called")
        return self.synchronize(account_id)

    def mneesync(self, account_id: Optional[int] = None, full_report: bool = False) -> Dict:
        """
        Synchronize MNEE token data for an account and get the resulting report.
        
        Args:
            account_id: Optional account ID to sync. Default is the first MNEE account.
            full_report: If True, return the full detailed report instead of the compact one.
            
        Returns:
            Dictionary with synchronization results.
        """
        from .mnee import MneeAccount
        
        wallet = self._wallet
        if account_id is None:
            # Find the first MNEE account
            mnee_accounts = []
            for account in wallet.get_accounts():
                if isinstance(account, MneeAccount):
                    mnee_accounts.append(account)
            
            if not mnee_accounts:
                return {"error": "No MNEE accounts found in wallet"}
            
            account = mnee_accounts[0]
            account_id = account.get_id()
        else:
            # Get the specified account
            account = wallet.get_account(account_id)
            if not isinstance(account, MneeAccount):
                return {"error": f"Account {account_id} is not an MNEE account"}
        
        # Perform synchronization
        account.synchronize()
        
        # Get the appropriate report based on the full_report flag
        if full_report and hasattr(account, '_last_full_synchronization_report'):
            report = account._last_full_synchronization_report
            return {
                "account_id": account_id,
                "sync_report": report,
                "note": "This is the full detailed report. Use without --full_report for a more concise view."
            }
        elif hasattr(account, '_last_synchronization_report'):
            report = account._last_synchronization_report
            return {
                "account_id": account_id,
                "sync_report": report
            }
        else:
            return {
                "account_id": account_id,
                "sync_report": {"error": "No synchronization report available"}
            }
    
    def mneehistory(self, account_id: Optional[int] = None, limit: int = 5, full_reports: bool = False) -> Dict:
        """
        Get MNEE token synchronization history.
        
        Args:
            account_id: Optional account ID. Default is the first MNEE account.
            limit: Maximum number of reports to retrieve. Default is 5.
            full_reports: If True, return full detailed reports instead of compact ones.
            
        Returns:
            Dictionary with synchronization history.
        """
        from .mnee import MneeAccount
        
        wallet = self._wallet
        if account_id is None:
            # Find the first MNEE account
            mnee_accounts = []
            for account in wallet.get_accounts():
                if isinstance(account, MneeAccount):
                    mnee_accounts.append(account)
            
            if not mnee_accounts:
                return {"error": "No MNEE accounts found in wallet"}
            
            account = mnee_accounts[0]
            account_id = account.get_id()
        else:
            # Get the specified account
            account = wallet.get_account(account_id)
            if not isinstance(account, MneeAccount):
                return {"error": f"Account {account_id} is not an MNEE account"}
        
        # Get sync reports - we'll modify the get_sync_reports method to handle the full_reports flag
        if hasattr(account, 'get_sync_reports'):
            # Pass the report type (event_type) based on whether we want full or compact reports
            event_type = 11 if full_reports else 10  # 11 for full reports, 10 for compact
            reports = account.get_sync_reports(limit, event_type=event_type)
            
            result = {
                "account_id": account_id,
                "sync_reports": reports,
                "count": len(reports)
            }
            
            # Add a note about report type
            if full_reports:
                result["note"] = "Showing full detailed reports. Use without --full_reports for more concise views."
            
            return result
        else:
            return {
                "account_id": account_id,
                "error": "Account does not support synchronization reports"
            }


param_descriptions = {
    'privkey': 'Private key. Type \'?\' to get a prompt.',
    'destination': 'Bitcoin SV address, contact or alias',
    'address': 'Bitcoin SV address',
    'seed': 'Seed phrase',
    'txid': 'Transaction ID',
    'pos': 'Position',
    'height': 'Block height',
    'tx': 'Serialized transaction (hexadecimal)',
    'key': 'Variable name',
    'pubkey': 'Public key',
    'message': 'Clear text message. Use quotes if it contains spaces.',
    'encrypted': 'Encrypted message',
    'amount': 'Amount to be sent (in BSV). Type \'!\' to send the maximum available.',
    'requested_amount': 'Requested amount (in BSV).',
    'outputs': 'list of ["address", amount]',
    'redeem_script': 'redeem script (hexadecimal)',
}

command_options = {
    'password':    ("-W", "Password"),
    'new_password':(None, "New Password"),
    'receiving':   (None, "Show only receiving addresses"),
    'change':      (None, "Show only change addresses"),
    'frozen':      (None, "Show only frozen addresses"),
    'unused':      (None, "Show only unused addresses"),
    'funded':      (None, "Show only funded addresses"),
    'balance':     ("-b", "Show the balances of listed addresses"),
    'labels':      ("-l", "Show the labels of listed addresses"),
    'nocheck':     (None, "Do not verify aliases"),
    'imax':        (None, "Maximum number of inputs"),
    'fee':         ("-f", "Transaction fee (in BSV)"),
    'from_addr':   ("-F", "Source address (must be a wallet address)"),
    'change_addr': ("-c", "Change address. Default is a spare address, or the source "
                    "address if it's not in the wallet"),
    'nbits':       (None, "Number of bits of entropy"),
    'language':    ("-L", "Default language for wordlist"),
    'privkey':     (None, "Private key. Set to '?' to get a prompt."),
    'unsigned':    ("-u", "Do not sign transaction"),
    'locktime':    (None, "Set locktime block number"),
    'domain':      ("-D", "List of addresses"),
    'memo':        ("-m", "Description of the request"),
    'expiration':  (None, "Time in seconds"),
    'timeout':     (None, "Timeout in seconds"),
    'force':       (None, "Create new address beyond gap limit, if no more addresses "
                    "are available."),
    'pending':     (None, "Show only pending requests."),
    'expired':     (None, "Show only expired requests."),
    'paid':        (None, "Show only paid requests."),
    'show_addresses': (None, "Show input and output addresses"),
    'show_fiat':   (None, "Show fiat value of transactions"),
    'year':        (None, "Show history for a given year"),
    'show_all':    (None, "Show all addresses including inactive ones"),
    'account_id':  (None, "Specify which account to use when multiple accounts are available"),
    'id':          (None, "Specify a specific address by key instance ID"),
    'address':     (None, "Filter results to show only a specific address"),
}


# don't use floats because of rounding errors
from .transaction import txdict_from_str
json_loads = lambda x: json.loads(x, parse_float=lambda x: str(Decimal(x)))
arg_types = {
    'num': int,
    'nbits': int,
    'imax': int,
    'year': int,
    'tx': txdict_from_str,
    'pubkeys': json_loads,
    'jsontx': json_loads,
    'inputs': json_loads,
    'outputs': json_loads,
    'fee': lambda x: str(Decimal(x)) if x is not None else None,
    'amount': lambda x: str(Decimal(x)) if x != '!' else '!',
    'locktime': int,
}

config_variables = {

    'addrequest': {
        'url_rewrite': ('Parameters passed to str.replace(), in order to create the r= part '
                        'of bitcoin: URIs. Example: '
                        "\"(\'file:///var/www/\',\'https://electrum.org/\')\""),
    },
    'listrequests':{
        'url_rewrite': ('Parameters passed to str.replace(), in order to create the r= part '
                        'of bitcoin: URIs. Example: '
                        "\"(\'file:///var/www/\',\'https://electrum.org/\')\""),
    }
}

def set_default_subparser(self, name, args=None) -> None:
    """see http://stackoverflow.com/questions/5176691"""
    subparser_found = False
    for arg in sys.argv[1:]:
        if arg in ['-h', '--help']:  # global help if no subparser
            break
    else:
        for x in self._subparsers._actions:
            if not isinstance(x, argparse._SubParsersAction):
                continue
            for sp_name in x._name_parser_map.keys():
                if sp_name in sys.argv[1:]:
                    subparser_found = True
        if not subparser_found:
            # insert default in first position, this implies no
            # global options without a sub_parsers specified
            if args is None:
                sys.argv.insert(1, name)
            else:
                args.insert(0, name)

# NOTE(rt12) Ignore typing due to '"Type[ArgumentParser]" has no attribute "set_default_subparser"'
argparse.ArgumentParser.set_default_subparser = set_default_subparser # type: ignore


# workaround https://bugs.python.org/issue23058
# see https://github.com/nickstenning/honcho/pull/121

def subparser_call(self, parser, namespace, values, option_string=None):
    from argparse import ArgumentError, SUPPRESS, _UNRECOGNIZED_ARGS_ATTR
    parser_name = values[0]
    arg_strings = values[1:]
    # set the parser name if requested
    if self.dest is not SUPPRESS:
        setattr(namespace, self.dest, parser_name)
    # select the parser
    try:
        parser = self._name_parser_map[parser_name]
    except KeyError:
        tup = parser_name, ', '.join(self._name_parser_map)
        msg = _('unknown parser {!r} (choices: {})').format(*tup)
        raise ArgumentError(self, msg)
    # parse all the remaining options into the namespace
    # store any unrecognized options on the object, so that the top
    # level parser can decide what to do with them
    namespace, arg_strings = parser.parse_known_args(arg_strings, namespace)
    if arg_strings:
        vars(namespace).setdefault(_UNRECOGNIZED_ARGS_ATTR, [])
        getattr(namespace, _UNRECOGNIZED_ARGS_ATTR).extend(arg_strings)

# NOTE(rt12) Ignore typing due to "Cannot assign to a method"
argparse._SubParsersAction.__call__ = subparser_call # type: ignore


def add_network_options(parser):
    parser.add_argument("-1", "--oneserver", action="store_true", dest="oneserver",
                        default=False, help="connect to one server only")
    parser.add_argument("-s", "--server", dest="server", default=None,
                        help="set server host:port:protocol, where protocol is either "
                        "t (tcp) or s (ssl)")
    parser.add_argument("-p", "--proxy", dest="proxy", default=None,
                        help="set proxy [type:]host[:port], where type is socks4 or socks5")


def add_global_options(parser):
    group = parser.add_argument_group('global options')
    group.add_argument("-v", "--verbose", action="store", dest="verbose",
                       const='info', default='warning', nargs='?',
                       choices = ('debug', 'info', 'warning', 'error'),
                       help="Set logging verbosity")
    group.add_argument("-D", "--dir", dest="electrum_sv_path", help="ElectrumSV directory")
    group.add_argument("-P", "--portable", action="store_true", dest="portable", default=False,
                       help="Use local 'electrum_data' directory")
    group.add_argument("-w", "--wallet", dest="wallet_path", help="wallet path")
    group.add_argument("-wp", "--walletpassword", dest="wallet_password", default=None,
                       help="Supply wallet password")

    # Select Network
    group.add_argument("--testnet", action="store_true", dest="testnet", default=False,
                       help="Use Testnet")
    group.add_argument("--scaling-testnet", action="store_true", dest="scalingtestnet",
                       default=False, help="Use Scaling Testnet")
    group.add_argument("--regtest", action="store_true", dest="regtest",
                       default=False, help="Use Regression Testnet")
    group.add_argument("--file-logging", action="store_true", dest="file_logging", default=False,
                       help="Redirect logging to log file")

    # REST API
    group.add_argument("--restapi", action="store_true", dest="restapi",
                       help="Run the built-in restapi")
    group.add_argument("--restapi-port", dest="restapi_port",
                       help="Set restapi port")
    group.add_argument("--restapi-username", dest="restapi_username",
                       help="Set restapi username (Basic Auth)")
    group.add_argument("--restapi-password", dest="restapi_password",
                       help="Set restapi password (Basic Auth)")

    # Wallet Creation
    group.add_argument("--no-password-check", action="store_true", dest="nopasswordcheck",
                       default=False, help="Skip password confirmation step for wallet creation")


def get_parser():
    global known_commands

    # create main parser
    parser = argparse.ArgumentParser(
        epilog="Run 'electrum-sv help <command>' to see the help for a command")
    add_global_options(parser)
    subparsers = parser.add_subparsers(dest='cmd', metavar='<command>')
    # gui
    parser_gui = subparsers.add_parser('gui',
                                       description="Run Electrum's Graphical User Interface.",
                                       help="Run GUI (default)")
    parser_gui.add_argument("url", nargs='?', default=None, help="bitcoin URI (or bip270 file)")
    parser_gui.add_argument("-g", "--gui", dest="gui", help="select graphical user interface",
                            choices=['qt'])
    parser_gui.add_argument("-o", "--offline", action="store_true", dest="offline", default=False,
                            help="Run offline")
    parser_gui.add_argument("-m", action="store_true", dest="hide_gui", default=False,
                            help="hide GUI on startup")
    parser_gui.add_argument("-L", "--lang", dest="language", default=None,
                            help="default language used in GUI")
    add_network_options(parser_gui)
    add_global_options(parser_gui)
    # daemon
    parser_daemon = subparsers.add_parser('daemon', help="Run Daemon")
    parser_daemon.add_argument("subcommand", choices=['start', 'status', 'stop',
                                                      'load_wallet', 'close_wallet'], nargs='?')
    parser_daemon.add_argument("-dapp", "--daemon-app-module", dest="daemon_app_module",
        help="Run the daemon control app from the given module")
    #parser_daemon.set_defaults(func=run_daemon)
    add_network_options(parser_daemon)
    add_global_options(parser_daemon)

    # commands
    for command_name in sorted(known_commands.keys()):
        command = known_commands[command_name]
        command_option_name = command_name
        subparser = subparsers.add_parser(command_option_name, help=command.help,
            description=command.description)
        add_global_options(subparser)

        if command_option_name == 'restore':
            subparser.add_argument("-o", "--offline", action="store_true", dest="offline",
                default=False, help="Run offline")

        for option_name, option_default_value in zip(command.options, command.defaults):
            short_option, help = command_options[option_name]
            long_option = '--' + option_name
            action = "store_true" if type(option_default_value) is bool else 'store'
            args = (short_option, long_option) if short_option else (long_option,)
            if action == 'store':
                _type = arg_types.get(option_name, str)
                subparser.add_argument(*args, dest=option_name, action=action,
                    default=option_default_value, help=help, type=_type)
            else:
                subparser.add_argument(*args, dest=option_name, action=action,
                    default=option_default_value, help=help)

        for param in command.params:
            h = param_descriptions.get(param, '')
            _type = arg_types.get(param, str)
            subparser.add_argument(param, help=h, type=_type)

        cvh = config_variables.get(command_option_name)
        if cvh:
            group = subparser.add_argument_group('configuration variables',
                                         '(set with setconfig/getconfig)')
            for k, v in cvh.items():
                group.add_argument(k, nargs='?', help=v)

    # 'gui' is the default command
    parser.set_default_subparser('gui')
    return parser
