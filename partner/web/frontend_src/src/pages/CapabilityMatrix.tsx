import React, { useEffect, useState } from "react";
import { api } from "../lib/api";
import { EmptyState, ErrorState } from "../components/EmptyState";

export function CapabilityMatrix() {
  const [info, setInfo] = useState<{ path: string; size_bytes: number } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api.capabilityMatrix().then(setInfo).catch((e) => setErr(e.message));
  }, []);
  if (err) return <ErrorState message={err} />;
  if (!info) return <EmptyState message="加载中…" />;
  return (
    <div>
      <h3>能力矩阵</h3>
      <p>
        完整文档：<code>{info.path}</code>（{info.size_bytes} 字节）
      </p>
      <p>
        <a href={info.path} target="_blank" rel="noreferrer">在 GitHub 查看</a>
      </p>
    </div>
  );
}
