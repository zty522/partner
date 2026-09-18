import React, { useEffect, useState, useRef } from "react";
import { api, Job, Subject } from "../lib/api";
import { EmptyState, ErrorState } from "../components/EmptyState";
import { ArtifactList } from "./ArtifactList";

export function RunConsole({ csrf, subject }: { csrf?: string; subject?: Subject }) {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<Job | null>(null);
  const [events, setEvents] = useState<any[]>([]);
  const [draftMessage, setDraftMessage] = useState("");
  const [draftProject, setDraftProject] = useState("");
  const [draftMode, setDraftMode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitResult, setSubmitResult] = useState<string | null>(null);
  const lastEventId = useRef(0);

  async function refreshJobs() {
    try {
      const r = await api.listJobs({ limit: 50 });
      setJobs(r.jobs);
    } catch (e: any) { setErr(e.message); }
  }
  useEffect(() => { refreshJobs(); }, []);

  async function loadTimeline(job: Job) {
    setSelected(job);
    const r = await api.jobTimeline(job.job_id);
    setEvents(r.events);
    lastEventId.current = (r.events.at(-1)?.id ?? 0) as number;
  }

  useEffect(() => {
    if (!selected) return;
    const es = new EventSource(`/api/jobs/${selected.job_id}/events`);
    es.addEventListener("history", (e: any) => {
      try {
        const obj = JSON.parse(e.data);
        setEvents((prev) => prev.some((x) => x.event_id === obj.event_id) ? prev : [...prev, obj]);
        if ((e as MessageEvent).lastEventId) lastEventId.current = Number((e as MessageEvent).lastEventId);
      } catch { /* ignore */ }
    });
    return () => es.close();
  }, [selected]);

  async function doSubmit() {
    if (!csrf || !subject) {
      setSubmitResult("需要先登录");
      return;
    }
    if (!subject.allowed_instances.length) {
      setSubmitResult("当前账户未授权任何实例");
      return;
    }
    setSubmitting(true);
    setSubmitResult(null);
    try {
      const instance = subject.allowed_instances[0];
      const r = await api.submit({
        instance,
        request_id: "web-" + Math.random().toString(36).slice(2, 14),
        message: draftMessage,
        project_id: draftProject || undefined,
        mode: draftMode || undefined,
      }, csrf);
      setSubmitResult(`status=${r.status} job_id=${r.job_id ?? "(none)"} assigned_instance=${r.assigned_instance ?? "(unassigned)"}`);
      refreshJobs();
    } catch (e: any) {
      setSubmitResult(`提交失败：${e.message}`);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="run-console">
      <section className="submit-form">
        <h3>提交新任务</h3>
        <textarea
          placeholder="消息正文（必填）"
          value={draftMessage}
          onChange={(e) => setDraftMessage(e.target.value)}
        />
        <input
          placeholder="project_id (可选，与 mode 互斥)"
          value={draftProject}
          onChange={(e) => setDraftProject(e.target.value)}
        />
        <input
          placeholder="mode (可选)"
          value={draftMode}
          onChange={(e) => setDraftMode(e.target.value)}
        />
        <button disabled={submitting || !csrf} onClick={doSubmit}>
          提交（硬路由到当前账户首个允许实例）
        </button>
        {submitResult && <pre className="submit-result">{submitResult}</pre>}
      </section>
      <section className="job-list">
        <h3>近期任务</h3>
        {err && <ErrorState message={err} />}
        {jobs === null && <EmptyState message="加载中…" />}
        {jobs && jobs.length === 0 && <EmptyState message="暂无任务" />}
        {jobs && jobs.length > 0 && (
          <ul>
            {jobs.map((j) => (
              <li key={j.job_id}>
                <button onClick={() => loadTimeline(j)}>
                  {j.job_id} · {j.status} · {j.project_id} · inst={j.assigned_instance || "(unassigned)"}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
      {selected && (
        <section className="timeline">
          <h3>时间线：{selected.job_id}</h3>
          <button onClick={() => api.cancel(selected.job_id, csrf!).catch(console.error)}>
            请求取消（带 ACK）
          </button>
          <ol>
            {events.map((e, i) => (
              <li key={e.event_id ?? i}>
                {e.event_type} · {e.status} · {e.at}
              </li>
            ))}
          </ol>
          <h4>产物</h4>
          <ArtifactList jobId={selected.job_id} />
        </section>
      )}
    </div>
  );
}
