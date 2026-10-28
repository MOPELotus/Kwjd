"""Hash-bound approvals for one-encode native face/subtitle segments."""
import json
from pathlib import Path
import re

from enhance_stage import atomic_json,sha256
from subtitle_timing import read_mapping


def create_recipe(path,*,post,episode,master_sha256,ass,srt,style,fonts,timing):
    data=dict(schema_version=1,scope='production',episode=episode,master_sha256=master_sha256,
        post_root=str(Path(post).absolute()),ass_path=str(Path(ass).absolute()),ass_sha256=sha256(ass),
        srt_path=str(Path(srt).absolute()),srt_sha256=sha256(srt),
        style_path=str(Path(style).absolute()),style_sha256=sha256(style),
        fonts_path=str(Path(post).absolute()/'styles/fonts'),fonts=fonts,subtitle_timing=timing,
        program_origin_seconds=0,video_policy='native_light_gfpgan_rife_ass_one_encode')
    if Path(path).exists():
        old=json.loads(Path(path).read_text())
        if old!=data:raise ValueError('Existing fused recipe changed; prior context retained')
    else:atomic_json(path,data)
    validate_recipe(path,sha256(path))
    return data


def validate_recipe(path,expected_sha256):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or sha256(path)!=expected_sha256:
        raise ValueError('Native recipe hash changed/missing')
    data=json.loads(path.read_text())
    if data.get('schema_version')!=1 or data.get('scope') not in ['production','bounded_test']:
        raise ValueError('Explicit production/test recipe scope required')
    if not re.fullmatch(r'S01E\d{2,3}',data.get('episode','')):
        raise ValueError('Invalid native recipe episode')
    if not re.fullmatch('[0-9a-f]{64}',data.get('master_sha256','')):
        raise ValueError('Native recipe needs master SHA')
    for key in ['ass','srt','style']:
        target=Path(data[key+'_path'])
        if not target.is_absolute() or not target.is_file() or target.is_symlink() or sha256(target)!=data[key+'_sha256']:
            raise ValueError('Changed/invalid fused '+key)
    fonts=Path(data['fonts_path'])
    actual={p.name for p in fonts.iterdir() if p.is_file() and p.suffix.lower() in ['.ttf','.otf','.ttc']}
    if actual!=set(data['fonts']):raise ValueError('Fused font set changed')
    for name,digest in data['fonts'].items():
        target=fonts/name
        if Path(name).name!=name or target.is_symlink() or sha256(target)!=digest:
            raise ValueError('Fused font hash changed')
    srt=Path(data['srt_path']);style=Path(data['style_path'])
    seal=json.loads(srt.with_name(srt.name+'.ready.json').read_text(encoding='utf-8-sig'))
    approval=json.loads(style.with_name(style.name+'.ready.json').read_text(encoding='utf-8-sig'))
    source_master=data['master_sha256']
    if data['scope']=='bounded_test':
        # Only the retained original-master cache qualifies for a test program
        # origin; this cannot become a whole-episode production approval.
        if (source_master!='e224ef25d8f9f4d600e02a31344f6c09f730e5cc1459e9ef53a115091a6c98cf'
                or data.get('program_origin_seconds')!=45):
            raise ValueError('Unknown bounded native cache/origin')
        source_master='07bd52ab2a3a7c540c4edb3c53aba63dfdf61f1d9db628cf6f03162946600e9d'
    elif data.get('program_origin_seconds')!=0:
        raise ValueError('Production program origin must match original master0')
    if (seal.get('reviewed') is not True or seal.get('sha256')!=data['srt_sha256']
            or approval.get('approved') is not True or approval.get('sha256')!=data['style_sha256']
            or approval.get('master_sha256')!=source_master or approval.get('fonts')!=data['fonts']):
        raise ValueError('Fused SRT/style/font approval differs or revoked')
    timing=read_mapping(data['post_root'],data['episode'],source_master,data['srt_sha256'])
    if timing!=data['subtitle_timing']:raise ValueError('Fused subtitle timing mapping changed')
    if sha256(path)!=expected_sha256:raise ValueError('Native recipe raced verification')
    return data
