import React, { useEffect, useState } from "react";
import { api, Artifact } from "../lib/api";
import { EmptyState, ErrorState } from "../components/EmptyState";

export function ArtifactList({ jobId }: { jobId?: string }) {
  const [items, setItems] = useState<Artifact[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api.listArtifacts(jobId ? { job_id: jobId, limit: 100 } : { limit: 100 })
      .then((r) => setItems(r.artifacts))
      .catch((e) => setErr(e.message));
  }, [jobId]);
  if (err) return <ErrorState message={err} />;
  if (items === null) return <EmptyState message="加载中…" />;
  if (items.length === 0) return <EmptyState message="尚无产物" />;
  return (
    <table className="artifact-list">
      <thead>
        <tr><th>artifact_id</th><th>job_id</th><th>type</th><th>size</th><th>sha256</th><th>path</th></tr>
      </thead>
      <tbody>
        {items.map((a) => (
          <tr key={a.artifact_id}>
            <td>{a.artifact_id}</td>
            <td>{a.job_id}</td>
            <td>{a.type}</td>
            <td>{a.size_bytes}</td>
            <td>{a.sha256.slice(0, 12)}…</td>
            <td>{a.path}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
