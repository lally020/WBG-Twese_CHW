"""Start the synthetic offline demo bound ONLY to localhost."""
import os
from getpass import getpass
if len(os.environ.get('COMPASS_PASSPHRASE',''))<12:
    print('Choose a local encryption passphrase (12+ characters). Save it: losing it loses access to the demo database.')
    os.environ['COMPASS_PASSPHRASE']=getpass('Database passphrase: ')
if len(os.environ.get('COMPASS_PASSPHRASE',''))<12:
    raise SystemExit('Passphrase too short')
print('\nOpen http://127.0.0.1:8765 in a browser.')
print('Runs on localhost only. SMS transport is SIMULATED, no real texts will be sent.\n')
import uvicorn
uvicorn.run('server:app',host='127.0.0.1',port=8765,reload=False,workers=1)
