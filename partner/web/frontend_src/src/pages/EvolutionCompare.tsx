import React, { useEffect, useState } from "react";
import { api, EvolutionRun } from "../lib/api";
import { EmptyState, ErrorState } from "../components/EmptyState";

export function EvolutionCompare() {
  const [runs, setRuns] = useState<EvolutionRun[] | null>(null);
  const [selected, setSelected] = useState<EvolutionRun | null>(null);
  const [comparison, setComparison] = useState<any | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.evolutionRuns({ limit: 50 })
      .then((r) => setRuns(r.runs))
      .catch((e) => setErr(e.message));
  }, []);

  useEffect(() => {
    if (!selected) return;
    api.evolutionCompare(selected.run_id)
      .then((r) => setComparison(r.comparison))
      .catch((e) => setErr(e.message));
  }, [selected]);

  if (err) return <ErrorState message={`无法读取运行列表：${err}`} />;
  if (runs === null) return <EmptyState message="加载中…" />;

  return (
    <div className="evolution-compare">
      <section>
        <h3>自进化实验运行</h3>
        {runs.length === 0 && <EmptyState message="尚无 evolution 运行" />}
        {runs.length > 0 && (
          <ul>
            {runs.map((r) => (
              <li key={r.run_id}>
                <button onClick={() => setSelected(r)}>
                  {r.run_id} · protocol={r.protocol_id} · state={r.state} ·
                  scores={r.score_count} · issues={r.issue_count}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
      {selected && (
        <section>
          <h3>比较：{selected.run_id}</h3>
          {comparison === null ? (
            <EmptyState message="加载比较数据…" />
          ) : (
            <pre className="json">
              {JSON.stringify(comparison, null, 2)}
            </pre>
          )}
        </section>
      )}
    </div>
  );
}
