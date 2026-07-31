import { type FormEvent, useState } from "react";
import { login } from "../api/client";
import type { UserInfo } from "../types/api";

interface LoginPageProps {
  onLoginSuccess: (user: UserInfo) => void;
}

export default function LoginPage({ onLoginSuccess }: LoginPageProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();

    const trimmedUser = username.trim();
    if (!trimmedUser || !password) return;

    setError(null);
    setLoading(true);

    try {
      const user = await login({ username: trimmedUser, password });
      onLoginSuccess(user);
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <h1 className="login-title">AI Learning</h1>
        <p className="login-subtitle">文档解读工作台</p>

        <form className="login-form" onSubmit={handleSubmit}>
          <div className="login-field">
            <label htmlFor="login-username">用户名</label>
            <input
              id="login-username"
              type="text"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              disabled={loading}
              maxLength={64}
              placeholder="请输入用户名"
            />
          </div>

          <div className="login-field">
            <label htmlFor="login-password">密码</label>
            <input
              id="login-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={loading}
              maxLength={256}
              placeholder="请输入密码"
            />
          </div>

          {error && <div className="login-error">❌ {error}</div>}

          <button
            type="submit"
            className="login-button"
            disabled={loading || !username.trim() || !password}
          >
            {loading ? "登录中..." : "登录"}
          </button>
        </form>
      </div>
    </div>
  );
}
