"""Rebuild isolated face dependencies/assets; never activate an inference backend."""
import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from fixed_workflow import locked_packages,REQUIREMENTS

ASSETS={
    'GFPGANv1.4.pth':('https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth',
                     'e2cd4703ab14f4d01fd1383a8a8b266f9a5833dacee8e6a79d3bf21a1b6be5ad'),
    'detection_Resnet50_Final.pth':('https://github.com/xinntao/facexlib/releases/download/v0.1.0/detection_Resnet50_Final.pth',
                                 '6d1de9c2944f2ccddca5f5e010ea5ae64a39845a86311af6fdf30841b0a5a16d'),
    'parsing_parsenet.pth':('https://github.com/xinntao/facexlib/releases/download/v0.2.2/parsing_parsenet.pth',
                           '3d558d8d0e42c20224f13cf5a29c79eba2d59913419f945545d8cf7b72920de2'),
}


def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--post',type=Path,required=True)
    args=parser.parse_args()
    post=args.post.resolve();post.mkdir(parents=True,exist_ok=True)
    env=post/'face-venv'; python=env/'bin/python'
    budget=15 if not env.exists() else 2
    if shutil.disk_usage(post).free<(25+budget)*1024**3:
        raise RuntimeError('Need 25GiB reserve plus fixed-runtime recovery budget')
    if not env.exists():subprocess.run(['python3','-m','venv',str(env)],check=True)
    expected=locked_packages()
    inspect_code=('import importlib.metadata as m,json,sys; names=json.loads(sys.argv[1]); '
                  'actual={d.metadata["Name"].lower().replace("_","-"):d.version for d in m.distributions()}; '
                  'print(json.dumps({name:actual.get(name) for name in names}))')
    actual=json.loads(subprocess.check_output([str(python),'-c',inspect_code,json.dumps(list(expected))],text=True))
    if actual!=expected:
        if shutil.disk_usage(post).free<35*1024**3:
            raise RuntimeError('Insufficient fixed-runtime reinstall headroom; no alternate stack')
        tools=[name+'=='+expected[name] for name in ['setuptools','wheel','cython']]
        subprocess.run([str(python),'-m','pip','install','--no-cache-dir',*tools],check=True)
        subprocess.run([str(python),'-m','pip','install','--no-cache-dir','--index-url',
                        'https://download.pytorch.org/whl/cu126','torch=='+expected['torch'],
                        'torchvision=='+expected['torchvision']],check=True)
        subprocess.run([str(python),'-m','pip','install','--no-cache-dir','--no-build-isolation',
                        '--extra-index-url','https://pypi.nvidia.com','-r',str(REQUIREMENTS)],check=True)
    verified=json.loads(subprocess.check_output([str(python),'-c',inspect_code,json.dumps(list(expected))],text=True))
    if verified!=expected:raise ValueError('Fixed runtime lock verification failed after installation')
    weights=post/'tools/face-candidates/weights';weights.mkdir(parents=True,exist_ok=True)
    for name,(url,digest) in ASSETS.items():
        target=weights/name
        if not target.exists():
            temp=target.with_suffix('.pth.downloading')
            subprocess.run(['curl','-fL','--retry','3','-C','-','-o',str(temp),url],check=True)
            if sha(temp)!=digest:raise ValueError('Asset SHA mismatch; partial retained: '+name)
            temp.rename(target)
        if sha(target)!=digest:raise ValueError('Existing asset differs: '+name)
    reports=post/'reports';reports.mkdir(exist_ok=True)
    result={'state':'DEPENDENCIES_AND_WEIGHTS_ONLY','backend_activated':False,
            'requirements_sha256':sha(REQUIREMENTS),'packages':verified,
            'weights':{n:sha(weights/n) for n in ASSETS}}
    (reports/'face-runtime-rebuild.json').write_text(json.dumps(result,indent=2),encoding='utf8')


if __name__=='__main__':
    main()
