import { useEffect, useMemo, useState } from "react";
import { api, Artifact } from "../lib/api";
import { EmptyState, ErrorState } from "../components/EmptyState";

const labels: Record<string, string> = {
  report_pdf: "最终 PDF 报告", report_markdown: "报告源稿", result_summary: "结果总结",
  run_summary: "运行总结", figure: "结果图", event_artifact: "运行证据",
};
const primaryKinds = new Set(["report_pdf", "report_markdown", "result_summary", "run_summary", "figure"]);

function sizeLabel(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
function fileName(path: string) { return path.split(/[\\/]/).pop() || path; }

export function ArtifactList({ jobId }: { jobId?: string }) {
  const [items, setItems] = useState<Artifact[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [preview, setPreview] = useState<Artifact | null>(null);
  const [showEvidence, setShowEvidence] = useState(false);
  useEffect(() => {
    api.listArtifacts(jobId ? { job_id: jobId, limit: 500 } : { limit: 200 })
      .then((r) => { setItems(r.artifacts); setErr(null); })
      .catch((e) => setErr(e.message));
  }, [jobId]);
  const unique = useMemo(() => {
    const byPath = new Map<string, Artifact>();
    for (const item of items || []) if (!byPath.has(item.path)) byPath.set(item.path, item);
    return [...byPath.values()];
  }, [items]);
  if (err) return <ErrorState message={err} />;
  if (items === null) return <EmptyState message="正在读取产物索引…" />;
  if (items.length === 0) return <EmptyState message="本次运行尚未登记产物" />;

  const newestByKind = new Map<string, Artifact>();
  for (const item of unique) if (primaryKinds.has(item.type) && !newestByKind.has(item.type)) newestByKind.set(item.type, item);
  const primary = [...newestByKind.values()];
  const primaryIds = new Set(primary.map((item) => item.artifact_id));
  const evidence = unique.filter((item) => !primaryIds.has(item.artifact_id));
  const report = primary.find((item) => item.type === "report_pdf");
  const figure = primary.find((item) => item.type === "figure");
  const displayedEvidence = showEvidence ? evidence : evidence.slice(0, 12);

  const card = (item: Artifact) => <article className={`artifact-card artifact-${item.type}`} key={item.artifact_id}>
    {item.type === "figure" && <img src={item.preview_url} alt={fileName(item.path)} loading="lazy" />}
    <div className="artifact-card-body">
      <span className="artifact-kind">{labels[item.type] || item.type || "产物"}</span>
      <h3>{fileName(item.path)}</h3>
      <p>{item.type === "report_pdf" ? "面向读者的完整研究报告" :
          item.type === "result_summary" ? "研究结论、指标和证据边界" :
          item.type === "run_summary" ? "本次运行、Flow 与交付情况" :
          item.type === "figure" ? "由真实测量数据生成的报告图件" :
          item.purpose || "可复核的运行证据"}</p>
      <div className="artifact-meta"><span>{sizeLabel(item.size_bytes)}</span><span>{item.sha256.slice(0, 10)}…</span></div>
      <div className="artifact-actions">
        {item.previewable && <button className="ghost" onClick={() => setPreview(item)}>查看</button>}
        <a className="artifact-download" href={item.download_url}>下载</a>
      </div>
    </div>
  </article>;

  return <div className="artifact-browser">
    {jobId && <div className="artifact-overview">
      <div><span className="eyebrow">DELIVERABLES</span><strong>{primary.length}</strong><small>读者产物</small></div>
      <div><span className="eyebrow">EVIDENCE</span><strong>{evidence.length}</strong><small>去重后的证据</small></div>
      <div><span className="eyebrow">REPORT</span><strong>{report ? "已生成" : "未生成"}</strong><small>最终 PDF</small></div>
      <div><span className="eyebrow">FIGURES</span><strong>{primary.filter((x) => x.type === "figure").length}</strong><small>当前结果图</small></div>
    </div>}

    {(report || figure) && <div className="featured-deliverable">
      {figure && <img src={figure.preview_url} alt="关键结果图" />}
      <div><span className="eyebrow">PRIMARY OUTPUT</span><h3>{report ? fileName(report.path) : "关键结果图"}</h3>
        <p>{report ? "报告由专门的报告 Event 编辑、事实核验、渲染和交付。" : "图件来自本次运行的真实测量产物。"}</p>
        {report && <div className="artifact-actions"><button className="primary" onClick={() => setPreview(report)}>在线阅读 PDF</button>
          <a className="artifact-download" href={report.download_url}>下载报告</a></div>}</div>
    </div>}

    <div className="artifact-section-title"><div><span className="eyebrow">READER OUTPUTS</span><h3>结果与报告</h3></div>
      <span className="muted">相同路径的重试版本已自动合并，只显示最新版</span></div>
    <div className="artifact-grid">{primary.map(card)}</div>

    <details className="evidence-vault">
      <summary><span>运行证据库</span><small>{evidence.length} 项 · 默认收起，供审计时使用</small></summary>
      <div className="artifact-grid evidence-grid">{displayedEvidence.map(card)}</div>
      {evidence.length > 12 && <button className="ghost evidence-more" onClick={() => setShowEvidence(!showEvidence)}>
        {showEvidence ? "收起" : `显示全部 ${evidence.length} 项证据`}</button>}
    </details>

    {preview && <div className="artifact-preview">
      <div className="section-heading"><div><span className="eyebrow">PREVIEW</span><h3>{fileName(preview.path)}</h3></div>
        <button className="ghost" onClick={() => setPreview(null)}>关闭预览</button></div>
      {preview.type === "figure" ? <img src={preview.preview_url} alt={fileName(preview.path)} /> :
        <iframe src={preview.preview_url} title={fileName(preview.path)} />}
    </div>}
  </div>;
}
