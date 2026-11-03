"""Second-pass audio triage: coarse-family consensus, then new prompts/windows.

Keeps first-pass receipts and human overrides intact. Cosines are not probabilities.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

import triage_mihoyo_music as base

VERSION = 'family-plus-five-windows-v2'
FAMILIES = {'usable': ('calm', 'cheerful'), 'action': ('intense', 'tense'), 'vocal': ('vocal',)}
PROMPTS = {
    'calm': [
        'Slow gentle instrumental music, peaceful and relaxing.',
        'Quiet reflective piano or strings, soft and mellow, even if sad.',
        'Calm atmospheric exploration background music with little percussion.',
        'A soothing instrumental melody with a gentle unhurried rhythm.'],
    'cheerful': [
        'Light playful instrumental music with a cheerful bouncing rhythm.',
        'Pleasant lively background music for everyday life in a town.',
        'Happy easygoing instrumental music with a gentle dance groove.',
        'Bright relaxed casual jazz or folk music with a tuneful rhythm.'],
    'intense': [
        'Aggressive fast-paced combat music with pounding drums.',
        'High energy battle music with loud dense percussion and driving rhythm.',
        'Intense action soundtrack with forceful repeated rhythmic accents.',
        'Powerful frantic orchestral or electronic fighting music.'],
    'tense': [
        'Threatening suspense music with dissonant rising strings and danger.',
        'Anxious urgent soundtrack with dramatic tension and unsettling sounds.',
        'Menacing horror music that creates fear and suspense.',
        'Tense ominous dramatic music for a dangerous confrontation.'],
    'vocal': [
        'Music with an audible human voice singing lyrics.',
        'A song with a prominent lead singer over the accompaniment.',
        'A group of human voices singing in a choir.',
        'Music featuring sustained human singing or vocal chanting.'],
}


def means(scores):
    return {k: sum(s[k] for s in scores) / len(scores) for k in PROMPTS}


def families(scores):
    return [{name: max(s[k] for k in keys) for name, keys in FAMILIES.items()} for s in scores]


def quick_decision(scores):
    """Disagreement within the same usable/excluded family is not uncertainty."""
    fs = families(scores)
    votes = [max(s, key=s.get) for s in fs]
    avg = {k: sum(s[k] for s in fs) / len(fs) for k in FAMILIES}
    order = sorted(avg, key=avg.get, reverse=True)
    top = order[0]
    margin = avg[top] - avg[order[1]]
    agreement = len(set(votes)) == 1
    dominant = votes.count(top) >= 2 and margin >= .06 and all(max(s.values()) - s[top] <= .07 for s in fs)
    if not (agreement or dominant):
        return None
    avg_labels = means(scores)
    label = max(FAMILIES[top], key=avg_labels.get)
    return dict(category=base.LABELS[label], method='family_consensus' if agreement else 'clear_dominant_family',
                family_margin=margin, family_votes=votes, human_listened=False)


def rhythm_features(clips):
    """Amplitude-normalized spectral onsets; diagnostics, never a mood oracle."""
    import numpy as np
    from scipy.signal import find_peaks
    values = []
    for clip in clips:
        if len(clip) < 2048:
            continue
        x = clip / max(float(np.sqrt(np.mean(clip ** 2))), 1e-7)
        frames = np.lib.stride_tricks.sliding_window_view(x, 2048)[::512]
        spectrum = np.abs(np.fft.rfft(frames * np.hanning(2048), axis=1))
        spectrum /= np.maximum(spectrum.sum(axis=1, keepdims=True), 1e-9)
        flux = np.maximum(np.diff(spectrum, axis=0), 0).sum(axis=1)
        peaks, _ = find_peaks(flux, height=float(np.median(flux) + np.std(flux)), distance=12)
        envelope = flux - flux.mean()
        correlation = np.correlate(envelope, envelope, mode='full')[len(envelope)-1:]
        pulse = float(max(correlation[31:94], default=0) / max(float(correlation[0]), 1e-9))
        values.append(dict(onsets_per_second=len(peaks)/(len(clip)/48000), pulse_strength=max(0, pulse),
                           normalized_flux=float(flux.mean())))
    return values


def resampled_decision(scores, features):
    """Use aggregate evidence; retain only close cross-family/real transitions."""
    avg = means(scores)
    fs = families(scores)
    votes = [max(s, key=s.get) for s in fs]
    fam_avg = {k: sum(s[k] for s in fs)/len(fs) for k in FAMILIES}
    rank = sorted(fam_avg, key=fam_avg.get, reverse=True)
    top = rank[0]
    margin = fam_avg[top] - fam_avg[rank[1]]
    strong = Counter()
    for s in fs:
        keys = sorted(s, key=s.get, reverse=True)
        if s[keys[0]] - s[keys[1]] >= .045:
            strong[keys[0]] += 1
    # Long clips with both clearly quiet and clearly forceful sections stay mixed.
    transition = strong['usable'] >= 2 and strong['action'] >= 2 and margin < .06
    ambiguous = margin < .008 or votes.count(top) < 2
    if transition or ambiguous:
        category = '混合/待复核'
        reason = 'strong_cross_family_transition' if transition else 'close_cross_family_evidence'
    else:
        label = max(FAMILIES[top], key=avg.get)
        # Slow/sad is still usable calm. Do not turn low-volume dark music into
        # action merely because an isolated suspense prompt wins a single window.
        if top == 'usable' and abs(avg['calm'] - avg['cheerful']) < .015 and features:
            # Only resolve the calm/cheerful subtype, never admission/exclusion.
            activity = sum(f['onsets_per_second'] for f in features)/len(features)
            pulse = sum(f['pulse_strength'] for f in features)/len(features)
            if activity < 1.2 and pulse < .2:
                label = 'calm'
        category = base.LABELS[label]
        reason = 'aggregate_family_and_subtype'
    return dict(category=category, method='new_prompt_ensemble_five_windows', reason=reason,
                family_margin=margin, family_votes=votes, strong_family_votes=dict(strong),
                mean_scores=avg, human_listened=False)


def file_sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def publish(args, overrides):
    base.atomic(args.evidence/'refinement-v2/overrides.json', dict(version=VERSION, reviews=overrides))
    rows, assets = base.build(args.root, args.evidence)
    base.export(args.root, args.evidence, rows, assets)
    return rows


_WORKER = None

def init_model(args):
    import torch
    from transformers import ClapModel, ClapProcessor
    torch.set_num_threads(args.threads)
    torch.manual_seed(0)
    processor = ClapProcessor.from_pretrained(args.model, local_files_only=True)
    model = ClapModel.from_pretrained(args.model, local_files_only=True).eval()
    prompt_sha = hashlib.sha256(json.dumps(PROMPTS, sort_keys=True).encode()).hexdigest()
    with torch.inference_mode():
        text = model.get_text_features(**processor(text=[p for v in PROMPTS.values() for p in v], padding=True, return_tensors='pt'))
        text = text/text.norm(dim=-1, keepdim=True)
    return processor,model,text,prompt_sha


def analyse_track(payload):
    global _WORKER
    row,args=payload
    import numpy as np
    import torch
    if _WORKER is None:
        _WORKER=init_model(args)
    processor,model,text,prompt_sha=_WORKER
    try:
        before = Path(row['file']).stat()
        if file_sha(row['file']) != row['sha256']:
            raise ValueError('Source SHA mismatch')
        starts = sorted(set(round(max(0, (row['duration']-10)*p), 3) for p in [.05,.275,.5,.725,.95]))
        clips = []
        for start in starts:
            raw = subprocess.check_output([args.ffmpeg,'-v','error','-ss',str(start),'-i',row['file'],
                  '-t','10','-vn','-ac','1','-ar','48000','-f','f32le','-'], timeout=45)
            clip = np.frombuffer(raw,dtype=np.float32).copy()
            if len(clip) < min(48000, row['duration']*48000*.8):
                raise ValueError('Insufficient sampled audio')
            clips.append(clip)
        features = rhythm_features(clips)
        with torch.inference_mode():
            audio = model.get_audio_features(**processor(audio=clips,sampling_rate=48000,return_tensors='pt',padding=True))
            audio = audio/audio.norm(dim=-1,keepdim=True)
            similarities = (audio@text.T).tolist()
        scores = [{k:sum(v[i*4:i*4+4])/4 for i,k in enumerate(PROMPTS)} for v in similarities]
        decision = resampled_decision(scores, features)
        after = Path(row['file']).stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
            raise ValueError('Source changed')
        review = dict(decision, version=VERSION, source_sha256=row['sha256'], model=base.MODEL,
                      model_revision=getattr(model.config,'_commit_hash',None), prompt_sha256=prompt_sha,
                      windows=[dict(start=t,seconds=min(10,row['duration']-t)) for t in starts],
                      cosine_scores=scores,rhythm_features=features,
                      initial_category=row.get('first_pass_mood', row['mood']),
                      scope='Five distributed samples, aggregate semantics and spectral onsets; not full human listening')
        return row['sha256'],review,None
    except Exception as error:
        return row['sha256'],None,type(error).__name__


def main(args):
    folder = args.evidence/'refinement-v2'
    folder.mkdir(parents=True, exist_ok=True)
    initial = base.read(args.evidence/'catalog.json')['tracks']
    candidates = [t for t in initial if any(s in t.get('first_pass_mood', t['mood']) for s in ['混合', '边界'])]
    unique = {t['sha256']: t for t in candidates}
    overrides = {}
    pending = []
    for sha, row in unique.items():
        receipt = folder/'reviews'/(sha+'.json')
        if receipt.exists():
            review = base.read(receipt)
            if review.get('version') == VERSION and review.get('source_sha256') == sha:
                overrides[sha] = review
                continue
        decision = quick_decision(row['audio_review']['cosine_scores'])
        if decision:
            review = dict(decision, version=VERSION, source_sha256=sha,
                          evidence='Existing SHA-bound three-window first-pass receipt; no new listening',
                          initial_category=row.get('first_pass_mood', row['mood']))
            base.atomic(receipt, review)
            overrides[sha] = review
        else:
            pending.append(row)
    rows = publish(args, overrides)
    base.atomic(folder/'status.json', dict(state='FAMILY_PASS_COMPLETE', groups=len(candidates),
                unique_candidates=len(unique), refined=len(overrides), pending=len(pending), errors=0))
    print(json.dumps(dict(state='FAMILY_PASS_COMPLETE', refined=len(overrides), pending=len(pending))), flush=True)
    if args.quick_only:
        return
    if not pending:
        return finish(args, candidates, overrides, 0)
    from concurrent.futures import ProcessPoolExecutor
    errors=0
    started=time.monotonic()
    selected=pending[:args.limit] if args.limit else pending
    # Workers only return results; the parent alone writes receipts/catalogs.
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for index,(sha,review,error) in enumerate(pool.map(analyse_track,((row,args) for row in selected))):
            if error:
                errors+=1
                base.atomic(folder/'errors'/(sha+'.json'),dict(type=error,source_sha256=sha))
            else:
                base.atomic(folder/'reviews'/(sha+'.json'),review)
                overrides[sha]=review
            base.atomic(folder/'status.json',dict(state='RUNNING',groups=len(candidates),unique_candidates=len(unique),
                        refined=len(overrides),resampled_this_run=index+1,remaining=len(pending)-index-1,
                        workers=args.workers,threads_per_worker=args.threads,errors=errors,elapsed_seconds=round(time.monotonic()-started,1)))
            if (index+1)%25==0:
                publish(args,overrides)
                print(json.dumps(dict(resampled=index+1,remaining=len(pending)-index-1,errors=errors)),flush=True)
    if args.limit and len(selected)<len(pending):
        publish(args,overrides)
        base.atomic(folder/'status.json',dict(state='BOUNDED_RUN_COMPLETE',groups=len(candidates),refined=len(overrides),
                    remaining=len(pending)-len(selected),errors=errors,resampled_this_run=len(selected),elapsed_seconds=round(time.monotonic()-started,1)))
        return
    finish(args,candidates,overrides,errors)


def finish(args,candidates,overrides,errors):
    rows = publish(args,overrides)
    folder = args.evidence/'refinement-v2'
    result = dict(state='COMPLETE' if not errors else 'INCOMPLETE',version=VERSION,
                  completed_at=datetime.now(timezone.utc).isoformat(),candidate_groups=len(candidates),
                  unique_reviews=len(overrides),errors=errors,remaining_uncertain=sum('混合' in t['mood'] or '边界' in t['mood'] for t in rows),
                  moods=dict(Counter(t['mood'] for t in rows)),catalog_sha256=file_sha(args.evidence/'catalog.json'),
                  human_store_untouched=True,human_listened=False)
    base.atomic(folder/'completion.json',result)
    base.atomic(folder/'status.json',result)
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path('D:/qingqingwu'))
    p.add_argument('--evidence',type=Path,default=Path('D:/qingqingwu/.kwjd/mihoyo-music/selection'))
    p.add_argument('--model',default='D:/qingqingwu/tools/music-analysis-models/clap-htsat-unfused')
    p.add_argument('--ffmpeg',default='D:/Apps/ffmpeg/ffmpeg.exe')
    p.add_argument('--threads',type=int,default=2)
    p.add_argument('--workers',type=int,choices=range(1,5),default=4)
    p.add_argument('--quick-only',action='store_true')
    p.add_argument('--limit',type=int,default=0)
    main(p.parse_args())
