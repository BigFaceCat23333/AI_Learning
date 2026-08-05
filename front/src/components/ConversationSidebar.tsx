import { useState, useEffect, useRef, useCallback } from "react";
import type { ConversationSummary, QuerySource } from "../types/api";
import {
  listConversations,
  getConversation,
  renameConversation,
  deleteConversation,
} from "../api/client";

interface Message {
  role: "user" | "assistant";
  content: string;
  sources?: QuerySource[];
  error?: boolean;
}

interface ConversationSidebarProps {
  activeConversationId: number | null;
  disabled?: boolean;
  onSelect: (convId: number, title: string, messages: Message[]) => void;
  onNew: () => void;
  onRename: (convId: number, newTitle: string) => void;
  onDelete: (convId: number) => void;
}

const PAGE_SIZE = 20;

export default function ConversationSidebar({
  activeConversationId,
  disabled = false,
  onSelect,
  onNew,
  onRename,
  onDelete,
}: ConversationSidebarProps) {
  const [items, setItems] = useState<ConversationSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [desktopCollapsed, setDesktopCollapsed] = useState(false);

  // 刷新/加载更多独立错误状态
  const [refreshLoading, setRefreshLoading] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const refreshActiveRef = useRef(false);     // 当前是否正在刷新
  const pendingRefreshRef = useRef(false);    // 进行中有新事件到达，完成后需再刷新一次
  const listSeqRef = useRef(0);               // 列表请求序号，防止旧响应覆盖新数据
  const [loadMoreLoading, setLoadMoreLoading] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);
  const loadMoreOffsetRef = useRef(0);

  // 改名状态
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [renameError, setRenameError] = useState<string | null>(null);

  // 删除确认
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  // 详情加载状态
  const [detailLoadingId, setDetailLoadingId] = useState<number | null>(null);
  const [detailError, setDetailError] = useState<{ id: number; msg: string } | null>(null);

  // 详情请求序号 + AbortController
  const detailSeqRef = useRef(0);
  const detailAbortRef = useRef<AbortController | null>(null);

  const loadedRef = useRef(false);

  /** 初始加载 —— 使用序号防慢速旧响应覆盖后续刷新结果 */
  const loadInitial = useCallback(async () => {
    listSeqRef.current += 1;
    const seq = listSeqRef.current;
    setListLoading(true);
    setListError(null);
    try {
      const res = await listConversations(0, PAGE_SIZE);
      if (seq !== listSeqRef.current) return;  // 已被后续刷新取代
      setItems(res.items);
      setTotal(res.total);
    } catch (err) {
      if (seq !== listSeqRef.current) return;
      setListError(err instanceof Error ? err.message : "加载失败");
    } finally {
      if (seq === listSeqRef.current) {
        setListLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    if (!loadedRef.current) {
      loadedRef.current = true;
      loadInitial();
    }
  }, [loadInitial]);

  /** 执行一次列表刷新请求（内部函数）——使用序号防慢速旧响应覆盖 */
  const _doRefresh = useCallback(async () => {
    listSeqRef.current += 1;
    const seq = listSeqRef.current;
    try {
      const res = await listConversations(0, PAGE_SIZE);
      if (seq !== listSeqRef.current) return true;  // 已被更新请求取代
      setItems(res.items);
      setTotal(res.total);
      setRefreshError(null);  // 成功后清除错误
      return true;
    } catch (err) {
      if (seq !== listSeqRef.current) return false;
      setRefreshError(err instanceof Error ? err.message : "刷新失败");
      return false;
    }
  }, []);

  /** 刷新列表（续聊/新建后触发）——合并并发事件：进行中有新事件到达时标记 pending，完成后自动再刷一次 */
  const refreshList = useCallback(async () => {
    // 正在刷新中：标记 pending 后返回，当前请求完成后会自动再刷
    if (refreshActiveRef.current) {
      pendingRefreshRef.current = true;
      return;
    }
    refreshActiveRef.current = true;
    setRefreshLoading(true);
    // 注意：不清除 refreshError，保持错误横幅可见以让"重试中..."按钮渲染

    await _doRefresh();

    // 如果在本次刷新期间又有新事件到达，再刷一次
    if (pendingRefreshRef.current) {
      pendingRefreshRef.current = false;
      await _doRefresh();
    }

    setRefreshLoading(false);
    setListLoading(false);  // 清除初始加载的 loading（可能被竞态跳过）
    refreshActiveRef.current = false;
  }, [_doRefresh]);

  /** 父组件通过事件触发刷新（续聊成功或新建会话成功后调用） */
  useEffect(() => {
    const handler = () => refreshList();
    window.addEventListener("conv-refresh", handler);
    return () => window.removeEventListener("conv-refresh", handler);
  }, [refreshList]);

  /** 加载更多 —— 独立 loading/error 状态，存储 offset 供重试，防重复请求 */
  const handleLoadMore = useCallback(async () => {
    if (items.length >= total || loadMoreLoading) return;
    const offset = items.length;
    loadMoreOffsetRef.current = offset;
    setLoadMoreLoading(true);
    setLoadMoreError(null);
    try {
      const res = await listConversations(offset, PAGE_SIZE);
      const existingIds = new Set(items.map((i) => i.id));
      const newItems = res.items.filter((i) => !existingIds.has(i.id));
      setItems((prev) => [...prev, ...newItems]);
      setTotal(res.total);
    } catch (err) {
      setLoadMoreError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoadMoreLoading(false);
    }
  }, [items, total, loadMoreLoading]);

  /** 重试加载更多（使用上次失败的 offset） */
  const retryLoadMore = useCallback(() => {
    setLoadMoreError(null);
    // 用 ref 中保存的 offset 触发重试——先强制允许再调用
    const offset = loadMoreOffsetRef.current;
    setLoadMoreLoading(true);
    listConversations(offset, PAGE_SIZE)
      .then((res) => {
        setItems((prev) => {
          const existingIds = new Set(prev.map((i) => i.id));
          const newItems = res.items.filter((i) => !existingIds.has(i.id));
          return [...prev, ...newItems];
        });
        setTotal(res.total);
      })
      .catch((err) => {
        setLoadMoreError(err instanceof Error ? err.message : "加载失败");
      })
      .finally(() => {
        setLoadMoreLoading(false);
      });
  }, []);

  /** 点击会话项 — 使用 AbortController + 序号防竞态 */
  const handleClick = useCallback(
    async (conv: ConversationSummary) => {
      if (conv.id === activeConversationId || disabled) return;

      // 取消上一个进行中的请求
      detailAbortRef.current?.abort();
      detailSeqRef.current += 1;
      const seq = detailSeqRef.current;
      const controller = new AbortController();
      detailAbortRef.current = controller;

      setDetailLoadingId(conv.id);
      setDetailError(null);

      try {
        const detail = await getConversation(conv.id);
        // 竞态保护：只有最新请求的结果才提交
        if (seq !== detailSeqRef.current || controller.signal.aborted) return;

        const msgs: Message[] = detail.messages.map((m) => ({
          role: m.role as "user" | "assistant",
          content: m.content,
          sources: m.sources ?? undefined,
        }));
        onSelect(conv.id, detail.title, msgs);
      } catch (err) {
        if (seq !== detailSeqRef.current || controller.signal.aborted) return;
        const msg = err instanceof Error ? err.message : "加载失败";
        setDetailError({ id: conv.id, msg });
      } finally {
        if (seq === detailSeqRef.current && !controller.signal.aborted) {
          setDetailLoadingId(null);
        }
      }
    },
    [activeConversationId, disabled, onSelect],
  );

  /** 重试加载失败的会话详情 */
  const retryDetail = useCallback(
    (conv: ConversationSummary) => {
      setDetailError(null);
      handleClick(conv);
    },
    [handleClick],
  );

  /** 开始改名 */
  const startRename = useCallback((conv: ConversationSummary) => {
    setEditingId(conv.id);
    setEditTitle(conv.title);
    setRenameError(null);
  }, []);

  /** 提交改名 */
  const submitRename = useCallback(
    async (convId: number) => {
      const trimmed = editTitle.trim();
      if (!trimmed || trimmed.length > 100) {
        setRenameError("标题长度需在 1～100 个字符之间。");
        return;
      }
      setRenameError(null);
      try {
        const updated = await renameConversation(convId, trimmed);
        setItems((prev) =>
          prev.map((i) => (i.id === convId ? { ...i, title: updated.title } : i)),
        );
        onRename(convId, updated.title);
        setEditingId(null);
      } catch (err) {
        setRenameError(err instanceof Error ? err.message : "改名失败");
      }
    },
    [editTitle, onRename],
  );

  /** 取消改名 */
  const cancelRename = useCallback(() => {
    setEditingId(null);
    setEditTitle("");
    setRenameError(null);
  }, []);

  /** 执行删除 */
  const confirmDelete = useCallback(
    async (convId: number) => {
      setDeleteLoading(true);
      setDeleteError(null);
      try {
        await deleteConversation(convId);
        setItems((prev) => prev.filter((i) => i.id !== convId));
        setTotal((prev) => prev - 1);
        onDelete(convId);
        setDeletingId(null);
      } catch (err) {
        setDeleteError(err instanceof Error ? err.message : "删除失败");
      } finally {
        setDeleteLoading(false);
      }
    },
    [onDelete],
  );

  const hasMore = items.length < total;

  const isDetailError = (convId: number) =>
    detailError !== null && detailError.id === convId;

  return (
    <>
      {/* 移动端切换按钮 */}
      <button
        className="conv-sidebar-toggle"
        onClick={() => setSidebarOpen((v) => !v)}
        aria-label="历史会话"
      >
        {sidebarOpen ? "✕" : "☰"} 历史会话
      </button>

      <div
        className={`conv-sidebar ${sidebarOpen ? "open" : ""} ${desktopCollapsed ? "collapsed" : ""}`}
      >
        <div className="conv-sidebar-header">
          <button
            type="button"
            className="conv-new-button"
            onClick={onNew}
            disabled={disabled}
          >
            ＋ 新建会话
          </button>
          <button
            type="button"
            className="conv-collapse-button"
            onClick={() => setDesktopCollapsed((collapsed) => !collapsed)}
            aria-label={desktopCollapsed ? "展开历史会话栏" : "收起历史会话栏"}
            aria-expanded={!desktopCollapsed}
            title={desktopCollapsed ? "展开历史会话栏" : "收起历史会话栏"}
          >
            {desktopCollapsed ? "»" : "«"}
          </button>
        </div>

        <div className="conv-list">
          {listLoading && items.length === 0 && (
            <div className="conv-list-status">加载中...</div>
          )}

          {/* 初始加载失败（无数据时全屏错误，刷新进行中不显示） */}
          {listError && items.length === 0 && !refreshError && !refreshLoading && (
            <div className="conv-list-error">
              <span>{listError}</span>
              <button className="conv-retry-button" onClick={loadInitial}>
                重试
              </button>
            </div>
          )}

          {/* 刷新失败横幅（空列表和非空列表都可见，重试期间保留错误 + 显示 loading 状态） */}
          {refreshError && (
            <div className="conv-list-error conv-refresh-error">
              <span>{refreshError}</span>
              <button
                className="conv-retry-button"
                onClick={() => refreshList()}
                disabled={refreshLoading}
              >
                {refreshLoading ? "重试中..." : "重试"}
              </button>
            </div>
          )}

          {/* 刷新中但无历史错误（纯 loading 横幅，如首次创建后的自动刷新） */}
          {refreshLoading && !refreshError && items.length === 0 && (
            <div className="conv-list-status">加载中...</div>
          )}

          {!listLoading && !listError && !refreshLoading && !refreshError && items.length === 0 && (
            <div className="conv-list-empty">暂无历史会话</div>
          )}

          {items.map((conv) => {
            const isLoading = detailLoadingId === conv.id;
            const hasDetailError = isDetailError(conv.id);

            return (
              <div
                key={conv.id}
                className={`conv-item ${conv.id === activeConversationId ? "active" : ""} ${isLoading ? "loading" : ""}`}
                onClick={() => {
                  if (editingId !== conv.id && !isLoading) handleClick(conv);
                }}
              >
                {editingId === conv.id ? (
                  <div
                    className="conv-edit-form"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <input
                      className="conv-edit-input"
                      value={editTitle}
                      onChange={(e) => setEditTitle(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") submitRename(conv.id);
                        if (e.key === "Escape") cancelRename();
                      }}
                      maxLength={100}
                      autoFocus
                    />
                    {renameError && (
                      <div className="conv-edit-error">{renameError}</div>
                    )}
                    <div className="conv-edit-actions">
                      <button
                        className="conv-edit-save"
                        onClick={() => submitRename(conv.id)}
                      >
                        保存
                      </button>
                      <button
                        className="conv-edit-cancel"
                        onClick={cancelRename}
                      >
                        取消
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="conv-item-main">
                      <div className="conv-item-title">
                        {isLoading ? "加载中..." : conv.title}
                      </div>
                      <div className="conv-item-time">
                        {new Date(conv.last_message_at).toLocaleString("zh-CN")}
                      </div>
                      {hasDetailError && (
                        <div className="conv-item-error">
                          {detailError!.msg}
                          <button
                            className="conv-retry-button"
                            onClick={(e) => {
                              e.stopPropagation();
                              retryDetail(conv);
                            }}
                          >
                            重试
                          </button>
                        </div>
                      )}
                    </div>
                    <div
                      className="conv-item-actions"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <button
                        className="conv-action-btn"
                        title="重命名"
                        onClick={() => startRename(conv)}
                        disabled={disabled}
                      >
                        ✎
                      </button>
                      <button
                        className="conv-action-btn conv-action-delete"
                        title="删除"
                        onClick={() => setDeletingId(conv.id)}
                        disabled={disabled}
                      >
                        ✕
                      </button>
                    </div>
                  </>
                )}
              </div>
            );
          })}

          {/* 加载更多：按钮 + 独立错误行 + 重试 */}
          {loadMoreError && (
            <div className="conv-list-error conv-loadmore-error">
              <span>{loadMoreError}</span>
              <button className="conv-retry-button" onClick={retryLoadMore}>
                重试
              </button>
            </div>
          )}

          {(hasMore || loadMoreLoading) && !loadMoreError && (
            <button
              className="conv-load-more"
              onClick={handleLoadMore}
              disabled={loadMoreLoading}
            >
              {loadMoreLoading ? "加载中..." : "加载更多"}
            </button>
          )}
        </div>
      </div>

      {/* 删除确认对话框 */}
      {deletingId !== null && (
        <div
          className="modal-overlay"
          onClick={() => !deleteLoading && setDeletingId(null)}
        >
          <div
            className="delete-confirm-modal"
            onClick={(e) => e.stopPropagation()}
          >
            <h2>删除会话</h2>
            <p>确定要从历史列表移除此会话吗？当前版本不提供恢复。</p>
            {deleteError && (
              <div className="delete-confirm-error">{deleteError}</div>
            )}
            <div className="delete-confirm-actions">
              <button
                className="profile-button-secondary"
                onClick={() => setDeletingId(null)}
                disabled={deleteLoading}
              >
                取消
              </button>
              <button
                className="delete-confirm-button"
                onClick={() => confirmDelete(deletingId)}
                disabled={deleteLoading}
              >
                {deleteLoading ? "删除中..." : "确定删除"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
