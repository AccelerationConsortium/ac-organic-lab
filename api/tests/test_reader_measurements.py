"""Offline acquisition/publication contract: real journal, mocked record HTTP."""
import json
from types import SimpleNamespace

import httpx
import pytest
import respx

from app.reader_measurements import ReaderJournal, ReaderRecorder, targets, measurements

BASE='http://records.test'


def auth(plate='PLATE-1'):
    return SimpleNamespace(project_id='project', authorization_id='auth-1', commit_sha='commit', package_digest='digest',
        binding={'reader':'cytation_5'}, plate_bindings={'reader_plate':plate}, steps=[
            {'step_id':'load','role':'reader','skill':'plate.load','args':{'plate_id':plate}},
            *[{'step_id':f'scan__{nm}','role':'reader','skill':'read.absorbance','args':{'wells':['A1','B1'],'wavelength_nm':nm}} for nm in [350,360]]])


@pytest.fixture
def setup(tmp_path):
    store=ReaderJournal(tmp_path/'results.sqlite3')
    recorder=ReaderRecorder(store,base_url=BASE,secret='test-only')
    return store,recorder


def mock_records(plate='PLATE-1'):
    samples=[{'sample_id':f's-{w}', 'experiment_id':'exp','hid':f'imported:{w}',
        'meta':{'plate':'old_nominal_label','plate_hid':plate,'well':w}} for w in ['A1','B1']]
    respx.get(BASE+'/samples').respond(200,json=samples)
    respx.get(BASE+'/containers').respond(200,json=[{'hid':plate,'container_id':'container'}])
    saved=[]
    def get(request):
        return httpx.Response(200,json=[m for m in saved if m['hid']==request.url.params['hid'] and m['sample_id']==request.url.params['sample_id']])
    def post(request):
        value=json.loads(request.content)
        assert request.headers['X-Auth-User']=='chemist'
        saved.append({**value,'measurement_id':f'm-{len(saved)}',
            'acquisition_params':{'solvent':None,'exposure_s':None,**value['acquisition_params']}})
        return httpx.Response(200,json=saved[-1])
    respx.get(BASE+'/measurements').mock(side_effect=get)
    route=respx.post(BASE+'/measurements').mock(side_effect=post)
    return saved,route


async def prepare(recorder,run='run-1',a=None):
    await recorder.prepare(run_id=run,auth=a or auth(),opened={'plan_id':'plan-'+run,'experiment_id':'exp'},operator='chemist')


@respx.mock
async def test_reread_appends_to_same_existing_samples_without_new_plates(setup):
    store,recorder=setup; saved,route=mock_records()
    for run in ['run-1','run-2']:
        await prepare(recorder,run)
        for nm in [350,360]: store.capture(run,f'scan__{nm}',{'wells':{'A1':1.2,'B1':None},'over_range':['B1']})
        store.state(run,'pending')
        result=await recorder.publish(run)
        assert result['state']=='published' and result['measurements_written']==2
        assert (await recorder.publish(run))['state']=='published'
    assert len(saved)==4 and route.call_count==4
    assert {m['sample_id'] for m in saved}=={'s-A1','s-B1'}
    assert len({m['hid'] for m in saved})==4
    assert all(m['meta']['plate_hid']=='PLATE-1' for m in saved)
    assert saved[1]['meta']['data']['signal']['values']==[None,None]
    assert saved[1]['meta']['data']['quality']==[1,1]
    assert saved[0]['meta']['step_ids']==['scan__350','scan__360']
    assert saved[0]['meta']['complete'] is True


@respx.mock
async def test_timeout_after_commit_retries_recording_not_acquisition(setup):
    store,recorder=setup; saved,route=mock_records()
    await prepare(recorder)
    store.capture('run-1','scan__350',{'wells':{'A1':.1,'B1':.2}})
    def post(request):
        value=json.loads(request.content); saved.append({**value,'measurement_id':'m-timeout'})
        raise httpx.ReadTimeout('response lost')
    route.mock(side_effect=post)
    result=await recorder.publish('run-1')
    assert result['state']=='pending' and len(saved)==1
    store=ReaderJournal(store.path); store.recover()
    # Retry finds the first committed row, then writes only the other well.
    route.mock(side_effect=lambda request: httpx.Response(200,json={**json.loads(request.content),'measurement_id':'m-other'}))
    result=await ReaderRecorder(store,base_url=BASE,secret='test-only').publish('run-1')
    assert result['state']=='published' and route.call_count==2
    assert len(store.get('run-1')['points'])==1
    assert all(not p['payload']['meta']['complete'] for p in store.get('run-1')['publications'])


