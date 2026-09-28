import React, { useEffect, useMemo, useState } from "react";
import { api, Job, RunTrace, Subject, TraceEvent } from "../lib/api";
import { EmptyState, ErrorState } from "../components/EmptyState";
import { ArtifactList } from "./ArtifactList";

const terminal = new Set(["completed", "failed", "cancelled"]);
const statusText: Record<string, string> = {
  completed: "已完成", running: "运行中", dispatched: "已分派", failed: "失败",
  blocked: "阻塞", cancelled: "已取消", skipped: "未触发", pending: "待执行",
  not_run: "未运行", improved: "已改善", inconclusive: "未定论", recovered: "修复后完成",
};
function StatusBadge({ value }: { value: string }) {
  return <span className={`status-badge status-${value || "unknown"}`}>{statusText[value] || value || "未知"}</span>;
}
function JsonBlock({ value }: { value: unknown }) { return <pre className="json-block">{JSON.stringify(value ?? {}, null, 2)}</pre>; }
function fmt(value: unknown, digits = 4) { return typeof value === "number" ? value.toFixed(digits) : "—"; }
function clock(value: string) { if (!value) return ""; const d = new Date(value); return Number.isNaN(d.valueOf()) ? value : d.toLocaleTimeString("zh-CN", {hour:"2-digit",minute:"2-digit",second:"2-digit"}); }

