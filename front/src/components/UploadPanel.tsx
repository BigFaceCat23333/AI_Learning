import { type ChangeEvent, type DragEvent, useState, useRef } from "react";
import type { DocumentUploadResponse } from "../types/api";
import { uploadDocument } from "../api/client";

const ALLOWED_TYPES = [".txt", ".md"];
const MAX_SIZE = 5 * 1024 * 1024; // 5MB

interface UploadPanelProps {
  onUploadSuccess: () => void;
}

export default function UploadPanel({ onUploadSuccess }: UploadPanelProps) {
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<DocumentUploadResponse | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function validateFile(file: File): string | null {
    const name = file.name.toLowerCase();
    const ext = name.lastIndexOf(".") >= 0 ? name.slice(name.lastIndexOf(".")) : "";
    if (!ALLOWED_TYPES.includes(ext)) {
      return `不支持的文件类型 "${ext || "无后缀"}"，仅支持 ${ALLOWED_TYPES.join(", ")}`;
    }
    if (file.size > MAX_SIZE) {
      const sizeMB = (file.size / (1024 * 1024)).toFixed(1);
      return `文件大小 ${sizeMB}MB 超出 5MB 限制`;
    }
    return null;
  }

  async function handleFile(file: File) {
    setError(null);
    setResult(null);

    const validationError = validateFile(file);
    if (validationError) {
      setError(validationError);
      return;
    }

    setUploading(true);
    try {
      const data = await uploadDocument(file);
      setResult(data);
      onUploadSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传失败");
    } finally {
      setUploading(false);
    }
  }

  function onFileChange(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) handleFile(file);
    // 重置 input，允许再次选择同一文件
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function onDragOver(e: DragEvent) {
    e.preventDefault();
    setDragging(true);
  }

  function onDragLeave(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) handleFile(file);
  }

  function onClickArea() {
    fileInputRef.current?.click();
  }

  return (
    <div className="upload-panel">
      <h3 className="panel-title">📄 文档上传</h3>

      <div
        className={`upload-area ${dragging ? "dragging" : ""} ${uploading ? "uploading" : ""}`}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        onClick={onClickArea}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".txt,.md"
          className="upload-input"
          onChange={onFileChange}
          disabled={uploading}
        />
        {uploading ? (
          <p className="upload-hint">⏳ 正在上传...</p>
        ) : (
          <p className="upload-hint">
            拖拽文件到此处，或点击选择文件
            <br />
            <span className="upload-limit">支持 .txt / .md，最大 5MB</span>
          </p>
        )}
      </div>

      {error && (
        <div className="upload-error">
          <span className="error-icon">❌</span> {error}
        </div>
      )}

      {result && (
        <div className="upload-result">
          <h4>✅ 上传成功</h4>
          <ul>
            <li><span className="label">文件名:</span> {result.filename}</li>
            <li><span className="label">类型:</span> {result.file_type}</li>
            <li><span className="label">文档 ID:</span> {result.document_id}</li>
            <li><span className="label">分片数:</span> {result.chunk_count}</li>
          </ul>
        </div>
      )}
    </div>
  );
}
