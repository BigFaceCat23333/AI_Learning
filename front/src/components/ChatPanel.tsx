import { type FormEvent, type KeyboardEvent, useState, useRef, useEffect, useCallback } from "react";
import type { QuerySource } from "../types/api";
import { queryDocument } from "../api/client";
import SourceList from "./SourceList";
import ConversationSidebar from "./ConversationSidebar";

interface Message {
  role: "user" | "assistant";
  content: string;
  sources?: QuerySource[];
  error?: boolean;
}

interface ChatPanelProps {
  hasDocuments: boolean;
}

export default function ChatPanel({ hasDocuments }: ChatPanelProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [topK, setTopK] = useState(3);
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // 活动会话 ID
  const [activeConversationId, setActiveConversationId] = useState<number | null>(null);

  // 防止快速切换时旧响应覆盖新选择
  const requestSeqRef = useRef(0);

  const chatEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  /** 通知侧栏刷新列表 */
  const notifySidebarRefresh = useCallback(() => {
    window.dispatchEvent(new CustomEvent("conv-refresh"));
  }, []);

  /** 清空当前会话（新建会话） */
  const handleNewConversation = useCallback(() => {
    // 递增序号使进行中的请求作废
    requestSeqRef.current += 1;
    setLoading(false);
    setMessages([]);
    setInput("");
    setErrorMsg(null);
    setActiveConversationId(null);
  }, []);

  /** 加载历史会话到当前视图 */
  const handleSelectConversation = useCallback(
    (convId: number, _title: string, historyMessages: Message[]) => {
      // 递增序号使进行中的请求作废
      requestSeqRef.current += 1;
      setLoading(false);
      setActiveConversationId(convId);
      setMessages(historyMessages);
      setErrorMsg(null);
      setInput("");
    },
    [],
  );

  /** 发送消息 */
  async function handleSend() {
    const question = input.trim();
    if (!question || loading) return;

    const seq = requestSeqRef.current;
    setInput("");
    setErrorMsg(null);
    setMessages((prev) => [...prev, { role: "user", content: question }]);
    setLoading(true);

    try {
      const response = await queryDocument(
        question,
        topK,
        activeConversationId ?? undefined,
      );

      // 防止旧响应覆盖新选择
      if (seq !== requestSeqRef.current) return;

      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: response.answer,
          sources: response.sources,
        },
      ]);

      // 首次发送成功时激活会话
      if (activeConversationId === null && response.conversation_id != null) {
        setActiveConversationId(response.conversation_id);
      }

      // 通知侧栏刷新（新会话和既有会话均需更新排序）
      notifySidebarRefresh();
    } catch (err) {
      if (seq !== requestSeqRef.current) return;
      const errMsg = err instanceof Error ? err.message : "请求失败";
      setErrorMsg(errMsg);
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: errMsg, error: true },
      ]);
    } finally {
      if (seq === requestSeqRef.current) {
        setLoading(false);
      }
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    handleSend();
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  /** 会话改名后回调（侧栏自行维护列表，此处仅占位） */
  const handleRename = useCallback((_convId: number, _newTitle: string) => {}, []);

  /** 删除会话后如果是当前会话则清空 */
  const handleDelete = useCallback(
    (convId: number) => {
      if (convId === activeConversationId) {
        handleNewConversation();
      }
    },
    [activeConversationId, handleNewConversation],
  );

  const canSend = input.trim().length > 0 && !loading && hasDocuments;

  return (
    <div className="chat-panel">
      <ConversationSidebar
        activeConversationId={activeConversationId}
        disabled={loading}
        onSelect={handleSelectConversation}
        onNew={handleNewConversation}
        onRename={handleRename}
        onDelete={handleDelete}
      />

      <div className="chat-main">
        <div className="chat-messages">
          {messages.length === 0 && !loading && (
            <div className="chat-empty">
              {hasDocuments
                ? "👋 文档已就绪，在下方输入问题开始解读。"
                : "📂 请先上传文档后再提问。"}
            </div>
          )}

          {messages.map((msg, index) => (
            <div
              key={index}
              className={`message ${msg.role} ${msg.error ? "error" : ""}`}
            >
              <div className="message-role">
                {msg.role === "user" ? "🧑 你" : "🤖 AI 解读"}
              </div>
              <div className="message-content">{msg.content}</div>
              {msg.sources && msg.sources.length > 0 && (
                <SourceList sources={msg.sources} />
              )}
            </div>
          ))}

          {loading && (
            <div className="message assistant loading">
              <div className="message-role">🤖 AI 解读</div>
              <div className="message-content typing-indicator">
                正在解读<span className="dots">...</span>
              </div>
            </div>
          )}

          <div ref={chatEndRef} />
        </div>

        {errorMsg && (
          <div className="chat-error-banner">{errorMsg}</div>
        )}

        <form className="chat-input-area" onSubmit={handleSubmit}>
          <div className="input-row">
            <textarea
              className="chat-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={
                hasDocuments
                  ? "输入问题，Enter 发送，Shift+Enter 换行"
                  : "请先上传文档"
              }
              rows={2}
              disabled={!hasDocuments || loading}
            />
            <div className="input-actions">
              <label className="topk-label">
                top_k:
                <select
                  value={topK}
                  onChange={(e) => setTopK(Number(e.target.value))}
                  disabled={loading}
                >
                  {Array.from({ length: 10 }, (_, i) => i + 1).map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="submit"
                className="send-button"
                disabled={!canSend}
              >
                发送
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}
