"""Metadata-first indexed resource catalog.

Read discipline: production Events call ``query`` / ``read`` /
``register`` — all of which hit the SQLite ``resources.db`` index or do
bounded point reads with a ``reads`` receipt.  Only ``maintain()`` walks
the filesystem, under a ``max_files`` budget, and it is invoked solely by
maintenance hooks (``scripts/refresh_resource_indexes.py`` and the
``index.bootstrap`` family) — never by a regular Event.
"""


"""Metadata-first file discovery; scans belong to explicit maintenance only."""
import hashlib
import json
import os
from pathlib import Path
from .runtime_storage import workspace_dir
from .sqlite_base import get_connection


class ResourceCatalog:
    def __init__(self,workspace):
        self.root=Path(workspace).resolve()
        self.db=workspace_dir(self.root)/'resources.db'
        c=get_connection(self.db)
        c.executescript('''CREATE TABLE IF NOT EXISTS files(
          path TEXT PRIMARY KEY,kind TEXT,scope TEXT,mtime INTEGER,size INTEGER,sha TEXT,body TEXT,metadata TEXT);
          CREATE INDEX IF NOT EXISTS files_kind_scope ON files(kind,scope,mtime DESC);
          CREATE TABLE IF NOT EXISTS reads(id INTEGER PRIMARY KEY,at TEXT DEFAULT CURRENT_TIMESTAMP,
          path TEXT,bytes INTEGER,purpose TEXT,truncated INTEGER);
          CREATE TABLE IF NOT EXISTS maintenance(root TEXT PRIMARY KEY,updated TEXT);
        ''')

    def register(self,path,kind,scope='',metadata=None,max_bytes=2_000_000):
        path=Path(path).resolve();c=get_connection(self.db)
        if not path.is_file():
            c.execute('DELETE FROM files WHERE path=?',(str(path),));return
        st=path.stat();old=c.execute('SELECT mtime,size FROM files WHERE path=?',(str(path),)).fetchone()
        if old and old['mtime']==st.st_mtime_ns and old['size']==st.st_size:
            c.execute('UPDATE files SET kind=?,scope=?,metadata=? WHERE path=?',(kind,str(scope),json.dumps(metadata or {}),str(path)))
            return
        body='';sha=''
        if path.suffix.lower() in {'.py','.md','.txt','.json','.yaml','.yml','.csv'} and st.st_size<=max_bytes:
            with path.open('rb') as f:raw=f.read(max_bytes+1)
            if len(raw)<=max_bytes:
                body=raw.decode('utf-8',errors='replace');sha=hashlib.sha256(raw).hexdigest()
        c.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?)',
            (str(path),kind,str(scope),st.st_mtime_ns,st.st_size,sha,body,json.dumps(metadata or {})))

    def query(self,kind,*,scope=None,terms=(),limit=20):
        clauses=['kind=?'];args=[kind]
        if scope is not None:clauses.append('scope=?');args.append(str(scope))
        terms=list(terms)[:12]
        if terms:
            clauses.append('('+' OR '.join('instr(lower(path || body),lower(?))>0' for _ in terms)+')');args+=terms
        args.append(min(500,max(1,int(limit))))
        rows=get_connection(self.db).execute('SELECT path,kind,scope,mtime,size,sha,metadata FROM files WHERE '+' AND '.join(clauses)+' ORDER BY mtime DESC,path LIMIT ?',args)
        return [dict(r) for r in rows]

    def read(self,path,*,max_bytes=12000,purpose='context'):
        path=Path(path).resolve()
        with path.open('rb') as f:raw=f.read(max_bytes+1)
        truncated=len(raw)>max_bytes
        get_connection(self.db).execute('INSERT INTO reads(path,bytes,purpose,truncated) VALUES (?,?,?,?)',
            (str(path),len(raw),purpose,int(truncated)))
        return {'path':str(path),'text':raw[:max_bytes].decode('utf-8',errors='replace'),
            'bytes_read':len(raw),'truncated':truncated,'sha256':hashlib.sha256(raw).hexdigest() if not truncated else None}

    def maintain(self,root,kind,*,scope='',max_files=20000):
        root=Path(root).resolve();seen=set();count=0
        for base,dirs,files in os.walk(root):
            dirs[:]=[d for d in dirs if d not in {'.git','__pycache__','.venv','node_modules','isolated','frozen'}]
            for name in files:
                path=Path(base)/name
                if path.suffix.lower() not in {'.py','.md','.txt','.json','.yaml','.yml','.pdf','.csv'}:continue
                if count>=max_files:raise RuntimeError('maintenance file budget exhausted; index incomplete')
                self.register(path,kind,scope);seen.add(str(path.resolve()));count+=1
        c=get_connection(self.db)
        for row in c.execute('SELECT path FROM files WHERE kind=? AND scope=?',(kind,str(scope))).fetchall():
            if Path(row['path']).is_relative_to(root) and row['path'] not in seen:c.execute('DELETE FROM files WHERE path=?',(row['path'],))
        c.execute("INSERT OR REPLACE INTO maintenance VALUES (?,datetime('now'))",(str(root),))
        return count


def register_runtime(path,value):
    path=Path(path).resolve()
    for ancestor in path.parents:
        if ancestor.name=='state':
            root=ancestor.parent;rel=path.relative_to(ancestor).parts
            kind = {'event_flows':'flow','improvement_evidence':'bundle','improvement_opportunities':'opportunity','opportunities':'opportunity'}.get(rel[0],'runtime')
            if rel[:2]==('application','jobs'):kind='job'
            if rel[:2]==('event_runtime','background') and path.name=='task.json':kind='background'
            ResourceCatalog(root).register(path,kind,str(value.get('project_id') or value.get('job_id') or ''),
                {k:value.get(k) for k in ('job_id','task_id','flow_id','flow_type','status','instance_id')})
            return


def job_records(workspace, *, project_id='', limit=100):
    from .job_repository import init
    repo=init(Path(workspace));c=get_connection(repo.db_path)
    sql='SELECT job_id FROM jobs';args=[]
    if project_id:sql+=' WHERE project_id=?';args.append(project_id)
    sql+=' ORDER BY created_at DESC LIMIT ?';args.append(limit)
    return [repo.get_record(r[0]) for r in c.execute(sql,args).fetchall()]


def related_jobs(workspace, root_event_id, flow_type='project_iteration'):
    from .job_repository import init
    repo=init(Path(workspace));c=get_connection(repo.db_path)
    c.execute('CREATE INDEX IF NOT EXISTS jobs_root_flow ON jobs(root_event_id,flow_type)')
    rows=c.execute('SELECT job_id FROM jobs WHERE root_event_id=? AND flow_type=? ORDER BY created_at',(root_event_id,flow_type)).fetchall()
    return [repo.get_record(r[0]) for r in rows]
