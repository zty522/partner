import React, { useEffect, useState } from "react";
import { ProjectList } from "./pages/ProjectList";
import { RunConsole } from "./pages/RunConsole";
import { LearningLibrary } from "./pages/LearningLibrary";
import { EvolutionCompare } from "./pages/EvolutionCompare";
import { CapabilityMatrix } from "./pages/CapabilityMatrix";
import { ArtifactList } from "./pages/ArtifactList";
import { ConfigViewPage } from "./pages/ConfigView";
import { TopBar } from "./components/TopBar";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { api } from "./lib/api";

type Tab = "projects" | "run" | "learning" | "evolution" | "capability" | "artifacts" | "config";

export function App() {
  const [tab, setTab] = useState<Tab>("projects");
  const [auth, setAuth] = useState<{ csrf?: string; subject?: any } | null>(null);

  useEffect(() => {
    api.healthz().catch(() => setAuth(null));
  }, []);

  return (
    <ErrorBoundary>
      <TopBar auth={auth} onLogin={setAuth} />
      <nav className="tabs">
        <button onClick={() => setTab("projects")}>项目工作台</button>
        <button onClick={() => setTab("run")}>会话与任务</button>
        <button onClick={() => setTab("learning")}>学习来源</button>
        <button onClick={() => setTab("evolution")}>自进化比较</button>
        <button onClick={() => setTab("artifacts")}>产物预览</button>
        <button onClick={() => setTab("capability")}>能力矩阵</button>
        <button onClick={() => setTab("config")}>配置视图</button>
      </nav>
      <main>
        {tab === "projects" && <ProjectList />}
        {tab === "run" && <RunConsole csrf={auth?.csrf} subject={auth?.subject} />}
        {tab === "learning" && <LearningLibrary />}
        {tab === "evolution" && <EvolutionCompare />}
        {tab === "artifacts" && <ArtifactList />}
        {tab === "capability" && <CapabilityMatrix />}
        {tab === "config" && <ConfigViewPage />}
      </main>
    </ErrorBoundary>
  );
}
