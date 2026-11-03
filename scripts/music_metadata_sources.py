"""Read public soundtrack metadata, without executing website scripts or audio."""
import json
from pathlib import Path
import re


class LiteralReader:
    """Only the JSON-like Seroval data subset: no functions/eval/property access."""
    def __init__(self,text):self.text=text;self.pos=0;self.refs={}
    def space(self):
        while self.pos<len(self.text) and self.text[self.pos].isspace():self.pos+=1
    def read(self):
        self.space();s=self.text;i=self.pos
        if s.startswith('$R[',i):
            m=re.match(r'\$R\[(\d+)\]',s[i:])
            if not m:raise ValueError('Invalid data reference')
            key=int(m[1]);self.pos+=m.end();self.space()
            if s[self.pos:self.pos+1]=='=':
                self.pos+=1;v=self.read();self.refs[key]=v;return v
            if key not in self.refs:raise ValueError('Unknown data reference')
            return self.refs[key]
        if s[i]=='"':
            end=i+1
            while end<len(s):
                if s[end]=='\\':end+=2;continue
                if s[end]=='"':break
                end+=1
            raw=re.sub(r'\\x([0-9a-fA-F]{2})',r'\\u00\1',s[i:end+1])
            self.pos=end+1;return json.loads(raw,strict=False)
        if s[i]=='{':
            self.pos+=1;result={};self.space()
            while s[self.pos]!='}':
                if s[self.pos]=='"':key=self.read()
                else:
                    m=re.match(r'[A-Za-z_$][\w$]*',s[self.pos:])
                    if not m:raise ValueError('Invalid object key')
                    key=m[0];self.pos+=m.end()
                self.space()
                if s[self.pos]!=':':raise ValueError('Expected colon')
                self.pos+=1;result[key]=self.read();self.space()
                if s[self.pos]==',':self.pos+=1;self.space()
                elif s[self.pos]!='}':raise ValueError('Expected object end')
            self.pos+=1;return result
        if s[i]=='[':
            self.pos+=1;result=[];self.space()
            while s[self.pos]!=']':
                result.append(self.read());self.space()
                if s[self.pos]==',':self.pos+=1;self.space()
                elif s[self.pos]!=']':raise ValueError('Expected array end')
            self.pos+=1;return result
        for token,value in [('!0',True),('!1',False),('true',True),('false',False),('null',None),('void 0',None),('undefined',None)]:
            if s.startswith(token,i):self.pos+=len(token);return value
        m=re.match(r'-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?',s[i:])
        if m:self.pos+=m.end();return json.loads(m[0])
        raise ValueError('Unsupported literal token')


def bootstrap(html):
    for script in re.findall(r'<script[^>]*>(.*?)</script>',html,re.S):
        m=re.search(r'\$R\[0\]=',script)
        if m:
            reader=LiteralReader(script[m.start():]);return reader.read()
    raise ValueError('No public metadata bootstrap')


def objects(value):
    if isinstance(value,dict):
        yield value
        for v in value.values():yield from objects(v)
    elif isinstance(value,list):
        for v in value:yield from objects(v)


def album_tracks(html):
    root=bootstrap(html);found={}
    for obj in objects(root):
        for disc in obj.get('discs',[]) if isinstance(obj.get('discs'),list) else []:
            if not isinstance(disc,dict):continue
            for row in disc.get('soundtracks',[]):
                if isinstance(row,dict) and all(k in row for k in ['slug','name','duration_sec']):
                    found[row['slug']]={**row,'disc':disc['name']}
    return list(found.values())


def track_detail(html):
    for obj in objects(bootstrap(html)):
        if 'trackDetail' in obj:return obj['trackDetail']
    raise ValueError('No track metadata')


def index_albums(html):
    found={}
    for obj in objects(bootstrap(html)):
        if all(k in obj for k in ['slug','name','total_tracks','release_date']):found[obj['slug']]=obj
    return list(found.values())
