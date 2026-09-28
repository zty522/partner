import React, { useState } from "react";
import { api, Subject } from "../lib/api";

export function TopBar({
  auth,
  onLogin,
}: { auth: { csrf?: string; subject?: Subject } | null; onLogin: (a: any) => void }) {
  const [token, setToken] = useState("");
  const [err, setErr] = useState<string | null>(null);

  async function submitLogin(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    try {
      const r = await api.login(token.trim());
      onLogin({ csrf: r.csrf_token, subject: r.subject });
    } catch (e: any) {
      setErr(e.message);
    }
  }

  if (auth?.subject) {
    return (
      <header className="topbar">
        <div className="brand-mark">P</div>
        <strong>Partner Observatory</strong>
        <span className="subject">
          已登录：{auth.subject.display_name}（{auth.subject.subject_id}）
          · 允许实例 {auth.subject.allowed_instances.join(", ") || "（无）"}
        </span>
      </header>
    );
  }
  return (
      <header className="topbar">
      <div className="brand-mark">P</div>
      <strong>Partner Observatory</strong>
      <form onSubmit={submitLogin} className="login-form">
        <input
          placeholder="粘贴启动时显示的访问令牌"
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
        />
        <button type="submit">进入工作台</button>
        {err && <span className="err">{err}</span>}
      </form>
    </header>
  );
}
