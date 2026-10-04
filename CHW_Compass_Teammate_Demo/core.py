"""CHW Compass domain logic. Prototype ONLY. NOT validated medical software."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import math
import re
import statistics
from pathlib import Path
import random
from store import EncryptedStore
from tiny_ai import TinyAI, TRAIN

HERE=Path(__file__).resolve().parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS transport_receipts (
 transport_id TEXT PRIMARY KEY, patient_id INTEGER, body TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS patients (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT NOT NULL,
 language TEXT NOT NULL CHECK(language IN ('English','Kiswahili')),
 condition TEXT NOT NULL, next_clinic TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS vitals (
 id INTEGER PRIMARY KEY, patient_id INTEGER REFERENCES patients(id), recorded_at TEXT,
 sbp INTEGER, dbp INTEGER, note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS messages (
 id INTEGER PRIMARY KEY, patient_id INTEGER REFERENCES patients(id), direction TEXT,
 body TEXT, created_at TEXT, intent TEXT, confidence REAL, status TEXT);
CREATE TABLE IF NOT EXISTS adherence (
 id INTEGER PRIMARY KEY, patient_id INTEGER REFERENCES patients(id), recorded_at TEXT,
 taken INTEGER CHECK(taken IN (0,1)), source TEXT);
CREATE TABLE IF NOT EXISTS review (
 id INTEGER PRIMARY KEY, patient_id INTEGER REFERENCES patients(id), kind TEXT, urgency TEXT,
 rationale TEXT, ai_suggestion TEXT, status TEXT DEFAULT 'open', created_at TEXT,
 outcome TEXT DEFAULT '', reviewer TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS outbox (
 id INTEGER PRIMARY KEY, patient_id INTEGER REFERENCES patients(id), category TEXT, cadence TEXT,
 body TEXT, due_at TEXT, status TEXT DEFAULT 'pending_approval', approved_by TEXT DEFAULT '',
 delivered_at TEXT);
CREATE TABLE IF NOT EXISTS quiz (
 id INTEGER PRIMARY KEY, patient_id INTEGER REFERENCES patients(id), topic TEXT,
 question TEXT, answer TEXT, correct INTEGER, answered_at TEXT);
CREATE TABLE IF NOT EXISTS feedback (
 id INTEGER PRIMARY KEY, message_id INTEGER UNIQUE REFERENCES messages(id),
 original_label TEXT, corrected_label TEXT, approved INTEGER DEFAULT 0,
 reviewer TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS pain_intakes (
 id INTEGER PRIMARY KEY, patient_id INTEGER REFERENCES patients(id), status TEXT NOT NULL,
 details TEXT NOT NULL DEFAULT '{}', pending_field TEXT, initial_message TEXT, review_id INTEGER,
 created_at TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS idx_pain_patient ON pain_intakes(patient_id,id DESC);
CREATE TABLE IF NOT EXISTS urgency_predictions (
 message_id INTEGER PRIMARY KEY REFERENCES messages(id), patient_id INTEGER REFERENCES patients(id),
 label TEXT, urgent_score REAL, model_version TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS idx_urgency_patient ON urgency_predictions(patient_id,message_id DESC);
CREATE TABLE IF NOT EXISTS audits (
 id INTEGER PRIMARY KEY, event TEXT, details TEXT, created_at TEXT);

CREATE INDEX IF NOT EXISTS idx_vitals_patient_date ON vitals(patient_id, recorded_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_adherence_patient_date ON adherence(patient_id, recorded_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_messages_patient_direction_id ON messages(patient_id, direction, id DESC);
CREATE INDEX IF NOT EXISTS idx_quiz_patient_id ON quiz(patient_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_review_status_urgency ON review(status, urgency, patient_id);
CREATE INDEX IF NOT EXISTS idx_outbox_status_due ON outbox(status, due_at, id);
CREATE INDEX IF NOT EXISTS idx_messages_direction ON messages(direction);
"""