@respx.mock
@pytest.mark.parametrize('kind',['wrong_plate','nominal_only','duplicate'])
async def test_missing_or_ambiguous_sample_identity_refuses_before_reads(setup,kind):
    store,recorder=setup; mock_records()
    sample={'sample_id':'s','experiment_id':'exp','meta':{'well':'A1','plate_hid':'OTHER'}}
    if kind=='nominal_only': sample['meta']={'well':'A1','plate':'reader_plate'}
    if kind=='duplicate': sample['meta']['plate_hid']='PLATE-1'
    respx.get(BASE+'/samples').respond(200,json=[sample,sample] if kind=='duplicate' else [sample])
    with pytest.raises(ValueError,match='existing sample'): await prepare(recorder)
    assert store.get('run-1') is None


@respx.mock
@pytest.mark.parametrize('response',[{'wells':{'A1':None,'B1':1}}, {'wells':{'A1':True,'B1':1}}, {'wells':{'A1':.1}}, {'wells':{'A1':float('nan'),'B1':1}}])
async def test_malformed_data_is_retained_but_never_published(setup,response):
    store,recorder=setup; _,route=mock_records(); await prepare(recorder)
    store.capture('run-1','scan__350',response)
    result=await recorder.publish('run-1')
    assert result['state']=='pending' and result['error']
    assert route.call_count==0 and len(store.get('run-1')['points'])==1


def test_no_implicit_first_plate_for_multiplate_reads():
    a=auth(); a.steps=a.steps[1:]
    with pytest.raises(ValueError,match='preceding plate.load'): targets(a)


@respx.mock
async def test_restart_preserves_partial_fluorescence_and_parameters(setup):
    store,recorder=setup; saved,_=mock_records()
    a=auth()
    for step in a.steps[1:]:
        step['skill']='read.fluorescence'
        step['args']={'wells':['A1','B1'],'excitation_nm':360,'emission_nm':int(step['step_id'].split('__')[1])+50,'focal_height_mm':7.0}
    await prepare(recorder,a=a)
    store.capture('run-1','scan__350',{'wells':{'A1':12,'B1':13}})
    store=ReaderJournal(store.path); store.recover()
    assert store.get('run-1')['state']=='interrupted'
    result=await ReaderRecorder(store,base_url=BASE,secret='test-only').publish('run-1')
    assert result['state']=='published'
    assert saved[0]['acquisition_params']['excitation_wavelength_nm']==360
    assert saved[0]['meta']['data']['axis']['coords']==[400]
    assert saved[0]['meta']['complete'] is False


@respx.mock
async def test_history_and_retry_routes_survive_restart_and_require_membership(setup,monkeypatch):
    from fastapi import FastAPI
    from app import workflow as wf
    from app import reader_measurements as rm
    store,recorder=setup; saved,route=mock_records()
    await prepare(recorder)
    store.capture('run-1','scan__350',{'wells':{'A1':.1,'B1':.2}})
    store.recover()
    app=FastAPI(); app.include_router(wf.build_workflow_router()); app.state.reader_journal=store
    async def scope(request,who): return {'member_projects':['project'] if who=='chemist' else []}
    monkeypatch.setattr(wf,'member_scope',scope)
    monkeypatch.setattr(rm.record,'BITACORADB_URL',BASE)
    monkeypatch.setattr(rm.record,'edge_secret',lambda:'test-only')
    monkeypatch.setenv('DASHBOARD_CONTROL_OPEN','false')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        path='/api/workflow/runs/run-1/measurements'
        assert (await client.get(path)).status_code==401
        assert (await client.post(path+'/retry',headers={'X-Auth-User':'outsider'})).status_code==403
        assert route.call_count==0
        headers={'X-Auth-User':'chemist'}
        assert (await client.get(path,headers=headers)).json()['state']=='interrupted'
        response=await client.post(path+'/retry',headers=headers)
        assert response.status_code==200 and response.json()['state']=='published'
        assert (await client.get(path,headers=headers)).json()['captured_steps']==1
        await client.post(path+'/retry',headers=headers)
        assert route.call_count==2
        # No live RunState or LabSession exists: retry only publishes stored data.
        assert 'run-1' not in wf._RUNS


