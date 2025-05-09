#!/bin/bash
# Run MNEE-related tests

# Navigate to the project root directory
cd "$(dirname "$0")"

# Set up Python path if needed
export PYTHONPATH=.:$PYTHONPATH

# Run specific MNEE tests
python -m unittest electrumsv.tests.test_mnee_simple
python -m unittest electrumsv.tests.test_mnee_persistence
python -m unittest electrumsv.tests.test_mnee_wallet_integration
python -m unittest electrumsv.tests.test_mnee_database_queries
python -m unittest electrumsv.tests.test_mnee_migration
python -m unittest electrumsv.tests.test_account_mnee

# Or to run all MNEE tests:
# python -m unittest discover -s electrumsv/tests -p 'test_mnee*.py'

echo ""
echo "MNEE tests completed" 