# Demonstration triggers only; local clinical protocols must replace these.
RED_PHRASES = [
 'chest pain', 'cannot breathe', 'difficulty breathing', 'slurred speech',
 'one sided weakness', 'one-sided weakness', 'face drooping',
 'sudden weakness', 'severe shortness of breath',
 'maumivu ya kifua', 'siwezi kupumua', 'uso umelegea', 'ongea kwa shida'
]

TEMPLATES = {
 'medication': {
  'English':'Hello from your care team. Have you taken your medication as prescribed today? Reply YES or NO.',
  'Kiswahili':'Habari kutoka timu yako ya afya. Je, umetumia dawa zako kama ulivyoelekezwa leo? Jibu NDIYO au HAPANA.'},
 'chw_visit':{
  'English':'Your community health worker plans a follow-up visit. Reply HELP if you need assistance with scheduling.',
  'Kiswahili':'Mhudumu wako wa afya ya jamii anapanga kukutembelea. Jibu MSAADA ikiwa unahitaji usaidizi wa kupanga muda.'},
 'clinic_visit':{
  'English':'Reminder from your care team: please check the date of your next clinic appointment. Reply HELP for assistance.',
  'Kiswahili':'Kumbusho kutoka timu yako ya afya: tafadhali hakiki tarehe ya miadi yako ya kliniki. Jibu MSAADA kwa usaidizi.'},
 'diet':{
  'English':'Health tip: consider vegetables and foods with less added salt when available. Ask your care team what suits you.',
  'Kiswahili':'Kidokezo cha afya: chagua mboga na chakula chenye chumvi kidogo inapowezekana. Uliza timu yako ya afya kuhusu mahitaji yako.'},
 'lifestyle':{
  'English':'Health tip: ask your care team about daily activity that is safe and appropriate for you.',
  'Kiswahili':'Kidokezo cha afya: uliza timu yako ya afya kuhusu shughuli za kila siku zilizo salama na zinazokufaa.'},
 'quiz':{
  'English':'Quick quiz: If you miss your medicines, should you A) tell your health worker, or B) hide it? Reply A or B.',
  'Kiswahili':'Swali fupi: Ukikosa kutumia dawa zako, je, A) umwambie mhudumu wa afya, au B) uficha? Jibu A au B.'}
}
QUIZ_BANK={
 'adherence': {
  'English': ('If you miss medicine doses, should you A) tell your health worker or B) hide it?', 'A'),
  'Kiswahili': ('Ukikosa dozi za dawa, je, A) umwambie mhudumu wa afya au B) uficha?', 'A')},
 'checkup': {
  'English': ('If you need to change a clinic appointment, should you A) contact the care team or B) ignore it?', 'A'),
  'Kiswahili': ('Ikiwa unahitaji kubadili miadi, je, A) uwasiliane na timu ya afya au B) uipuuze?', 'A')},
 'diet': {
  'English': ('For routine healthy eating, which is generally advised? A) less added salt, B) more added salt', 'A'),
  'Kiswahili': ('Kwa lishe bora, nini hushauriwa kwa ujumla? A) chumvi kidogo, B) chumvi zaidi', 'A')},
}

def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def days_ago(n):
    return (datetime.now(timezone.utc)-timedelta(days=n)).replace(microsecond=0).isoformat()

def tokens_present(body):
    norm=re.sub(r'[^\w\s-]', ' ', body.lower().replace("can't", 'cannot').replace("can’t", 'cannot'))
    norm=re.sub(r'\s+', ' ', norm)
    extra=('chest hurts','cannot breathe','trouble breathing','shortness of breath','passed out','fainted')
    return [term for term in (*RED_PHRASES, *extra) if term in norm]

