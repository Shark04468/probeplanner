"""Explicit CCF frame and unavailable MRI pixels must not inherit legacy trust."""
import base64
import json
from pathlib import Path
import sys

import nibabel as nib
import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from backend.atlas_sections import AtlasSectionSource,SectionError,sha256,slice_array


RAS=np.array([[0,0,.025,0],[-.025,0,0,0],[0,-.025,0,0],[0,0,0,1.]])


def write_volume(path,array,affine=RAS):
    image=nib.Nifti1Image(array,affine);image.header.set_xyzt_units('mm');nib.save(image,path)


def rebind(manifest):
    data=json.loads(manifest.read_text())
    for role,record in data['artifacts'].items():
        record['sha256']=sha256(manifest.parent/record['path'])
    p=manifest.parent/'provenance.json'
    provenance=json.loads(p.read_text())
    provenance['sourceHashes']={k:data['artifacts'][k]['sha256'] for k in
                               ('warped_mri','annotation','sampling_domain','jacobian')}
    p.write_text(json.dumps(provenance))
    data['artifacts']['reconstruction_provenance']['sha256']=sha256(p)
    manifest.write_text(json.dumps(data))


@pytest.fixture
def research_profile(tmp_path):
    shape=(17,13,19);a,d,m=np.indices(shape)
    raw=(1+100*a+10*d+m).astype(np.float32)
    valid=np.ones(shape,np.uint8);valid[3,4,5]=0
    raw[valid==0]=np.nan
    labels=np.ones(shape,np.uint32)*17
    jac=np.ones(shape,np.float32);jac[0]=np.nan;jac[-2,1,1]=-.1
    for name,array in [('mri.nii',raw),('domain.nii',valid),('jacobian.nii',jac)]:
        write_volume(tmp_path/name,array)
    np.save(tmp_path/'annotation.npy',labels)
    files=dict(annotation='annotation.npy',warped_mri='mri.nii',sampling_domain='domain.nii',
               jacobian='jacobian.nii',reconstruction_provenance='provenance.json')
    (tmp_path/'provenance.json').write_text(json.dumps(dict(sourceHashes={},planningEligible=False,anatomyAccepted=False)))
    manifest=tmp_path/'display.json'
    manifest.write_text(json.dumps(dict(schemaVersion='probeplanner.ccf_mri_research_display.v1',
        coordinateFrame='allen_ccf25_original_ras',purpose='research_display_only',planningEligible=False,
        anatomyAccepted=False,profileId='test_research',atlasShape=shape,voxelSizeUm=25,
        artifacts={k:dict(path=v,required=True,sha256=sha256(tmp_path/v)) for k,v in files.items()})))
    rebind(manifest)
    return manifest,raw,labels,valid


def pixels(row):
    return np.frombuffer(base64.b64decode(row['rgbaBase64']),np.uint8).reshape(row['height'],row['width'],4)


def test_mapped_source_and_hash_bound_scope(research_profile):
    source=AtlasSectionSource(research_profile[0])
    assert isinstance(source.mri,np.memmap) and source.mri.mode=='r'
    result=source.section('coronal',source.world_coordinate('coronal',3))
    assert result['coordinateFrame']=='allen_ccf25_original_ras'
    assert result['researchOnly'] is True and result['planningEligible'] is False
    assert 'sampling_domain' in result['sourceHashes']
    assert result['samplingCoverage']<1


@pytest.mark.parametrize('axis,index', [('coronal',3),('horizontal',4),('sagittal',5)])
def test_unknown_mri_not_filled_by_ccf_labels(research_profile,axis,index):
    manifest,raw,labels,valid=research_profile;source=AtlasSectionSource(manifest)
    row=source.section(axis,source.world_coordinate(axis,index))
    rgba=pixels(row);missing=slice_array(valid,axis,index)==0
    assert np.all(rgba[missing]==0)
    ccf=pixels(source.section(axis,source.world_coordinate(axis,index),'ccf_only'))
    assert np.all(ccf[missing,3]==255), 'Atlas-only labels are independent of MRI availability'


