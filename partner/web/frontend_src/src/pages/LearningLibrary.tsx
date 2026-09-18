import React, { useEffect, useState } from "react";
import { api, ExternalSource } from "../lib/api";
import { EmptyState, ErrorState } from "../components/EmptyState";

export function LearningLibrary() {
  const [items, setItems] = useState<ExternalSource[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api.externalSources()
      .then((r) => setItems(r.sources))
      .catch((e) => setErr(e.message));
  }, []);
  if (err) return <ErrorState message={`无法读取学习来源：${err}`} />;
  if (items === null) return <EmptyState message="加载中…" />;
  if (items.length === 0) return <EmptyState message="尚无登记的学习来源" />;
  return (
    <table className="learning-library">
      <thead>
        <tr><th>id</th><th>title</th><th>URL</th><th>version</th><th>licence</th><th>status</th></tr>
      </thead>
      <tbody>
        {items.map((s) => (
          <tr key={s.id}>
            <td>{s.id}</td>
            <td>{s.title}</td>
            <td><a href={s.url} target="_blank" rel="noreferrer">{s.url}</a></td>
            <td>{s.version_reference}</td>
            <td>{s.licence}</td>
            <td>{s.status}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
