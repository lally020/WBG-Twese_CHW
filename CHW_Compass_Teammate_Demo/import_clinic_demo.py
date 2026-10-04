"""Import PRECONVERTED, SYNTHETIC clinic workbook into a separate encrypted Compass DB.
No identity inference: LIENS_VISITES is the only clinic-day identity mapping.
Never run this against real data or a production DB.
"""
import argparse,json,os
from pathlib import Path
from core import Compass

def import_bundle(bundle_path,db_path,passphrase):
    bundle=json.loads(Path(bundle_path).read_text(encoding='utf-8'))
    if bundle.get('synthetic') is not True:
        raise ValueError('Only explicitly synthetic bundles are allowed')
    path=Path(db_path)
    if path.exists(): raise FileExistsError('Refusing to overwrite existing DB: '+str(path))
    sheets=bundle['sheets']
    registry=sheets['REGISTRE_DEMO']
    links=sheets['LIENS_VISITES']
    chw=sheets['SUIVI_CHW']
    sms=sheets['SMS_DEMO']
    quizzes=sheets['QUIZ_DEMO']
    ids={row['patient_id']:i for i,row in enumerate(registry,1)}
    assert len(ids)==len(registry),'Non-unique source patient ID'
    assert len({(r['onglet_source'],str(r['N° de ligne (pas ID)'])) for r in links})==len(links)
    C=Compass(path,passphrase,seed=False)
    C.db.script('''
      CREATE TABLE IF NOT EXISTS imported_clinic_encounters (
        id INTEGER PRIMARY KEY, source_tab TEXT NOT NULL, source_row INTEGER NOT NULL,
        patient_id INTEGER NOT NULL REFERENCES patients(id), visit_date TEXT,
        original_fields_json TEXT NOT NULL,
        UNIQUE(source_tab,source_row));
      CREATE TABLE IF NOT EXISTS import_provenance (
        key TEXT PRIMARY KEY, value TEXT NOT NULL);
    ''')
    for name,fn in ids.items():
        p=registry[fn-1]
        full=(str(p['Prénom fictif'])+' '+str(p['Nom fictif'])).strip()
        condition='Hypertension' # Enrollment candidate only; clinical status not inferred from names
        C.db.execute('INSERT INTO patients(id,name,phone,language,condition,next_clinic,created_at) VALUES(?,?,?,?,?,?,?)',
          (fn,full,'NO_REAL_PHONE_'+name,'Kiswahili',condition,None,'2026-06-06T00:00:00+00:00'))
    encounters=0
    for l in links:
        tab=l['onglet_source']; rowidx=int(l['N° de ligne (pas ID)']); pid=ids[l['patient_id']]
        if tab not in sheets: raise ValueError('Missing clinic sheet '+tab)
        matched=[r for r in sheets[tab] if int(r['N°'])==rowidx]
        if len(matched)!=1: raise ValueError('Ambiguous original row '+tab+' '+str(rowidx))
        # Do not parse unstructured clinical notes into numeric readings
        C.db.execute('INSERT INTO imported_clinic_encounters(source_tab,source_row,patient_id,visit_date,original_fields_json) VALUES(?,?,?,?,?)',
        (tab,rowidx,pid,l['date_onglet'],json.dumps(matched[0],ensure_ascii=False)))
        encounters+=1
    for r in chw:
        pid=ids[r['patient_id']]
        sbp,dbp=r['systolique_mmHg'],r['diastolique_mmHg']
        if not (isinstance(sbp,(int,float)) and isinstance(dbp,(int,float))):continue
        note='Fictional CHW follow-up '+str(r.get('visit_id',''))+'; '+str(r.get('symptômes_déclarés') or '')
        C.db.execute('INSERT INTO vitals(patient_id,recorded_at,sbp,dbp,note) VALUES(?,?,?,?,?)',
          (pid,str(r['date_visite'])+'T12:00:00+00:00',int(sbp),int(dbp),note))
        adherence=str(r.get('observance_déclarée') or '').lower().strip()
        if adherence in ('oui','régulière','reguliere','complète','complete','bonne','non','partielle','irrégulière','irreguliere'):
            C.db.execute('INSERT INTO adherence(patient_id,recorded_at,taken,source) VALUES(?,?,?,?)',
              (pid,str(r['date_visite'])+'T12:00:00+00:00',1 if adherence in ('oui','régulière','reguliere','complète','complete','bonne') else 0,'Fictional CHW report; partial mapped to 0 for demo'))
    for r in sms:
        pid=ids[r['patient_id']]
        # SMS entries are explicitly UNVALIDATED sample text; keep unclassified for reviewer
        C.db.execute('INSERT INTO messages(patient_id,direction,body,created_at,intent,confidence,status) VALUES(?,?,?,?,?,?,?)',
          (pid,'inbound' if r['sens']=='entrant' else 'outbound',str(r['texte']),str(r['date_message'])+'T12:00:00+00:00','unreviewed',0.0,'imported_needs_review'))
    for r in quizzes:
        pid=ids[r['patient_id']]
        C.db.execute('INSERT INTO quiz(patient_id,topic,question,answer,correct,answered_at) VALUES(?,?,?,?,?,?)',
        (pid,str(r['thème']),str(r['question']),str(r['réponse_donnée']),int(r['correct (1/0)']),str(r['date'])+'T12:00:00+00:00'))
    C.db.execute('INSERT INTO import_provenance(key,value) VALUES(?,?)',('data_type','100% SYNTHETIC, no real patients'))
    C.db.execute('INSERT INTO import_provenance(key,value) VALUES(?,?)',('source',bundle.get('source_file','')))
    counts={k:C.db.one('SELECT COUNT(*) AS n FROM '+table)['n'] for k,table in [('patients','patients'),('clinic_encounters','imported_clinic_encounters'),('vitals','vitals'),('sms','messages'),('quizzes','quiz')]}
    C.audit('synthetic_workbook_import',str(counts))
    return counts

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--bundle',default='import_data/clinic_demo.json');p.add_argument('--db',default='private_data/workbook_demo.sqlite.aes');a=p.parse_args()
    phrase=os.environ.get('COMPASS_PASSPHRASE','')
    if len(phrase)<12: phrase=__import__('getpass').getpass('Demo database passphrase (12+ characters): ')
    print(import_bundle(a.bundle,a.db,phrase))