@pytest.mark.parametrize('axis,index', [('coronal',3),('horizontal',4),('sagittal',5)])
def test_fractional_slice_requires_both_valid_neighbors(research_profile,axis,index):
    manifest,_,_,valid=research_profile;source=AtlasSectionSource(manifest)
    coordinate=(source.world_coordinate(axis,index)+source.world_coordinate(axis,index+1))/2
    row=source.section(axis,coordinate,'mri_only')
    expected=(slice_array(valid,axis,index)>0)&(slice_array(valid,axis,index+1)>0)
    rgba=pixels(row)
    assert np.all(rgba[~expected]==0)
    assert row['samplingCoverage']==pytest.approx(expected.mean())


def test_original_grid_has_same_array_world_placement_without_reorientation(research_profile):
    source=AtlasSectionSource(research_profile[0]);row=source.section('z',5.4-3*.025,'mri_only')
    np.testing.assert_allclose(row['origin'],[-5.7125,.4525,5.325])
    np.testing.assert_allclose(row['u'],[19*.025,0,0])
    np.testing.assert_allclose(row['v'],[0,-13*.025,0])


def test_integer_coordinate_roundoff_does_not_require_the_next_slice(research_profile):
    manifest,raw,_,valid=research_profile
    raw[1,4,5]=np.nan;valid[1,4,5]=0
    write_volume(manifest.parent/'mri.nii',raw)
    write_volume(manifest.parent/'domain.nii',valid);rebind(manifest)
    source=AtlasSectionSource(manifest)
    row=source.section('z',source.world_coordinate('z',2),'mri_only')
    assert row['samplingCoverage']==1., 'A zero-weight neighboring slice must not censor valid MRI'
    assert row['sampleIndex']==2.


def test_jacobian_missing_and_negative_remain_visible(research_profile):
    source=AtlasSectionSource(research_profile[0])
    img,_=source.qc_image('coronal',0,'jacobian')
    assert np.all(img==[214,210,208])
    img,_=source.qc_image('coronal',15,'jacobian')
    np.testing.assert_array_equal(img[1,1],[0,0,0])


@pytest.mark.parametrize('field,value',[('planningEligible',True),('anatomyAccepted',True),
    ('coordinateFrame','legacy'),('purpose','planning'),('valid',True),('voxelSizeUm',50)])
def test_manifest_cannot_silently_promote_preview(research_profile,field,value):
    manifest=research_profile[0];data=json.loads(manifest.read_text());data[field]=value
    manifest.write_text(json.dumps(data))
    with pytest.raises(SectionError):AtlasSectionSource(manifest)


@pytest.mark.parametrize('mutation',['wrong_affine','domain_not_binary','nan_valid','finite_missing','domain_affine',
                                   'provenance','hash','removed_binding','file_change'])
def test_bad_data_and_bindings_fail_closed(research_profile,mutation):
    manifest,raw,_,valid=research_profile;root=manifest.parent
    if mutation=='wrong_affine':
        write_volume(root/'mri.nii',raw,np.diag([-.025,-.025,.025,1.]));rebind(manifest)
    elif mutation=='domain_affine':
        write_volume(root/'domain.nii',valid,np.eye(4));rebind(manifest)
    elif mutation in ('domain_not_binary','nan_valid','finite_missing'):
        valid=valid.copy()
        if mutation=='domain_not_binary':valid[3,4,5]=2
        elif mutation=='nan_valid':valid[3,4,5]=1
        else:valid[2,2,2]=0
        write_volume(root/'domain.nii',valid);rebind(manifest)
    elif mutation=='provenance':
        p=root/'provenance.json';data=json.loads(p.read_text());data['planningEligible']=True
        p.write_text(json.dumps(data));rebind(manifest)
    elif mutation in ('hash','removed_binding'):
        data=json.loads(manifest.read_text())
        if mutation=='hash':data['artifacts']['sampling_domain']['sha256']='0'*64
        else:del data['artifacts']['sampling_domain']
        manifest.write_text(json.dumps(data))
    else:
        source=AtlasSectionSource(manifest)
        with (root/'domain.nii').open('ab') as f:f.write(b'changed')
        with pytest.raises(SectionError,match='changed'):source.section('z',5.3)
        return
    with pytest.raises(SectionError):AtlasSectionSource(manifest)
