"""Place the verified first-chapter intro version in the final directory.

Keep the original body and its cloud receipt as a separately identified source.
No encoding, quality approval, deletion, or cloud receipt identity changes.
"""
import argparse
from pathlib import Path

from episode_lifecycle import catalog, load, now, save, sha256


def promote(root, ep='S03E02'):
    root = Path(root).resolve()
    if ep != 'S03E02':
        raise ValueError('Only the first Jigong chapter receives the intro')
    report_path = root / '.kwjd/publishing' / f'{ep}.with-intro.json'
    receipt_path = root / '.kwjd/receipts' / f'{ep}.json'
    report, receipt = load(report_path), load(receipt_path)
    target = root / 'final' / catalog(root, ep)['final_name']
    body, published = Path(report['body_path']), Path(report['path'])
    body_store = root / 'publish/sources' / f'{ep}.body.mkv'
    if (report.get('state') != 'PUBLISHING_COPY_VERIFIED'
            or report.get('intro_seconds') != 27
            or not all(report.get(k) is True for k in (
                'body_coded_picture_payloads_identical', 'body_pcm_identical',
                'join_and_first_middle_end_decode_passed'))
            or not all(receipt.get(k) is True for k in (
                'received', 'technical_checked', 'quality_checked', 'windows_cleanup_published'))
            or receipt['sha256'] != report['body_sha256']):
        raise ValueError('Need verified intro append and accepted body receipt')
    if report.get('canonical_final') is True:
        if published != target or sha256(target) != report['sha256']:
            raise ValueError('Canonical final changed')
        return report
    for path in (target, body, published, body_store):
        if not path.resolve().is_relative_to(root) or path.is_symlink():
            raise ValueError('Media path leaves the data root')
    if (body != target or published == target or body_store.exists()
            or sha256(body) != report['body_sha256']
            or sha256(published) != report['sha256']):
        raise ValueError('Unexpected source/target identity; retain all files')
    history = root / '.kwjd/publishing/history'
    save(history / f'{ep}.body-receipt.json', receipt)
    save(history / f'{ep}.with-intro-before-promotion.json', report)
    body_store.parent.mkdir(parents=True, exist_ok=True)
    body.replace(body_store)
    try:
        published.replace(target)
    except OSError:
        body_store.replace(body)
        raise
    receipt.update(local_path=str(body_store), local_role='verified_body_source',
                   canonical_final_path=str(target), canonical_final_sha256=report['sha256'])
    save(receipt_path, receipt)
    report.update(body_path=str(body_store), path=str(target), canonical_final=True,
                  promoted_at=now())
    save(report_path, report)
    save(root / '.kwjd/publishing' / f'{ep}.final.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    print(promote(args.root)['path'])
