import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { fetchCaptcha, login } from "../api/client";
import type { UserInfo } from "../types/api";

interface LoginPageProps {
  onLoginSuccess: (user: UserInfo) => void;
  successMessage?: string | null;
}

export default function LoginPage({ onLoginSuccess, successMessage }: LoginPageProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [captchaCode, setCaptchaCode] = useState("");
  const [captchaId, setCaptchaId] = useState<string | null>(null);
  const [captchaBlobUrl, setCaptchaBlobUrl] = useState<string | null>(null);
  const [captchaLoading, setCaptchaLoading] = useState(false);
  const [captchaError, setCaptchaError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Blob URL ref，用于卸载和替换时释放
  const blobUrlRef = useRef<string | null>(null);
  // 验证码请求 generation 防止旧请求覆盖新请求结果
  const captchaGenRef = useRef(0);
  // 组件是否已挂载标记，防止卸载后 setState
  const mountedRef = useRef(true);

  /** 释放指定 Blob URL（如果不是当前显示的则不重置状态） */
  const revokeBlobUrl = useCallback((url: string | null) => {
    if (url) URL.revokeObjectURL(url);
  }, []);

  /** 释放当前 blob */
  const revokeCurrentBlob = useCallback(() => {
    if (blobUrlRef.current) {
      URL.revokeObjectURL(blobUrlRef.current);
      blobUrlRef.current = null;
    }
  }, []);

  /** 加载验证码 */
  const loadCaptcha = useCallback(async () => {
    // 刷新开始立即清空挑战状态，防止提交旧挑战
    setCaptchaId(null);
    setCaptchaBlobUrl(null);
    setCaptchaCode("");
    setCaptchaError(null);
    setCaptchaLoading(true);
    revokeCurrentBlob();

    // 递增 generation，此后的旧请求结果将被忽略
    captchaGenRef.current += 1;
    const myGen = captchaGenRef.current;

    try {
      const { captchaId: id, blobUrl } = await fetchCaptcha();
      // 仅当组件仍挂载且未有更新的请求发起时才应用结果
      if (!mountedRef.current || captchaGenRef.current !== myGen) {
        // 过期结果或已卸载：立即释放 blob，不 setState
        revokeBlobUrl(blobUrl);
        return;
      }
      setCaptchaId(id);
      setCaptchaBlobUrl(blobUrl);
      blobUrlRef.current = blobUrl;
    } catch (err) {
      if (!mountedRef.current || captchaGenRef.current !== myGen) return;
      setCaptchaError(err instanceof Error ? err.message : "验证码加载失败");
    } finally {
      if (mountedRef.current && captchaGenRef.current === myGen) {
        setCaptchaLoading(false);
      }
    }
  }, [revokeCurrentBlob, revokeBlobUrl]);

  // 首次加载验证码；卸载时标记已卸载并释放资源
  useEffect(() => {
    mountedRef.current = true;
    loadCaptcha();
    return () => {
      mountedRef.current = false;
      // 递增 generation 使所有进行中的请求结果失效
      captchaGenRef.current += 1;
      revokeCurrentBlob();
    };
  }, [loadCaptcha, revokeCurrentBlob]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();

    const trimmedUser = username.trim();
    if (!trimmedUser || !password || !captchaCode || !captchaId) return;

    setError(null);
    setLoading(true);

    try {
      const user = await login({
        username: trimmedUser,
        password,
        captcha_id: captchaId,
        captcha_code: captchaCode,
      });
      onLoginSuccess(user);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "登录失败";
      setError(msg);
      // 登录失败后清空验证码并自动换一张
      setCaptchaCode("");
      loadCaptcha();
    } finally {
      setLoading(false);
    }
  }

  /** 点击图片或刷新按钮触发重新加载 */
  function handleCaptchaRefresh() {
    if (captchaLoading || loading) return;
    loadCaptcha();
  }

  const canSubmit =
    !loading &&
    !captchaLoading &&
    username.trim().length > 0 &&
    password.length > 0 &&
    captchaCode.trim().length > 0 &&
    captchaId !== null;

  return (
    <div className="login-page">
      <div className="login-card">
        <h1 className="login-title">AI Learning</h1>
        <p className="login-subtitle">文档解读工作台</p>

        {successMessage && (
          <div className="login-success-banner">{successMessage}</div>
        )}

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

          <div className="login-field">
            <label htmlFor="login-captcha">验证码</label>
            <div className="captcha-row">
              <input
                id="login-captcha"
                type="text"
                autoComplete="off"
                value={captchaCode}
                onChange={(e) => setCaptchaCode(e.target.value)}
                disabled={loading || captchaLoading}
                maxLength={10}
                placeholder="请输入验证码"
                className="captcha-input"
              />
              <button
                type="button"
                className="captcha-image-button"
                onClick={handleCaptchaRefresh}
                disabled={captchaLoading || loading}
                aria-label="刷新验证码"
                title="点击刷新验证码"
              >
                {captchaLoading ? (
                  <span className="captcha-loading-text">加载中...</span>
                ) : captchaBlobUrl ? (
                  <img
                    src={captchaBlobUrl}
                    alt="验证码"
                    className="captcha-img"
                  />
                ) : (
                  <span className="captcha-error-text">加载失败</span>
                )}
              </button>
            </div>
            {captchaError && (
              <span className="captcha-inline-error">
                {captchaError}{" "}
                <button
                  type="button"
                  className="captcha-retry-link"
                  onClick={handleCaptchaRefresh}
                >
                  重试
                </button>
              </span>
            )}
          </div>

          {error && <div className="login-error">❌ {error}</div>}

          <button
            type="submit"
            className="login-button"
            disabled={!canSubmit}
          >
            {loading ? "登录中..." : "登录"}
          </button>
        </form>
      </div>
    </div>
  );
}
