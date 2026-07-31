import type {
  ApiError,
  DocumentListItem,
  DocumentUploadResponse,
  LoginRequest,
  OnUnauthorized,
  PasswordChangeRequest,
  ProfileUpdateRequest,
  QueryRequest,
  QueryResponse,
  UserInfo,
} from "../types/api";

const BASE_URL: string =
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api";

/** 401 统一回调（由 App 组件设置） */
let onUnauthorized: OnUnauthorized | null = null;

export function setOnUnauthorized(callback: OnUnauthorized) {
  onUnauthorized = callback;
}

/** 从 FastAPI 错误响应中提取 detail 消息 */
async function extractDetail(response: Response): Promise<string> {
  try {
    const body: ApiError = await response.json();
    return body.detail ?? `请求失败 (HTTP ${response.status})`;
  } catch {
    return `请求失败 (HTTP ${response.status})`;
  }
}

/** 通用请求包装：credentials: "include" + 统一 401 处理 */
async function apiFetch(
  path: string,
  init?: RequestInit,
  opts?: { notifyUnauthorized?: boolean },
): Promise<Response> {
  const shouldNotify = opts?.notifyUnauthorized ?? true;
  const response = await fetch(`${BASE_URL}${path}`, {
    ...init,
    credentials: "include",
  });

  if (response.status === 401 && shouldNotify && onUnauthorized) {
    onUnauthorized();
  }

  return response;
}

// ── 认证接口 ──

/** 登录（登录接口自身的 401 不触发全局回调，由 LoginPage 展示错误） */
export async function login(body: LoginRequest): Promise<UserInfo> {
  const response = await apiFetch(
    "/auth/login",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    { notifyUnauthorized: false },
  );

  if (!response.ok) {
    const detail = await extractDetail(response);
    throw new Error(detail);
  }

  return response.json() as Promise<UserInfo>;
}

/** 退出登录 */
export async function logout(): Promise<void> {
  await apiFetch("/auth/logout", { method: "POST" });
}

/** 获取当前用户 */
export async function getMe(): Promise<UserInfo> {
  const response = await apiFetch("/auth/me");

  if (!response.ok) {
    const detail = await extractDetail(response);
    throw new Error(detail);
  }

  return response.json() as Promise<UserInfo>;
}

// ── 业务接口 ──

/** 上传文档到后端 */
export async function uploadDocument(
  file: File,
): Promise<DocumentUploadResponse> {
  const formData = new FormData();
  formData.append("file", file);

  const response = await apiFetch("/documents/upload", {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const detail = await extractDetail(response);
    throw new Error(detail);
  }

  return response.json() as Promise<DocumentUploadResponse>;
}

/** 向后端提交问题，获取解读结果 */
export async function queryDocument(
  question: string,
  topK: number = 3,
): Promise<QueryResponse> {
  const body: QueryRequest = { question, top_k: topK };

  const response = await apiFetch("/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const detail = await extractDetail(response);
    throw new Error(detail);
  }

  return response.json() as Promise<QueryResponse>;
}

/** 获取历史文档列表 */
export async function listDocuments(): Promise<DocumentListItem[]> {
  const response = await apiFetch("/documents");

  if (!response.ok) {
    const detail = await extractDetail(response);
    throw new Error(detail);
  }

  return response.json() as Promise<DocumentListItem[]>;
}

/** 下载文档原文件，通过 Blob 和临时对象 URL 触发浏览器下载 */
export async function downloadDocument(
  documentId: number,
  filename: string,
): Promise<void> {
  const response = await apiFetch(`/documents/${documentId}/download`);

  if (!response.ok) {
    const detail = await extractDetail(response);
    throw new Error(detail);
  }

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

// ── 验证码与个人资料接口 ──

/** 获取验证码图片，返回 { captchaId, blobUrl } */
export async function fetchCaptcha(): Promise<{ captchaId: string; blobUrl: string }> {
  const response = await apiFetch("/auth/captcha", {}, { notifyUnauthorized: false });

  if (!response.ok) {
    const detail = await extractDetail(response);
    throw new Error(detail);
  }

  const captchaId = response.headers.get("X-Captcha-Id");
  if (!captchaId) {
    throw new Error("验证码响应缺少挑战 ID。");
  }

  const blob = await response.blob();
  const blobUrl = URL.createObjectURL(blob);
  return { captchaId, blobUrl };
}

/** 更新个人资料 */
export async function updateProfile(body: ProfileUpdateRequest): Promise<UserInfo> {
  const response = await apiFetch("/auth/me/profile", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const detail = await extractDetail(response);
    throw new Error(detail);
  }

  return response.json() as Promise<UserInfo>;
}

/** 上传头像 */
export async function uploadAvatar(file: File): Promise<UserInfo> {
  const formData = new FormData();
  formData.append("file", file);

  const response = await apiFetch("/auth/me/avatar", {
    method: "PUT",
    body: formData,
  });

  if (!response.ok) {
    const detail = await extractDetail(response);
    throw new Error(detail);
  }

  return response.json() as Promise<UserInfo>;
}

/** 删除头像 */
export async function deleteAvatar(): Promise<UserInfo> {
  const response = await apiFetch("/auth/me/avatar", {
    method: "DELETE",
  });

  if (!response.ok) {
    const detail = await extractDetail(response);
    throw new Error(detail);
  }

  return response.json() as Promise<UserInfo>;
}

/** 构造头像完整 URL */
export function getAvatarUrl(avatarUrl: string | null | undefined): string | null {
  if (!avatarUrl) return null;
  return `${BASE_URL}${avatarUrl}`;
}

/** 修改密码（当前密码错误不触发全局未授权回调） */
export async function changePassword(body: PasswordChangeRequest): Promise<void> {
  const response = await apiFetch(
    "/auth/me/password",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    { notifyUnauthorized: false },
  );

  if (!response.ok) {
    const detail = await extractDetail(response);
    throw new Error(detail);
  }
}
