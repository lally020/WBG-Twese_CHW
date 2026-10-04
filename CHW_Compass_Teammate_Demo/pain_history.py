"""Structured, user-reported pain history. No diagnosis or clinical clearance."""
import re

FIELDS=('onset','provocation','palliation','quality','region','radiation','severity','timing')
LABELS=dict(zip(FIELDS,('Onset','Worse with','Better with','Quality','Location','Spread','Severity','Timing')))
QUESTIONS={
 'English':{
  'onset':'When did the pain start? Did it begin suddenly or gradually?',
  'provocation':'What seems to bring the pain on or make it worse?',
  'palliation':'What, if anything, makes it feel better?',
  'quality':'How would you describe the pain in your own words?',
  'region':'Where exactly do you feel the pain?',
  'radiation':'Does the pain spread anywhere else, or stay in one place?',
  'severity':'How strong is the pain right now, from 0 (no pain) to 10 (worst imaginable)?',
  'timing':'Is it there all the time, or does it come and go? How long does it last?'},
 'Kiswahili':{
  'onset':'Maumivu yalianza lini? Yalianza ghafla au taratibu?',
  'provocation':'Ni nini kinachosababisha au kuongeza maumivu?',
  'palliation':'Ni nini kinachopunguza maumivu?',
  'quality':'Unaweza kuelezea maumivu kwa maneno yako?',
  'region':'Unahisi maumivu sehemu gani hasa?',
  'radiation':'Maumivu yanaenea sehemu nyingine au yanabaki sehemu moja?',
  'severity':'Maumivu ni kiwango gani sasa, kutoka 0 (hakuna) hadi 10 (makali zaidi)?',
  'timing':'Maumivu yapo muda wote au yanakuja na kuondoka? Yanadumu kwa muda gani?'}
}

def mentions_pain(body):
 return bool(re.search(r'\b(pain|painful|hurts?|aching|ache|headache|backache|toothache|stomachache|sore|maumivu|inauma)\b',body,re.I))

def screen_flags(body):
 # Conservative demonstration stop rules. No detected phrase means no clearance.
 text=body.lower().replace('’',"'")
 flags=[]
 if re.search(r'\bchest\b|kifua',text):flags.append('Chest-related complaint requires direct human assessment')
 if re.search(r'\b(sudden(?:ly)?|ghafla)\b.{0,40}\b(severe|worst|unbearable)\b|\b(severe|worst|unbearable)\b.{0,40}\bsudden',text):flags.append('Sudden severe pain description')
 return flags

def extract_explicit(body):
 """Conservative English extraction; only literal, visible phrases, never inference."""
 result={}
 text=body.lower()
 region=re.search(r'\b(?:(?:left|right|lower|upper)\s+)?(?:knee|ankle|shoulder|elbow|wrist|hand|foot|back|neck|hip|abdomen|stomach|head|chest)\b',text)
 if region:result['region']=region.group()
 quality=re.search(r'\b(sharp|dull|aching|burning|throbbing|stabbing|cramping)\b',text)
 if quality:result['quality']=quality.group()
 severity=re.search(r'\b(10|[0-9])\s*(?:/\s*10|out of 10)\b',text)
 if severity:result['severity']=severity.group(1)+'/10'
 onset=re.search(r'\b(?:since|started)\s+(?:yesterday|today|this morning|last night|\d+\s+(?:days?|hours?|weeks?)\s+ago)\b',text)
 if onset:result['onset']=onset.group()
 if re.search(r'\b(constant|all the time)\b',text):result['timing']='constant (patient reported)'
 elif re.search(r'\b(comes and goes|on and off)\b',text):result['timing']='comes and goes (patient reported)'
 return result

def next_field(details):
 return next((key for key in FIELDS if key not in details),None)

def summary(details):
 return '\n'.join(f'{LABELS[key]}: {details.get(key,"Not provided")}' for key in FIELDS)
