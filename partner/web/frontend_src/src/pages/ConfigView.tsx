import React, { useEffect, useState } from "react";
import { api, ConfigView as Config } from "../lib/api";
import { EmptyState, ErrorState } from "../components/EmptyState";

export function ConfigViewPage() {
  const [cfg, setCfg] = useState<Config | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api.config()
      .then(setCfg)
      .catch((e) => setErr(e.message));
  }, []);
  if (err) return <ErrorState message={err} />;
  if (!cfg) return <EmptyState message="加载中…" />;
  return (
    <div className="config-view">
      <h3>实例 / 模型 / 预算配置</h3>
      <table>
        <thead>
          <tr><th>实例</th><th>项目</th><th>persona</th><th>Bot</th><th>名称</th></tr>
        </thead>
        <tbody>
          {cfg.instances.map((i) => (
            <tr key={i.instance_id}>
              <td>{i.instance_id}</td>
              <td>{i.project_id}</td>
              <td>{i.persona_hint}</td>
              <td>{i.bot_id}</td>
              <td>{i.bot_name}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p>当前模型：{cfg.model}</p>
      <details>
        <summary>预算视图</summary>
        <pre>{JSON.stringify(cfg.budget, null, 2)}</pre>
      </details>
    </div>
  );
}