export function RunConsole({ csrf, subject, initialJobId = "" }: { csrf?: string; subject?: Subject; initialJobId?: string }) {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<Job | null>(null);
  const [trace, setTrace] = useState<RunTrace | null>(null);
  const [eventDetail, setEventDetail] = useState<any[] | null>(null);
  const [selectedEvent, setSelectedEvent] = useState<TraceEvent | null>(null);
  const [eventView, setEventView] = useState<"business" | "all">("business");
  const [eventPage, setEventPage] = useState(0);
  const [jobSearch, setJobSearch] = useState("");
  const [updateView, setUpdateView] = useState<"milestones" | "events">("milestones");
  const [draftMessage, setDraftMessage] = useState("");
  const [draftProject, setDraftProject] = useState("");
  const [instance, setInstance] = useState(subject?.allowed_instances[0] || "");
  const [maxRounds, setMaxRounds] = useState(5);
  const [reportPolicy, setReportPolicy] = useState("milestone");
  const [notificationMode, setNotificationMode] = useState("standard");
  const [syncQq, setSyncQq] = useState(true);
  const [activeTab, setActiveTab] = useState<"research" | "iterations" | "eventflow" | "artifacts" | "evolution" | "messages">("research");
  const [submitting, setSubmitting] = useState(false);
  const [submitResult, setSubmitResult] = useState<string | null>(null);

  async function refreshJobs() {
    if (!subject) return;
    try { const r = await api.listJobs({ limit: 40 }); setJobs(r.jobs); setErr(null); }
    catch (e: any) { setErr(e.message); }
  }
  async function loadTrace(jobId: string, view = eventView, page = eventPage) {
    setTrace(await api.runTrace(jobId, { view, after: page * 40, limit: 40 }));
  }
  async function openJob(jobOrId: Job | string) {
    try {
      const job = typeof jobOrId === "string" ? await api.getJob(jobOrId) : jobOrId;
      setSelected(job); setEventPage(0); setEventView("business");
      setActiveTab("research");
      setTrace(await api.runTrace(job.job_id, { view: "business", limit: 40 }));
      setEventDetail(null); setSelectedEvent(null);
      const url = new URL(window.location.href); url.searchParams.set("job", job.job_id);
      window.history.replaceState({}, "", url);
    } catch (e: any) { setErr(e.message); }
  }
  useEffect(() => { refreshJobs(); }, [subject]);
  useEffect(() => { if (subject && initialJobId) openJob(initialJobId); }, [subject, initialJobId]);
  useEffect(() => {
    if (!selected || terminal.has(selected.status)) return;
    const timer = window.setInterval(async () => {
      try { const [job, next] = await Promise.all([api.getJob(selected.job_id), api.runTrace(selected.job_id, {view:eventView, after:eventPage*40, limit:40})]); setSelected(job); setTrace(next); refreshJobs(); }
      catch { /* keep the last truthful snapshot */ }
    }, 3000);
    return () => window.clearInterval(timer);
  }, [selected?.job_id, selected?.status, eventView, eventPage]);

  async function changeEventView(view: "business" | "all", page = 0) {
    if (!selected) return; setEventView(view); setEventPage(page); setSelectedEvent(null); setEventDetail(null);
    try { await loadTrace(selected.job_id, view, page); } catch (e: any) { setErr(e.message); }
  }
  async function inspectEvent(event: TraceEvent) {
    if (!selected) return; setSelectedEvent(event); setEventDetail(null);
    try { setEventDetail((await api.traceEvent(selected.job_id, event.event_id)).lifecycle); }
    catch (e: any) { setErr(e.message); }
  }
  async function doSubmit() {
    if (!csrf || !subject) { setSubmitResult("请先登录"); return; }
    if (!draftMessage.trim() || !draftProject.trim() || !instance) { setSubmitResult("请填写消息、项目和实例"); return; }
    setSubmitting(true); setSubmitResult(null);
    try {
      const r = await api.submit({ instance, request_id: "web-" + crypto.randomUUID(), message: draftMessage.trim(), project_id: draftProject.trim(), report_policy: reportPolicy, sync_qq: syncQq, execution_constraints: { evolution_cycle: true, max_rounds: maxRounds, notification_mode: notificationMode } }, csrf);
      setSubmitResult(`已接收，Job ${r.job_id}`); await refreshJobs(); if (r.job_id) await openJob(r.job_id);
    } catch (e: any) { setSubmitResult(`提交失败：${e.message}`); } finally { setSubmitting(false); }
  }

  const filteredJobs = useMemo(() => (jobs || []).filter((job) => !jobSearch || `${job.project_id} ${job.request} ${job.job_id}`.toLowerCase().includes(jobSearch.toLowerCase())).slice(0, 24), [jobs, jobSearch]);
  const updates = updateView === "events" ? (trace?.recent_event_updates || []) : (trace?.user_updates || []);
  const visibleUpdates = updateView === "events" ? updates.slice(-40) : updates;
  const outcome = trace?.outcome;
  const contract = trace?.intent_contract || {};
  const constraints = Array.isArray(contract.constraints) ? contract.constraints as string[] : [];
  const bootstrap = outcome?.project.bootstrap || {};
  const ci = Array.isArray(bootstrap.delta_rmse_ci_95) ? bootstrap.delta_rmse_ci_95 : [];
  const baselineBar = typeof outcome?.project.baseline === "number" ? 100 : 0;
  const candidateBar = typeof outcome?.project.candidate === "number" && outcome.project.baseline
    ? Math.max(0, Math.min(100, outcome.project.candidate / outcome.project.baseline * 100)) : 0;

  return <div className="observatory">
    {!subject && <div className="login-callout">登录后可以发起任务并查看真实运行。</div>}
    <details className="composer panel"><summary><span><b>发起新任务</b><small>发送原始消息，选择实例、轮数和报告策略</small></span><span>＋</span></summary>
      <div className="composer-body"><textarea placeholder="告诉 Partner 要推进什么" value={draftMessage} onChange={(e) => setDraftMessage(e.target.value)} />
        <div className="form-grid"><label>实例<select value={instance} onChange={(e) => setInstance(e.target.value)}>{(subject?.allowed_instances || []).map((id) => <option key={id}>{id}</option>)}</select></label>
          <label>项目<input placeholder="例如 molecular_generation" value={draftProject} onChange={(e) => setDraftProject(e.target.value)} /></label>
          <label>安全上限（非计划轮数）<input type="number" min={1} max={20} value={maxRounds} onChange={(e) => setMaxRounds(Number(e.target.value))} /></label>
          <label>报告<select value={reportPolicy} onChange={(e) => setReportPolicy(e.target.value)}><option value="none">不生成 PDF</option><option value="milestone">里程碑报告</option><option value="final">最终报告</option></select></label>
          <label>消息密度<select value={notificationMode} onChange={(e) => setNotificationMode(e.target.value)}><option value="standard">普通（约 8–15 条）</option><option value="audit">审计（业务 Event）</option><option value="debug">调试（全部细节）</option></select></label></div>
        <label className="qq-sync"><input type="checkbox" checked={syncQq} onChange={(e) => setSyncQq(e.target.checked)} /> 同步发送到该实例已验证的 QQ 会话</label>
        <button className="primary" disabled={submitting || !csrf} onClick={doSubmit}>{submitting ? "正在提交…" : "提交并打开运行页"}</button>
        {submitResult && <div className="inline-result">{submitResult}</div>}</div>
    </details>

    <div className="workspace-grid">
      <aside className="job-sidebar panel"><div className="section-heading"><div><span className="eyebrow">RUNS</span><h2>运行记录</h2></div><button className="ghost" onClick={refreshJobs}>刷新</button></div>
        <input className="job-search" placeholder="搜索项目、消息或 Job" value={jobSearch} onChange={(e) => setJobSearch(e.target.value)} />
        {err && <ErrorState message={err} />}{jobs === null && <EmptyState message={subject ? "加载中…" : "登录后显示任务"} />}
        <div className="job-cards">{filteredJobs.map((job) => <button key={job.job_id} className={`job-card ${selected?.job_id === job.job_id ? "selected" : ""}`} onClick={() => openJob(job)}>
          <span className="job-title">{job.request?.split("\n")[0]?.slice(0, 58) || job.job_id}</span><span className="job-meta"><StatusBadge value={job.status} /> 实例 {job.assigned_instance || "-"}</span><span className="job-project">{job.project_id}</span></button>)}</div>
      </aside>

      <main className="run-detail panel">
        {!selected && <EmptyState message="选择一次运行，查看结论、过程、消息、报告和证据。" />}
        {selected && <>
          <header className="run-header"><div><span className="eyebrow">RUN OVERVIEW</span><h1>{selected.project_id}</h1><div className="run-id">{selected.job_id} · 实例 {selected.assigned_instance}</div></div><StatusBadge value={trace?.completion.status || selected.status} /></header>

          <nav className="report-tabs" aria-label="运行详情分区">
            {([['research','研究报告'],['iterations','实验与迭代'],['eventflow','Event / Flow'],['artifacts','产物与证据'],['evolution','Partner 自进化'],['messages','消息与回执']] as const).map(([id,label]) =>
              <button key={id} className={activeTab === id ? "active" : ""} onClick={() => setActiveTab(id)}>{label}</button>)}
          </nav>

          {activeTab === "research" && <>
          <section className="outcome-hero">
            <div className="outcome-copy"><span className="eyebrow">RESULT</span><h2>{outcome?.headline || "正在形成可验证结论"}</h2>
              <p>{trace?.completion.recovered ? `初次终态失败，经过 ${trace.completion.recovery_count} 次有界修复后完成。失败历史仍保留在审计记录中。` : "当前状态来自权威 Job、Flow 和交付回执。"}</p></div>
            <div className="metric-strip"><div><span>Baseline RMSE</span><strong>{fmt(outcome?.project.baseline)}</strong></div><div><span>Candidate RMSE</span><strong>{fmt(outcome?.project.candidate)}</strong></div><div><span>绝对改善</span><strong>{fmt(outcome?.project.effect)}</strong></div><div><span>相对改善</span><strong>{typeof outcome?.project.relative_improvement_percent === "number" ? `${outcome.project.relative_improvement_percent.toFixed(1)}%` : "—"}</strong></div></div>
            <div className="truth-chips"><span className={outcome?.project.split_reused ? "pass" : "warn"}>冻结 split {outcome?.project.split_reused ? "已复用" : "未确认"}</span><span className={outcome?.learning.consumed ? "pass" : "warn"}>主动学习 {outcome?.learning.consumed ? "已消费" : "未消费"}</span><span className={outcome?.delivery.report ? "pass" : "warn"}>PDF {outcome?.delivery.report ? "已送达" : "未送达"}</span><span className={outcome?.evolution.production_effective ? "pass" : "neutral"}>自进化 {outcome?.evolution.production_effective ? "已生效" : outcome?.evolution.decision || "未运行"}</span></div>
          </section>

          <section className="research-grid">
            <article className="research-card protocol-card"><span className="eyebrow">QUESTION &amp; PROTOCOL</span><h3>研究问题与冻结协议</h3>
              <p className="research-question">{String(contract.goal || trace?.message?.split("\n")[0] || "未记录研究问题")}</p>
              <div className="protocol-facts"><span>实际迭代 <b>{outcome?.project.rounds ?? "—"}</b> 轮</span><span>评价指标 <b>{outcome?.project.metric || "—"}</b></span><span>泄漏检查 <b>{outcome?.project.leakage_check || "未确认"}</b></span></div>
              {constraints.length > 0 && <ul>{constraints.slice(0,4).map((item,index)=><li key={index}>{item}</li>)}</ul>}
            </article>
            <article className="research-card result-chart"><span className="eyebrow">MATCHED RESULT</span><h3>冻结条件下的匹配比较</h3>
              <div className="compare-row"><span>Baseline</span><div><i style={{width:`${baselineBar}%`}} /></div><b>{fmt(outcome?.project.baseline)}</b></div>
              <div className="compare-row candidate"><span>Candidate</span><div><i style={{width:`${candidateBar}%`}} /></div><b>{fmt(outcome?.project.candidate)}</b></div>
              <p>{ci.length === 2 ? `RMSE 改善的 95% CI：${fmt(ci[0])}–${fmt(ci[1])}；配对 bootstrap ${bootstrap.n_replicates || "—"} 次。` : "本次投影尚未取得可展示的置信区间。"}</p>
            </article>
            <article className="research-card"><span className="eyebrow">ACTIVE LEARNING</span><h3>学习内容如何进入下一轮</h3>
              <p>{outcome?.learning.consumed ? `来源绑定的 handoff 已被后续实验消费；采用机制：${outcome.learning.mechanism || "见学习证据"}。` : "尚未证明学习产物被后续项目轮消费。"}</p>
              {outcome?.learning.source_url && <a href={outcome.learning.source_url} target="_blank" rel="noreferrer">查看原始学习来源</a>}
              <small>学习改变了实验设计约束；数值改善仍以冻结匹配比较为准，不单独归因于阅读行为。</small>
            </article>
            <article className="research-card"><span className="eyebrow">LIMITS &amp; NEXT</span><h3>结论边界与下一步</h3>
              <p>当前结论只适用于本次冻结数据、target-group folds、模型和预算。流程完成不代表跨数据集泛化成立。</p>
              <strong>{outcome?.project.status === "improved" ? "下一步：在独立数据集或外部 target holdout 上验证泛化。" : "下一步：依据失败证据冻结新的、可区分假设后再运行。"}</strong>
            </article>
          </section>

          {trace?.acceptance && <section className="acceptance-panel">
            <div className="acceptance-score"><span className="eyebrow">EFFECT ACCEPTANCE</span><strong>{trace.acceptance.score}</strong><small>/ 100</small><StatusBadge value={trace.acceptance.status === "passed" ? "completed" : "failed"} /></div>
            <div className="acceptance-body"><div className="section-heading"><div><h2>效果验收</h2><p>{trace.acceptance.interpretation}</p></div><span className="muted">{trace.acceptance.passed}/{trace.acceptance.required} 项必需检查通过</span></div>
              <div className="acceptance-checks">{trace.acceptance.checks.filter((item) => item.required).map((item) => <details key={item.id} className={`acceptance-check check-${item.status}`}><summary><span>{item.status === "passed" ? "✓" : item.status === "failed" ? "!" : "–"}</span><b>{item.label}</b><small>{item.status === "passed" ? "已证明" : item.status === "failed" ? "未证明" : "不适用"}</small></summary><p>{item.expected}</p><JsonBlock value={item.actual} /></details>)}</div>
            </div>
          </section>}

          {trace?.completion.error && <div className="closure-error">{trace.completion.error}</div>}
          </>}

          {activeTab === "iterations" &&
          <section><div className="section-heading"><div><span className="eyebrow">JOURNEY</span><h2>这次运行经历了什么</h2></div><span className="muted">项目迭代按真实次数展开，底层 Event 可单独审计</span></div>
            <div className="stage-timeline">{(trace?.stages || []).map((stage, index) => <article className={`stage-card stage-${stage.status}`} key={stage.name}>
              <div className="stage-index">{String(index + 1).padStart(2, "0")}</div><div><div className="stage-title"><h3>{stage.name}</h3><StatusBadge value={stage.status} /></div><p>{stage.description}</p><strong>{stage.summary || "没有可展示的结论"}</strong><small>{stage.event_count} 个业务 Event{stage.failed_count ? ` · ${stage.failed_count} 个失败记录` : ""}</small></div></article>)}</div>
          </section>}

          {activeTab === "messages" &&
          <section><div className="section-heading"><div><span className="eyebrow">UPDATES</span><h2>Partner 发出的过程消息</h2></div><div className="segmented"><button className={updateView === "milestones" ? "active" : ""} onClick={() => setUpdateView("milestones")}>里程碑 {trace?.milestone_update_count || 0}</button><button className={updateView === "events" ? "active" : ""} onClick={() => setUpdateView("events")}>逐 Event {trace?.user_update_count || 0}</button></div></div>
            <p className="section-note">每条消息都由 compose Event 编辑、critic Event 审查，再分别投影到网页和 QQ；这里默认聚合里程碑。</p>
            <div className="update-feed">{visibleUpdates.map((item, index) => <article key={`${item.at}-${index}`}><time>{clock(item.at)}</time><span className="update-dot" /><div><small>{item.flow_name || item.phase}</small><p>{item.message}</p></div></article>)}</div>
            {updateView === "events" && (trace?.user_update_count || 0) > 40 && <div className="trace-warning">逐 Event 模式显示最近 40 条消息；完整消息与渠道回执保留在技术 Event 和运行日志中。</div>}
          </section>}

          {activeTab === "artifacts" &&
          <section><div className="section-heading"><div><span className="eyebrow">OUTPUTS</span><h2>报告、结果和可复核产物</h2></div></div><ArtifactList jobId={selected.job_id} /></section>
          }

          {activeTab === "eventflow" && <>
          <details className="message-card"><summary>发送给 Partner 的原始消息</summary><p>{trace?.message || selected.request}</p></details>
          <details className="message-card"><summary>意图契约与冻结边界</summary><JsonBlock value={trace?.intent_contract} /></details>

          <section className="flow-section"><div className="section-heading"><div><span className="eyebrow">FLOW MAP</span><h2>实际 Event Flow</h2></div><span className="muted">默认折叠子步骤，避免流程图横向失控</span></div>
            {(trace?.flows || []).map((flow) => <details className="flow-card" key={flow.flow_id}><summary><div><strong>{flow.flow_type}</strong><span>{flow.nodes.length} 个节点 · {flow.flow_id}</span></div><StatusBadge value={flow.status} /></summary>
              {flow.description && <p className="flow-description">{flow.description}</p>}<div className="flow-nodes">{flow.nodes.map((node, index) => <React.Fragment key={node.node_id}>{index > 0 && <span className="connector">→</span>}<div className={`flow-node node-${node.runtime_status}`}><small>{node.node_id}</small><span>{node.event_type}</span><StatusBadge value={node.runtime_status} /></div></React.Fragment>)}</div></details>)}
          </section>

          <section><div className="section-heading"><div><span className="eyebrow">EVENT EXPLORER</span><h2>Event 时间线</h2></div><div className="segmented"><button className={eventView === "business" ? "active" : ""} onClick={() => changeEventView("business")}>业务 Event</button><button className={eventView === "all" ? "active" : ""} onClick={() => changeEventView("all")}>全部技术 Event</button></div></div>
            <div className="event-explainer">本次共有 <b>{trace?.counts.business_events || 0}</b> 个业务 Event和 <b>{trace?.counts.infrastructure_events || 0}</b> 个通知/渠道 Event。当前第 {eventPage + 1} 页，显示 {trace?.events.length || 0}/{trace?.total_visible_events || 0}。</div>
            <div className="event-table">{(trace?.events || []).map((event) => <button key={`${event.sequence}-${event.event_id}`} onClick={() => inspectEvent(event)} className={selectedEvent?.event_id === event.event_id ? "active" : ""}><span className="seq">{String(event.sequence).padStart(3, "0")}</span><span><strong>{event.node_id}</strong><small>{event.event_type}</small></span><span className="event-summary">{event.summary || "已记录输入与输出"}</span><StatusBadge value={event.status} /><span className="duration">{event.duration_ms == null ? "" : `${Math.round(event.duration_ms)} ms`}</span></button>)}</div>
            <div className="pager"><button className="ghost" disabled={eventPage === 0} onClick={() => changeEventView(eventView, eventPage - 1)}>上一页</button><button className="ghost" disabled={!trace?.has_more} onClick={() => changeEventView(eventView, eventPage + 1)}>下一页</button></div>
          </section></>}

          {activeTab === "evolution" && <section className="outcome-hero"><div className="outcome-copy"><span className="eyebrow">PARTNER EVOLUTION</span><h2>自进化结算：{outcome?.evolution.decision || "未运行"}</h2><p>{outcome?.evolution.summary || "本轮没有形成可验证的 Partner 机制改动。"}</p></div><div className="truth-chips"><span className={outcome?.evolution.production_effective ? "pass" : "neutral"}>生产生效：{outcome?.evolution.production_effective ? "是" : "否"}</span><span className="neutral">问题：{outcome?.evolution.issue || "无可靠候选"}</span></div></section>}

          {activeTab === "eventflow" && selectedEvent && <section className="inspector"><div className="section-heading"><div><span className="eyebrow">EVENT INSPECTOR</span><h2>{selectedEvent.node_id}</h2></div><button className="ghost" onClick={() => { setSelectedEvent(null); setEventDetail(null); }}>关闭</button></div><div className="inspector-meta">{selectedEvent.event_type} · {selectedEvent.event_id}</div>
            {eventDetail === null ? <EmptyState message="读取中…" /> : eventDetail.map((row, index) => <details key={index} open className="lifecycle-card"><summary>{row.phase} · {row.status}</summary>{row.input !== undefined && <><h4>输入</h4><JsonBlock value={row.input} /></>}{row.output !== undefined && <><h4>输出</h4><JsonBlock value={row.output} /></>}</details>)}</section>}
          {csrf && !terminal.has(selected.status) && <button className="danger" onClick={() => api.cancel(selected.job_id, csrf)}>请求取消</button>}
        </>}
      </main>
    </div>
  </div>;
}
