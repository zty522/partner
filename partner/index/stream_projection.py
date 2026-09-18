"""Incremental, workspace-local projections of append-only evidence streams.

Offsets and rows commit together. Readers stat one known file and ingest only
complete new lines; malformed records fail visibly without advancing watermarks.
"""
import json
from pathlib import Path
from .runtime_storage import workspace_dir
from .sqlite_base import get_connection


class StreamProjection:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.db = workspace_dir(self.root) / 'streams.db'
        c = get_connection(self.db)
        c.executescript('''
        CREATE TABLE IF NOT EXISTS offsets(path TEXT PRIMARY KEY, identity TEXT, offset INTEGER);
        CREATE TABLE IF NOT EXISTS records(path TEXT, offset INTEGER, entity TEXT, project TEXT,
          kind TEXT, payload TEXT, PRIMARY KEY(path,offset));
        CREATE INDEX IF NOT EXISTS records_entity ON records(path,entity,offset DESC);
        CREATE INDEX IF NOT EXISTS records_project ON records(path,project,offset DESC);
        ''')

    def sync(self, path):
        path = Path(path).resolve(); key = str(path)
        c = get_connection(self.db)
        c.execute('BEGIN IMMEDIATE')
        try:
            if not path.exists():
                c.execute('DELETE FROM records WHERE path=?',(key,))
                c.execute('DELETE FROM offsets WHERE path=?',(key,))
                c.execute('COMMIT'); return
            with path.open('rb') as f:
                import os
                st = os.fstat(f.fileno()); identity = f'{st.st_dev}:{st.st_ino}'
                old = c.execute('SELECT * FROM offsets WHERE path=?',(key,)).fetchone()
                offset = old['offset'] if old and old['identity']==identity and old['offset']<=st.st_size else 0
                if not offset:
                    c.execute('DELETE FROM records WHERE path=?',(key,))
                f.seek(offset)
                while True:
                    begin = f.tell(); line = f.readline()
                    if not line or not line.endswith(b'\n'): break
                    offset = f.tell()
                    if not line.strip(): continue
                    row = json.loads(line)
                    entity = str(row.get('record_id') or row.get('memory_id') or row.get('event_id') or '')
                    c.execute('INSERT OR REPLACE INTO records VALUES (?,?,?,?,?,?)',
                        (key,begin,entity,str(row.get('project_id') or ''),str(row.get('record_kind') or ''),json.dumps(row,ensure_ascii=False)))
                c.execute('INSERT OR REPLACE INTO offsets VALUES (?,?,?)',(key,identity,offset))
            c.execute('COMMIT')
        except BaseException:
            c.execute('ROLLBACK'); raise

    def rows(self, path, *, limit=100, entity=None, project=None):
        self.sync(path)
        clauses=['path=?']; args=[str(Path(path).resolve())]
        if entity is not None: clauses.append('entity=?');args.append(entity)
        if project is not None: clauses.append('project IN (?,?)');args.extend([project,''])
        args.append(max(1,int(limit)))
        return [json.loads(r[0]) for r in get_connection(self.db).execute(
            'SELECT payload FROM records WHERE '+' AND '.join(clauses)+' ORDER BY offset DESC LIMIT ?',args)]

    def event(self, events_path, event_id):
        rows=self.rows(events_path,entity=event_id,limit=10000)
        merged={}
        for r in reversed(rows): merged.update(r)
        return merged

    def summaries(self, events_path, summaries_path, *, limit=100, project_id='', series='', include_audit=False):
        self.sync(events_path);self.sync(summaries_path)
        c=get_connection(self.db)
        # Filter before LIMIT and preserve complete summary payload semantics.
        clauses=['s.path=?'];args=[str(Path(summaries_path).resolve())]
        if project_id:
            clauses.append("json_extract(e.payload,'$.project_id')=?");args.append(project_id)
        if series:
            clauses.append("json_extract(e.payload,'$.series')=?");args.append(series)
        if not include_audit:clauses.append("COALESCE(json_extract(e.payload,'$.payload.acceptance_run'),0)=0")
        sql="""SELECT e.payload,s.payload FROM records s LEFT JOIN records e
          ON e.path=? AND e.entity=s.entity AND e.kind='event_created'
          WHERE """+' AND '.join(clauses)+' ORDER BY s.offset DESC LIMIT ?'
        rows=c.execute(sql,[str(Path(events_path).resolve()),*args,max(1,int(limit))]).fetchall()
        return [{**json.loads(r[0] or '{}'),**json.loads(r[1])} for r in rows]

    def memory(self,path,*,project_id='',limit=8,status=None,query=''):
        self.sync(path)
        c=get_connection(self.db)
        # Latest stable ID wins, and explicit supersedes removes an old ID.
        clauses=['r.path=?'];args=[str(Path(path).resolve())]
        clauses.append("(r.entity='' OR NOT EXISTS (SELECT 1 FROM records n WHERE n.path=r.path AND n.entity=r.entity AND n.offset>r.offset))")
        clauses.append("(r.entity='' OR NOT EXISTS (SELECT 1 FROM records n WHERE n.path=r.path AND json_extract(n.payload,'$.supersedes')=r.entity))")
        clauses.append("COALESCE(json_extract(r.payload,'$.status'),'active') NOT IN ('revoked','retired','suspended','superseded')")
        if project_id:clauses.append('r.project IN (?,?)');args.extend([project_id,''])
        if status:clauses.append("json_extract(r.payload,'$.status')=?");args.append(status)
        if query:
            words=query.split()[:8]
            clauses.append('('+' OR '.join('instr(lower(r.payload),lower(?))>0' for _ in words)+')');args.extend(words)
        args.append(max(1,int(limit)))
        return [json.loads(r[0]) for r in c.execute('SELECT r.payload FROM records r WHERE '+' AND '.join(clauses)+' ORDER BY r.offset DESC LIMIT ?',args)]

    def work_item_rows(self, path, work_item_id):
        self.sync(path)
        c=get_connection(self.db)
        c.execute("CREATE INDEX IF NOT EXISTS records_work_item ON records(path,json_extract(payload,'$.work_item_id'),offset)")
        return [json.loads(r[0]) for r in c.execute(
            "SELECT payload FROM records WHERE path=? AND json_extract(payload,'$.work_item_id')=? ORDER BY offset DESC",
            (str(Path(path).resolve()),str(work_item_id)))]

    def flow_events(self, events_path, flow_ids, root_event_id=''):
        self.sync(events_path)
        ids=[str(f) for f in flow_ids if f]
        if not ids and not root_event_id:return {}
        c=get_connection(self.db)
        c.execute("CREATE INDEX IF NOT EXISTS records_flow ON records(path,json_extract(payload,'$.flow_id'))")
        c.execute("CREATE INDEX IF NOT EXISTS records_root ON records(path,json_extract(payload,'$.root_event_id'))")
        conditions=[];args=[str(Path(events_path).resolve())]
        if ids:
            conditions.append("json_extract(payload,'$.flow_id') IN ("+','.join('?' for _ in ids)+')');args.extend(ids)
        if root_event_id:
            conditions.append("json_extract(payload,'$.root_event_id')=?");args.append(root_event_id)
        selected=c.execute("SELECT DISTINCT entity FROM records WHERE path=? AND ("+' OR '.join(conditions)+')',args)
        return {r[0]:self.event(events_path,r[0]) for r in selected.fetchall()}
