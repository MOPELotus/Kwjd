"""Production policy locked independently of disposable machine-local assets."""
import json
from pathlib import Path


LOCK=Path(__file__).resolve().parents[1]/'config/workflows/native-face-v1.json'


def validate_fixed_profile(cfg):
    lock=json.loads(LOCK.read_text())
    expected=dict(rife_backend=lock['production_rife_backend'],restoration_backend='cuda_gfpgan_fused',
        restore=True,restore_gpu=lock['devices']['face']['index'],
        restore_gpu_name=lock['devices']['face']['name'],
        restore_memory_gib=lock['devices']['face']['allocation_cap_gib'],
        restore_free_reserve_gib=lock['devices']['face']['free_reserve_gib'],
        gpu=lock['devices']['rife']['index'],gpu_name=lock['devices']['rife']['name'],
        encoder_gpu=lock['devices']['nvenc_index'],light_filter=lock['picture']['light_filter'],
        max_video_mbps=lock['encode']['maxrate_mbps'],
        segment_source_frames=lock['scheduling']['segment_source_frames'],
        pipeline_prefetch=True,pipeline_prefetch_frames=lock['scheduling']['queued_cpu_frames'],
        resource_wait_in_process=lock['scheduling']['resource_wait_in_process'])
    changed=[name for name,value in expected.items() if cfg.get(name)!=value or
             (isinstance(value,(bool,int)) and type(cfg.get(name)) is not type(value))]
    if type(cfg.get('input_frame_rate')) is not int or cfg['input_frame_rate'] not in lock['picture']['input_fps']:
        changed.append('input_frame_rate')
    if changed:raise ValueError('Fixed production workflow differs: '+', '.join(changed))
    return lock['workflow_id']
