"""Exercise only an isolated API backed by a synthetic database; never calls SIP."""
import json, os, urllib.request, urllib.parse, urllib.error
from pathlib import Path
assert os.environ.get("OUTDIALER_ISOLATED_TEST") == "yes", "Refuse production use"
base = "http://127.0.0.1:8080"
checks = []
def request(path, data=None, headers=None, expected=200):
    req = urllib.request.Request(base+path, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=20) as r: result=(r.status,r.read(),dict(r.headers))
    except urllib.error.HTTPError as e: result=(e.code,e.read(),dict(e.headers))
    assert result[0] == expected, (path,result[0],result[1][:250])
    return result

def form(path, values):
    return request(path, urllib.parse.urlencode(values).encode(), {"Content-Type":"application/x-www-form-urlencoded","Referer":base+"/"})
def passed(name): checks.append(name)
assert json.loads(request('/health')[1]) == {"status":"ok"}; passed('health')
assert b"Out Caller" in request('/')[1]; passed('html_dashboard')
assert json.loads(request('/status')[1])['campaign_enabled'] is False; passed('synthetic_campaign_disabled')
assert b'name,phone' in request('/template.csv')[1]; passed('csv_template')
form('/contacts/add', {'campaign_id':'default','name':'Synthetic One','phone':'+15555550101'}); passed('urlencoded_form')
boundary='codex-isolated-boundary'
body=('--'+boundary+'\r\nContent-Disposition: form-data; name="campaign_id"\r\n\r\ndefault\r\n--'+boundary+'\r\nContent-Disposition: form-data; name="file"; filename="synthetic.csv"\r\nContent-Type: text/csv\r\n\r\nname,phone,notes\nSynthetic Two,+15555550102,isolated fixture\n\r\n--'+boundary+'--\r\n').encode()
request('/contacts/import',body,{'Content-Type':'multipart/form-data; boundary='+boundary,'Referer':base+'/'});passed('multipart_csv_import')
contacts=json.loads(request('/contacts')[1]);assert len(contacts)==2;passed('database_roundtrip')
first=next(c for c in contacts if c['name']=='Synthetic One')
request('/agi/result', json.dumps({'contact_id':first['id'],'digit':'1','party_size':'2'}).encode(), {'Content-Type':'application/json'});passed('agi_result_without_call')
contacts=json.loads(request('/contacts')[1]);assert next(c for c in contacts if c['id']==first['id'])['status']=='attending';passed('result_persistence')
request('/agi/result',b'{}',{'Content-Type':'application/json'},expected=400);passed('invalid_result_rejected')
request('/contacts/add',b'name=missing-phone',{'Content-Type':'application/x-www-form-urlencoded'},expected=422);passed('form_validation')
assert b'Synthetic Two' in request('/contacts/export.csv')[1];passed('csv_export')
record=Path('/recordings/isolated.wav');record.write_bytes(bytes(range(256))*4)
r=request('/recordings/isolated.wav',headers={'Range':'bytes=0-9'},expected=206);assert len(r[1])==10;passed('recording_range_response')
record.unlink()
form('/contacts/'+first['id']+'/delete',{'campaign_id':'default'});assert len(json.loads(request('/contacts')[1]))==1;passed('contact_delete')
assert json.loads(request('/status')[1])['total_attempts']==0;passed('no_call_attempts')
assert json.loads(request('/status')[1])['campaign_enabled'] is False;passed('campaign_remains_disabled')
from app.worker import normalize_phone, safe_var
assert normalize_phone('(555) 555-0100','nanp_1')=='15555550100'
assert '\n' not in safe_var('synthetic\nvalue');passed('worker_import_and_pure_helpers')
print('OUTDIALER_CONTRACT_RESULT '+json.dumps({'passed':len(checks),'checks':checks,'sip_calls':0,'external_provider_requests':0}))
