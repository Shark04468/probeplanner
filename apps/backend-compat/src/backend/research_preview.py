"""Opt-in, read-only research display using the real atlas backend and UI."""
from io import BytesIO
import re
import threading

from flask import jsonify, request, send_file
from PIL import Image
from werkzeug.wsgi import ClosingIterator

from .atlas_sections import AtlasSectionSource, SectionError


STATUS_FIELDS = frozenset(('ok','statusContractVersion','engineVersion','activeAtlasMode',
    'effectiveAtlasMode','atlasLoaded','backendAtlasLoaded','atlasError','backendAtlasError',
    'atlasLoaderAlive','atlasLoad','atlasLoadPct','voxelSize','atlasShape','atlasSignature',
    'atlasHash','authEnabled','requestId','includeRegions','regionScope','regionCount',
    'availableRegionCount','plannerRegionCount','indexedRegionCount','catalogRegionCount',
    'presentRegionCount','regionIndex','regions','meshCacheSize','lastSeen'))
SURFACES = ('brain_surface','outer_tissue','skull')
MESH_ROUTE = re.compile(r'/api/(?:allen_mesh|allen_mesh_lod|mesh_lod)/\d+')
HEAVY_DISPLAY_ROUTES = frozenset(('/api/allen_mesh/batch', '/api/mri/section',
                                 '/api/mri/registration/qc/overlay_image'))


def _serialize_display_requests(wsgi_app):
    slot = threading.Lock()

    def serve(environ, start_response):
        path = environ.get('PATH_INFO', '')
        if path not in HEAVY_DISPLAY_ROUTES and not MESH_ROUTE.fullmatch(path):
            return wsgi_app(environ, start_response)
        slot.acquire()
        try:
            response = wsgi_app(environ, start_response)
        except BaseException:
            slot.release()
            raise
        closed = False

        def finish():
            nonlocal closed
            if not closed:
                closed = True
                try:
                    if hasattr(response, 'close'):
                        response.close()
                finally:
                    slot.release()

        def stream():
            # Release on exhaustion, even if the server later fails while draining the socket.
            try:
                yield from response
            finally:
                finish()

        return ClosingIterator(stream(), finish)

    return serve


class ResearchPreview:
    def __init__(self, manifest, annotation_sha256):
        self.source = AtlasSectionSource(manifest)
        if not self.source.research or self.source.hashes['annotation'] != annotation_sha256:
            raise SectionError('Research display annotation differs from the loaded atlas')
        self.lock = threading.RLock()
        self.enabled = True
        self.overlay = 'mri_ccf_overlay'
        self.labels = True
        self.mesh_loading_policy = None

    def configure_mesh_loading(self, app, policy):
        if policy['mode'] == 'serial':
            app.wsgi_app = _serialize_display_requests(app.wsgi_app)
        self.mesh_loading_policy = policy

    def identity(self):
        return dict(researchOnly=True, planningEligible=False, anatomyAccepted=False,
            displayReady=True, profileId=self.source.profile_id,
            manifestSha256=self.source.manifest_hash, sourceHashes=dict(self.source.hashes),
            coordinateFrame='allen_ccf25_original_ras',scope='registration_inspection_not_native_planning')

    def surface_status(self):
        return dict(ok=True,ready=False,configured=False,missingLayers=list(SURFACES),
            layers={k:dict(ready=False,source='unavailable_for_this_reconstruction') for k in SURFACES})

    def appearance(self):
        with self.lock:
            layers=dict(showOuterBoundary=False,showSkull=False,showBrainSurface=False,
                        showCcfLabels=self.labels,overlayMode=self.overlay)
            layer_status=dict(hybridActive=True,activeAtlasMode='mri_ccf_hybrid',
                profileId=self.source.profile_id,appearanceActive=self.enabled,appearanceConfigured=True,
                appearanceLayers=layers,surfaceMeshes=self.surface_status(),
                riskLayerStructuredMaskEnabled=False,riskLayerSdfEnabled=False,
                artifactReady=False,displayReady=True,researchOnly=True,planningEligible=False,
                riskLayerSources=[],riskLayerDistanceModel='unavailable_in_research_preview')
            return dict(ok=True,activeAtlasMode='mri_ccf_hybrid',useMriAppearance=self.enabled,
                        appearanceLayers=layers,mriLayerStatus=layer_status,
                        registrationPreview=self.identity())

    def quality(self):
        return dict(passed=False,status='research_only',planningEligible=False,
                    reason='Anatomical acceptance is incomplete; inspection only')

    def status(self, base):
        data={k:v for k,v in base.items() if k in STATUS_FIELDS}
        signature=base.get('atlasSignature') or {}
        data['atlasSignature']={k:signature[k] for k in ('atlasMode','shape','hash',
            'availableRegionCount','catalogRegionCount','presentRegionCount') if k in signature}
        appearance=self.appearance()
        profile={**self.identity(), 'active':True,'artifactReady':False,
            'useMriAppearance':appearance['useMriAppearance'],**appearance['appearanceLayers']}
        data.update(registrationPreview=self.identity(),mriLayerStatus=appearance['mriLayerStatus'],
            mriCcfHybrid=profile,mriProfile=profile,activeProfile=profile,
            artifactReady=False,displayReady=True,fullBrainPlannerReady=False,
            supportedAtlasModes=['mri_ccf_hybrid'],
            reconstructionQuality=self.quality(),qualityGateFailureCard=None,
            riskModel=dict(enabled=False,structuredMaskEnabled=False,sdfEnabled=False,
                           source=[],reason='research_preview_no_safety_assessment'),
            workflowState={},workflowSummary={},latestMriReconstructTask=None)
        if self.mesh_loading_policy is not None:
            data['researchMeshLoading'] = self.mesh_loading_policy
        return data

    def summary(self):
        return dict(ok=True,activeAtlasMode='mri_ccf_hybrid',profileId=self.source.profile_id,
            qualityGatePassed=False,quality=self.quality(),metrics={},
            registrationPreview=self.identity(),status='research_only',
            summary=self.source.provenance.get('reconstructionSummary',{}))


