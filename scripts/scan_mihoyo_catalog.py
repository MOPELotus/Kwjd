"""Checkpointed metadata-only TuneWeave catalogue survey; never stores credentials."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.request

ARTISTS = ('qq:001uz8tl04tdL8', 'qq:001ID6zs3NNni7',
           'netease:12487174', 'netease:94007859')


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    temp.replace(path)


def next_offset(response, current):
    pagination = response.get('meta', {}).get('pagination', {})
    if not pagination.get('has_more'):
        return None
    value = pagination.get('next_offset')
    if not isinstance(value, int) or value <= current:
        raise ValueError('Non-advancing upstream pagination')
    return value


def survey(base, root):
    status = {'status': 'RUNNING', 'albums': 0, 'tracks': 0, 'errors': []}
    checkpoint = root/'scan-status.json'
    def request(path):
        cache = root/'pages'/(hashlib.sha256(path.encode()).hexdigest() + '.json')
        if cache.exists():
            return json.loads(cache.read_text(encoding='utf8'))
        for attempt in range(3):
            try:
                time.sleep(.75)
                with urllib.request.urlopen(base + path, timeout=45) as response:
                    result = json.load(response)
                if not result.get('ok'):
                    raise ValueError('TuneWeave returned unsuccessful response')
                save(cache, result)
                return result
            except urllib.error.HTTPError as error:
                if error.code in (401,403,429) or error.code < 500 or attempt == 2:
                    raise
                time.sleep(3 * (attempt+1))
            except (TimeoutError, urllib.error.URLError):
                if attempt == 2:
                    raise
                time.sleep(3 * (attempt+1))

    def pages(path):
        offset = 0
        for _ in range(256):
            result = request(path + '?limit=100&offset=' + str(offset))
            if not isinstance(result.get('data'), list):
                raise ValueError('Unexpected list response')
            yield from result['data']
            cursor = next_offset(result, offset)
            if cursor is None:
                return
            offset = cursor
        raise ValueError('Pagination budget exceeded')

    albums, tracks = {}, {}
    save(checkpoint, status)
    for artist in ARTISTS:
        status['current'] = artist
        save(checkpoint, status)
        for album in pages('/v1/artists/' + artist + '/albums'):
            albums[album['ref']] = album
        status['albums'] = len(albums)
        save(root/'albums.json', list(albums.values()))
        save(checkpoint, status)
    for index, (ref, album) in enumerate(albums.items(), 1):
        status.update(current=ref, albums_scanned=index-1)
        save(checkpoint, status)
        try:
            for track in pages('/v1/albums/' + ref + '/tracks'):
                track['survey_album_ref'] = ref
                tracks[track['ref']] = track
        except Exception as error:
            status['errors'].append({'album_ref':ref, 'type':type(error).__name__,
                                     'http_status':getattr(error, 'code', None)})
            # Authentication/rate limits stop the survey; don't treat it as missing music.
            if getattr(error, 'code', None) in (401,403,429):
                raise
        status['tracks'] = len(tracks)
        save(root/'tracks.json', list(tracks.values()))
        save(checkpoint, status)
    counts = {}
    for platform in ('qq','netease'):
        counts[platform] = {'albums':sum(a.startswith(platform+':') for a in albums),
                            'tracks':sum(t.startswith(platform+':') for t in tracks)}
    status.update(status='COMPLETE_WITH_ERRORS' if status['errors'] else 'COMPLETE',
                  albums_scanned=len(albums), counts=counts)
    save(checkpoint, status)
    print(json.dumps(status, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base', default='http://127.0.0.1:7832')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    try:
        survey(args.base, args.output)
    except Exception as error:
        save(args.output/'scan-failure.json', {'type':type(error).__name__,
                                              'http_status':getattr(error, 'code', None)})
        raise SystemExit(2)
