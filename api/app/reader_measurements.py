"""Append reader measurements to existing physical samples; never repeat a read.

The journal is a durable outbox, not a scientific database. It retains device
responses until publication (including across restart). Samples must already
identify the physical plate and well; nominal names alone are not evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx

from . import record

READS = {"read.absorbance": ("UV-VIS-ABS", "wavelength_nm", "AU"),
         "read.fluorescence": ("PL", "emission_nm", "RFU")}


def targets(auth):
    loaded, out = {}, {}
    for step in auth.steps:
        role, skill = step.get("role"), step.get("skill")
        args = step.get("args") or {}
        if skill == "plate.load":
            loaded[role] = args.get("plate_id")
        elif skill == "plate.unload":
            loaded.pop(role, None)
        elif skill in READS:
            hid = loaded.get(role)
            names = [name for name, bound in auth.plate_bindings.items() if bound == hid]
            if not hid or len(names) != 1:
                raise ValueError("Reader acquisition requires a preceding plate.load and one unambiguous authorized physical plate binding")
            wells = args.get("wells")
            if not isinstance(wells, list) or not wells or len(set(wells)) != len(wells):
                raise ValueError("Reader acquisition requires unique explicit wells")
            out[step["step_id"]] = {"plate_hid": hid, "plate": names[0], "role": role,
                "skill": skill, "args": args, "instrument": auth.binding[role],
                "group": step["step_id"].split("__", 1)[0]}
    return out


def physical_sample(sample, hid, well, container_id=None):
    meta = sample.get("meta") or {}
    explicit = [meta[key] for key in ("plate_hid", "physical_plate_hid") if meta.get(key)]
    if any(value != hid for value in explicit):
        return False
    identity = explicit[0] if explicit else None
    if identity is None and container_id and meta.get("container_id") == container_id:
        identity = hid
    if identity is None and meta.get("plate") == hid:
        identity = hid
    return identity == hid and meta.get("well") == well


class ReaderJournal:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS reader_runs (run_id TEXT PRIMARY KEY, project TEXT NOT NULL, context TEXT NOT NULL, state TEXT NOT NULL, error TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS reader_points (run_id TEXT NOT NULL, step_id TEXT NOT NULL, captured_at TEXT NOT NULL, response TEXT NOT NULL, PRIMARY KEY(run_id,step_id))')
            db.execute('CREATE TABLE IF NOT EXISTS reader_publications (run_id TEXT NOT NULL, hid TEXT NOT NULL, payload TEXT NOT NULL, measurement_id TEXT, PRIMARY KEY(run_id,hid))')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db: yield db
        finally: db.close()

    def recover(self):
        with self.db() as db:
            db.execute("UPDATE reader_runs SET state='interrupted' WHERE state='acquiring'")

    def begin(self, run_id, project, context):
        with self.db() as db:
            db.execute('INSERT INTO reader_runs VALUES (?,?,?,?,NULL)',
                       (run_id, project, json.dumps(context, allow_nan=False), 'acquiring'))

    def capture(self, run_id, step_id, response):
        # SQLite commits before continuation. Invalid device payloads remain
        # inspectable; publication validation reports rather than invents values.
        with self.db() as db:
            db.execute('INSERT INTO reader_points VALUES (?,?,?,?)',
                (run_id, step_id, datetime.now(timezone.utc).isoformat(), json.dumps(response)))

    def state(self, run_id, state, error=None):
        with self.db() as db:
            db.execute('UPDATE reader_runs SET state=?,error=? WHERE run_id=?',(state,error,run_id))

    def get(self, run_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM reader_runs WHERE run_id=?',(run_id,)).fetchone()
            if row is None: return None
            points = db.execute('SELECT * FROM reader_points WHERE run_id=? ORDER BY rowid',(run_id,)).fetchall()
            pubs = db.execute('SELECT * FROM reader_publications WHERE run_id=?',(run_id,)).fetchall()
        return {**dict(row), 'context':json.loads(row['context']),
                'points':[{**dict(p),'response':json.loads(p['response'])} for p in points],
                'publications':[{**dict(p),'payload':json.loads(p['payload'])} for p in pubs]}

    def stage(self, run_id, payload):
        encoded=json.dumps(payload,sort_keys=True,allow_nan=False)
        with self.db() as db:
            old=db.execute('SELECT payload FROM reader_publications WHERE run_id=? AND hid=?',(run_id,payload['hid'])).fetchone()
            if old and old[0] != encoded: raise ValueError('Frozen measurement payload changed')
            db.execute('INSERT OR IGNORE INTO reader_publications VALUES (?,?,?,NULL)',(run_id,payload['hid'],encoded))

    def published(self, run_id, hid, measurement_id):
        with self.db() as db:
            db.execute('UPDATE reader_publications SET measurement_id=? WHERE run_id=? AND hid=?',(measurement_id,run_id,hid))


class ReaderRecorder:
    def __init__(self, store, *, base_url=None, secret=None):
        self.store=store
        self.base_url=base_url if base_url is not None else record.BITACORADB_URL
        self.secret=secret if secret is not None else record.edge_secret()

    def headers(self, context):
        return {'X-Edge-Secret':self.secret,'X-Auth-User':context['operator'],
                'X-Auth-Projects':context['project']}

    async def prepare(self, *, run_id, auth, opened, operator):
        specs=targets(auth)
        if not specs: return
        if not self.base_url or not self.secret: raise ValueError('Reader measurement record layer is not configured')
        ctx={'targets':specs,'project':auth.project_id,'operator':operator,
             'plan_id':opened['plan_id'],'experiment_id':opened['experiment_id'],
             'authorization_id':auth.authorization_id,'source_commit':auth.commit_sha,
             'package_digest':auth.package_digest,'samples':{}}
        async with httpx.AsyncClient(base_url=self.base_url,headers=self.headers(ctx),timeout=15) as client:
            response=await client.get('/samples',params={'experiment_id':ctx['experiment_id']}); response.raise_for_status()
            samples=response.json()
            for hid in {s['plate_hid'] for s in specs.values()}:
                result=await client.get('/containers',params={'hid':hid}); result.raise_for_status()
                containers=[c for c in result.json() if c.get('hid')==hid]
                if len(containers)!=1: raise ValueError(f'Physical plate {hid} is not uniquely registered')
                for well in {w for s in specs.values() if s['plate_hid']==hid for w in s['args']['wells']}:
                    matches=[s for s in samples if str(s.get('experiment_id'))==str(ctx['experiment_id']) and physical_sample(s,hid,well,containers[0]['container_id'])]
                    if len(matches)!=1:
                        raise ValueError(f'{hid}:{well} needs exactly one existing sample in this experiment with an explicit physical plate identity; found {len(matches)}. Register or reconcile sample identity before acquisition.')
                    ctx['samples'][f'{hid}:{well}']=str(matches[0]['sample_id'])
        self.store.begin(run_id,auth.project_id,ctx)

    async def publish(self, run_id, *, recorded_by=None):
        row=self.store.get(run_id)
        if row is None: return {'state':'not_applicable'}
        try:
            for payload in measurements(row): self.store.stage(run_id,payload)
            row=self.store.get(run_id)
            async with httpx.AsyncClient(base_url=self.base_url,headers=self.headers({**row['context'], 'operator': recorded_by or row['context']['operator']}),timeout=15) as client:
                for item in row['publications']:
                    if item['measurement_id']: continue
                    payload=item['payload']
                    # A timeout can follow a committed write. Exact lookup plus
                    # the server's unique (sample_id,hid) constraint makes retry
                    # safe without issuing a second acquisition.
                    response=await client.get('/measurements',params={'sample_id':payload['sample_id'],'hid':payload['hid']})
                    response.raise_for_status()
                    existing=[m for m in response.json() if m.get('hid')==payload['hid'] and str(m.get('sample_id'))==payload['sample_id']]
                    if not existing:
                        response=await client.post('/measurements',json=payload)
                        if response.status_code==409:
                            response=await client.get('/measurements',params={'sample_id':payload['sample_id'],'hid':payload['hid']})
                            response.raise_for_status(); existing=response.json()
                        else:
                            response.raise_for_status(); existing=[response.json()]
                    if len(existing)!=1 or any(existing[0].get(k)!=payload[k] for k in ('sample_id','hid','technique','instrument','meta')) or any(existing[0].get('acquisition_params',{}).get(k)!=v for k,v in payload['acquisition_params'].items()):
                        raise ValueError('Measurement identity conflict; stored result differs from the frozen read')
                    self.store.published(run_id,payload['hid'],str(existing[0]['measurement_id']))
            self.store.state(run_id,'published')
        except Exception as exc:
            self.store.state(run_id,'pending',str(exc))
        saved=self.store.get(run_id)
        return {'state':saved['state'],'error':saved['error'],'captured_steps':len(saved['points']),
                'measurements_written':sum(bool(p['measurement_id']) for p in saved['publications'])}


def measurements(row):
    ctx=row['context']; groups={}
    for point in row['points']:
        target=ctx['targets'][point['step_id']]; args=target['args']
        technique,axis,unit=READS[target['skill']]
        response=point['response']; values=response.get('wells') if isinstance(response,dict) else None
        over=response.get('over_range',[]) if isinstance(response,dict) else []
        if not isinstance(values,dict) or set(values)!=set(args['wells']) or not isinstance(over,list) or not set(over)<=set(values):
            raise ValueError(f"Malformed reader response at {point['step_id']}; raw response retained")
        coord=args[axis]
        if isinstance(coord,bool) or not isinstance(coord,(int,float)) or not math.isfinite(coord): raise ValueError('Invalid wavelength')
        for well,value in values.items():
            saturated=well in over
            if (value is None) != saturated or (value is not None and (isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value))):
                raise ValueError(f'Invalid value/over-range flag for {well}; raw response retained')
            key=(target['group'],target['role'],target['plate_hid'],well,technique,args.get('excitation_nm'),args.get('focal_height_mm',7.0) if technique=='PL' else None)
            groups.setdefault(key,[]).append((coord,value,1 if saturated else 0,point,target))
    for key,points in groups.items():
        points.sort(key=lambda p:p[0])
        if len({p[0] for p in points})!=len(points): raise ValueError('Repeated wavelength within one scan; separate authored step IDs are required')
        group,role,hid,well,technique,excitation,focal=key
        target=points[0][4]; sample_id=ctx['samples'][f'{hid}:{well}']
        expected=[sid for sid,s in ctx['targets'].items() if s['group']==group and s['role']==role and s['plate_hid']==hid and s['skill']==target['skill'] and well in s['args']['wells'] and s['args'].get('excitation_nm')==excitation and (s['args'].get('focal_height_mm',7.0) if technique=='PL' else None)==focal]
        acquired=[p[3]['step_id'] for p in points]
        params={'technique':technique}
        if technique=='PL': params['excitation_wavelength_nm']=excitation
        else: params.update(wavelength_min_nm=points[0][0],wavelength_max_nm=points[-1][0])
        yield {'sample_id':sample_id,'hid':'reader:'+hashlib.sha256(json.dumps([row['run_id'],key]).encode()).hexdigest(),
            'title':f'{hid} {well} {technique} — {group}', 'technique':technique,
            'instrument':target['instrument'],'operator':ctx['operator'],
            'measured_at':max(p[3]['captured_at'] for p in points),'acquisition_params':params,
            'meta':{'run_id':row['run_id'],'plan_id':ctx['plan_id'],'authorization_id':ctx['authorization_id'],
                'source_commit':ctx['source_commit'],'package_digest':ctx['package_digest'],
                'plate':target['plate'],'plate_hid':hid,'well':well,'step_ids':acquired,
                'expected_step_ids':expected,'complete':set(acquired)==set(expected),
                'focal_height_mm':focal,'raw_responses':[{'step_id':p[3]['step_id'],'captured_at':p[3]['captured_at'],'response':p[3]['response'],'args':p[4]['args']} for p in points],
                'data':{'schema_version':1,'kind':'spectrum','variant':technique,
                    'axis':{'quantity':'emission wavelength' if technique=='PL' else 'wavelength','unit':'nm','coords':[p[0] for p in points]},
                    'signal':{'quantity':'fluorescence' if technique=='PL' else 'absorbance','unit':'RFU' if technique=='PL' else 'AU','values':[p[1] for p in points]},
                    'quality':[p[2] for p in points]}}}
