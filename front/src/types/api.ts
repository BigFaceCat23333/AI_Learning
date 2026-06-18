/** 与后端 api/schemas.py 对应的前端类型定义 */

export interface DocumentUploadResponse {
  document_id: number;
  filename: string;
  file_type: string;
  chunk_count: number;
}

export interface QueryRequest {
  question: string;
  top_k: number;
}

export interface QuerySource {
  document_id: number;
  filename: string;
  file_type: string;
  chunk_id: number;
  chunk_index: number;
  chunk_text: string;
  score: number;
  chunk_metadata: Record<string, unknown>;
}

export interface QueryResponse {
  question: string;
  answer: string;
  sources: QuerySource[];
}

/** FastAPI 通用错误响应 { detail: string } */
export interface ApiError {
  detail: string;
}
