"""Experimental binary urgency classifier with abstention; synthetic English data.

Not clinically validated. Nonurgent is a text classification, not clearance.
"""
from sklearn.pipeline import make_pipeline
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

URGENT = [
 'I have crushing chest pain and cannot breathe',
 'My face drooped suddenly and one arm is weak',
 'I suddenly cannot speak clearly', 'I collapsed and cannot stay awake',
 'I am bleeding heavily and feel faint', 'My lips are blue and I am struggling to breathe',
 'The worst headache of my life started suddenly', 'I took too many tablets and feel very ill',
 'My throat is swelling and I cannot swallow', 'I want to hurt myself right now',
 'I passed out and now I am confused', 'My chest is tight and I am sweating with pain',
 'I cannot catch my breath while sitting still', 'One side of my body suddenly stopped moving',
 'I am vomiting blood', 'I have severe pain and I am losing consciousness',
 'There is a lot of blood and it will not stop', 'My child is unresponsive',
 'I have had repeated seizures and have not recovered', 'I cannot breathe and feel like I will die',
]
NONURGENT = [
 'I forgot my medicine this morning', 'I ran out of medicine and need a refill',
 'I cannot afford the prescription', 'The bus did not come so I missed the clinic',
 'Can someone visit me next week', 'I took my pills today',
 'I want help remembering my medication', 'What time is my appointment',
 'Please explain the last question', 'Hello how are you',
 'I do not have money for transport', 'The pharmacy is too far away',
 'Can we change my visit to tomorrow', 'I need a reminder in the evening',
 'My prescription bottle is empty', 'Thank you for checking in',
 'I want to talk about healthy meals', 'I missed the reminder because I was at work',
 'My phone battery died yesterday', 'I prefer messages in the morning',
]

class UrgencyClassifier:
    version='synthetic-urgency-v1'
    def __init__(self, spec=None, use_registry=True):
        import os, threading
        from pathlib import Path
        self.lock=threading.RLock();self.registry_error=None;self.loaded_digest=None
        value=os.environ.get('COMPASS_URGENCY_POLICY')
        self.path=None if not use_registry or spec is not None or value=='baseline' else Path(value) if value else Path(__file__).resolve().parent/'evaluation/model_registry/active.json'
        self.spec=spec or self.base_spec()
        self._install(self.spec)
        self._reload()
    @staticmethod
    def base_spec():
        return {'version':'synthetic-urgency-v1','C':1.0,'urgent_threshold':.60,'nonurgent_threshold':.40,'extra':[]}
    def _install(self,spec):
        import math
        if not isinstance(spec.get('version'),str):raise ValueError('Missing policy version')
        for key in ('C','urgent_threshold','nonurgent_threshold'):
            if type(spec.get(key)) not in (int,float) or not math.isfinite(spec[key]):raise ValueError('Invalid policy value')
        if not .1<=spec['C']<=30 or not 0<=spec['nonurgent_threshold']<spec['urgent_threshold']<=1:raise ValueError('Invalid policy bounds')
        extra=spec.get('extra',[])
        if not isinstance(extra,list) or len(extra)>500:raise ValueError('Invalid training set')
        if any(set(row)!={'text','label'} or row['label'] not in ('urgent','nonurgent') or not isinstance(row['text'],str) or not 5<=len(row['text'])<=500 for row in extra):raise ValueError('Invalid training example')
        model=make_pipeline(TfidfVectorizer(analyzer='char_wb',ngram_range=(2,4),sublinear_tf=True),
            LogisticRegression(C=spec['C'],max_iter=300,random_state=17,class_weight='balanced'))
        model.fit(URGENT+NONURGENT+[r['text'] for r in extra],['urgent']*len(URGENT)+['nonurgent']*len(NONURGENT)+[r['label'] for r in extra])
        self.model=model;self.spec=spec;self.version=spec['version']
    def _reload(self):
        import hashlib,json
        if self.path is None:return
        with self.lock:
            try:
                if not self.path.exists():
                    if self.loaded_digest is not None:self._install(self.base_spec());self.loaded_digest=None
                    return
                raw=self.path.read_bytes()
                if len(raw)>500000:raise ValueError('Oversized model policy')
                fingerprint=hashlib.sha256(raw).hexdigest()
                if fingerprint!=self.loaded_digest:
                    self._install(json.loads(raw));self.loaded_digest=fingerprint
                self.registry_error=None
            except Exception as exc:
                # Preserve last working model, and expose the load failure.
                self.registry_error=type(exc).__name__
    def predict(self,body,language='English'):
        self._reload()
        with self.lock:
            index=list(self.model.classes_).index('urgent')
            score=float(self.model.predict_proba([body])[0][index])
            label='urgent' if score>=self.spec['urgent_threshold'] else 'nonurgent' if score<=self.spec['nonurgent_threshold'] else 'uncertain'
            if language!='English' or len(body.strip())<5:label='uncertain'
            return {'label':label,'urgent_score':round(score,4),'version':self.version,
                'needs_review':label!='nonurgent','clinically_validated':False,
                'score_calibrated':False,'registry_error':self.registry_error}