class Compass:
    def __init__(self, path, passphrase, seed=True):
        self.db=EncryptedStore(path,passphrase)
        self.db.script(SCHEMA)
        if seed and self.db.one('SELECT COUNT(*) AS n FROM patients')['n']==0:
            self.seed()
        self.ai=TinyAI(self.db)

    def audit(self, event, details):
        self.db.execute('INSERT INTO audits(event,details,created_at) VALUES(?,?,?)',(event,details,now()))

    def seed(self):
        rnd=random.Random(39)
        names=['Amina M.','Joseph K.','Fatuma S.','Moses T.','Neema W.','Paul O.','Zawadi R.','Daniel P.',
               'Grace L.','Juma N.','Mary A.','Hassan V.','Esther G.','Samuel K.','Halima J.','Peter C.',
               'Rehema B.','David N.','Rose M.','Ali D.','Lilian S.','John O.','Sarah K.','Farid H.']
        for i,name in enumerate(names,1):
            lang='Kiswahili' if i%3!=0 else 'English'
            condition='Hypertension & diabetes' if i%4==0 else 'Hypertension'
            self.db.execute('INSERT INTO patients(id,name,phone,language,condition,next_clinic,created_at) VALUES(?,?,?,?,?,?,?)',
                (i,name,f'+000000{i:04d}',lang,condition,days_ago(-rnd.randint(5,28))[:10],days_ago(60)))
            # Three groups: worsening, chronically high, stable.
            base=125 if i%3==0 else (153 if i%3==1 else 138)
            for j in range(8):
                sbp=base+rnd.randint(-5,5)+(j*4 if i%7==0 else 0)
                dbp=round(0.56*sbp)+rnd.randint(0,5)
                self.db.execute('INSERT INTO vitals(patient_id,recorded_at,sbp,dbp,note) VALUES(?,?,?,?,?)',
                    (i,days_ago((8-j)*7),sbp,dbp,'Synthetic CHW visit'))
            if i==2:
                sbp,dbp=188,122
            elif i==7:
                sbp,dbp=176,108
            elif i==11:
                sbp,dbp=120,77
            else:
                sbp,dbp=base+rnd.randint(-8,11),round(.58*base)+rnd.randint(-3,9)
            self.db.execute('INSERT INTO vitals(patient_id,recorded_at,sbp,dbp,note) VALUES(?,?,?,?,?)',
                (i,days_ago(rnd.randint(0,4)),sbp,dbp,'Synthetic recent reading'))
            for d in range(7):
                taken=0 if (i%5==0 and d<3) or (i%7==0 and d<2) else 1
                self.db.execute('INSERT INTO adherence(patient_id,recorded_at,taken,source) VALUES(?,?,?,?)',
                    (i,days_ago(d),taken,'Synthetic SMS check-in'))
            if i%4==0:
                for j in range(3):
                    self.db.execute('INSERT INTO quiz(patient_id,topic,question,answer,correct,answered_at) VALUES(?,?,?,?,?,?)',
                        (i,'adherence','Synthetic demo quiz','A' if j>0 else 'B',1 if j>0 else 0,days_ago(20-j*7)))
        self.db.execute('INSERT INTO messages(patient_id,direction,body,created_at,intent,confidence,status) VALUES(?,?,?,?,?,?,?)',
            (11,'inbound','My chest hurts and I cannot breathe',now(),'symptoms',0.6,'review_required'))
        self.create_review(11,'symptoms','urgent','Reported potentially serious symptoms','Immediate human assessment required')

    def create_review(self,patient_id,kind,urgency,rationale,suggestion):
        return self.db.execute('INSERT INTO review(patient_id,kind,urgency,rationale,ai_suggestion,status,created_at) VALUES(?,?,?,?,?,?,?)',
            (patient_id,kind,urgency,rationale,suggestion,'open',now()))

    def patient(self,pid):
        row=self.db.one('SELECT * FROM patients WHERE id=?',(pid,))
        if not row: raise ValueError('Patient not found')
        row['vitals']=self.db.all('SELECT * FROM vitals WHERE patient_id=? ORDER BY recorded_at ASC, id ASC',(pid,))
        row['adherence']=self.db.all('SELECT * FROM adherence WHERE patient_id=? ORDER BY recorded_at DESC',(pid,))
        row['messages']=self.db.all('SELECT * FROM messages WHERE patient_id=? ORDER BY id DESC LIMIT 40',(pid,))
        row['quizzes']=self.db.all('SELECT * FROM quiz WHERE patient_id=? ORDER BY id DESC',(pid,))
        row['assessment']=self.assess(pid)
        pain=self.db.one('SELECT * FROM pain_intakes WHERE patient_id=? ORDER BY id DESC LIMIT 1',(pid,))
        if pain:
            import json
            pain['details']=json.loads(pain['details'])
        row['pain_history']=pain
        row['sms_urgency']=self.db.one('SELECT * FROM urgency_predictions WHERE patient_id=? ORDER BY message_id DESC LIMIT 1',(pid,))
        return row

    def assess(self,pid):
        v=self.db.all('SELECT sbp,dbp,recorded_at FROM vitals WHERE patient_id=? ORDER BY recorded_at DESC, id DESC LIMIT 12',(pid,))
        a=self.db.all('SELECT taken FROM adherence WHERE patient_id=? ORDER BY recorded_at DESC, id DESC LIMIT 7',(pid,))
        msgs=self.db.all("SELECT body FROM messages WHERE patient_id=? AND direction='inbound' ORDER BY id DESC LIMIT 4",(pid,))
        qs=self.db.all('SELECT correct FROM quiz WHERE patient_id=? ORDER BY id DESC LIMIT 5',(pid,))
        flags=[]; priority=0; z=None
        if not v:
            flags.append('No BP history — assessment requires worker review');priority=max(priority,1)
        else:
            current=v[0]
            if current['sbp']>=180 or current['dbp']>=120:
                flags.append('Severely elevated BP reading (demo threshold): repeat measurement and promptly consult clinic protocol');priority=2
            elif current['sbp']>=160 or current['dbp']>=100:
                flags.append('Elevated BP above demo review threshold');priority=max(priority,1)
            elif current['sbp']>=140 or current['dbp']>=90:
                flags.append('Above demonstration follow-up target');priority=max(priority,1)
            hist=v[1:]
            if len(hist)>=5:
                mean=statistics.mean(row['sbp'] for row in hist)
                std=statistics.stdev(row['sbp'] for row in hist)
                if std>=3:
                    z=(current['sbp']-mean)/std
                    if z>2.5:
                        flags.append('Unusual systolic BP increase relative to historical variation');priority=max(priority,1)
                else:
                    flags.append('Historical BP varies too little to calculate a meaningful z-score')
            else: flags.append('Insufficient BP readings for a personal z-score')
        danger=[]
        for row in msgs:
            danger.extend(tokens_present(row['body']))
        if danger:
            priority=2;flags.insert(0,'Possible urgent symptom terms in SMS — immediate human assessment; text recognition is incomplete')
        if self.db.one("SELECT id FROM review WHERE patient_id=? AND status='open' AND urgency='urgent' LIMIT 1",(pid,)):
            priority=2;flags.insert(0,'Open urgent human-review task; urgency remains until reviewed')
        adherence=(sum(row['taken'] for row in a)/len(a)) if a else None
        if adherence is not None and adherence<.8:
            flags.append('Reported medication adherence below 80% in the latest records');priority=max(priority,1)
        if qs and sum(q['correct'] for q in qs)/len(qs)<0.6:
            flags.append('Recent quiz responses indicate an educational gap; not a clinical urgency score')
        return {'priority':['routine','followup','urgent'][priority], 'flags':flags,
            'current_sbp':v[0]['sbp'] if v else None,'current_dbp':v[0]['dbp'] if v else None,
            'systolic_z':round(z,2) if z is not None else None,
            'adherence_fraction':round(adherence,2) if adherence is not None else None,
            'quiz_fraction':round(sum(q['correct'] for q in qs)/len(qs),2) if qs else None,
            'danger_terms':list(dict.fromkeys(danger)), 'statistical_not_clinical':True}

    def patient_page(self, limit=50, offset=0):
        """Bounded patient listing; does not scan the whole patient registry."""
        if type(limit) is not int or type(offset) is not int or not 1 <= limit <= 100 or offset < 0:
            raise ValueError('limit must be 1–100 and offset must be nonnegative')
        total = self.db.one('SELECT COUNT(*) AS n FROM patients')['n']
        rows = self.db.all('SELECT * FROM patients ORDER BY id LIMIT ? OFFSET ?', (limit, offset))
        for patient in rows:
            patient['assessment'] = self.assess(patient['id'])
        return {'patients': rows, 'total': total, 'limit': limit, 'offset': offset}

    def all_patients(self):
        rows=self.db.all('SELECT * FROM patients ORDER BY id')
        for p in rows: p['assessment']=self.assess(p['id'])
        priority={'urgent':0,'followup':1,'routine':2}
        return sorted(rows,key=lambda p:(priority[p['assessment']['priority']],p['id']))

    def add_vitals(self,pid,sbp,dbp,notes=''):
        if not 50<=sbp<=300 or not 30<=dbp<=200:
            raise ValueError('BP value is out of accepted demo entry bounds; confirm units and reread')
        self.patient(pid)
        self.db.execute('INSERT INTO vitals(patient_id,recorded_at,sbp,dbp,note) VALUES(?,?,?,?,?)',(pid,now(),sbp,dbp,notes))
        a=self.assess(pid)
        if a['priority']!='routine':
            self.create_review(pid,'vital_signs',a['priority'],"; ".join(a['flags']),"Human review under clinic-approved workflow")
        self.audit('vital_added',f'Synthetic patient {pid}; {sbp}/{dbp}')
        return a

    def receive_sms(self,pid,body):
        self.patient(pid)
        if not body.strip(): raise ValueError('Message cannot be empty')
        if len(body)>500: raise ValueError('Maximum length is 500 characters')
        # An exact A/B reply to a previously delivered SMS quiz is interpreted by the
        # fixed quiz rule, NOT by the AI text model. Only one answer per outbound quiz.
        normalized=body.strip().lower()
        quiz_sms=self.db.one("SELECT id,created_at FROM messages WHERE patient_id=? AND direction='outbound' AND intent='quiz' ORDER BY id DESC LIMIT 1",(pid,))
        pending_quiz=bool(quiz_sms and quiz_sms['created_at']>=days_ago(14) and
            not self.db.one("SELECT id FROM messages WHERE patient_id=? AND direction='inbound' AND id>? AND lower(trim(body)) IN ('a','b') LIMIT 1",(pid,quiz_sms['id'])))
        if normalized in ('a','b') and pending_quiz:
            response=self.quiz_answer(pid,'adherence',normalized)
            pred={'label':'quiz_answer','proposed_label':'quiz_answer','confidence':1.0,
                  'margin':1.0,'needs_human':False,'quiz_correct':response['correct']}
        else:
            pred=self.ai.predict(body)
        is_danger=bool(tokens_present(body))
        status='review_required' if is_danger or pred['needs_human'] or pred['label'] in ('symptoms','side_effect') else 'classification_proposed'
        msg_id=self.db.execute('INSERT INTO messages(patient_id,direction,body,created_at,intent,confidence,status) VALUES(?,?,?,?,?,?,?)',
            (pid,'inbound',body,now(),pred['proposed_label'],pred['confidence'],status))
        # Only deterministic exact replies become adherence records; AI guesses never directly alter adherence.
        lower=body.strip().lower()
        yes={'yes','y','1','ndiyo','nimechukua dawa','nimetumia dawa zangu'}
        no={'no','n','2','hapana','sikutumia dawa','sijameza dawa leo'}
        if lower in yes | no:
            self.db.execute('INSERT INTO adherence(patient_id,recorded_at,taken,source) VALUES(?,?,?,?)',
                (pid,now(),1 if lower in yes else 0,'Patient-reported exact SMS reply'))
        if is_danger:
            self.create_review(pid,'symptoms','urgent','Message contains potentially serious symptom phrase; needs immediate human assessment',
                'Review immediately per clinic safety protocol; do not rely on automated diagnosis')
        elif pred['needs_human'] or pred['label'] in ('symptoms','side_effect'):
            self.create_review(pid,'ambiguous_sms','followup',f'Message requires review; candidate intent {pred["proposed_label"]}, p={pred["confidence"]}',
                'Confirm interpretation and decide next steps')
        elif pred['label'] in ('missed','cost','access'):
            self.create_review(pid,'adherence_barrier','followup',f'Potential adherence barrier: {pred["label"]}; requires confirmation',
                'Check patient circumstances and follow approved support workflow')
        self.audit('sms_received',f'Synthetic patient {pid}; classified as {pred["proposed_label"]}')
        return {'message_id':msg_id,**pred,'danger_flag':is_danger,
                'patient_auto_ack':'Thank you. Your care team will review your message. If you have sudden or severe symptoms, seek urgent in-person medical help rather than waiting for SMS.'}

    def reviews(self):
        return self.db.all("SELECT r.*,p.name FROM review r JOIN patients p ON p.id=r.patient_id ORDER BY CASE r.urgency WHEN 'urgent' THEN 0 WHEN 'followup' THEN 1 ELSE 2 END, r.id DESC")

    def resolve_review(self,rid,reviewer,outcome):
        if not reviewer.strip() or not outcome.strip(): raise ValueError('Reviewer and decision required')
        if not self.db.one('SELECT * FROM review WHERE id=?',(rid,)): raise ValueError('Review not found')
        self.db.execute("UPDATE review SET status='resolved',reviewer=?,outcome=? WHERE id=?",(reviewer,outcome,rid))
        self.audit('human_review',f'Review {rid}: {reviewer}: {outcome}')

    def add_feedback(self,message_id,corrected,reviewer):
        if corrected not in TRAIN: raise ValueError('Unrecognized category')
        m=self.db.one("SELECT * FROM messages WHERE id=? AND direction='inbound'",(message_id,))
        if not m: raise ValueError('Incoming message not found')
        if not reviewer.strip(): raise ValueError('Reviewer name needed')
        self.db.execute("INSERT INTO feedback(message_id,original_label,corrected_label,approved,reviewer,created_at) VALUES(?,?,?,?,?,?) ON CONFLICT(message_id) DO UPDATE SET corrected_label=excluded.corrected_label,approved=1,reviewer=excluded.reviewer,created_at=excluded.created_at",
            (message_id,m['intent'],corrected,1,reviewer,now()))
        self.audit('correction_approved',f'Message {message_id} corrected by {reviewer}')

    def message_templates(self,lang):
        return {key:val[lang] for key,val in TEMPLATES.items()}

    def schedule(self,pid,cadence,categories,days=30):
        p=self.patient(pid)
        if cadence not in ('daily','weekly','monthly'): raise ValueError('Cadence must be daily/weekly/monthly')
        if not categories or any(cat not in TEMPLATES for cat in categories): raise ValueError('Unknown or empty SMS categories')
        if not 1 <= days <= 90: raise ValueError('Days must be 1–90')
        delta={'daily':1,'weekly':7,'monthly':30}[cadence]
        start=datetime.now(timezone.utc)
        rows=[]
        for day in range(0,days,delta):
            due=(start+timedelta(days=day)).replace(microsecond=0).isoformat()
            for cat in categories:
                rows.append((pid,cat,cadence,TEMPLATES[cat][p['language']],due,'pending_approval'))
        # One transaction + one encrypted snapshot for the complete schedule.
        # The original human-approval policy remains unchanged.
        with self.db.batch():
            self.db.many("INSERT INTO outbox (patient_id,category,cadence,body,due_at,status) VALUES(?,?,?,?,?,?)",rows)
            self.audit('scheduled',f'Patient {pid}: {len(rows)} messages scheduled for review')
        return len(rows)

    def outbox(self):
        return self.db.all('SELECT o.*,p.name,p.language FROM outbox o JOIN patients p ON p.id=o.patient_id ORDER BY o.due_at, o.id DESC LIMIT 400')

    def approve_outbox(self,msg_id,reviewer):
        if not reviewer.strip():raise ValueError('Approver required')
        if not self.db.one('SELECT * FROM outbox WHERE id=?',(msg_id,)):raise ValueError('Message not found')
        self.db.execute("UPDATE outbox SET status='queued_offline',approved_by=? WHERE id=? AND status='pending_approval'",(reviewer,msg_id))
        self.audit('sms_approved',f'Message {msg_id} approved by {reviewer}')

    def simulate_delivery(self,msg_id):
        msg=self.db.one("SELECT * FROM outbox WHERE id=?",(msg_id,))
        if not msg or msg['status']!='queued_offline':
            raise ValueError('Message must be approved and queued first')
        if msg['due_at']>now():raise ValueError('Message is not due yet')
        self.db.execute("UPDATE outbox SET status='simulated_delivered',delivered_at=? WHERE id=?",(now(),msg_id))
        self.db.execute('INSERT INTO messages(patient_id,direction,body,created_at,intent,confidence,status) VALUES(?,?,?,?,?,?,?)',
            (msg['patient_id'],'outbound',msg['body'],now(),msg['category'],None,'simulated_only'))
        self.audit('sms_simulated',f'Fake delivery only, message {msg_id}')

    def quiz_question(self,pid,topic):
        p=self.patient(pid)
        if topic not in QUIZ_BANK:raise ValueError('Unknown topic')
        return {'topic':topic,'question':QUIZ_BANK[topic][p['language']][0],'options':['A','B'],'language':p['language']}

    def quiz_answer(self,pid,topic,answer):
        q=self.quiz_question(pid,topic)
        gold=QUIZ_BANK[topic][q['language']][1]
        a=answer.strip().upper()
        if a not in ('A','B'): raise ValueError('Answer A or B')
        correct=int(a==gold)
        self.db.execute('INSERT INTO quiz(patient_id,topic,question,answer,correct,answered_at) VALUES(?,?,?,?,?,?)',
            (pid,topic,q['question'],a,correct,now()))
        return {'correct':bool(correct),'note':'Thank you. A CHW can review this education topic with you.' if not correct else 'Thank you. Your answer was recorded.'}

    def dashboard(self):
        """Batched summary. Reads aggregate cohorts instead of 4+ queries per patient.

        The demo thresholds are unchanged. This is still not a validated triage system.
        Snapshot reads can change between queries if an agent writes concurrently.
        """
        total = self.db.one('SELECT COUNT(*) AS n FROM patients')['n']
        open_reviews = self.db.one("SELECT COUNT(*) AS n FROM review WHERE status='open'")['n']
        inbound = self.db.one("SELECT COUNT(*) AS n FROM messages WHERE direction='inbound'")['n']

        # Current pressure is newest by recorded_at/id, as in assess().
        # First/last visit use recorded_at; id breaks otherwise unspecified ties.
        vitals = self.db.all("""
            WITH ranked AS (
              SELECT patient_id, sbp, dbp,
                ROW_NUMBER() OVER (PARTITION BY patient_id ORDER BY recorded_at DESC, id DESC) AS rn,
                ROW_NUMBER() OVER (PARTITION BY patient_id ORDER BY recorded_at ASC, id ASC) AS oldest_rn,
                COUNT(*) OVER (PARTITION BY patient_id) AS total_n
              FROM vitals
            )
            SELECT patient_id,
              MAX(CASE WHEN rn=1 THEN sbp END) AS sbp,
              MAX(CASE WHEN rn=1 THEN dbp END) AS dbp,
              MAX(CASE WHEN oldest_rn=1 AND total_n>1 THEN sbp END) AS baseline,
              MAX(CASE WHEN rn=1 AND total_n>1 THEN sbp END) AS recent,
              COUNT(CASE WHEN rn BETWEEN 2 AND 12 THEN 1 END) AS hist_n,
              SUM(CASE WHEN rn BETWEEN 2 AND 12 THEN sbp ELSE 0 END) AS hist_sum,
              SUM(CASE WHEN rn BETWEEN 2 AND 12 THEN sbp*sbp ELSE 0 END) AS hist_sq
            FROM ranked GROUP BY patient_id
        """)
        # Adherence and quiz fractions use the same most-recent sample sizes as assess().
        adherences = self.db.all("""
            WITH ranked AS (
              SELECT patient_id,taken,
                ROW_NUMBER() OVER(PARTITION BY patient_id ORDER BY recorded_at DESC,id DESC) rn
              FROM adherence
            )
            SELECT patient_id,AVG(taken*1.0) AS rate
            FROM ranked WHERE rn<=7 GROUP BY patient_id
        """)
        quizzes = self.db.all("""
            WITH ranked AS (
              SELECT patient_id,correct,
                ROW_NUMBER() OVER(PARTITION BY patient_id ORDER BY id DESC) rn
              FROM quiz
            )
            SELECT patient_id,AVG(correct*1.0) AS rate
            FROM ranked WHERE rn<=5 GROUP BY patient_id
        """)
        # Do not use model classifications for danger detection; preserve exact
        # phrase matching in the original assess() implementation.
        recent_sms = self.db.all("""
            WITH ranked AS (
              SELECT patient_id,body,
                ROW_NUMBER() OVER(PARTITION BY patient_id ORDER BY id DESC) rn
              FROM messages WHERE direction='inbound'
            )
            SELECT patient_id,body FROM ranked WHERE rn<=4
        """)

        # Each dictionary has at most one entry per patient (not per encounter).
        adherence_by = {r['patient_id']:r['rate'] for r in adherences}
        quiz_by = {r['patient_id']:r['rate'] for r in quizzes}
        vitals_by = {r['patient_id']:r for r in vitals}
        danger_ids = {r['patient_id'] for r in recent_sms if tokens_present(r['body'] or '')}
        danger_ids.update(r['patient_id'] for r in self.db.all("SELECT DISTINCT patient_id FROM review WHERE status='open' AND urgency='urgent'"))
        urgent = followup = 0
        baseline = []
        recent = []
        adherence_rates = []
        quiz_rates = []
        # Existing patients may have arbitrary IDs; use one bounded scan.
        for item in self.db.all('SELECT id FROM patients'):
            pid = item['id']
            v = vitals_by.get(pid)
            a = adherence_by.get(pid)
            q = quiz_by.get(pid)
            severity = 0
            if v is None:
                severity = 1
            else:
                sbp,dbp = v['sbp'],v['dbp']
                if sbp>=180 or dbp>=120:
                    severity=2
                elif sbp>=140 or dbp>=90:
                    severity=1
                n=v['hist_n']
                if n>=5:
                    mean=v['hist_sum']/n
                    variance=(v['hist_sq']-v['hist_sum']**2/n)/(n-1)
                    if variance>=9:  # matches assess() stdev >= 3
                        if (sbp-mean)/math.sqrt(variance)>2.5:
                            severity=max(severity,1)
                if v['baseline'] is not None:
                    baseline.append(v['baseline'])
                    recent.append(v['recent'])
            if pid in danger_ids:
                severity=2
            if a is not None:
                adherence_rates.append(round(a,2))  # assess() rounds before dashboard mean
                if a<0.8: severity=max(severity,1)
            if q is not None:
                quiz_rates.append(round(q,2))
            if severity==2: urgent+=1
            elif severity==1: followup+=1
        return {
            'patients':total, 'urgent':urgent,'followup':followup,
            'open_reviews':open_reviews,
            'adherence_percent':round(100*statistics.mean(adherence_rates),1) if adherence_rates else None,
            'quiz_percent':round(100*statistics.mean(quiz_rates),1) if quiz_rates else None,
            'inbound_sms':inbound,
            'baseline_mean_sbp':round(statistics.mean(baseline),1) if baseline else None,
            'latest_mean_sbp':round(statistics.mean(recent),1) if recent else None,
            'model_metrics':self.ai.metrics(), 'model_version':self.ai.version,
            'stroke_rate':'Not measurable: no clinically verified stroke events or follow-up denominator. Requires prospective study.',
            'demo_data':True
        }
