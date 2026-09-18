import React, { useEffect, useState } from "react";
import { api, Project } from "../lib/api";
import { EmptyState, ErrorState } from "../components/EmptyState";

export function ProjectList() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.listProjects()
      .then((r) => setProjects(r.projects))
      .catch((e) => setErr(e.message));
  }, []);

  if (err) return <ErrorState message={`无法读取项目列表：${err}`} />;
  if (projects === null) return <EmptyState message="加载中…" />;
  if (projects.length === 0) return <EmptyState message="尚无 active 项目" />;

  return (
    <table className="project-list">
      <thead>
        <tr>
          <th>project_id</th>
          <th>状态</th>
          <th>实例</th>
          <th>简介</th>
        </tr>
      </thead>
      <tbody>
        {projects.map((p) => (
          <tr key={p.project_id}>
            <td>{p.project_id}</td>
            <td>{p.status}</td>
            <td>{p.owner_instance}</td>
            <td>{p.summary}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
