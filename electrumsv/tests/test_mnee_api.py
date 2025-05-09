#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Tests for MNEE API integration in the ElectrumSV wallet.
"""

import unittest
from unittest.mock import patch, MagicMock, Mock
import json
import tempfile
import time

# Import from mnee.py 
from electrumsv.mnee import (
    MNEE_API_URL_PRODUCTION, MNEE_API_URL_SANDBOX,
    MneeAccount
)

class MockResponse:
    """Mock urllib.request response object"""
    def __init__(self, json_data, status_code=200):
        self.json_data = json_data
        self.status_code = status_code
        
    def read(self):
        return json.dumps(self.json_data).encode()
        
    def getcode(self):
        return self.status_code
        
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

class TestMNEEAPIIntegration(unittest.TestCase):
    
    def setUp(self):
        # Create mock account
        self.mock_account = MagicMock(spec=MneeAccount)
        
        # Mock app_state.config for tests
        self.mock_config = MagicMock()
        self.config_values = {
            'mnee_api_key_production': 'test_production_key',
            'mnee_api_key_sandbox': 'test_sandbox_key',
            'mnee_environment': 'sandbox',
            'mnee_token_id': ''
        }
        self.mock_config.get.side_effect = lambda key, default='': self.config_values.get(key, default)
        self.mock_config.set_key.side_effect = lambda key, value: self.config_values.update({key: value})
        
    @patch('urllib.request.Request')
    @patch('urllib.request.urlopen')
    @patch('electrumsv.mnee.app_state')
    def test_fetch_txids_for_multiple_addresses(self, mock_app_state, mock_urlopen, mock_request):
        """Test successful MNEE txids fetch for multiple addresses"""
        # Setup mock app_state
        mock_app_state.config = self.mock_config
        
        # Setup mock response
        mock_data = {
            'addresses': {
                'addr1': {
                    'transactions': [
                        {'txid': 'tx1', 'value': 100},
                        {'txid': 'tx2', 'value': 200}
                    ]
                },
                'addr2': {
                    'transactions': [
                        {'txid': 'tx3', 'value': 300}
                    ]
                }
            }
        }
        mock_urlopen.return_value = MockResponse(mock_data)
        
        # Call the method as an instance method
        addresses = ['addr1', 'addr2']
        result = self.mock_account.fetch_txids_for_multiple_addresses(addresses)
        
        # Check results
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)
        self.assertEqual(len(result['addr1']), 2)
        self.assertEqual(len(result['addr2']), 1)
        self.assertEqual(result['addr1'][0], 'tx1')
        self.assertEqual(result['addr2'][0], 'tx3')
        
        # Verify the URL used
        mock_request.assert_called_once()
        args, kwargs = mock_request.call_args
        self.assertIn(MNEE_API_URL_SANDBOX, args[0])
        self.assertIn('test_sandbox_key', args[0])
        self.assertEqual(kwargs['method'], 'POST')
        
    @patch('urllib.request.Request')
    @patch('urllib.request.urlopen')
    @patch('electrumsv.mnee.app_state')
    def test_fetch_txids_production_url(self, mock_app_state, mock_urlopen, mock_request):
        """Test that production environment uses correct URL"""
        # Setup mock app_state with production environment
        mock_app_state.config = self.mock_config
        self.config_values['mnee_environment'] = 'production'
        
        # Setup mock response
        mock_data = {'addresses': {'addr1': {'transactions': [{'txid': 'tx1'}]}}}
        mock_urlopen.return_value = MockResponse(mock_data)
        
        # Call the method as an instance method
        addresses = ['addr1']
        result = self.mock_account.fetch_txids_for_multiple_addresses(addresses)
        
        # Check that production URL was used
        mock_request.assert_called_once()
        args, kwargs = mock_request.call_args
        self.assertIn(MNEE_API_URL_PRODUCTION, args[0])
        
    @patch('urllib.request.Request')
    @patch('urllib.request.urlopen')
    @patch('electrumsv.mnee.app_state')
    def test_fetch_txids_failure(self, mock_app_state, mock_urlopen, mock_request):
        """Test handling of API fetch failure"""
        # Setup mock app_state
        mock_app_state.config = self.mock_config
        
        # Simulate an HTTP error
        mock_urlopen.side_effect = Exception("Connection error")
        
        # Call the method as an instance method
        addresses = ['addr1']
        result = self.mock_account.fetch_txids_for_multiple_addresses(addresses)
        
        # Should return empty dict on failure
        self.assertEqual(result, {'addr1': []})
        
    @patch('urllib.request.Request')
    @patch('urllib.request.urlopen')
    @patch('electrumsv.mnee.app_state')
    def test_fetch_txids_alternative_response_format(self, mock_app_state, mock_urlopen, mock_request):
        """Test handling of alternative API response format"""
        # Setup mock app_state
        mock_app_state.config = self.mock_config
        
        # Mock response in different format (results instead of addresses)
        mock_data = {
            'results': {
                'addr1': [{'txid': 'tx1'}, {'txid': 'tx2'}],
                'addr2': [{'txid': 'tx3'}]
            }
        }
        mock_urlopen.return_value = MockResponse(mock_data)
        
        # Call the method as an instance method
        addresses = ['addr1', 'addr2']
        result = self.mock_account.fetch_txids_for_multiple_addresses(addresses)
        
        # Check results - should be able to handle this format too
        self.assertEqual(len(result), 2)
        self.assertEqual(len(result['addr1']), 2)
        self.assertEqual(result['addr1'][0], 'tx1')

if __name__ == '__main__':
    unittest.main() 