def install_research_preview(app, manifest, annotation_sha256):
    if app.config.get('RESEARCH_PREVIEW') is not None:
        raise SectionError('Research preview already installed')
    preview=ResearchPreview(manifest,annotation_sha256)
    app.config['RESEARCH_PREVIEW']=preview

    @app.before_request
    def research_display_request():
        if not request.path.startswith('/api/'):
            return None
        try:
            preview.source.verify_unchanged()
            path=request.path
            if request.method=='OPTIONS':
                return None
            if request.method in ('GET','HEAD'):
                if request.args.get('retry','').lower() in ('1','true','on','yes'):
                    raise SectionError('Atlas reload is disabled during a pinned research preview')
                if path=='/api/mri/section':
                    coordinate=float(request.args.get('coordinate','nan'))
                    return jsonify(preview.source.section(request.args.get('axis','y'),coordinate,
                                      request.args.get('mode','mri_ccf_overlay')))
                if path=='/api/mri/appearance':return jsonify(preview.appearance())
                if path=='/api/mri_surface_meshes/status':return jsonify(preview.surface_status())
                if path=='/api/mri/registration/summary':return jsonify(preview.summary())
                if path=='/api/atlas/mode':
                    return jsonify(dict(ok=True,activeAtlasMode='mri_ccf_hybrid',
                        supportedAtlasModes=['mri_ccf_hybrid'],registrationPreview=preview.identity()))
                if path=='/api/reconstruction/quality':return jsonify(preview.quality())
                if path=='/api/mri/registration/qc':
                    return jsonify({**preview.summary(),'checks':{},'passed':False})
                if path=='/api/mri/overlay/status':
                    return jsonify(dict(ok=True,qualityGatePassed=False,profileId=preview.source.profile_id,
                        registrationPreview=preview.identity(),
                        layers={k:dict(configured=True) for k in ('mriTemplate','annotationBoundary','jacobianHeatmap')},
                        missingLayers=['skullBoundary','outerTissueBoundary','layeredSdf'],
                        overlayModes=['mri_template','annotation_boundary','checkerboard','jacobian_heatmap']))
                if path=='/api/mri/registration/qc/overlay_image':
                    index=request.args.get('index')
                    rgb,info=preview.source.qc_image(request.args.get('axis','coronal'),
                        int(index) if index is not None else None,request.args.get('mode','boundary'))
                    stream=BytesIO();Image.fromarray(rgb).save(stream,format='PNG');stream.seek(0)
                    response=send_file(stream,mimetype='image/png')
                    response.headers['X-Section-Manifest-SHA256']=preview.source.manifest_hash
                    response.headers['X-Section-Comparison']=info['comparison']
                    return response
                if path in ('/api/status','/api/regions','/api/regions_unity','/api/version'):
                    return None
                if MESH_ROUTE.fullmatch(path):
                    return None
            if request.method=='POST':
                if path in ('/api/allen_mesh/batch','/api/probe_contact_profile'):
                    return None
                if path=='/api/mri/appearance':
                    body=request.get_json(silent=True)
                    if not isinstance(body,dict) or type(body.get('useMriAppearance')) is not bool:
                        raise SectionError('A boolean useMriAppearance field is required')
                    mode=body.get('overlayMode',preview.overlay)
                    if mode not in ('mri_only','ccf_only','mri_ccf_overlay'):
                        raise SectionError('Unsupported appearance mode')
                    if 'showCcfLabels' in body and type(body['showCcfLabels']) is not bool:
                        raise SectionError('showCcfLabels must be boolean')
                    with preview.lock:
                        preview.enabled=body['useMriAppearance'];preview.overlay=mode
                        preview.labels=body.get('showCcfLabels',preview.labels)
                    return jsonify(preview.appearance())
            return jsonify(dict(ok=False,error='Research preview permits inspection only',
                code='research_preview_read_only',planningEligible=False)),409
        except (SectionError,ValueError,TypeError) as exc:
            return jsonify(dict(ok=False,error=str(exc),code='research_preview_unavailable',planningEligible=False)),409

    @app.after_request
    def research_display_response(response):
        if not request.path.startswith('/api/'):
            return response
        if request.method=='GET' and request.path=='/api/status' and response.status_code==200:
            response=app.make_response(jsonify(preview.status(response.get_json() or {})))
        elif request.path=='/api/probe_contact_profile' and response.status_code==200:
            data=response.get_json() or {}
            data.update(scope='ccf_atlas_contact_inspection_only',planningEligible=False,
                        registrationPreview=preview.identity())
            response=app.make_response(jsonify(data))
        response.headers['Cache-Control']='no-store'
        response.headers['X-ProbePlanner-Research-Only']='true'
        response.headers['X-Section-Manifest-SHA256']=preview.source.manifest_hash
        return response
    return preview
