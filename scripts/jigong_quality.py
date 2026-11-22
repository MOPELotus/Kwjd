"""Verify actual Codex quality evidence and receipt-gated chapter delivery.

This validates evidence integrity; it never invents a review or marks quality.
"""
import argparse
import json
from pathlib import Path
import re
from episode_lifecycle import cleanup,load,sha256,validate_receipt

EPISODE='S03E02'
MASTER='0aaa63c922cd92e211c3515003169aed8c466251b66f4b233e539cb62cb0baa6'

def verify_quality(root,quality=None):
    root=Path(root).resolve();post=root/'post'
    quality=Path(quality) if quality else post/'reports'/f'{EPISODE}.quality.json'
    data=load(quality);report=load(post/'reports'/f'{EPISODE}.json')
    if (data.get('schema_version')!=1 or data.get('episode')!=EPISODE
        or data.get('master_sha256')!=MASTER or data.get('review_complete') is not True
        or data.get('final_sha256')!=report.get('sha256')
        or data.get('final_bytes')!=report.get('bytes')
        or report.get('master_sha256')!=MASTER
        or report.get('final_decode_checked') is not True or report.get('final_pcm_equivalent') is not True):
        raise ValueError('Quality identity or actual final technical proof differs')
    if type(data.get('final_bytes')) is not int or data['final_bytes']<=0:
        raise ValueError('Actual final byte count required')
    for section,key in [('subtitles','srt_sha256'),('subtitles','ass_sha256'),('audio','decoded_pcm_sha256')]:
        if not re.fullmatch(r'[0-9a-f]{64}',data.get(section,{}).get(key,'')):
            raise ValueError('Actual asset hash required: '+key)
    reviewer=data.get('reviewed_by',{})
    if reviewer.get('kind')!='codex' or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',reviewer.get('session_id','')):
        raise ValueError('Actual Codex review session required')
    if not isinstance(data.get('accepted_limitations'),list):raise ValueError('Explicit accepted review limits required')
    for section in ('visual','subtitles','synchronization','audio'):
        record=data.get(section,{})
        if (record.get('checked') is not True or record.get('issues')!=[]
            or not isinstance(record.get('scope'),str) or not record['scope'].strip()
            or not isinstance(record.get('evidence'),list) or not record['evidence']):
            raise ValueError('Incomplete actual review section: '+section)
        for item in record['evidence']:
            path=Path(item['path'])
            if (not path.is_absolute() or not path.resolve().is_relative_to(post.resolve())
                or path.is_symlink() or not path.is_file() or sha256(path)!=item.get('sha256')):
                raise ValueError('Quality evidence changed or escaped post root')
    if (data['audio'].get('source_pcm_equal') is not True
        or data['audio'].get('decoded_pcm_sha256')!=report.get('decoded_pcm_sha256')):
        raise ValueError('Quality audio is not original-mix PCM bound')
    if (data['subtitles'].get('srt_sha256')!=report.get('srt_sha256')
        or data['subtitles'].get('ass_sha256')!=report.get('fused_subtitle_binding',{}).get('ass_sha256')):
        raise ValueError('Quality subtitle assets differ from encoded final')
    return dict(quality_path=str(quality),quality_sha256=sha256(quality),**data)

def delivery_step(root):
    root=Path(root);post=root/'post'
    receipt=post/'receipts'/f'{EPISODE}.json'
    if not receipt.exists():return dict(stage='WAIT_DELIVERY')
    record=load(receipt);validate_receipt(record,EPISODE,'linux')
    quality=verify_quality(root)
    if (record.get('sha256')!=quality['final_sha256'] or record.get('master_sha256')!=MASTER
        or record.get('bytes')!=quality['final_bytes']
        or record.get('quality_evidence_sha256')!=quality['quality_sha256']):
        raise ValueError('Windows actual receipt does not bind this reviewed final')
    cleaned=cleanup(root,EPISODE,'linux',receipt,execute=True)
    local=post/'receipts'/f'{EPISODE}.windows-cleanup.json'
    if not local.exists():return dict(stage='WAIT_WINDOWS_CLEANUP',linux_cleanup=cleaned)
    windows=load(local)
    if any(windows.get(key)!=value for key,value in dict(episode=EPISODE,role='windows',state='CLEANED',final_sha256=record['sha256']).items()):
        raise ValueError('Windows cleanup evidence differs from received final')
    return dict(stage='DELIVERED_AND_GENERATED_ARTIFACTS_CLEANED',linux_cleanup=cleaned,windows_cleanup=windows)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True,type=Path);p.add_argument('--delivery-step',action='store_true');a=p.parse_args()
    print(json.dumps(delivery_step(a.root) if a.delivery_step else verify_quality(a.root),ensure_ascii=False,indent=2))
