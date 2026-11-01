"""Build a relocatable, byte-verified deployment copy without rewriting old evidence."""
import argparse
from copy import deepcopy
import hashlib
import json
import shutil
from pathlib import Path


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8')


def prepare(source, structures, output):
    source, structures, output = source.resolve(), structures.resolve(), output.resolve()
    if output.exists():
        raise ValueError('Output must not already exist; prior attempts are preserved')
    original = read(source/'artifact_manifest.json')
    output.mkdir(parents=True)
    copied = []
    artifacts = {}

    def copy_bound(src, relative, expected=None):
        actual = sha(src)
        if expected and actual != expected:
            raise ValueError(f'Source hash mismatch: {relative}')
        dst = output/relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        if sha(dst) != actual:
            raise ValueError(f'Copy hash mismatch: {relative}')
        copied.append({'source': str(src), 'path': relative, 'sha256': actual, 'bytes': dst.stat().st_size})
        return {'path': relative, 'sha256': actual, 'required': True, 'exists': True}

    locations = {'annotation': 'annotation.npy', 'region_map': 'region_map.json',
                 'stats': 'stats.json', 'registration_summary': 'registration/registration_summary.json',
                 'jacobian': 'jacobian.npy', 'skull_mask': 'skull_mask.npy',
                 'outer_tissue_mask': 'outer_tissue_mask.npy', 'layered_sdf': 'layered_sdf.npz'}
    for key, relative in locations.items():
        old = original['artifacts'][key]
        archived_relative = 'provenance/original_'+Path(relative).name if key in {'stats', 'registration_summary'} else relative
        record = copy_bound(source/relative, archived_relative, old['sha256'])
        artifacts[key] = {**old, **record}
    # Keep both maps bound; the explicit paper loader initializes from the same v2 map as regression.
    artifacts['region_map_v2'] = copy_bound(source/'region_map_v2.json', 'region_map_v2.json')
    artifacts['structures'] = copy_bound(structures, 'structures.json')
    warped = source/'registration/ambmc_in_ccf.nii.gz'
    if warped.exists():
        artifacts['warped_mri'] = copy_bound(warped, 'registration/ambmc_in_ccf.nii.gz')
    stats = read(source/'stats.json')
    for field, key in {'annotationPath': 'annotation', 'regionMapPath': 'region_map',
                       'statsPath': 'stats', 'jacobianPath': 'jacobian', 'skullMaskPath': 'skull_mask',
                       'outerTissueMaskPath': 'outer_tissue_mask', 'layeredSdfPath': 'layered_sdf',
                       'registrationSummaryPath': 'registration_summary', 'warpedMriPath': 'warped_mri'}.items():
        if key in artifacts:
            stats[field] = locations.get(key, artifacts[key]['path'])
    stats['anatomicalTreAvailable'] = False
    stats['deploymentDerivation'] = {'sourceSha256': original['artifacts']['stats']['sha256'],
                                    'changes': 'Paths relocated; anatomical TRE availability explicitly false; historical numeric values unchanged'}
    summary = read(source/'registration/registration_summary.json')
    summary['anatomicalTreAvailable'] = False
    summary['deploymentDerivation'] = {'sourceSha256': original['artifacts']['registration_summary']['sha256'],
                                      'changes': 'Deployment scope added; historical registration fields retained as archival metadata'}
    for key, value in [('stats', stats), ('registration_summary', summary)]:
        relative = locations[key]
        write(output/relative, value)
        artifacts[key] = {**artifacts[key], 'path': relative, 'sha256': sha(output/relative),
                          'derivedMetadata': True}
    surface_path = source/'mri_surfaces/surface_manifest.json'
    if surface_path.exists():
        surface = read(surface_path)
        original_surface_hash = sha(surface_path)
        for layer, info in surface.get('layers', {}).items():
            for lod, item in info.get('lods', {}).items():
                name = f'{layer}.lod{lod}.obj'
                relative = 'mri_surfaces/'+name
                artifacts[f'surface_{layer}_{lod}'] = copy_bound(surface_path.parent/name, relative, item['sha256'])
                item['path'] = name
            if '0' in info.get('lods', {}):
                info['canonicalPath'] = info['lods']['0']['path']
        surface['profileDir'] = '..'
        surface['derivedFromManifestSha256'] = original_surface_hash
        write(output/'mri_surfaces/surface_manifest.json', surface)
        artifacts['mri_surface_meshes'] = {'path': 'mri_surfaces/surface_manifest.json', 'required': True,
                                         'sha256': sha(output/'mri_surfaces/surface_manifest.json')}
        copy_bound(surface_path, 'provenance/original_surface_manifest.json')
    copy_bound(source/'artifact_manifest.json', 'provenance/original_artifact_manifest.json')
    manifest = deepcopy(original)
    manifest.update(profileId='paper_population_template', artifacts=artifacts, valid=True,
                    validityScope='File integrity for atlas-space software planning; not anatomical registration validity',
                    anatomicalTreAvailable=False, nativeSpaceSafetyValidated=False,
                    derivation={'originalManifestSha256': sha(source/'artifact_manifest.json'),
                                'preparationScriptSha256': sha(Path(__file__)),
                                'changes': 'Relative deployment paths and explicit scope; image/mask/SDF bytes and numeric metrics unchanged; original metadata retained separately'})
    write(output/'artifact_manifest.json', manifest)
    contract = {'schemaVersion': 'probeplanner.paper_profile.v1', 'files': {},
                'atlasMode': 'mri_ccf_hybrid', 'ontologyInitialization': 'all supplied structure records; left-hemisphere centroid convention',
                'scope': 'Population-template demonstration and finite-pool software validation; not native surgical safety'}
    for path in sorted(output.rglob('*')):
        if path.is_file():
            contract['files'][path.relative_to(output).as_posix()] = sha(path)
    write(output/'paper_profile.json', contract)
    # Local-only source paths stay outside the deployable profile contract.
    write(output.parent/(output.name+'_copy_provenance.json'), {'files': copied,
           'paperProfileSha256': sha(output/'paper_profile.json'), 'originalInputsUnchanged': all(sha(Path(r['source']))==r['sha256'] for r in copied)})
    return {'files': len(contract['files']), 'paper_profile_sha256': sha(output/'paper_profile.json')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-profile', type=Path, required=True)
    parser.add_argument('--structures', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source_profile, args.structures, args.output)), flush=True)
