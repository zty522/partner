"""Persist actual downloaded bytes and extracted text before model reading."""
from pathlib import Path
import hashlib
import json
from html.parser import HTMLParser
from urllib.parse import urlsplit
import httpx
from partner.runtime.action_execution import write_json


class TextParser(HTMLParser):
    def __init__(self):super().__init__();self.parts=[];self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in {'script','style'}:self.hidden+=1
    def handle_endtag(self,tag):
        if tag in {'script','style'}:self.hidden=max(0,self.hidden-1)
    def handle_data(self,data):
        if not self.hidden and data.strip():self.parts.append(data.strip())


def fetch(url,directory):
    if urlsplit(url).scheme not in {'https','http'} or urlsplit(url).username:raise ValueError('invalid source URL')
    folder=Path(directory)/hashlib.sha256(url.encode()).hexdigest()[:20];folder.mkdir(parents=True,exist_ok=True)
    body=bytearray()
    with httpx.Client(timeout=15,follow_redirects=True,trust_env=False) as client:
        with client.stream('GET',url,headers={'User-Agent':'Partner source reader'}) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body)>12_000_000:raise ValueError('source exceeds bounded download size')
            final_url=str(response.url);content_type=response.headers.get('content-type','')
    if bytes(body).startswith(b'%PDF'):
        import fitz
        with fitz.open(stream=bytes(body),filetype='pdf') as doc:
            text='\n'.join(f'Page {i+1}\n'+page.get_text() for i,page in enumerate(doc))
    else:
        text=bytes(body).decode('utf-8',errors='replace')
        if 'html' in content_type:
            parser=TextParser();parser.feed(text);text='\n'.join(parser.parts)
    if len(text.strip())<100:raise ValueError('source contains insufficient readable content')
    (folder/'source.bin').write_bytes(body);(folder/'source.txt').write_text(text)
    receipt={'url':url,'final_url':final_url,'content_type':content_type,'bytes':len(body),
             'raw_path':str(folder/'source.bin'),'text_path':str(folder/'source.txt'),
             'sha256':hashlib.sha256(body).hexdigest(),'text_sha256':hashlib.sha256(text.encode()).hexdigest(),
             'text_chars':len(text)}
    write_json(folder/'receipt.json',receipt)
    return receipt


def read_verified(row,limit=12000):
    raw=Path(row['raw_path']).read_bytes();text=Path(row['text_path']).read_text()
    if hashlib.sha256(raw).hexdigest()!=row['sha256'] or hashlib.sha256(text.encode()).hexdigest()!=row['text_sha256']:
        raise ValueError('downloaded source changed')
    full_length = len(text)
    fragment = urlsplit(row['url']).fragment
    selection = 'document_start'
    if fragment and 'html' in row.get('content_type',''):
        from bs4 import BeautifulSoup
        document = BeautifulSoup(raw, 'html.parser')
        target = document.find(id=fragment)
        if target is not None:
            section = target.find_parent('dl') or target.find_parent('section') or target
            text = section.get_text('\n', strip=True)
            selection = 'requested_anchor:' + fragment
    return {'url':row['url'],'text':text[:limit],'total_chars':full_length,
            'excerpt_only':selection != 'document_start' or len(text)>limit,
            'selection':selection}
