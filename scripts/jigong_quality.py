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

def valid_reviewer(reviewer, episode):
    """User-authorized Nov27 delegation; preserve actual reviewer provenance."""
    if not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', reviewer.get('session_id', '')):
        return False
    if reviewer.get('kind') == 'codex':
        return True
    ids = reviewer.get('response_ids')
    return (re.fullmatch(r'S03E(?:0[4-9]|1[0-4])', episode or '') is not None
            and reviewer.get('kind') == 'external_omp_responses'
            and reviewer.get('provider') == 'jigong-relay'
            and reviewer.get('endpoint') == 'https://api.zhehentiaohe.cn/v1/responses'
            and reviewer.get('model') == 'gpt-6.1-sol'
            and reviewer.get('reasoning_effort') == 'medium'
            and isinstance(ids, list) and bool(ids)
            and all(isinstance(x, str) and bool(x.strip()) for x in ids))


def master_identity(root,episode):
    restart_path=Path(root)/'.kwjd/jigong-native25-restart.json'
    restarting=restart_path.is_file() and load(restart_path).get('active') is True
    if episode==EPISODE and not restarting:return MASTER
    if not re.fullmatch(r'S03E(?:0[2-9]|1[0-4])',episode):
        raise ValueError('Episode is outside the managed Jigong task')
    receipt=load(Path(root)/'post/handoffs'/f'{episode}.master-received.json')
    digest=receipt.get('sha256','')
    if (receipt.get('episode')!=episode or receipt.get('transfer_verified') is not True
        or not re.fullmatch(r'[0-9a-f]{64}',digest)
        or type(receipt.get('bytes')) is not int or receipt['bytes']<=0):
        raise ValueError('Actual episode master receipt required')
    if restarting:
        from jigong_master_restart import enforce
        enforce(episode,digest,receipt.get('master_frame_rate'))
        if receipt.get('master_frame_rate')!='25/1':
            raise ValueError('Restart quality needs actual native25 receiving receipt')
    return digest


def verify_quality(root,quality=None,episode=EPISODE):
    root=Path(root).resolve();post=root/'post';master=master_identity(root,episode)
    quality=Path(quality) if quality else post/'reports'/f'{episode}.quality.json'
    data=load(quality);report=load(post/'reports'/f'{episode}.json')
    if (data.get('schema_version')!=1 or data.get('episode')!=episode
        or data.get('master_sha256')!=master or data.get('review_complete') is not True
        or data.get('final_sha256')!=report.get('sha256')
        or data.get('final_bytes')!=report.get('bytes')
        or report.get('master_sha256')!=master
        or report.get('final_decode_checked') is not True or report.get('final_pcm_equivalent') is not True):
        raise ValueError('Quality identity or actual final technical proof differs')
    if type(data.get('final_bytes')) is not int or data['final_bytes']<=0:
        raise ValueError('Actual final byte count required')
    for section,key in [('subtitles','srt_sha256'),('subtitles','ass_sha256'),('audio','decoded_pcm_sha256')]:
        if not re.fullmatch(r'[0-9a-f]{64}',data.get(section,{}).get(key,'')):
            raise ValueError('Actual asset hash required: '+key)
    reviewer=data.get('reviewed_by',{})
    if not valid_reviewer(reviewer, episode):
        raise ValueError('Actual authorized review session required')
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

def delivery_step(root,episode=EPISODE):
    root=Path(root);post=root/'post'
    hold=post/'handoffs'/f'{episode}.cleanup-hold.json'
    if hold.is_file():
        dependency=load(hold)
        if dependency.get('active') is True:
            if dependency.get('episode')!=episode or dependency.get('master_sha256')!=master_identity(root,episode):
                raise ValueError('Cleanup dependency hold identity differs')
            return dict(stage='WAIT_MEDIA_DEPENDENCY_HOLD',hold_path=str(hold),
                        reason=dependency.get('reason','Media still needed downstream'))
    receipt=post/'receipts'/f'{episode}.json'
    if not receipt.exists():return dict(stage='WAIT_DELIVERY')
    record=load(receipt);validate_receipt(record,episode,'linux')
    quality=verify_quality(root,episode=episode)
    if (record.get('sha256')!=quality['final_sha256'] or record.get('master_sha256')!=master_identity(root,episode)
        or record.get('bytes')!=quality['final_bytes']
        or record.get('quality_evidence_sha256')!=quality['quality_sha256']):
        raise ValueError('Windows actual receipt does not bind this reviewed final')
    cleaned=cleanup(root,episode,'linux',receipt,execute=True)
    local=post/'receipts'/f'{episode}.windows-cleanup.json'
    if not local.exists():return dict(stage='WAIT_WINDOWS_CLEANUP',linux_cleanup=cleaned)
    windows=load(local)
    if any(windows.get(key)!=value for key,value in dict(episode=episode,role='windows',state='CLEANED',final_sha256=record['sha256']).items()):
        raise ValueError('Windows cleanup evidence differs from received final')
    return dict(stage='DELIVERED_AND_GENERATED_ARTIFACTS_CLEANED',linux_cleanup=cleaned,windows_cleanup=windows)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True,type=Path);p.add_argument('--delivery-step',action='store_true');p.add_argument('--episode',default=EPISODE);a=p.parse_args()
    print(json.dumps(delivery_step(a.root,a.episode) if a.delivery_step else verify_quality(a.root,episode=a.episode),ensure_ascii=False,indent=2))
