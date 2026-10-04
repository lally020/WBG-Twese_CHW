"""Offline, fictional-patient, policy-bounded adaptive SMS orchestration."""
from datetime import datetime, timezone, timedelta
from threading import RLock
import re
import json
from pain_history import mentions_pain, screen_flags, extract_explicit, next_field, summary, QUESTIONS
from local_llm import LocalConversation
from urgency_ai import UrgencyClassifier
from core import TEMPLATES, now, tokens_present

REASONS = {
    'English': 'Thanks for telling us. What made it difficult? Reply 1 FORGOT, 2 SIDE EFFECTS, 3 RAN OUT / COST, or 4 OTHER. Do not change a dose based on this message.',
    'Kiswahili': 'Asante kwa kutujulisha. Sababu ni ipi? Jibu 1 UMESAHAU, 2 MADHARA YA DAWA, 3 DAWA ZIMEISHA / GHARAMA, au 4 NYINGINE. Usibadilishe dozi kutokana na ujumbe huu.'
}
ACK = {
    'English': 'Thank you. Your response has been recorded. If you need urgent medical help, seek in-person care; SMS is not an emergency service.',
    'Kiswahili': 'Asante. Jibu lako limehifadhiwa. Kwa dharura tafuta huduma ya afya ana kwa ana; SMS si huduma ya dharura.'
}
HELP = {
    'English': 'Thanks. I have requested a community health worker to review this difficulty. Please do not wait for a text response if you need urgent care.',
    'Kiswahili': 'Asante. Nimeomba mhudumu wa afya ya jamii apitie tatizo hili. Usisubiri SMS ikiwa unahitaji huduma ya dharura.'
}

