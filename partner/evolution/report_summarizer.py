
# Sprint 7: Report summarizer — reads latest research output and creates clean QQ message
import os, glob, re

def summarize_latest_output(workspace: str, instance_id: str = "") -> str:
    '''Read the latest research output and create a brief summary for QQ.'''
    task_dir = os.path.join(workspace, "state", "tasks")
    if not os.path.isdir(task_dir):
        return ""
    
    # Find all report files from the last hour
    reports = []
    for root, dirs, files in os.walk(task_dir):
        for fn in files:
            if fn.endswith(('.md', '.csv')) and any(kw in fn.lower() for kw in ['report', 'analysis', 'comparison', 'result']):
                fp = os.path.join(root, fn)
                sz = os.path.getsize(fp)
                mtime = os.path.getmtime(fp)
                reports.append((mtime, fp, fn, sz))
    
    if not reports:
        return ""
    
    reports.sort(reverse=True)
    latest = reports[0]
    
    # Read first 300 chars of content
    try:
        with open(latest[1]) as f:
            content = f.read(500)
    except:
        content = ""
    
    # Extract title and first meaningful paragraph
    title = ""
    for l in content.split('\n'):
        if l.startswith('# ') and not l.startswith('# 第'):
            title = l[2:].strip()
            break
    
    # Generate summary
    summary = f"[{instance_id}] ✅ 研究产出\n📄 {latest[2]} ({latest[3]:,}b)"
    if title:
        summary += f"\n   {title}"
    
    return summary
