"""Interactive setup: credentials stay in process memory, never in source code."""
from getpass import getpass
from pathlib import Path
import logging
import httpx
import uvicorn
from twilio.rest import Client
from twilio.http.http_client import TwilioHttpClient
from sms_bridge import Bridge,Config,create_app
from store import EncryptedStore

if __name__=='__main__':
    print('Compass real SMS DEMO — use your own consenting test phone and fictional patient data.')
    with httpx.Client(timeout=10,trust_env=False) as local:
        response=local.get('http://127.0.0.1:8765/api/patients')
        response.raise_for_status()
        patients=response.json()
    print('Available fictional patients:')
    for patient in patients[:50]:
        print(patient['id'], patient['name'], patient['assessment']['priority'])
    cfg=Config(account=input('Twilio Account SID: ').strip(),token=getpass('Twilio Auth Token (hidden): '),
        number=input('Twilio SMS number (+country code): ').strip(),
        webhook=input('Public HTTPS webhook URL ending in /sms: ').strip(),
        patients={input('Your consenting test phone (+country code): ').strip():int(input('Fictional patient ID from dashboard: '))},
        live=input('Type SEND to enable real outgoing SMS (anything else = drafts only): ').strip()=='SEND')
    cfg.validate()
    if next(iter(cfg.patients.values())) not in {p["id"] for p in patients}:
        raise ValueError("Choose an existing fictional patient ID")
    db=EncryptedStore(Path(__file__).resolve().parent/'private_data'/'sms_bridge.sqlite.aes',getpass('Bridge storage passphrase (12+ characters; reuse on restart): '))
    client=Client(cfg.account,cfg.token,http_client=TwilioHttpClient(timeout=20,max_retries=0))
    bridge=Bridge(cfg,db,client.messages.create)
    logging.basicConfig(level=logging.INFO)
    print('Bridge on 127.0.0.1:8766. Tunnel ONLY 8766. Keep the main app running on 8765.')
    uvicorn.run(create_app(bridge),host='127.0.0.1',port=8766,access_log=False)
