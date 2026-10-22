"""Acceptance of scene edits: the resulting frames, preserved assets and ownership."""
import base64
import json
import subprocess
from uuid import uuid4

import pytest
from sqlalchemy import select

from tests.test_video_studio import state, error, PNG
from tests.test_video_autopilot import auto, mock_gateway
from postbridge.models.domain import MediaAssetOrm, VideoMaterialOrm
from postbridge.services.video_studio import VideoCreate, VideoRevise, create_project, revise_project, get_project, store_video_material
from postbridge.services.video_library import list_materials, update_material, MaterialUpdate
from postbridge.services.video_media import inspect_media
from postbridge.workers import video_tasks


def test_library_is_reusable_searchable_and_tenant_scoped(state):
    session, tenant, other, material, _ = state
    aid = material['media_asset_id']
    updated = update_material(session, tenant, aid, MaterialUpdate(name='Demo screen', folder='Launch'))
    assert updated['width'] == 1 and updated['kind'] == 'image'
    assert list_materials(session, tenant, folder='Launch', search='Demo')['items'][0]['asset_id'] == aid
    assert list_materials(session, tenant, search='%')['items'] == []
    assert list_materials(session, other)['items'] == []
    assert list_materials(session, other)['folders'] == []
    with error('VIDEO_ASSET_NOT_FOUND'):
        update_material(session, other, aid, MaterialUpdate(name='Stolen'))


@pytest.mark.parametrize("replace_clip", [False, True])
def test_scene_regeneration_reuses_other_visuals_and_costs_only_one_image(auto, monkeypatch, replace_clip):
    session, tenant, _, material, _ = auto
    calls = mock_gateway(monkeypatch)
    original_id = material['media_asset_id']
    if replace_clip:
        tmp = auto[-1]
        clip = tmp / 'original-clip.mp4'
        subprocess.run(['ffmpeg', '-nostdin', '-y', '-v', 'error', '-loop', '1', '-i', str(tmp / 'source.png'),
            '-vf', 'scale=2:2', '-t', '3', '-c:v', 'libx264', '-threads', '1', str(clip)], check=True, capture_output=True, timeout=30)
        original_id = store_video_material(session, tenant, clip)['media_asset_id']
    scenes = [{'asset_id': original_id, 'duration_seconds': 1, 'caption': 'First', 'scene_id': 'opening', **({'source_start_seconds': 1} if replace_clip else {})},
        {'asset_id': material['media_asset_id'], 'duration_seconds': 1, 'caption': 'End', 'purpose': 'cta', 'motion': 'pan_left'}]
    project = create_project(session, tenant, VideoCreate(brief='Demo', request_key=str(uuid4()), scenes=scenes))
    body = VideoRevise(brief='Demo', request_key=str(uuid4()), expected_version=1, scenes=[
        {**scenes[0], 'image_prompt': 'Blue abstract illustration', 'regenerate_image': True}, scenes[1]])
    revised = revise_project(session, tenant, project['id'], body)
    video_tasks.process_video_job.run(revised['current']['job_id'])
    session.expire_all()
    current = get_project(session, tenant, project['id'])['current']
    assert current['status'] == 'succeeded', current
    assert len(calls) == 1 and calls[0][0].endswith('/images/generations')
    assert current['usage']['tokens_charged'] == 20
    assert current['scenes'][0]['asset_id'] != material['media_asset_id']
    assert current['scenes'][0]['scene_id'] == 'opening'
    assert not current['scenes'][0].get('source_start_seconds')
    assert current['scenes'][0]['image_prompt'] == 'Blue abstract illustration'
    assert not current['scenes'][0].get('regenerate_image')
    assert current['scenes'][1]['asset_id'] == material['media_asset_id']
    assert current['scenes'][1]['motion'] == 'pan_left'
    # Replayed requests restore exactly the already paid version.
    replay = revise_project(session, tenant, project['id'], body)
    video_tasks.process_video_job.run(replay['requested_revision']['job_id'])
    assert len(calls) == 1 and replay['current_version'] == 2


