"""Offline, explicitly non-clinical multilingual message-intent classifier."""
from __future__ import annotations
import json
from pathlib import Path
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.metrics import accuracy_score, f1_score

# SYNTHETIC demonstration samples. English + Kiswahili; not clinically evaluated.
TRAIN = {
 "taken": ["yes took the tablet", "i took all my medicine", "yes i remembered the dose", "i had my pill today", "all medicines taken", "1 yes", "nimetumia dawa zangu", "ndiyo nimemeza dawa", "nimekunywa dawa leo", "nimechukua dawa"],
 "missed": ["i forgot to take my pills", "i missed my medication today", "no i did not take it", "i missed two doses", "2 no", "i stopped my tablets", "nimesahau dawa", "sijameza dawa leo", "sikutumia dawa", "niliacha kunywa dawa"],
 "cost": ["i cannot afford the pills", "medication is too expensive", "i do not have money for medicine", "the price is too much", "no funds to buy tablets", "sina pesa ya dawa", "dawa ni ghali", "siwezi kununua dawa", "bei ya dawa ni kubwa"],
 "access": ["pharmacy is too far", "i cannot get to the clinic", "transportation is difficult", "no bus to collect medicine", "refill is not available", "hospital is too far away", "hospitali iko mbali", "sina nauli ya kwenda kliniki", "siwezi kufika kliniki", "dawa haipatikani"],
 "side_effect": ["tablets make me dizzy", "medicine makes me nauseated", "the pills upset my stomach", "i feel unwell after my medicine", "i feel sick from taking pills", "dawa inanifanya nihisi kizunguzungu", "dawa inaniletea kichefuchefu", "nahisi vibaya baada ya dawa"],
 "symptoms": ["i have a headache", "i feel weak today", "my chest hurts", "i am feeling ill", "i have blurry vision", "ninahisi maumivu", "ninaumwa kichwa", "nahisi mgonjwa", "nina maumivu ya kifua"],
 "nutrition": ["what foods should i avoid", "how much salt can i eat", "give me diet advice", "tips for healthy eating", "can i have vegetables", "nile chakula gani", "ushauri wa chakula", "nipunguze chumvi", "chakula chenye afya"],
 "appointment": ["when is my clinic visit", "please book a checkup", "need a CHW home visit", "when should i see nurse", "remind me about appointment", "lini niende kliniki", "nahitaji miadi", "nataka kumuona muuguzi", "nitakuja lini kliniki"],
}
TEST = [
 ("yes i took today's dose", "taken"), ("nimetumia dawa leo", "taken"),
 ("forgot my tablets this morning", "missed"), ("sijatumia dawa", "missed"),
 ("i have no money to buy medication", "cost"), ("dawa inanigharimu pesa nyingi", "cost"),
 ("i cannot travel to the hospital", "access"), ("sina usafiri wa kufika kliniki", "access"),
 ("these pills give me stomach trouble", "side_effect"), ("dawa imenifanya niwe na kichefuchefu", "side_effect"),
 ("i feel ill and have a headache", "symptoms"), ("ninaumwa leo", "symptoms"),
 ("please advise me about salt", "nutrition"), ("ninaweza kula nini", "nutrition"),
 ("what date is my appointment", "appointment"), ("ninahitaji kutembelewa na CHW", "appointment"),
]

class TinyAI:
    def __init__(self, store):
        self.store=store
        self.model=self._train([])
        self.version=1

    def _train(self, extra):
        x=[]; y=[]
        for cls, messages in TRAIN.items():
            x.extend(messages);y.extend([cls]*len(messages))
        for message, cls in extra:
            if cls in TRAIN and message.strip():
                x.append(message);y.append(cls)
        model=make_pipeline(TfidfVectorizer(analyzer="char_wb",ngram_range=(2,4),min_df=1,sublinear_tf=True),LogisticRegression(max_iter=300,random_state=2,class_weight="balanced"))
        model.fit(x,y)
        return model

    def predict(self, txt):
        p=self.model.predict_proba([txt])[0]
        pairs=sorted(zip(self.model.classes_,p),key=lambda kv:kv[1],reverse=True)
        label, confidence = pairs[0]
        label = str(label)
        # Probabilities are uncalibrated; margin is an additional abstention gate.
        unsure=bool(confidence < 0.22 or (len(pairs)>1 and (confidence-pairs[1][1])<0.06))
        return {"label":label if not unsure else "uncertain","proposed_label":label,"confidence":round(float(confidence),3),"margin":round(float(confidence-pairs[1][1]),3),"needs_human":unsure}

    def metrics(self, model=None):
        m=model or self.model
        true=[lab for _,lab in TEST]
        pred=[str(q) for q in m.predict([msg for msg,_ in TEST])]
        return {"accuracy":round(float(accuracy_score(true,pred)),3),"macro_f1":round(float(f1_score(true,pred,average="macro",zero_division=0)),3),"tested_examples":len(TEST),"examples": [{"message":text,"expected":gold,"predicted":got} for (text,gold),got in zip(TEST,pred)]}

    def candidate(self):
        samples=self.store.all("SELECT m.body,f.corrected_label FROM feedback f JOIN messages m ON m.id=f.message_id WHERE f.approved=1")
        approved=[(s["body"],s["corrected_label"]) for s in samples]
        model=self._train(approved)
        return {"model":model,"feedback_count":len(approved),"before":self.metrics(),"after":self.metrics(model)}

    def promote(self, model):
        self.model=model
        self.version+=1
