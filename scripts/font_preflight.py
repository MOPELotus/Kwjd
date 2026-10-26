"""Verify approved ASS fonts through fontconfig and an actual libass render."""
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET


def preflight(style, fonts, directory, log):
    style, fonts, directory, log = map(Path,(style,fonts,directory,log))
    directory.mkdir(parents=True,exist_ok=True)
    root=ET.Element('fontconfig');ET.SubElement(root,'dir').text=str(fonts)
    ET.SubElement(root,'cachedir').text=str(directory/'fontconfig-cache')
    config=directory/'fonts.conf';ET.ElementTree(root).write(config,encoding='utf-8',xml_declaration=True)
    env=os.environ.copy();env['FONTCONFIG_FILE']=str(config)
    text=style.read_text(encoding='utf-8-sig')
    families={line.split(',')[1].strip() for line in text.splitlines() if line.startswith('Style: ')}
    allowed=set();matches={}
    for path in fonts.iterdir():
        if path.suffix.lower() in ('.ttf','.otf','.ttc') and path.is_file():
            names=subprocess.check_output(['fc-scan','--format','%{postscriptname}\n',str(path)],text=True)
            allowed.update(name.strip() for line in names.splitlines() for name in line.split(',') if name.strip())
    for family in families:
        match=subprocess.check_output(['fc-match','--format','%{family}\n%{file}\n',family],env=env,text=True).splitlines()
        matched=match[-1] if len(match)>=2 else ''
        if (not matched or Path(matched).resolve().parent != fonts.resolve()
                or family.casefold() not in [x.strip().casefold() for x in match[0].split(',')]):
            raise ValueError('Fontconfig did not resolve packaged font: '+family)
        matches[family]=matched
    if not families or not allowed:raise ValueError('No approved style/font identities to verify')
    sample=directory/'font-preflight.ass'
    # Deliberate synthetic glyph probe; no replacement-master quality claim.
    header=text.split('[Events]')[0]
    sample.write_text(header+'[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
                      +'Dialogue: 0,0:00:00.00,0:00:00.20,Default,,0,0,0,,卿卿误我 字体核验\n')
    escape=lambda p:str(p).replace('\\','/').replace(':',r'\:').replace("'",r"\'")
    result=subprocess.run(['ffmpeg','-nostdin','-hide_banner','-v','info','-f','lavfi',
        '-i','color=s=1920x1080:r=5:d=0.2','-vf',
        f"ass=filename='{escape(sample)}':fontsdir='{escape(fonts)}'",
        '-frames:v','1','-f','null','-'],env=env,capture_output=True,text=True,check=True)
    with log.open('a') as stream:stream.write(result.stdout+result.stderr)
    selected=re.findall(r'fontselect:.* -> (.+?), \d+, (.+)',result.stderr)
    if not selected or any(last.strip() not in allowed for _,last in selected):
        raise ValueError('libass selected an unapproved fallback or did not prove font identity')
    return {'fontconfig_matches':matches,'libass_postscript_names':sorted({last.strip() for _,last in selected})}
