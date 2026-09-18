import React, { useState } from "react";
import { api, Subject } from "../lib/api";

export function TopBar({
  auth,
  onLogin,
}: { auth: { csrf?: string; subject?: Subject } | null; onLogin: (a: any) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);

  async function submitLogin(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    try {
      const r = await api.login(username, password);
      onLogin({ csrf: r.csrf, subject: r.subject });
    } catch (e: any) {
      setErr(e.message);
    }
  }

  if (auth?.subject) {
    return (
      <header className="topbar">
        <strong>Partner 工作台</strong>
        <span className="subject">
          已登录：{auth.subject.display_name}（{auth.subject.subject_id}）
          · 允许实例 {auth.subject.allowed_instances.join(", ") || "（无）"}
        </span>
      </header>
    );
  }
  return (
    <header className="topbar">
      <strong>Partner 工作台</strong>
      <form onSubmit={submitLogin} className="login-form">
        <input
          placeholder="用户名"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
        <input
          placeholder="密码"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <button type="submit">登录</button>
        {err && <span className="err">{err}</span>}
      </form>
    </header>
  );
}
