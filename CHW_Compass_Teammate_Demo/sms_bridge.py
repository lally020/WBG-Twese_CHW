"""Optional Twilio bridge for consenting testers and fictional patients only.
Run exactly one bridge process. Never tunnel the dashboard port (8765).
"""
import json
import logging
import re
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import urlparse
import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from twilio.request_validator import RequestValidator
from store import EncryptedStore

log=logging.getLogger('compass.sms')
STOP={'STOP','STOPALL','UNSUBSCRIBE','CANCEL','END','QUIT','REVOKE','OPTOUT'}

@dataclass
class Config:
    account: str
    token: str
    number: str
    webhook: str
    patients: dict
    live: bool=False

    def validate(self):
        u=urlparse(self.webhook)
        if u.scheme!='https' or not u.netloc or u.path!='/sms' or u.query or u.fragment or u.username:
            raise ValueError('Webhook must be an exact public HTTPS URL ending in /sms, without query parameters')
        if not re.fullmatch(r'AC[0-9a-fA-F]{32}',self.account) or not self.token:
            raise ValueError('A valid Twilio Account SID and Auth Token are required')
        for number in [self.number,*self.patients]:
            if not re.fullmatch(r'\+[1-9][0-9]{7,14}',number):raise ValueError('Use E.164 phone numbers, including +country code')
        if not self.patients or any(type(pid)!=int or pid<1 for pid in self.patients.values()):
            raise ValueError('Map consenting test numbers to positive fictional patient IDs')

class Bridge:
    def __init__(self,config,db,send,process=None):
        config.validate()
        self.config,self.db,self.send=config,db,send
        self.process=process or self.process_local
        self.stop=threading.Event()
        self.db.script('''CREATE TABLE IF NOT EXISTS queue (
 sid TEXT PRIMARY KEY, phone TEXT, pid INTEGER, body TEXT, state TEXT,
 reply TEXT, outbound_sid TEXT, attempts INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS blocked (phone TEXT PRIMARY KEY);''')
        # Never blindly resend after an ambiguous provider result or a crash.
        self.db.execute("UPDATE queue SET state='send_unknown' WHERE state='sending'")
        self.db.execute("UPDATE queue SET state='pending' WHERE state='processing'")

    def accept(self,p):
        cfg=self.config
        if p.get('AccountSid')!=cfg.account or p.get('To')!=cfg.number or p.get('From') not in cfg.patients:
            raise HTTPException(403,'Sender or destination not allowed')
        sid,body,phone=p.get('MessageSid',''),p.get('Body',''),p['From']
        if not re.fullmatch(r'SM[0-9a-fA-F]{32}',sid):raise HTTPException(400,'Invalid message SID')
        with self.db.batch():
            existing=self.db.one('SELECT * FROM queue WHERE sid=?',(sid,))
            if existing:
                if existing['phone']!=phone or existing['body']!=body:raise HTTPException(409,'Message SID conflict')
                return
            keyword=body.strip().upper()
            if keyword in STOP:
                self.db.execute('INSERT OR IGNORE INTO blocked VALUES(?)',(phone,))
                self.db.execute("UPDATE queue SET state='suppressed' WHERE phone=? AND state IN ('pending','draft','processing')",(phone,))
                state='opt_out'
            elif keyword in {'START','UNSTOP'}:
                self.db.execute('DELETE FROM blocked WHERE phone=?',(phone,))
                state='opt_in' # Provider handles its own subscription response.
            elif self.db.one('SELECT * FROM blocked WHERE phone=?',(phone,)):
                state='suppressed'
            elif not body.strip() or len(body)>500 or p.get('NumMedia','0')!='0':
                state='unsupported' # Text only; never silently truncate clinical content.
            else:state='pending'
            self.db.execute('INSERT INTO queue(sid,phone,pid,body,state) VALUES(?,?,?,?,?)',
                (sid,phone,cfg.patients[phone],body,state))
        log.info('Incoming SMS queued: %s',state) # No numbers, message text or secrets.

    def process_local(self,row):
        with httpx.Client(timeout=180,trust_env=False) as client:
            response=client.post(f"http://127.0.0.1:8765/api/patients/{row['pid']}/sms",
                json={'body':row['body'],'transport_id':'twilio:'+row['sid']})
            response.raise_for_status()
            reply=response.json().get('patient_auto_ack')
            if not isinstance(reply,str) or not reply.strip():raise ValueError('No patient response produced')
            return reply

    def step(self):
        row=self.db.one("SELECT * FROM queue WHERE state='pending' ORDER BY rowid LIMIT 1")
        if not row:return False
        sid=row['sid']
        if self.config.patients.get(row['phone'])!=row['pid']:
            self.db.execute("UPDATE queue SET state='suppressed' WHERE sid=?",(sid,))
            return True
        self.db.execute("UPDATE queue SET state='processing',attempts=attempts+1 WHERE sid=?",(sid,))
        try:
            reply='Compass DEMO: '+self.process(row)
            if len(reply)>1500:raise ValueError('Reply too long')
        except Exception:
            self.db.execute("UPDATE queue SET state=? WHERE sid=?",('failed_processing' if row['attempts']>=2 else 'pending',sid))
            log.warning('Local processing failed; inspect the local app. No SMS sent.')
            return True
        with self.db.batch():
            blocked=self.db.one('SELECT * FROM blocked WHERE phone=?',(row['phone'],))
            state='suppressed' if blocked else ('sending' if self.config.live else 'draft')
            self.db.execute('UPDATE queue SET reply=?,state=? WHERE sid=?',(reply,state,sid))
        if state!='sending':return True
        try:
            outbound=self.send(to=row['phone'],from_=self.config.number,body=reply)
        except Exception:
            self.db.execute("UPDATE queue SET state='send_unknown' WHERE sid=?",(sid,))
            log.warning('SMS submission failed or is uncertain. Check Twilio logs before any manual retry.')
        else:
            self.db.execute("UPDATE queue SET state='provider_accepted',outbound_sid=? WHERE sid=?",(outbound.sid,sid))
            log.info('SMS accepted by provider; check Twilio Console for delivery status.')
        return True

    def run(self):
        while not self.stop.is_set():
            try:self.step()
            except Exception:log.error('Bridge queue error; inspect local configuration.')
            self.stop.wait(1)

def create_app(bridge):
    @asynccontextmanager
    async def lifespan(app):
        worker=threading.Thread(target=bridge.run,daemon=True)
        worker.start()
        yield
        bridge.stop.set()
        worker.join(timeout=2)
    app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    @app.post('/sms')
    async def sms(request:Request):
        if request.url.query:raise HTTPException(400,'Unexpected query')
        raw=bytearray()
        async for part in request.stream():
            raw.extend(part)
            if len(raw)>16384:raise HTTPException(413,'Too large')
        # Preserve all signed parameters, including duplicate values.
        from starlette.datastructures import FormData
        from urllib.parse import parse_qsl
        if request.headers.get('content-type','').split(';')[0]!='application/x-www-form-urlencoded':
            raise HTTPException(415,'Expected form body')
        params=FormData(parse_qsl(raw.decode('utf-8'),keep_blank_values=True))
        if not RequestValidator(bridge.config.token).validate(bridge.config.webhook,params,request.headers.get('x-twilio-signature','')):
            raise HTTPException(403,'Invalid signature')
        if any(len(params.getlist(k))!=1 for k in params):raise HTTPException(400,'Duplicate fields')
        bridge.accept(params)
        return Response('<Response/>',media_type='application/xml')
    return app
