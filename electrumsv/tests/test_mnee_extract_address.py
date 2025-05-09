#!/usr/bin/env python
#
# Test MNEE script address extraction
#

import unittest
from unittest.mock import MagicMock, patch
import logging
import sys
from typing import Dict, List, Optional, Set, Tuple
from bitcoinx import Script, Address, Bitcoin

# Add the parent directory to the path so we can import electrumsv modules
sys.path.append('..')

from electrumsv.mnee import MneeAccount
from electrumsv.logs import logs

# Set up logging
logs.set_level(logging.DEBUG)

class TestMNEEScriptAddressExtraction(unittest.TestCase):
    """Test the _extract_address_from_script method in MneeAccount."""
    
    def setUp(self):
        """Set up the test with a mock MneeAccount instance."""
        # Create a mock account
        self.mock_account = MagicMock(spec=MneeAccount)
        
        # Patch the logger
        self.mock_logger = MagicMock()
        
        # Manually copy the _extract_address_from_script method to our mock
        # Since it's a helper method that works mostly independently
        self.mock_account._extract_address_from_script = MneeAccount._extract_address_from_script

    def test_extract_address_from_bsv20_script(self):
        """Test extracting an address from a BSV-20 token script."""
        # The example BSV-20 script provided
        bsv20_script_bytes = b'\x00c\x03ordQ\x12application/bsv-20\x00Lu{"p":"bsv-20","op":"transfer","id":"ae59f3b898ec61acbdb6cc7a245fabeded0c094bf046f35206a3aec60ef88127_0","amt":"1000"}hv\xa9\x14]4\xbe\x17\x8f\x0b\xc3,=\x85g\x14\'\xf1\xe7\x06\x94\xca\x8a;\x88\xad!\x02\n\x17}j^o:\x86\x89\xac\xd2\xe3\x13\xbd\x1c\xf0\xdc\xf5\xa2C\xd1\xccg\xb7!\x86\x02\xae\xe9\xe0K/\xac'
        
        # Create a Script object
        script = Script(bsv20_script_bytes)
        
        # Call the method we want to test
        with patch('electrumsv.mnee.logger', self.mock_logger):
            extracted_address = self.mock_account._extract_address_from_script(script, 0)
        
        # Manually extract the expected address from the script for verification
        # In a P2PKH script, the pubkey hash is after 0xa9 0x14 and is 20 bytes long
        pubkey_hash_start = bsv20_script_bytes.find(b'\xa9\x14') + 2
        if pubkey_hash_start > 0:
            pubkey_hash = bsv20_script_bytes[pubkey_hash_start:pubkey_hash_start+20]
            expected_address = Address(pubkey_hash, Bitcoin).to_string()
            print(f"Expected address: {expected_address}")
        else:
            expected_address = None
        
        # Assert that the extracted address matches the expected address
        self.assertIsNotNone(extracted_address, "Failed to extract address from BSV-20 script")
        if expected_address:
            self.assertEqual(extracted_address, expected_address, 
                            f"Extracted address {extracted_address} doesn't match expected {expected_address}")
            print(f"Successfully extracted address: {extracted_address}")
        
        # Test with direct script object to verify manual parsing
        parsed_address = self._parse_script_manually(bsv20_script_bytes)
        print(f"Manually parsed address: {parsed_address}")
        
    def test_extract_address_from_various_script_types(self):
        """Test address extraction from different script types."""
        # Test with P2PKH script
        p2pkh_hex = "76a9140099e8a102adbdcb6a9ca41b5f56ca6f00a34ba488ac"
        p2pkh_script = Script.from_hex(p2pkh_hex)
        
        with patch('electrumsv.mnee.logger', self.mock_logger):
            p2pkh_address = self.mock_account._extract_address_from_script(p2pkh_script, 0)
        
        self.assertIsNotNone(p2pkh_address, "Failed to extract address from P2PKH script")
        print(f"P2PKH address: {p2pkh_address}")
        
        # Test with P2SH script
        p2sh_hex = "a914ec156e4f0b9c09f01c6016ed44b0917d4f530a8f87"
        p2sh_script = Script.from_hex(p2sh_hex)
        
        with patch('electrumsv.mnee.logger', self.mock_logger):
            p2sh_address = self.mock_account._extract_address_from_script(p2sh_script, 0)
        
        self.assertIsNotNone(p2sh_address, "Failed to extract address from P2SH script")
        print(f"P2SH address: {p2sh_address}")
        
    def _parse_script_manually(self, script_bytes):
        """Manual script parsing for verification."""
        # Check for P2PKH pattern (OP_DUP OP_HASH160 <pubkey_hash> OP_EQUALVERIFY OP_CHECKSIG)
        p2pkh_start = script_bytes.find(b'\xa9\x14')
        if p2pkh_start >= 0:
            pubkey_hash = script_bytes[p2pkh_start+2:p2pkh_start+22]
            try:
                return Address(pubkey_hash, Bitcoin).to_string()
            except Exception as e:
                print(f"Error creating address: {e}")
                return None
                
        # Check for P2SH pattern (OP_HASH160 <script_hash> OP_EQUAL)
        p2sh_start = script_bytes.find(b'\xa9\x14')
        if p2sh_start >= 0 and len(script_bytes) >= p2sh_start+23 and script_bytes[p2sh_start+22:p2sh_start+23] == b'\x87':
            script_hash = script_bytes[p2sh_start+2:p2sh_start+22]
            try:
                return Address(script_hash, Bitcoin, addr_type='p2sh').to_string()
            except Exception as e:
                print(f"Error creating P2SH address: {e}")
                return None
                
        return None

if __name__ == '__main__':
    unittest.main() 