@respx.mock
@pytest.mark.parametrize('dry_run',[False,True])
async def test_start_route_wires_executor_responses_to_measurements(setup,monkeypatch,dry_run):
    import asyncio
    from fastapi import FastAPI
    from app import workflow as wf
    from app import reader_measurements as rm
    from test_workflow import _auth, _package
    import lab_skills
    from lab_skills import Lab
    from lab_skills.registry import Registry
    from lab_skills.plan import PlanRunReport, PlanReport, StepRunReport
    store,_=setup; saved,_=mock_records()
    spec=auth()
    approved=_auth(package=_package(steps=spec.steps),project_id='project',binding=spec.binding,plate_bindings=spec.plate_bindings)
    async def fetch(*a,**kw): return approved
    async def opened(**kw): return {'opened':True,'plan_id':'plan-live','experiment_id':'exp'}
    async def closed(**kw): return {'written':True}
    async def audit(*a,**kw): pass
    async def custody(*a,**kw): return {},[]
    async def scope(*a,**kw): return {'member_projects':['project']}
    async def execute(plan,session,**kw):
        output=[]
        for i,step in enumerate(plan.steps):
            reason=await kw['gate'](step)
            assert reason is None
            result=StepRunReport(step_id=step.id,step_index=i,role=step.role,skill=step.skill,
                equipment_id='cytation_5',status='dry_run' if kw['dry_run'] else 'succeeded',
                response={'wells':{'A1':.1,'B1':.2}} if step.skill in rm.READS else {})
            output.append(result); await kw['on_step'](result)
        return PlanRunReport(ok=True,dry_run=kw['dry_run'],validation=PlanReport(ok=True,steps=[]),steps=output)
    for name,value in [('fetch_authorization',fetch),('open_run_record',opened),('close_run_record',closed),('_record_run_event',audit),('custody_at_start',custody),('member_scope',scope)]: monkeypatch.setattr(wf,name,value)
    monkeypatch.setattr(wf,'lab_session',lambda *a:Lab.connect(registry=Registry(equipment=[]),binding={}))
    monkeypatch.setattr(lab_skills,'execute_plan',execute)
    monkeypatch.setattr(rm.record,'BITACORADB_URL',BASE)
    monkeypatch.setattr(rm.record,'edge_secret',lambda:'test-only')
    app=FastAPI();app.include_router(wf.build_workflow_router());app.state.reader_journal=store
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.post('/api/workflow/runs',headers={'X-Auth-User':'chemist'},json={'authorization_id':'ra_test','dry_run':dry_run})
        assert response.status_code==202,response.text
        run_id=response.json()['run_id']
        try:
            for _ in range(100):
                if wf._RUNS[run_id].status=='finished': break
                await asyncio.sleep(.01)
            state=wf._RUNS[run_id]
            assert state.result['ok'] is True,state.result
            if dry_run:
                assert not saved and store.get(run_id) is None
            else:
                assert len(saved)==2
                assert state.result['record']['measurements']['state']=='published'
                assert saved[0]['meta']['plan_id']=='plan-live'
                assert saved[0]['meta']['run_id']==run_id
        finally: wf._RUNS.pop(run_id,None)


def test_conflicting_physical_identifiers_are_not_accepted():
    from app.reader_measurements import physical_sample
    assert not physical_sample({"meta": {"plate_hid": "PLATE-1", "physical_plate_hid": "PLATE-2", "well": "A1"}}, "PLATE-1", "A1")
