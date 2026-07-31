import { useCallback, useEffect, useRef, useState } from "react";
import type { DocumentListItem, UserInfo } from "./types/api";
import { getMe, listDocuments, logout, setOnUnauthorized } from "./api/client";
import LoginPage from "./components/LoginPage";
import UploadPanel from "./components/UploadPanel";
import ChatPanel from "./components/ChatPanel";

/** 页面初始状态 */
type AuthPhase =
  | { tag: "checking" }
  | { tag: "logged-out" }
  | { tag: "error"; message: string }
  | { tag: "logged-in"; user: UserInfo };

/** 重置工作台内存状态 */
function blankWorkspace() {
  return {
    documents: [] as DocumentListItem[],
    listLoading: true,
    listError: null as string | null,
    hasUploadedDocs: false,
  };
}

export default function App() {
  const [phase, setPhase] = useState<AuthPhase>({ tag: "checking" });
  const [documents, setDocuments] = useState<DocumentListItem[]>([]);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [hasUploadedDocs, setHasUploadedDocs] = useState(false);
  // 用 ref 追踪当前是否已登录，避免在已退出时继续设置文档状态
  const loggedInRef = useRef(false);

  /** 清空工作台状态并切换到未登录 */
  function resetWorkspaceState() {
    loggedInRef.current = false;
    const ws = blankWorkspace();
    setDocuments(ws.documents);
    setListLoading(ws.listLoading);
    setListError(ws.listError);
    setHasUploadedDocs(ws.hasUploadedDocs);
    setPhase({ tag: "logged-out" });
  }

  // 401 统一回调：回到登录页并清空工作台
  const handleUnauthorized = useCallback(() => {
    resetWorkspaceState();
  }, []);

  // 页面启动时检查会话
  useEffect(() => {
    setOnUnauthorized(handleUnauthorized);
    checkSession();
  }, [handleUnauthorized]);

  async function checkSession() {
    try {
      const user = await getMe();
      loggedInRef.current = true;
      setPhase({ tag: "logged-in", user });
    } catch (err) {
      if (err instanceof Error && err.message.includes("401") ||
          (err instanceof Error && err.message.includes("Not authenticated"))) {
        setPhase({ tag: "logged-out" });
      } else {
        setPhase({ tag: "error", message: err instanceof Error ? err.message : "连接服务器失败" });
      }
    }
  }

  async function loadDocuments() {
    if (!loggedInRef.current) return;
    setListLoading(true);
    setListError(null);
    try {
      const docs = await listDocuments();
      if (!loggedInRef.current) return;
      setDocuments(docs);
      setHasUploadedDocs(docs.length > 0);
    } catch (err) {
      if (!loggedInRef.current) return;
      setListError(err instanceof Error ? err.message : "加载知识库失败");
    } finally {
      if (loggedInRef.current) setListLoading(false);
    }
  }

  // 登录成功
  function handleLoginSuccess(user: UserInfo) {
    loggedInRef.current = true;
    setPhase({ tag: "logged-in", user });
  }

  // 上传成功回调
  function handleUploadSuccess() {
    setHasUploadedDocs(true);
    loadDocuments();
  }

  // 退出登录：清空工作台并回到登录页
  async function handleLogout() {
    try {
      await logout();
    } catch {
      // 即使退出请求失败也清空本地状态
    }
    resetWorkspaceState();
  }

  // 登录后加载文档列表
  useEffect(() => {
    if (phase.tag === "logged-in") {
      loadDocuments();
    }
  }, [phase.tag === "logged-in" ? (phase as { tag: "logged-in"; user: UserInfo }).user.user_id : null]);

  // ── 未登录 ──
  if (phase.tag === "checking") {
    return (
      <div className="login-page">
        <div className="login-card">
          <p className="login-loading">⏳ 检查登录状态...</p>
        </div>
      </div>
    );
  }

  if (phase.tag === "logged-out") {
    return <LoginPage onLoginSuccess={handleLoginSuccess} />;
  }

  if (phase.tag === "error") {
    return (
      <div className="login-page">
        <div className="login-card">
          <p className="login-error">❌ {phase.message}</p>
          <button
            type="button"
            className="login-button"
            onClick={checkSession}
          >
            重试
          </button>
        </div>
      </div>
    );
  }

  // ── 已登录工作台 ──
  const { user } = phase;

  return (
    <div className="app">
      <header className="app-header">
        <h1>AI Learning</h1>
        <span className="app-subtitle">文档解读工作台</span>
        <div className="app-header-right">
          <span className="app-user">👤 {user.username}</span>
          <button
            type="button"
            className="app-logout-button"
            onClick={handleLogout}
          >
            退出
          </button>
        </div>
      </header>
      <main className="app-main">
        <aside className="app-sidebar">
          <UploadPanel
            onUploadSuccess={handleUploadSuccess}
            documents={documents}
            listLoading={listLoading}
            listError={listError}
            onRetry={loadDocuments}
          />
        </aside>
        <section className="app-content">
          <ChatPanel hasDocuments={hasUploadedDocs} />
        </section>
      </main>
    </div>
  );
}