class FollowupAgent:
    def __init__(self, compass):
        self.C = compass
        self.lock = RLock()
        self.conversation_model = LocalConversation()
        self.urgency_model = UrgencyClassifier()
        self.C.db.script('''
          CREATE TABLE IF NOT EXISTS agent_enrollment(
            patient_id INTEGER PRIMARY KEY, consent INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 0, cadence TEXT NOT NULL DEFAULT 'daily',
            last_run TEXT, next_due TEXT, policy_version TEXT NOT NULL DEFAULT 'demo-v2');
          CREATE INDEX IF NOT EXISTS idx_agent_due ON agent_enrollment(active,consent,next_due,patient_id);
          CREATE TABLE IF NOT EXISTS agent_events(
            id INTEGER PRIMARY KEY, patient_id INTEGER, kind TEXT, detail TEXT, created_at TEXT);
          CREATE TABLE IF NOT EXISTS agent_conversations(
            patient_id INTEGER PRIMARY KEY, state TEXT NOT NULL DEFAULT 'idle',
            last_question_at TEXT, last_action TEXT, updated_at TEXT);
        ''')

    def event(self, pid, kind, detail):
        self.C.db.execute('INSERT INTO agent_events(patient_id,kind,detail,created_at) VALUES(?,?,?,?)', (pid,kind,detail,now()))

    def _state(self,pid):
        return self.C.db.one('SELECT * FROM agent_conversations WHERE patient_id=?',(pid,)) or {'state':'idle'}

    def _setstate(self,pid,state,action):
        self.C.db.execute('''INSERT INTO agent_conversations(patient_id,state,last_question_at,last_action,updated_at)
          VALUES(?,?,?,?,?) ON CONFLICT(patient_id) DO UPDATE SET state=excluded.state,
          last_question_at=excluded.last_question_at,last_action=excluded.last_action,updated_at=excluded.updated_at''',
          (pid,state,now(),action,now()))

    def _active(self,pid):
        return self.C.db.one('SELECT * FROM agent_enrollment WHERE patient_id=? AND active=1 AND consent=1',(pid,))

    def enroll(self,pid,cadence='daily'):
        if cadence not in ('daily','weekly','monthly'): raise ValueError('Unknown cadence')
        self.C.patient(pid)
        with self.lock:
            self.C.db.execute('''INSERT INTO agent_enrollment(patient_id,consent,active,cadence,next_due,policy_version)
              VALUES(?,1,1,?,?,'demo-v2') ON CONFLICT(patient_id) DO UPDATE SET
              consent=1,active=1,cadence=excluded.cadence,next_due=excluded.next_due,
              policy_version='demo-v2' ''',(pid,cadence,now()))
            self._setstate(pid,'idle','enroll')
            self.event(pid,'enrolled','Fictional demo consent; adaptive workflow enabled')
        return {'enrolled':True,'patient_id':pid,'cadence':cadence,'simulated_only':True}

    def stop(self,pid):
        with self.lock:
            self.C.db.execute('UPDATE agent_enrollment SET active=0,consent=0 WHERE patient_id=?',(pid,))
            self._setstate(pid,'idle','stopped')
            self.event(pid,'stopped','No further automatic outreach')
        return {'enrolled':False,'patient_id':pid}

    def status(self):
        return {'enrollments':self.C.db.all('SELECT * FROM agent_enrollment ORDER BY patient_id'),
          'conversations':self.C.db.all('SELECT * FROM agent_conversations ORDER BY patient_id'),
          'events':self.C.db.all('SELECT * FROM agent_events ORDER BY id DESC LIMIT 60'),
          'conversation_engine':self.conversation_model.status(),
          'simulated_only':True,'policy':'Local conversational replies; separate experimental urgency classification; clinical concerns to human review.'}

    def _send(self,pid,body,category):
        # Do not call this unless _active was checked under self.lock.
        mid=self.C.db.execute('''INSERT INTO outbox(patient_id,category,cadence,body,due_at,status,approved_by)
          VALUES(?,?,?,?,?,?,?)''',(pid,category,'agent',body,now(),'queued_offline','Demo preauthorized policy v2'))
        self.C.simulate_delivery(mid)
        self.event(pid,'agent_sent',f'{category} SMS #{mid}, simulated only')
        return mid

    def _human(self,pid,reason,urgent=False,already_flagged=False):
        if not already_flagged:
            self.C.create_review(pid,'agent_followup','urgent' if urgent else 'followup',reason,
              'Human review needed; do not autonomously modify treatment')
        self._setstate(pid,'awaiting_human','escalated')
        self.event(pid,'human_handoff',reason)
        return {'action':'human_handoff','reason':reason}

    def tick(self, limit=100):
        with self.lock, self.C.db.batch():
            limit = max(1, min(int(limit), 500))
            rows=self.C.db.all('SELECT * FROM agent_enrollment WHERE active=1 AND consent=1 AND next_due<=? ORDER BY next_due,patient_id LIMIT ?',(now(),limit))
            results=[]
            for row in rows:
                pid=row['patient_id']
                due_days={'daily':1,'weekly':7,'monthly':30}[row['cadence']]
                next_due=(datetime.now(timezone.utc)+timedelta(days=due_days)).replace(microsecond=0).isoformat()
                state=self._state(pid)['state']
                if state in ('awaiting_adherence','awaiting_reason','awaiting_quiz','awaiting_human','conversation_open','pain_intake'):
                    if state!='awaiting_human':
                        age=self._state(pid).get('last_question_at') or now()
                        try: stale=(datetime.now(timezone.utc)-datetime.fromisoformat(age)).total_seconds()>48*3600
                        except ValueError: stale=False
                        if stale: self._human(pid,'Patient did not respond to previous SMS within 48 hours')
                    # Never overwrite an unresolved question or escalation.
                    self.C.db.execute('UPDATE agent_enrollment SET next_due=? WHERE patient_id=?',(next_due,pid))
                    results.append({'patient_id':pid,'action':'waiting_for_reply_or_human'})
                    continue
                if self.C.assess(pid)['priority']=='urgent':
                    self._human(pid,'Urgent risk flags; routine outreach suspended',urgent=True)
                    self.C.db.execute('UPDATE agent_enrollment SET next_due=? WHERE patient_id=?',(next_due,pid))
                    results.append({'patient_id':pid,'action':'human_handoff'})
                    continue
                lang=self.C.patient(pid)['language']
                question=TEMPLATES['medication'][lang]
                if self.conversation_model.model:
                    question='How has taking your prescribed medicine been going today? Tell me about any difficulties or questions you have.' if lang=='English' else 'Matumizi ya dawa ulizoandikiwa yanaendeleaje leo? Niambie changamoto au maswali uliyo nayo.'
                msg=self._send(pid,question,'medication')
                self._setstate(pid,'awaiting_adherence','medication_question')
                self.C.db.execute('UPDATE agent_enrollment SET last_run=?,next_due=? WHERE patient_id=?',(now(),next_due,pid))
                results.append({'patient_id':pid,'action':'sent_simulated','message_ids':[msg]})
            return results

    def receive_reply(self, pid, body, transport_id=None):
        """Persist inbound, controller changes and simulated reply in one transaction.

        Phrase rules are a limited offline interpretation aid, not a general LLM.
        Unknown language always receives a visible acknowledgment and human review.
        """
        if not body.strip() or len(body) > 500:
            raise ValueError('Message must contain 1–500 characters')
        cached=self._transport_existing(pid,body,transport_id)
        if cached is not None:return cached
        # Inference runs outside the database transaction; never freeze the store
        # during model startup. Recheck the state before using its suggestion.
        patient_snapshot = self.C.patient(pid)
        state_snapshot = self._state(pid)['state']
        pain_snapshot=self.C.db.one("SELECT * FROM pain_intakes WHERE patient_id=? AND status='active' ORDER BY id DESC LIMIT 1",(pid,))
        pain_related=bool(pain_snapshot or mentions_pain(body) or screen_flags(body))
        urgency_text=(pain_snapshot['initial_message']+' '+body) if pain_snapshot else body
        urgency_result = self.urgency_model.predict(urgency_text,patient_snapshot['language'])
        if pain_related:
            with self.lock, self.C.db.batch():
                cached=self._transport_existing(pid,body,transport_id)
                if cached is not None:return cached
                return self._transport_save(pid,body,transport_id,self._pain_reply(pid,body,urgency_result))

        model_result = None if tokens_present(body) or urgency_result['label']=='urgent' else self.conversation_model.interpret(
            body, state_snapshot, patient_snapshot['language'],
            [{'role':m['direction'],'body':m['body']} for m in reversed(patient_snapshot['messages'][:8])])
        with self.lock, self.C.db.batch():
            cached=self._transport_existing(pid,body,transport_id)
            if cached is not None:return cached
            if model_result and self._state(pid)['state']==state_snapshot:
                return self._transport_save(pid,body,transport_id,self._conversational_reply(pid,body,model_result,urgency_result))
            patient = self.C.patient(pid)
            before = self.C.db.one('SELECT COALESCE(MAX(id),0) AS n FROM messages')['n']
            state = self._state(pid)['state']
            active = bool(self._active(pid))
            text = body.lower().replace("’", "'")
            canonical = body
            kind = None
            # Clinical and access concerns interrupt menus; no treatment advice.
            if re.search(r"dizz|side effect|makes me sick|rash|vomit|faint", text):
                kind = 'side_effect'
            elif re.search(r"ran out|run out|out of (?:medicine|medication|pills)|afford|cost|pharmacy|refill", text) and not re.search(r"food|water|rice",text):
                kind = 'access'
            elif re.search(r"visit me|appointment|someone visit|come tomorrow|reschedule", text):
                kind = 'appointment'
            elif re.search(r"don't understand|do not understand|what do you mean|confused", text):
                kind = 'clarify'
            elif active and state == 'awaiting_adherence':
                if re.search(r"forgot|didn't take|did not take|haven't taken|have not taken|missed|not yet", text):
                    canonical = 'NO'
                elif re.fullmatch(r"(?:yes[,! ]*)?i (?:took|have taken) (?:my |the )?(?:pills|medicine|medication)(?: today| this morning| already)?[.! ]*", text):
                    canonical = 'YES'
            elif active and state == 'awaiting_reason' and re.search(r"forgot|forget", text):
                canonical = '1'
            if state != state_snapshot:
                model_result = None
            if model_result and not kind:
                candidate = model_result['intent']
                if candidate in ('access','side_effect','appointment','clarify','symptoms'):
                    kind = candidate
                elif candidate in ('missed','forgot') and state == 'awaiting_reason':
                    canonical = '1'  # bounded barrier selection; raw text retained
            urgent = bool(tokens_present(body)) or urgency_result['label']=='urgent' or bool(model_result and model_result['urgent'])
            if urgent:
                result = self.C.receive_sms(pid, body)
                result['danger_flag'] = True
                result['agent'] = self._human(pid,'Potential urgent symptoms in patient reply',urgent=True,
                    already_flagged=bool(tokens_present(body)))
                if active:
                    self._send(pid,'Your message may describe an urgent problem. Seek urgent in-person medical help now rather than waiting for SMS. Your care team has been alerted for review.' if patient['language']=='English' else
                        'Ujumbe wako unaweza kuonyesha dharura. Tafuta huduma ya afya sasa; usisubiri SMS. Timu ya afya imearifiwa.', 'agent_ack')
            elif active and kind in ('side_effect', 'access', 'appointment', 'symptoms'):
                result = self.C.receive_sms(pid, body)
                result['agent'] = self._human(pid, {
                    'side_effect':'Patient reports a possible medication side effect; clinical review required',
                    'access':'Patient reports medication supply or affordability barrier',
                    'appointment':'Patient requests a visit or appointment; CHW must confirm arrangements',
                    'symptoms':'Patient reports symptoms; clinical review required'
                }[kind])
                replies = {
                    'access':'I understand you are having trouble getting medicine. I have flagged this for your community health worker. Is it a refill, cost, or transport problem?',
                    'side_effect':'I understand you may be having a side effect. I have flagged it for clinical review. What symptoms are you having, and when did they start?',
                    'appointment':'I have passed your visit request to your community health worker. A visit is not booked yet. What day and time would work for you?',
                    'symptoms':'I have flagged your symptoms for clinical review. Please describe what you are feeling and when it started. For sudden or severe symptoms, seek urgent in-person help.'}
                self._send(pid,replies[kind] if patient['language']=='English' else HELP[patient['language']], 'agent_ack')
            elif (active and model_result and model_result['intent'] in ('taken','missed','forgot')
                    and state == 'awaiting_adherence' and canonical == body
                    and body.strip().lower() not in ('yes','no','1','2','y','n','ndiyo','hapana')):
                result = self.C.receive_sms(pid,body)
                self._send(pid, 'To make sure I understood: did you take your prescribed medicine today? You can reply YES or NO.' if patient['language']=='English' else TEMPLATES['medication']['Kiswahili'],'agent_question')
                self._setstate(pid,'awaiting_adherence','confirming_model_interpretation')
                result['agent'] = {'action':'confirming_adherence'}
            elif active and kind == 'clarify' and state in ('awaiting_adherence','awaiting_reason','awaiting_quiz'):
                prediction = self.C.ai.predict(body)
                mid = self.C.db.execute('INSERT INTO messages(patient_id,direction,body,created_at,intent,confidence,status) VALUES(?,?,?,?,?,?,?)',
                    (pid,'inbound',body,now(),prediction['proposed_label'],prediction['confidence'],'clarification_requested'))
                question = (REASONS[patient['language']] if state == 'awaiting_reason' else
                    TEMPLATES['quiz' if state == 'awaiting_quiz' else 'medication'][patient['language']])
                self._send(pid, question, 'agent_question')
                result = {'message_id':mid, **prediction, 'danger_flag':False, 'agent':{'action':'clarified_question'}}
            else:
                result = self._receive_legacy(pid, canonical)
                if canonical != body:
                    self.C.db.execute('UPDATE messages SET body=? WHERE id=?',(body,result['message_id']))
                    result['interpretation_method'] = 'limited_phrase_rule'
            outbound = self.C.db.one("SELECT body FROM messages WHERE patient_id=? AND direction='outbound' AND id>? ORDER BY id DESC LIMIT 1",(pid,before))
            if not outbound:
                if active:
                    reply = HELP[patient['language']] if self._state(pid)['state']=='awaiting_human' else ACK[patient['language']]
                    self._send(pid, reply, 'agent_ack')
                else:
                    # Respond to inbound contact without enrolling for future outreach.
                    reply = HELP[patient['language']]
                    self.C.db.execute('INSERT INTO messages(patient_id,direction,body,created_at,intent,confidence,status) VALUES(?,?,?,?,?,?,?)',
                        (pid,'outbound',reply,now(),'agent_ack',1.0,'simulated_only'))
                outbound = {'body':reply}
            # Flexible model wording is limited to nonclinical clarification and
            # greetings. Clinical actions and messages remain controller-owned.
            if (model_result and model_result['intent'] in ('greeting','other')
                    and not urgent and kind is None
                    and self._state(pid)['state'] != 'awaiting_quiz'
                    and not re.search(r'\b(dose|mg|tablet|pill|medicine|medication|diagnos|treat|take|stop|start|safe)\w*\b',model_result['reply'],re.I)):
                latest = self.C.db.one("SELECT id FROM messages WHERE patient_id=? AND direction='outbound' AND id>? ORDER BY id DESC LIMIT 1",(pid,before))
                self.C.db.execute('UPDATE messages SET body=? WHERE id=?',(model_result['reply'],latest['id']))
                outbox = self.C.db.one("SELECT id FROM outbox WHERE patient_id=? AND category='agent_ack' ORDER BY id DESC LIMIT 1",(pid,))
                if active and outbox:self.C.db.execute('UPDATE outbox SET body=? WHERE id=?',(model_result['reply'],outbox['id']))
                outbound['body'] = model_result['reply']
            if (model_result and model_result['needs_review'] and self._state(pid)['state']!='awaiting_human'
                    and (result.get('agent') or {}).get('action')!='confirming_adherence'):
                self._human(pid,'Local model interpretation requires human confirmation')
            result['conversation_engine'] = 'local_model' if model_result else 'rules_fallback'
            result['model_status'] = self.conversation_model.status()
            result['model_intent'] = model_result['intent'] if model_result else None
            result['patient_auto_ack'] = outbound['body']
            result['reply_persisted'] = True
            self._record_urgency(pid,result['message_id'],urgency_result)
            result['sms_urgency'] = urgency_result
            result['needs_human'] = bool(result.get('needs_human') or result['danger_flag'] or urgency_result['needs_review'])
            if urgency_result['label']=='uncertain' and self._state(pid)['state']!='awaiting_human':
                self.C.create_review(pid,'sms_urgency','followup','Experimental urgency classifier abstained; review message','Human assessment required')
            return self._transport_save(pid,body,transport_id,result)

    def _transport_existing(self,pid,body,transport_id):
        if not transport_id:return None
        row=self.C.db.one('SELECT * FROM transport_receipts WHERE transport_id=?',(transport_id,))
        if row:
            if row['patient_id']!=pid or row['body']!=body:
                raise ValueError('Transport ID already used for a different message')
            return json.loads(row['result'])
        return None

    def _transport_save(self,pid,body,transport_id,result):
        if transport_id:
            self.C.db.execute('INSERT INTO transport_receipts VALUES(?,?,?,?)',
                (transport_id,pid,body,json.dumps(result)))
        return result

    def _pain_reply(self,pid,body,urgency):
        patient=self.C.patient(pid)
        lang=patient['language']
        intake=self.C.db.one("SELECT * FROM pain_intakes WHERE patient_id=? AND status='active' ORDER BY id DESC LIMIT 1",(pid,))
        mid=self.C.db.execute('INSERT INTO messages(patient_id,direction,body,created_at,intent,confidence,status) VALUES(?,?,?,?,?,?,?)',
            (pid,'inbound',body,now(),'pain_history',0.0,'structured_intake'))
        self._record_urgency(pid,mid,urgency)
        context=(intake['initial_message']+' '+body) if intake else body
        urgent=bool(tokens_present(context) or screen_flags(context) or urgency['label']=='urgent' or patient['assessment']['priority']=='urgent')
        if urgent:
            if intake:self.C.db.execute("UPDATE pain_intakes SET status='interrupted_urgent',updated_at=? WHERE id=?",(now(),intake['id']))
            action=self._human(pid,'Pain history interrupted: potential urgent concern or unresolved urgent patient flag',urgent=True)
            reply='Your pain or another unresolved alert needs prompt human assessment. Seek urgent in-person medical help rather than waiting for SMS. I have stopped the pain questions and recorded an urgent review task.' if lang=='English' else 'Maumivu au tahadhari nyingine yanahitaji ukaguzi wa haraka. Tafuta huduma ya afya ana kwa ana; usisubiri SMS. Maswali yamesimamishwa na ombi la haraka limehifadhiwa.'
        elif intake and body.strip().lower() in ('stop','cancel','acha'):
            self.C.db.execute("UPDATE pain_intakes SET status='cancelled',updated_at=? WHERE id=?",(now(),intake['id']))
            self._setstate(pid,'awaiting_human','pain_intake_cancelled')
            action={'action':'pain_intake_cancelled'}
            reply='I have stopped the questions. Your care team can review the information already recorded.' if lang=='English' else 'Nimesimamisha maswali. Timu yako inaweza kupitia taarifa zilizohifadhiwa.'
        else:
            details=json.loads(intake['details']) if intake else {}
            if not intake:
                details.update(extract_explicit(body))
                rid=self.C.db.execute('INSERT INTO review(patient_id,kind,urgency,rationale,ai_suggestion,created_at) VALUES(?,?,?,?,?,?)',
                    (pid,'pain_history','followup','Pain history in progress; urgency has not been excluded','Human assessment required; no automated diagnosis',now()))
                iid=self.C.db.execute('INSERT INTO pain_intakes(patient_id,status,details,initial_message,review_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                    (pid,'active',json.dumps(details),body,rid,now(),now()))
                intake={'id':iid,'pending_field':None,'review_id':rid}
            else:
                pending=intake['pending_field']
                # Store answers verbatim under the question asked; no diagnosis.
                if pending=='severity':
                    number=re.fullmatch(r'\s*(10|[0-9])(?:\s*(?:/\s*10|out of 10))?\s*',body,re.I)
                    if number:details[pending]=number.group(1)+'/10'
                    elif body.strip().lower() in ("i don't know","not sure","skip"):details[pending]='Not provided (patient declined/unsure)'
                elif body.strip().lower() in ("i don't understand","what do you mean","explain"):
                    pass
                elif pending:
                    details[pending]=body
                    for key,value in extract_explicit(body).items():details.setdefault(key,value)
            pending=next_field(details)
            completed=pending is None
            self.C.db.execute('UPDATE pain_intakes SET details=?,pending_field=?,status=?,updated_at=? WHERE id=?',
                (json.dumps(details),pending,'complete' if completed else 'active',now(),intake['id']))
            self.C.db.execute('UPDATE review SET rationale=? WHERE id=?',
                ('Patient-reported pain history ('+('complete' if completed else 'in progress')+'); clinical interpretation required.\n'+summary(details),intake['review_id']))
            if completed:
                self._setstate(pid,'awaiting_human','pain_history_complete')
                action={'action':'pain_history_complete'}
                reply='Thank you. I have saved your pain history for your care team to review. This does not establish that the pain is safe or provide a diagnosis.' if lang=='English' else 'Asante. Historia ya maumivu imehifadhiwa kwa ukaguzi. Hii si utambuzi wala uthibitisho kuwa hakuna hatari.'
            else:
                self._setstate(pid,'pain_intake','pain_'+pending)
                action={'action':'pain_question','field':pending}
                prefix=('I can collect a pain history for your care team; this does not rule out an urgent problem. ' if lang=='English' else 'Naweza kukusanya historia kwa timu yako; hii haiondoi uwezekano wa dharura. ') if intake['pending_field'] is None else ''
                reply=prefix+QUESTIONS[lang][pending]
        if self._active(pid):self._send(pid,reply,'agent_question' if action['action']=='pain_question' else 'agent_ack')
        else:self.C.db.execute('INSERT INTO messages(patient_id,direction,body,created_at,intent,confidence,status) VALUES(?,?,?,?,?,?,?)',
            (pid,'outbound',reply,now(),'pain_history',1.0,'simulated_only'))
        self.event(pid,'pain_history',action['action'])
        return {'message_id':mid,'label':'pain_history','proposed_label':'pain_history','confidence':0.0,
            'needs_human':True,'danger_flag':urgent,'agent':action,'sms_urgency':urgency,
            'conversation_engine':'structured_pain_intake','response_source':'pain_history_controller',
            'model_status':self.conversation_model.status(),'patient_auto_ack':reply,'reply_persisted':True,
            'pain_history':self.C.patient(pid)['pain_history']}

    def _record_urgency(self,pid,mid,prediction):
        self.C.db.execute('INSERT INTO urgency_predictions VALUES(?,?,?,?,?,?)',
            (mid,pid,prediction['label'],prediction['urgent_score'],prediction['version'],now()))

    def _conversational_reply(self,pid,body,model,urgency):
        """Called within the reply transaction. Conversation and triage are separate."""
        prior_state=self._state(pid)['state']
        patient=self.C.patient(pid)
        intent_prediction=self.C.ai.predict(body)
        mid=self.C.db.execute('INSERT INTO messages(patient_id,direction,body,created_at,intent,confidence,status) VALUES(?,?,?,?,?,?,?)',
            (pid,'inbound',body,now(),model['intent'],intent_prediction['confidence'],'conversational'))
        self._record_urgency(pid,mid,urgency)
        urgent=bool(tokens_present(body)) or urgency['label']=='urgent' or model['urgent']
        existing_urgent=self.C.db.one("SELECT id FROM review WHERE patient_id=? AND status='open' AND urgency='urgent' LIMIT 1",(pid,))
        needs_review=(urgent or urgency['needs_review'] or model['needs_review'] or
            model['intent'] in ('symptoms','side_effect','access','appointment'))
        action={'action':'conversational_reply'}
        if needs_review:
            action=self._human(pid,'Open-ended SMS: '+model['intent']+'; classifier '+urgency['label'],urgent=urgent)
        elif self._state(pid)['state']!='awaiting_human':
            self._setstate(pid,'conversation_open','open_ended_reply')
        # Open-ended conversational text does not directly change adherence records.
        reply=model['reply']
        response_source='local_model'
        # Supplemental output checks are not a guarantee of clinical safety.
        prohibited=re.search(r'\b\d+(?:\.\d+)?\s*(?:mg|mcg|ml)\b|\b(?:take|stop|start|increase|decrease|double|skip)\b.{0,40}\b(?:dose|medicine|medication|tablet|pill)\b|\b(?:booked|scheduled|diagnosed|you are safe|nothing to worry)\b',reply,re.I)
        if prohibited:
            reply='I can help collect information for your care team. Could you tell me more about what you need help with?' if patient['language']=='English' else 'Naweza kusaidia kukusanya taarifa kwa timu yako ya afya. Unahitaji msaada gani?'
            response_source='guarded_fallback'
            if not needs_review:action=self._human(pid,'Generated response withheld for human review')
        if urgent or existing_urgent:
            reply='Your message or an unresolved alert may need urgent attention. Seek urgent in-person medical help rather than waiting for SMS. Your care team has a review task.' if patient['language']=='English' else 'Huenda unahitaji huduma ya haraka. Tafuta huduma ya afya ana kwa ana; usisubiri SMS. Timu yako ina ombi la ukaguzi.'
            response_source='urgent_controller'
        elif needs_review:
            reply += ' A review task has been recorded for your care team.' if patient['language']=='English' else ' Ombi la ukaguzi limehifadhiwa kwa timu yako ya afya.'
        if self._active(pid):self._send(pid,reply,'agent_ack')
        else:
            self.C.db.execute('INSERT INTO messages(patient_id,direction,body,created_at,intent,confidence,status) VALUES(?,?,?,?,?,?,?)',
                (pid,'outbound',reply,now(),'agent_ack',1.0,'simulated_only'))
        self.event(pid,'conversational_reply',f"intent={model['intent']}; urgency={urgency['label']}; source={response_source}")
        return {'message_id':mid,**intent_prediction,'model_intent':model['intent'],
            'danger_flag':bool(urgent or existing_urgent),'needs_human':bool(needs_review or existing_urgent),
            'agent':action,'patient_auto_ack':reply,'reply_persisted':True,
            'sms_urgency':urgency,'conversation_engine':'local_model','response_source':response_source,
            'model_status':self.conversation_model.status()}

    def _receive_legacy(self,pid,body):
        """Single entry point used by FastAPI: protect numbered menus from core's 1/2 adherence parser."""
        with self.lock:
            if not self._active(pid):
                result=self.C.receive_sms(pid,body)
                return {**result,'agent':None}
            state=self._state(pid)['state']
            answer=body.strip().lower()
            # Danger signs override any conversation state or model interpretation.
            if tokens_present(body):
                result=self.C.receive_sms(pid,body)
                action=self._human(pid,'Potential danger phrase in incoming SMS; immediate human assessment',urgent=True,already_flagged=True)
                return {**result,'agent':action}
            if state=='awaiting_reason':
                # Keep a raw inbound audit without treating '1' as YES or '2' as NO.
                prediction=self.C.ai.predict(body)
                message_id=self.C.db.execute('''INSERT INTO messages(patient_id,direction,body,created_at,intent,confidence,status)
                  VALUES(?,?,?,?,?,?,?)''',(pid,'inbound',body,now(),prediction['proposed_label'],prediction['confidence'],'agent_reason'))
                mapped={'1':'forgot','2':'side_effect','3':'access','4':'other'}
                reason=mapped.get(answer)
                if not reason:
                    candidate=prediction.get('label')
                    # AI can suggest a barrier category, but cannot independently finalize safety decisions.
                    if not prediction.get('needs_human') and candidate in ('missed','cost','access','side_effect'):
                        reason={'missed':'forgot','cost':'access','access':'access','side_effect':'side_effect'}[candidate]
                if reason is None or reason in ('other','side_effect'):
                    action=self._human(pid,'Uncertain or potentially clinical medication barrier: '+(reason or 'unclassified'))
                    return {'message_id':message_id,**prediction,'danger_flag':False,'agent':action}
                if reason=='access':
                    action=self._human(pid,'Patient reports medication supply or affordability barrier')
                    self._send(pid,HELP[self.C.patient(pid)['language']],'agent_ack')
                    return {'message_id':message_id,**prediction,'danger_flag':False,'agent':action}
                # Forgot: record issue, reply with neutral acknowledgment, then follow up later.
                self.C.create_review(pid,'missed_medicine','followup','Patient reported forgetting prescribed medication',
                  'Check adherence needs through normal CHW process')
                self._send(pid, 'Thanks for explaining that you forgot. I have asked your community health worker to help with reminders. Would a reminder at a different time help?' if self.C.patient(pid)['language']=='English' else ACK['Kiswahili'],'agent_ack')
                self._setstate(pid,'idle','forgot_recorded')
                self.event(pid,'followup_selected','Forgotten medication; routine CHW task logged')
                return {'message_id':message_id,**prediction,'danger_flag':False,
                        'agent':{'action':'recorded_followup','reason':'forgot'}}
            # All other replies pass through existing vetted core recordkeeping/safety rules.
            result=self.C.receive_sms(pid,body)
            if state=='awaiting_adherence' and answer in ('no','n','2','hapana','sikutumia dawa','sijameza dawa leo'):
                self._send(pid,REASONS[self.C.patient(pid)['language']],'agent_question')
                self._setstate(pid,'awaiting_reason','asked_reason')
                self.event(pid,'adaptive_question','Missed dose reported; asked why')
                action={'action':'asked_reason'}
            elif state=='awaiting_adherence' and answer in ('yes','y','1','ndiyo','nimechukua dawa','nimetumia dawa zangu'):
                self._send(pid,TEMPLATES['quiz'][self.C.patient(pid)['language']],'quiz')
                self._setstate(pid,'awaiting_quiz','asked_quiz')
                action={'action':'asked_quiz'}
            elif state=='awaiting_quiz' and answer in ('a','b'):
                self._send(pid,ACK[self.C.patient(pid)['language']],'agent_ack')
                self._setstate(pid,'idle','quiz_answered')
                self.event(pid,'education_completed','Quiz reply recorded; returning to scheduled routine')
                action={'action':'quiz_complete'}
            elif state=='awaiting_human':
                action={'action':'human_review_pending'}
            else:
                action=self._human(pid,'Unexpected or uncertain response; human review required',
                  already_flagged=bool(result.get('danger_flag')))
            return {**result,'agent':action}

    def on_reply(self,pid,body,result):
        """Legacy API compatibility. New server calls receive_reply directly."""
        return {'action':'use_receive_reply'}