def test_image_generation_can_be_the_only_material(auto, monkeypatch):
    session, tenant, _, _, _ = auto
    calls = mock_gateway(monkeypatch)
    project = create_project(session, tenant, VideoCreate(brief='Demo', request_key=str(uuid4()), scenes=[{
        'image_prompt': 'Abstract blue shapes', 'duration_seconds': 1, 'caption': 'Try it'}]))
    video_tasks.process_video_job.run(project['current']['job_id'])
    session.expire_all()
    assert get_project(session, tenant, project['id'])['current']['status'] == 'succeeded'
    assert len(calls) == 1


def test_clip_trim_produces_the_selected_frames_and_rejects_out_of_bounds(state):
    from postbridge.infrastructure.media_storage import copy_media_object_to_file
    session, tenant, _, _, tmp = state
    clip = tmp / 'red-blue.mp4'
    subprocess.run(['ffmpeg', '-nostdin', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=red:s=160x90:r=24:d=2',
        '-f', 'lavfi', '-i', 'color=blue:s=160x90:r=24:d=2', '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[out]',
        '-map', '[out]', '-threads', '1', '-c:v', 'libx264', str(clip)], check=True, capture_output=True, timeout=30)
    source = store_video_material(session, tenant, clip)
    scenes = [{'asset_id': source['media_asset_id'], 'source_start_seconds': 2, 'duration_seconds': 1}]
    project = create_project(session, tenant, VideoCreate(brief='Blue only', request_key=str(uuid4()), scenes=scenes))
    video_tasks.process_video_job.run(project['current']['job_id'])
    session.expire_all()
    current = get_project(session, tenant, project['id'])['current']
    assert current['status'] == 'succeeded'
    output = tmp / 'selected.mp4'
    copy_media_object_to_file(session.get(MediaAssetOrm, current['output_asset_id']).object_key, output)
    pixel = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-ss', '0.5', '-i', str(output),
        '-vf', 'crop=2:2:(iw-2)/2:(ih-2)/2,scale=1:1', '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-threads', '1', 'pipe:1'],
        check=True, capture_output=True, timeout=30).stdout
    assert pixel[2] > 200 and pixel[0] < 30  # The chosen fragment is blue, never the red opening.
    import wave
    from postbridge.services.video_media import render_montage
    audio = tmp / 'expanded-language.wav'
    with wave.open(str(audio), 'wb') as wav:
        wav.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\x00\x00' * 36000)
    voiced_scene = {'asset_id': source['media_asset_id'], 'source_start_seconds': 3, 'duration_seconds': 1.5,
        'speech_asset_id': 'test-audio', 'narration_text': 'A longer translation'}
    voice_dir = tmp / 'voiced'; voice_dir.mkdir()
    voiced = render_montage([voiced_scene], {source['media_asset_id']: clip}, voice_dir, speech_sources={'test-audio': audio})
    assert voiced_scene['source_start_seconds'] == 2.5
    assert 1.4 < inspect_media(voiced)['duration_seconds'] < 1.7
    with error('VIDEO_INVALID_TRIM'):
        create_project(session, tenant, VideoCreate(brief='Invalid trim' , request_key=str(uuid4()),
            scenes=[{**scenes[0], 'source_start_seconds': 3.5}]))


@pytest.mark.parametrize('motion', ['zoom_in', 'pan_left', 'pan_right'])
def test_image_motion_fade_and_final_brand_produce_valid_montage(state, motion):
    session, tenant, _, material, _ = state
    project = create_project(session, tenant, VideoCreate(brief='Branded ending', request_key=str(uuid4()), brand_name='Demo', brand_color='#38bdf8',
        scenes=[{'asset_id': material['media_asset_id'], 'duration_seconds': 1, 'caption': 'Try Demo',
                 'motion': motion, 'transition': 'fade', 'purpose': 'cta'}]))
    video_tasks.process_video_job.run(project['current']['job_id'])
    session.expire_all()
    current = get_project(session, tenant, project['id'])['current']
    assert current['status'] == 'succeeded', current
    assert current['brand_name'] == 'Demo'
    assert current['storyboard']['schema_version'] == 1
    assert current['storyboard']['scenes'][0]['purpose'] == 'cta'
