#!/bin/bash
set -e
cd "$(dirname "$0")"
if [ -d .venv ]; then source .venv/bin/activate; fi
export COMPASS_DB_PATH="$PWD/private_data/workbook_demo.sqlite.aes"
if [ ! -f "$COMPASS_DB_PATH" ]; then
  echo 'Creating a SEPARATE encrypted database for the synthetic workbook.'
  python3 import_clinic_demo.py --bundle import_data/clinic_demo.json --db "$COMPASS_DB_PATH"
  echo 'Please enter the SAME passphrase again at the next prompt.'
fi
python3 launch.py
