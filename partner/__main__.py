"""Support 'python -m partner' entry point (main module)."""
import sys
import os

# Set UTF-8 encoding for cross-platform compatibility
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

import sys, os, argparse
# Parse instance-specific args before falling through to CLI
parser = argparse.ArgumentParser()
parser.add_argument('--instance-id', default=os.environ.get('PARTNER_INSTANCE_ID', 'default'))
parser.add_argument('--workspace', default=os.environ.get('PARTNER_WORKSPACE', ''))
args, remaining = parser.parse_known_args()

# Set environment variables for the bridge to pick up
os.environ['PARTNER_INSTANCE_ID'] = args.instance_id
if args.workspace:
    os.environ['PARTNER_WORKSPACE'] = args.workspace

from partner.cli import main
sys.argv = [sys.argv[0]] + remaining
main()
