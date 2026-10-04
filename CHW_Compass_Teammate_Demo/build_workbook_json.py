"""Developer tool: run with artifact_tool installed to convert the synthetic workbook to a JSON import bundle."""
import json,sys
from pathlib import Path
from artifact_tool import Blob,SpreadsheetFile
source=Path(sys.argv[1]) if len(sys.argv)>1 else Path('CHW_Compass_Classeur_Clinique_Fictif.xlsx')
target=Path(sys.argv[2]) if len(sys.argv)>2 else Path('import_data/clinic_demo.json')
w=SpreadsheetFile.import_xlsx(Blob.load(str(source)))
limits={'CLINIQUE_':(120,13),'REGISTRE_DEMO':(80,8),'LIENS_VISITES':(180,5),'SUIVI_CHW':(180,10),'SMS_DEMO':(150,8),'QUIZ_DEMO':(150,12)}
output={'synthetic':True,'source_file':source.name,'sheets':{}}
for s in w.worksheets.items:
    k=next((p for p in limits if s.name.startswith(p)),None)
    if not k:continue
    max_rows,columns=limits[k]
    rows=s.get_range_by_indexes(0,0,max_rows,columns).values
    headings=[str(v).strip() if v is not None else '' for v in rows[0]]
    result=[dict(zip(headings,row)) for row in rows[1:] if any(v is not None and str(v).strip() for v in row)]
    output['sheets'][s.name]=result
    print(s.name,len(result))
target.parent.mkdir(parents=True,exist_ok=True)
target.write_text(json.dumps(output,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
print('Wrote:',target)
