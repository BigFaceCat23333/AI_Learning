import { type FormEvent, type KeyboardEvent, useState, useRef, useEffect } from "react";
import type { QueryResponse } from "../types/api";
import { queryDocument } from "../api/client";
import SourceList from "./SourceList";

interface Message {
  role: "user" | "assistant";
  content: string;
  sources?: QueryResponse["sources"];
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
  const chatEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function handleSend() {
    const question = input.trim();
    if (!question || loading) return;

    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: question }]);
    setLoading(true);

    try {
      const response = await queryDocument(question, topK);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: response.answer,
          sources: response.sources,
        },
      ]);
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : "请求失败";
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: errMsg, error: true },
      ]);
    } finally {
      setLoading(false);
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

  const canSend = input.trim().length > 0 && !loading && hasDocuments;

  return (
    <div className="chat-panel">
      <h3 className="panel-title">💬 文档解读</h3>

      <div className="chat-messages">
        {messages.length === 0 && (
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
  );
}
