"""Real Flask hooks must fail closed while retaining atlas inspection."""
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

from flask import Flask,jsonify
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from backend.research_preview import install_research_preview, _serialize_display_requests
from backend.atlas_sections import SectionError,sha256
from test_research_atlas_sections import research_profile


@pytest.fixture
def app(research_profile):
    app=Flask(__name__);app.testing=True
    @app.route('/api/status')
    def status():
        return jsonify(dict(ok=True,atlasLoaded=True,regions={'CA1':{}},
            mriCcfHybrid={'warpedMriPath':'old'},riskModel={'enabled':True},
            reconstructionQuality={'passed':True},workflowState={'old_evidence':True},
            atlasSignature={'hash':'actual_annotation','riskModel':{'enabled':True},
                            'mriConfig':{'warpedMriPath':'old'}},
            versionFreezeState={'frozen':True}))
    @app.route('/api/optimize',methods=['POST'])
    def optimize():raise AssertionError('Planner must not run')
    @app.route('/api/probe_contact_profile',methods=['POST'])
    def contact():return jsonify(dict(ok=True,contactRegionSet=['CA1']))
    @app.route('/api/allen_mesh/<int:region>')
    def mesh(region):return 'real mesh route'
    install_research_preview(app,research_profile[0],sha256(research_profile[0].parent/'annotation.npy'))
    return app


def test_status_cannot_publish_old_risk_or_quality(app):
    data=app.test_client().get('/api/status').get_json()
    assert data['atlasLoaded'] is True and 'CA1' in data['regions']
    assert data['riskModel']['enabled'] is False
    assert data['reconstructionQuality']['passed'] is False
    assert data['registrationPreview']['researchOnly'] is True
    assert data['artifactReady'] is False and data['displayReady'] is True
    assert 'warpedMriPath' not in data['mriCcfHybrid']
    assert 'versionFreezeState' not in data and data['workflowState']=={}
    assert data['atlasSignature']=={'hash':'actual_annotation'}


def test_status_publishes_the_actual_research_mesh_loading_policy(app):
    assert 'researchMeshLoading' not in app.test_client().get('/api/status').get_json()
    policy = dict(mode='serial', reason='low_memory', availableBytes=123, parallelMinimumBytes=3*1024**3)
    app.config['RESEARCH_PREVIEW'].configure_mesh_loading(app, policy)
    assert app.test_client().get('/api/status?includeRegions=0').get_json()['researchMeshLoading'] == policy


def test_serial_research_requests_wait_for_body_close_but_status_does_not():
    def handler(environ, start_response):
        return [environ['PATH_INFO'].encode()]

    serve = _serialize_display_requests(handler)
    waiting = Event()

    def second_request():
        waiting.set()
        body = serve({'PATH_INFO': '/api/mri/section'}, None)
        try:
            return b''.join(body)
        finally:
            body.close()

    mesh = serve({'PATH_INFO': '/api/allen_mesh_lod/17'}, None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        second = pool.submit(second_request)
        try:
            assert waiting.wait(timeout=1)
            assert not second.done()
            status = pool.submit(serve, {'PATH_INFO': '/api/status'}, None)
            assert status.result(timeout=1) == [b'/api/status']
        finally:
            mesh.close()
        assert second.result(timeout=1) == b'/api/mri/section'


def test_serial_request_failure_does_not_hold_slot():
    def handler(environ, start_response):
        if environ.get('fail'):
            raise ValueError('fixture error')
        return [b'OK']

    serve = _serialize_display_requests(handler)
    with pytest.raises(ValueError, match='fixture error'):
        serve({'PATH_INFO': '/api/mri/section', 'fail': True}, None)
    body = serve({'PATH_INFO': '/api/mri/section'}, None)
    try:
        assert b''.join(body) == b'OK'
    finally:
        body.close()


def test_exhausted_serial_body_releases_slot_before_transport_cleanup():
    serve = _serialize_display_requests(lambda environ, start_response: [b'OK'])
    first = serve({'PATH_INFO': '/api/mri/section'}, None)
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert b''.join(first) == b'OK'
        second = pool.submit(serve, {'PATH_INFO': '/api/mri/section'}, None)
        try:
            body = second.result(timeout=1)
        finally:
            first.close()
        try:
            assert b''.join(body) == b'OK'
        finally:
            body.close()


@pytest.mark.parametrize('method,path', [('post','/api/optimize'),('post','/api/export_pinpoint'),
    ('post','/api/atlas/mode'),('post','/api/mri/activate'),('post','/api/register_landmarks'),
    ('post','/api/mri/reconstruct/start'),('get','/api/task/status/old'),
    ('get','/api/replay/old'),('get','/api/clinical_contract'),('get','/api/mri_surface_mesh/brain_surface')])
def test_planning_old_evidence_and_old_surfaces_blocked(app,method,path):
    response=getattr(app.test_client(),method)(path,json={})
    assert response.status_code==409
    assert response.get_json()['code']=='research_preview_read_only'


def test_actual_section_endpoint_and_contact_scope(app):
    client=app.test_client()
    response=client.get('/api/mri/section?axis=z&coordinate=5.325&mode=mri_ccf_overlay')
    assert response.status_code==200 and response.get_json()['researchOnly']
    assert response.headers['X-ProbePlanner-Research-Only']=='true'
    data=client.post('/api/probe_contact_profile',json={}).get_json()
    assert data['contactRegionSet']==['CA1'] and data['planningEligible'] is False
    assert data['scope']=='ccf_atlas_contact_inspection_only'
    assert client.get('/api/allen_mesh/17').data==b'real mesh route'


def test_surface_appearance_cannot_reenable_legacy_mesh(app):
    client=app.test_client()
    data=client.post('/api/mri/appearance',json=dict(useMriAppearance=True,showSkull=True,
                     showBrainSurface=True,showOuterBoundary=True)).get_json()
    for key in ('showSkull','showBrainSurface','showOuterBoundary'):
        assert data['appearanceLayers'][key] is False
    assert client.get('/api/mri_surface_meshes/status').get_json()['ready'] is False


def test_no_fallback_after_source_changes(app,research_profile):
    with (research_profile[0].parent/'domain.nii').open('ab') as f:f.write(b'changed')
    response=app.test_client().get('/api/status')
    assert response.status_code==409
    assert response.get_json()['code']=='research_preview_unavailable'


def test_annotation_identity_required(research_profile):
    with pytest.raises(SectionError,match='annotation'):
        install_research_preview(Flask(__name__),research_profile[0],'0'*64)


def test_retry_cannot_reload_an_unpinned_atlas(app):
    assert app.test_client().get('/api/status?retry=true').status_code==409
