"""Lossless source handoff before releasing a transferred Linux master copy.

These are stage receipts, not Windows final-delivery/episode-cleanup receipts.
Finals remain governed by the shared episode_lifecycle module.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile

from enhance_stage import atomic_json, check_space, probe, sha256, validate_video


def release_verified_file(path, expected_hash):
    """Isolate by same-filesystem rename, then hash/delete the isolated inode.

    The transfer process does not share the Worker lock. A same-name replacement
    racing the rename is preserved/restored if its hash differs. No deletion ever
    targets the public pathname after hashing it.
    """
    path = Path(path)
    directory = Path(tempfile.mkdtemp(prefix='.'+path.name+'.release-',dir=path.parent))
    isolated = directory/'artifact'
    journal = directory/'receipt.json'
    record = {'original_path':str(path),'sha256':expected_hash,'status':'isolation_intent'}
    atomic_json(journal,record)
    os.rename(path,isolated)
    if isolated.is_symlink() or sha256(isolated) != expected_hash:
        record['status']='replacement_preserved';atomic_json(journal,record)
        try:
            os.link(isolated,path,follow_symlinks=False)
        except FileExistsError:
            pass  # A still newer transfer owns the public path; keep both.
        else:
            isolated.unlink()
        raise ValueError('Same-name replacement changed during release; preserved, never deleted')
    record['status']='verified_isolated';atomic_json(journal,record)
    isolated.unlink()
    record['status']='released';atomic_json(journal,record)
    return str(journal)


def resume_verified_release(path, expected_hash):
    """Recover only private isolation journals for this exact path and hash."""
    path=Path(path);completed=[]
    for directory in path.parent.glob('.'+path.name+'.release-*'):
        if directory.is_symlink() or not directory.is_dir() or directory.stat().st_uid != os.getuid():
            continue
        journal=directory/'receipt.json'
        if not journal.is_file() or journal.is_symlink():continue
        record=json.loads(journal.read_text())
        if (record.get('original_path') != str(path) or record.get('sha256') != expected_hash
                or record.get('status') not in ('isolation_intent','verified_isolated','released')):
            continue
        artifact=directory/'artifact'
        if artifact.exists() or artifact.is_symlink():
            if artifact.is_symlink() or sha256(artifact) != expected_hash:
                raise ValueError('Interrupted release contains a replacement; retained for inspection')
            artifact.unlink()
        record['status']='released';atomic_json(journal,record);completed.append(str(journal))
    return completed


def decoded_pcm_hash(path):
    result = subprocess.run([
        'ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-i', str(path),
        '-map', '0:a:0', '-vn', '-c:a', 'pcm_s32le', '-f', 'hash',
        '-hash', 'sha256', '-'], check=True, capture_output=True, text=True)
    line = result.stdout.strip()
    if not line.startswith('SHA256=') or len(line) != 71:
        raise ValueError('FFmpeg did not provide a decoded PCM SHA256')
    return line[7:]


def decoded_video_frames(path, log):
    with Path(log).open('a') as output:
        result = subprocess.run(['ffmpeg','-nostdin','-v','error','-xerror',
            '-err_detect','explode','-i',str(path),'-map','0:v:0','-an',
            '-progress','pipe:1','-nostats','-f','null','-'],
            stdout=subprocess.PIPE,stderr=output,check=True,text=True)
    counts = [int(line.split('=',1)[1]) for line in result.stdout.splitlines() if line.startswith('frame=')]
    if not counts or counts[-1] <= 0:
        raise ValueError('Full decode did not prove the final video frame count')
    return counts[-1]


def validate_audio(source_probe, audio_probe):
    streams = [s for s in source_probe['streams'] if s['codec_type'] == 'audio']
    if len(streams) != 1:
        raise ValueError('Source handoff requires exactly one mixed audio stream')
    original = streams[0]
    audio = [s for s in audio_probe['streams'] if s['codec_type'] == 'audio']
    if len(audio) != 1:
        raise ValueError('Preserved mixed track must be a single stream')
    saved = audio[0]
    if (saved.get('codec_name') != 'flac'
            or int(saved.get('bits_per_raw_sample', 0)) != 24
            or int(saved.get('sample_rate', 0)) < 48000
            or saved.get('sample_rate') != original.get('sample_rate')
            or saved.get('channels') != original.get('channels')):
        raise ValueError('Preserved FLAC must retain source channels/rate at24bit >=48kHz')
    video = next(s for s in source_probe['streams'] if s['codec_type'] == 'video')
    return float(original.get('start_time', 0)) - float(video.get('start_time', 0))


def validate_bundle(bundle_path, enhanced, expected_hash):
    record = json.loads(Path(bundle_path).read_text())
    if (record.get('schema_version') != 1
            or Path(bundle_path).name != record.get('episode','')+'.source.ready.json'
            or record.get('master_sha256') != expected_hash
            or record.get('enhanced_sha256') != sha256(enhanced)
            or record.get('enhanced_decode_checked') is not True):
        raise ValueError('Source handoff does not bind expected master/enhancement')
    # Audio path is fixed next to the receipt; never trust a receipt-supplied path.
    audio = Path(bundle_path).with_name(record['episode'] + '.mixed.flac')
    if record.get('audio_sha256') != sha256(audio):
        raise ValueError('Preserved mixed audio hash differs; retain all existing assets')
    validate_video(record['source_probe'], probe(enhanced))
    offset = validate_audio(record['source_probe'], probe(audio))
    if offset != record.get('audio_offset_seconds'):
        raise ValueError('Preserved audio timing receipt differs')
    return record, audio


def preserve_source(master, enhanced, checkpoint, bundle_path, episode, log):
    """Recover a hash-bound audio handoff without repeating AI or deleting source.

    An intent identifies the exact source/enhancement and the private audio inode.
    Completed audio is fully rechecked before publication; only an owned partial
    which cannot decode may be isolated and released for an audio-only retry.
    """
    master, enhanced, bundle_path = map(Path, (master, enhanced, bundle_path))
    expected = checkpoint['master_sha256']
    if bundle_path.exists():
        return validate_bundle(bundle_path, enhanced, expected)
    if checkpoint['sha256'] != sha256(enhanced) or sha256(master) != expected:
        raise ValueError('Source/enhancement differs before lossless handoff')
    src = probe(master)
    validate_video(src, probe(enhanced))
    audio = bundle_path.with_name(episode + '.mixed.flac')
    partial = audio.with_name(audio.stem + '.partial.flac')
    intent_path = bundle_path.with_name(episode + '.source.pending.json')
    binding = {'schema_version':1, 'episode':episode, 'master_sha256':expected,
               'enhanced_sha256':checkpoint['sha256'], 'enhanced_checkpoint':checkpoint,
               'audio_path':str(audio), 'partial_path':str(partial)}
    intent = None
    if intent_path.exists() or intent_path.is_symlink():
        if intent_path.is_symlink():
            raise ValueError('Audio handoff intent is a symlink; retained')
        intent = json.loads(intent_path.read_text())
        if any(intent.get(k) != v for k,v in binding.items()):
            raise ValueError('Audio handoff intent differs from source/enhancement; retained')
    for path in (audio,partial):
        if path.is_symlink():
            raise ValueError('Orphan preserved audio/partial symlink retained')
    if (audio.exists() or partial.exists()) and intent is None:
        raise ValueError('Orphan preserved audio/partial retained; matching intent required')
    if audio.exists() and partial.exists() and not os.path.samefile(audio,partial):
        raise ValueError('Different preserved audio and partial both exist; retained')
    seconds = float(src['format']['duration'])
    stream = next(s for s in src['streams'] if s['codec_type'] == 'audio')
    rate = int(stream['sample_rate']);channels = int(stream['channels'])
    if rate < 48000 or int(stream.get('bits_per_raw_sample', stream.get('bits_per_sample', 0))) != 24:
        raise ValueError('Expected native24bit >=48kHz mixed master; no silent precision conversion')

    def owned_partial():
        if not partial.is_file() or partial.is_symlink() or not intent:
            return False
        stat = partial.stat()
        return intent.get('partial_inode') == [stat.st_dev,stat.st_ino]

    # An interrupted isolated release must be completed before opening a new inode.
    if intent and intent.get('status') == 'partial_release_intent':
        digest = intent.get('released_partial_sha256')
        if not isinstance(digest,str) or len(digest) != 64:
            raise ValueError('Invalid partial release intent; retained')
        resume_verified_release(partial,digest)
        if partial.exists():
            if not owned_partial() or sha256(partial) != digest:
                raise ValueError('Partial changed during release recovery; retained')
            release_verified_file(partial,digest)
        intent['status']='partial_released';atomic_json(intent_path,intent)
    candidate = audio if audio.exists() else partial if partial.exists() else None
    recovered_encoding_partial = bool(candidate == partial and intent
                                      and intent.get('status') == 'encoding' and owned_partial())
    if candidate:
        if intent.get('status') == 'audio_verified':
            if intent.get('audio_sha256') != sha256(candidate):
                raise ValueError('Pending preserved audio hash differs; retained')
        elif candidate == audio or not owned_partial() or intent.get('status') != 'encoding':
            raise ValueError('Orphan audio does not match a recoverable handoff intent; retained')
        try:
            audio_probe = probe(candidate)
            offset = validate_audio(src,audio_probe)
            saved_pcm = decoded_pcm_hash(candidate)
        except (subprocess.CalledProcessError,ValueError,KeyError,StopIteration):
            # A previously hash-verified asset or public audio is never discarded.
            if candidate != partial or intent.get('status') != 'encoding' or not owned_partial():
                raise
            digest = sha256(partial)
            intent.update(status='partial_release_intent',released_partial_sha256=digest)
            atomic_json(intent_path,intent)
            journal=release_verified_file(partial,digest)
            intent.update(status='partial_released',partial_release_receipt=journal)
            atomic_json(intent_path,intent)
            candidate=None
    if candidate is None:
        recovered_encoding_partial = False
        if intent and intent.get('status') == 'audio_verified':
            raise ValueError('Verified pending audio is missing; retained for inspection')
        check_space(bundle_path.parent, int(seconds * rate * channels * 4 + 16*1024**2), 25)
        # Exclusive creation and inode binding allow recovery without trusting a
        # same-name file created by a different process. FFmpeg writes this fd.
        with partial.open('xb') as audio_out:
            os.chmod(partial,0o600)
            stat=os.fstat(audio_out.fileno())
            intent={**binding,'status':'encoding','partial_inode':[stat.st_dev,stat.st_ino],
                    'previous_release':intent if intent else None}
            atomic_json(intent_path,intent)
            with Path(log).open('a') as output:
                subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-xerror',
                    '-i', str(master), '-map', '0:a:0', '-vn', '-c:a', 'flac',
                    '-sample_fmt', 's32', '-bits_per_raw_sample', '24', '-f','flac','pipe:1'],
                    stdout=audio_out, stderr=output, check=True)
            audio_out.flush();os.fsync(audio_out.fileno())
        candidate=partial
        offset = validate_audio(src, probe(candidate))
        saved_pcm = decoded_pcm_hash(candidate)
    frames = decoded_video_frames(enhanced,log)
    packet_count = int(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0',
        '-count_packets','-show_entries','stream=nb_read_packets','-of','csv=p=0',str(master)],text=True).strip())
    if frames != (packet_count*12+4)//5:
        raise ValueError('Enhanced frame count does not preserve the entire25fps master')
    pcm = decoded_pcm_hash(master)
    if pcm != saved_pcm:
        # FLAC truncated exactly at a complete block can probe and decode cleanly
        # while containing fewer samples. Only the interrupted, inode-owned
        # encoding partial may be released; newly encoded or already hash-verified
        # files remain evidence of a genuine encoder/source mismatch.
        if recovered_encoding_partial and candidate == partial and owned_partial():
            if sha256(master) != expected or sha256(enhanced) != checkpoint['sha256']:
                raise ValueError('Source/enhancement changed during handoff; source retained')
            digest = sha256(partial)
            intent.update(status='partial_release_intent',released_partial_sha256=digest,
                          partial_release_reason='decoded PCM differs from complete source',
                          incomplete_decoded_pcm_sha256=saved_pcm,expected_decoded_pcm_sha256=pcm)
            atomic_json(intent_path,intent)
            journal=release_verified_file(partial,digest)
            intent.update(status='partial_released',partial_release_receipt=journal)
            atomic_json(intent_path,intent)
            return preserve_source(master,enhanced,checkpoint,bundle_path,episode,log)
        raise ValueError('Decoded PCM is not sample-identical; source retained')
    if sha256(master) != expected or sha256(enhanced) != checkpoint['sha256']:
        raise ValueError('Source/enhancement changed during handoff; source retained')
    record = {'schema_version': 1, 'episode': episode, 'master_sha256': expected,
              'enhanced_sha256': checkpoint['sha256'], 'enhanced_checkpoint': checkpoint,
              'audio_sha256': sha256(candidate), 'decoded_pcm_sha256': pcm,
              'audio_offset_seconds': offset, 'source_probe': src,
              'enhanced_decode_checked': True, 'pcm_equivalent': True,
              'enhanced_frame_count':frames}
    intent.update(status='audio_verified',audio_sha256=record['audio_sha256'])
    atomic_json(intent_path,intent)
    if not audio.exists():
        os.link(partial,audio)
    if partial.exists():
        if not os.path.samefile(partial,audio):
            raise ValueError('Partial replaced during audio publication; retained')
        partial.unlink()
    atomic_json(bundle_path, record)
    return record, audio


def release_master_copy(post, master, bundle_path, enhanced, expected_hash, *, worker_lock_held=False):
    """Release only this exact transferred copy after verifying the durable handoff."""
    if not worker_lock_held:
        raise ValueError('Worker lock required for stage cleanup')
    post, master = Path(post), Path(master)
    record, _ = validate_bundle(bundle_path, enhanced, expected_hash)
    if (not record.get('pcm_equivalent') or master.is_symlink()
            or master.parent.resolve() != (post/'incoming').resolve()
            or master.name not in [record['episode']+'.clean-master'+ext for ext in ('.mov','.mkv','.mp4')]):
        raise ValueError('Stage cleanup restricted to exact received master copy')
    audit = Path(bundle_path).with_name(record['episode']+'.master-released.json')
    if not master.exists():
        if audit.exists() and json.loads(audit.read_text()).get('master_sha256') == expected_hash:
            resume_verified_release(master,expected_hash)
            return
        raise ValueError('Missing master without stage-release audit')
    if sha256(master) != expected_hash:
        raise ValueError('Same-name replacement differs; must never delete it')
    # Persist intent before unlink so a crash is recoverable and traceable.
    decision = {'episode':record['episode'], 'master_sha256':expected_hash,
                'relative_path':'incoming/'+master.name, 'bundle_sha256':sha256(bundle_path),
                'status':'verified_release_intent'}
    atomic_json(audit, decision)
    decision['isolation_receipt'] = release_verified_file(master,expected_hash)
    decision['status'] = 'released'
    atomic_json(audit, decision)


def release_enhancement_inputs(post, episode, enhanced, audio, final, report, *, worker_lock_held=False):
    """After full final decode/PCM acceptance, retain the final and compact evidence.

    This never removes the final: only the independently matched previous-stage
    video/audio files. The journal permits idempotent recovery after a crash.
    """
    if not worker_lock_held:
        raise ValueError('Worker lock required for stage cleanup')
    post, enhanced, audio, final = map(Path,(post,enhanced,audio,final))
    if (report.get('episode') != episode or report.get('final_decode_checked') is not True
            or report.get('final_pcm_equivalent') is not True
            or report.get('sha256') != sha256(final)):
        raise ValueError('Final acceptance receipt missing or obsolete; previous stage retained')
    directory = post/'cache'/episode
    if (enhanced != directory/(episode+'.enhanced.mkv') or audio != directory/(episode+'.mixed.flac')
            or final.parent.resolve() != (post/'final').resolve()):
        raise ValueError('Previous-stage cleanup outside fixed episode paths')
    expected = [(enhanced,report['enhanced_sha256']),(audio,report['preserved_audio_sha256'])]
    journal = directory/(episode+'.enhancement-released.json')
    prior = json.loads(journal.read_text()) if journal.exists() else {}
    for path,digest in expected:
        if path.is_symlink() or (path.exists() and sha256(path) != digest):
            raise ValueError('Previous-stage file changed; never delete replacement')
        if not path.exists() and (prior.get('final_sha256') != report['sha256']
                or prior.get('artifacts',{}).get(path.name) != digest):
            raise ValueError('Missing previous-stage file without release journal')
        if not path.exists():resume_verified_release(path,digest)
    if all(not p.exists() for p,_ in expected) and prior.get('status') == 'released':
        return
    decision = {'episode':episode,'final_sha256':report['sha256'],
                'artifacts':{p.name:h for p,h in expected},'status':'verified_release_intent'}
    atomic_json(journal,decision)
    decision['isolation_receipts'] = []
    for path,digest in expected:
        if path.exists():decision['isolation_receipts'].append(release_verified_file(path,digest))
    decision['status']='released';atomic_json(journal,decision)
