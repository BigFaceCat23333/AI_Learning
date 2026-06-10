import type {
  ApiError,
  DocumentUploadResponse,
  QueryRequest,
  QueryResponse,
} from "../types/api";

const BASE_URL: string =
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api";

/** 从 FastAPI 错误响应中提取 detail 消息 */
async function extractDetail(response: Response): Promise<string> {
  try {
    const body: ApiError = await response.json();
    return body.detail ?? `请求失败 (HTTP ${response.status})`;
  } catch {
    return `请求失败 (HTTP ${response.status})`;
  }
}

/** 上传文档到后端 */
export async function uploadDocument(
  file: File,
): Promise<DocumentUploadResponse> {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(`${BASE_URL}/documents/upload`, {
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

  const response = await fetch(`${BASE_URL}/query`, {
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
