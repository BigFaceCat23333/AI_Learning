import { useCallback, useEffect, useRef, useState } from "react";
import type { DocumentListItem, UserInfo } from "./types/api";
import { getAvatarUrl, getMe, listDocuments, logout, setOnUnauthorized } from "./api/client";
import LoginPage from "./components/LoginPage";
import ProfileSettings from "./components/ProfileSettings";
import UploadPanel from "./components/UploadPanel";
import ChatPanel from "./components/ChatPanel";

/** 页面初始状态 */
type AuthPhase =
  | { tag: "checking" }
  | { tag: "logged-out"; successMessage?: string | null }
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
  const [showProfile, setShowProfile] = useState(false);

  // 单调递增的 session generation，每轮登录递增，替代布尔值避免跨用户竞态
  const sessionGenRef = useRef(0);
  // 用于取消旧请求的 AbortController
  const abortRef = useRef<AbortController | null>(null);

  /** 清空工作台状态并递增 generation，取消所有正在进行的请求。
   * 可传入成功消息以在登录页显示。*/
  function resetWorkspaceState(successMessage?: string) {
    sessionGenRef.current += 1;
    // 取消旧请求
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    const ws = blankWorkspace();
    setDocuments(ws.documents);
    setListLoading(ws.listLoading);
    setListError(ws.listError);
    setHasUploadedDocs(ws.hasUploadedDocs);
    setShowProfile(false);
    setPhase({ tag: "logged-out", successMessage: successMessage ?? null });
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
      sessionGenRef.current += 1;
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
    // 发起时捕获当前 generation 并创建 AbortController
    const gen = sessionGenRef.current;
    const controller = new AbortController();
    abortRef.current = controller;

    setListLoading(true);
    setListError(null);
    try {
      const docs = await listDocuments();
      // 校验 generation：必须是同一会话且未被取消
      if (sessionGenRef.current !== gen || controller.signal.aborted) return;
      setDocuments(docs);
      setHasUploadedDocs(docs.length > 0);
    } catch (err) {
      if (sessionGenRef.current !== gen || controller.signal.aborted) return;
      // AbortError 不视为错误
      if (err instanceof DOMException && err.name === "AbortError") return;
      setListError(err instanceof Error ? err.message : "加载知识库失败");
    } finally {
      if (sessionGenRef.current === gen && !controller.signal.aborted) {
        setListLoading(false);
      }
    }
  }

  // 登录成功
  function handleLoginSuccess(user: UserInfo) {
    sessionGenRef.current += 1;
    setPhase({ tag: "logged-in", user });
  }

  // 上传成功回调
  function handleUploadSuccess() {
    setHasUploadedDocs(true);
    loadDocuments();
  }

  // 文档逻辑删除成功后立即同步列表和对话可用状态。
  function handleDocumentDeleted(documentId: number) {
    const remainingDocuments = documents.filter(
      (document) => document.document_id !== documentId,
    );
    setDocuments(remainingDocuments);
    setHasUploadedDocs(remainingDocuments.length > 0);
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

  /** 个人资料更新后刷新当前用户状态 */
  function handleUserUpdate(updatedUser: UserInfo) {
    setPhase((prev) =>
      prev.tag === "logged-in" ? { ...prev, user: updatedUser } : prev,
    );
  }

  /** 修改密码成功后立即执行完整清理，将成功消息带到登录页 */
  function handlePasswordChanged(message: string) {
    resetWorkspaceState(message);
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
    return (
      <LoginPage
        onLoginSuccess={handleLoginSuccess}
        successMessage={phase.successMessage ?? null}
      />
    );
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
  const displayLabel = user.display_name || user.username;
  const avatarSrc = getAvatarUrl(user.avatar_url);

  return (
    <div className="app">
      <header className="app-header">
        <h1>AI Learning</h1>
        <span className="app-subtitle">文档解读工作台</span>
        <div className="app-header-right">
          <button
            type="button"
            className="app-user-button"
            onClick={() => setShowProfile(true)}
            title="个人设置"
          >
            <span className="app-user-avatar">
              {avatarSrc ? (
                <img src={avatarSrc} alt="" className="app-user-avatar-img" />
              ) : (
                <span className="app-user-avatar-placeholder">
                  {displayLabel.charAt(0).toUpperCase()}
                </span>
              )}
            </span>
            <span className="app-user-name">{displayLabel}</span>
          </button>
          <button
            type="button"
            className="app-logout-button"
            onClick={handleLogout}
          >
            退出
          </button>
        </div>
      </header>

      {showProfile && (
        <ProfileSettings
          user={user}
          onUserUpdate={handleUserUpdate}
          onPasswordChanged={handlePasswordChanged}
          onClose={() => setShowProfile(false)}
        />
      )}

      <main className="app-main">
        <aside className="app-sidebar">
          <UploadPanel
            onUploadSuccess={handleUploadSuccess}
            documents={documents}
            listLoading={listLoading}
            listError={listError}
            onRetry={loadDocuments}
            onDeleteSuccess={handleDocumentDeleted}
          />
        </aside>
        <section className="app-content">
          <ChatPanel hasDocuments={hasUploadedDocs} />
        </section>
      </main>
    </div>
  );
}
