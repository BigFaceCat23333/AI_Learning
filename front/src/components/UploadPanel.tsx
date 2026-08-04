import { type ChangeEvent, type DragEvent, useEffect, useState, useRef } from "react";
import type { DocumentListItem, DocumentUploadResponse } from "../types/api";
import { cancelDocumentUpload, deleteDocument, downloadDocument, uploadDocument } from "../api/client";

const ALLOWED_TYPES = [".txt", ".md"];
const MAX_SIZE = 5 * 1024 * 1024; // 5MB

interface UploadPanelProps {
  onUploadSuccess: () => void;
  documents: DocumentListItem[];
  listLoading: boolean;
  listError: string | null;
  onRetry: () => void;
  onDeleteSuccess: (documentId: number) => void;
}

/** 格式化 ISO 时间为本地可读字符串 */
function formatTime(iso: string): string {
  try {
    const date = new Date(iso);
    return date.toLocaleString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export default function UploadPanel({
  onUploadSuccess,
  documents,
  listLoading,
  listError,
  onRetry,
  onDeleteSuccess,
}: UploadPanelProps) {
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadPhase, setUploadPhase] = useState<"uploading" | "processing" | null>(null);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadFilename, setUploadFilename] = useState("");
  const [cancelling, setCancelling] = useState(false);
  const [cancelNotice, setCancelNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<DocumentUploadResponse | null>(null);
  const [downloadingIds, setDownloadingIds] = useState<Set<number>>(new Set());
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<DocumentListItem | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const deleteConfirmRef = useRef<HTMLButtonElement>(null);
  const uploadAbortRef = useRef<AbortController | null>(null);
  const uploadIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (!deleteTarget) return;

    deleteConfirmRef.current?.focus();
    function handleKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape" && !deleting) {
        setDeleteTarget(null);
        setDeleteError(null);
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [deleteTarget, deleting]);

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
    setCancelNotice(null);

    const validationError = validateFile(file);
    if (validationError) {
      setError(validationError);
      return;
    }

    setUploading(true);
    setUploadPhase("uploading");
    setUploadProgress(0);
    setUploadFilename(file.name);
    const uploadId = crypto.randomUUID();
    const controller = new AbortController();
    uploadIdRef.current = uploadId;
    uploadAbortRef.current = controller;
    try {
      const data = await uploadDocument(file, ({ phase, percent }) => {
        setUploadPhase(phase);
        setUploadProgress(percent);
      }, { uploadId, signal: controller.signal });
      setResult(data);
      onUploadSuccess();
    } catch (err) {
      if (controller.signal.aborted) {
        setCancelNotice("上传已取消，文档不会入库。");
      } else {
        setError(err instanceof Error ? err.message : "上传失败");
      }
    } finally {
      setUploading(false);
      setUploadPhase(null);
      setCancelling(false);
      if (uploadIdRef.current === uploadId) {
        uploadIdRef.current = null;
        uploadAbortRef.current = null;
      }
    }
  }

  async function handleCancelUpload() {
    const uploadId = uploadIdRef.current;
    const controller = uploadAbortRef.current;
    if (!uploadId || !controller || cancelling) return;

    setCancelling(true);
    setError(null);
    try {
      await cancelDocumentUpload(uploadId);
      controller.abort();
    } catch (err) {
      setError(err instanceof Error ? `取消失败：${err.message}` : "取消失败，请重试。");
      setCancelling(false);
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

  async function handleDownload(doc: DocumentListItem) {
    setDownloadError(null);
    setDownloadingIds((prev) => new Set(prev).add(doc.document_id));
    try {
      await downloadDocument(doc.document_id, doc.filename);
    } catch (err) {
      setDownloadError(
        err instanceof Error ? err.message : "下载失败",
      );
    } finally {
      setDownloadingIds((prev) => {
        const next = new Set(prev);
        next.delete(doc.document_id);
        return next;
      });
    }
  }

  function requestDelete(doc: DocumentListItem) {
    setDeleteTarget(doc);
    setDeleteError(null);
  }

  function closeDeleteDialog() {
    if (deleting) return;
    setDeleteTarget(null);
    setDeleteError(null);
  }

  async function confirmDelete() {
    if (!deleteTarget || deleting) return;

    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteDocument(deleteTarget.document_id);
      onDeleteSuccess(deleteTarget.document_id);
      setDeleteTarget(null);
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "删除失败，请重试。");
    } finally {
      setDeleting(false);
    }
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
          <p className="upload-hint">⏳ 正在处理 {uploadFilename}</p>
        ) : (
          <p className="upload-hint">
            拖拽文件到此处，或点击选择文件
            <br />
            <span className="upload-limit">支持 .txt / .md，最大 5MB</span>
          </p>
        )}
      </div>

      {uploading && uploadPhase && (
        <div className="upload-progress-panel" aria-live="polite">
          <div className="upload-progress-header">
            <span>
              {uploadPhase === "uploading"
                ? "正在上传文件"
                : "文件已上传，正在解析、分块并生成向量"}
            </span>
            <div className="upload-progress-actions">
              {uploadPhase === "uploading" && <strong>{uploadProgress}%</strong>}
              <button
                type="button"
                className="upload-cancel-button"
                onClick={handleCancelUpload}
                disabled={cancelling}
              >
                {cancelling ? "取消中..." : "取消上传"}
              </button>
            </div>
          </div>
          <div
            className={`upload-progress-track ${uploadPhase === "processing" ? "processing" : ""}`}
            role="progressbar"
            aria-label={uploadPhase === "uploading" ? "文件上传进度" : "文档处理进度"}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={uploadPhase === "uploading" ? uploadProgress : undefined}
          >
            <div
              className="upload-progress-bar"
              style={{ width: uploadPhase === "uploading" ? `${uploadProgress}%` : "35%" }}
            />
          </div>
          {uploadPhase === "processing" && (
            <p className="upload-progress-note">大文件生成向量可能需要数分钟，请勿关闭页面或重复上传。</p>
          )}
        </div>
      )}

      {cancelNotice && <div className="upload-cancel-notice">{cancelNotice}</div>}

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

      {/* 知识库文件列表 */}
      <h3 className="panel-title doc-list-title">📚 知识库文件</h3>

      <div className="doc-list">
        {listLoading && (
          <div className="doc-list-status">⏳ 加载中...</div>
        )}

        {listError && !listLoading && (
          <div className="doc-list-error">
            <span>❌ {listError}</span>
            <button
              type="button"
              className="doc-retry-button"
              onClick={onRetry}
            >
              重试
            </button>
          </div>
        )}

        {!listLoading && !listError && documents.length === 0 && (
          <div className="doc-list-empty">
            暂无文档，上传后在此展示。
          </div>
        )}

        {!listLoading && !listError && documents.length > 0 && (
          <ul className="doc-items">
            {documents.map((doc) => {
              const isDownloading = downloadingIds.has(doc.document_id);
              return (
                <li key={doc.document_id} className="doc-item">
                  <div className="doc-item-info">
                    <span className="doc-item-name" title={doc.filename}>
                      {doc.filename}
                    </span>
                    <span className="doc-item-meta">
                      <span className="doc-item-type">{doc.file_type.toUpperCase()}</span>
                      <span className="doc-item-chunks">{doc.chunk_count} 分片</span>
                      <span className="doc-item-time">
                        {formatTime(doc.created_at)}
                      </span>
                    </span>
                  </div>
                  <div className="doc-item-actions">
                    <button
                      type="button"
                      className="doc-delete-button"
                      onClick={() => requestDelete(doc)}
                      title={`删除 ${doc.filename}`}
                      aria-label={`删除文档 ${doc.filename}`}
                    >
                      🗑
                    </button>
                    <button
                      type="button"
                      className="doc-download-button"
                      disabled={isDownloading}
                      onClick={() => handleDownload(doc)}
                      title={isDownloading ? "下载中..." : "下载原文件"}
                    >
                      {isDownloading ? "⏳" : "⬇"}
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}

        {downloadError && (
          <div className="doc-download-error">❌ {downloadError}</div>
        )}
      </div>

      {deleteTarget && (
        <div
          className="modal-overlay"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closeDeleteDialog();
          }}
        >
          <div
            className="delete-confirm-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-confirm-title"
            aria-describedby="delete-confirm-description"
          >
            <h2 id="delete-confirm-title">确认删除文档？</h2>
            <p id="delete-confirm-description">
              删除后，文档“{deleteTarget.filename}”将不再显示，也不会继续参与 AI 问答。
            </p>
            {deleteError && <div className="delete-confirm-error">❌ {deleteError}</div>}
            <div className="delete-confirm-actions">
              <button
                type="button"
                className="profile-button-secondary"
                onClick={closeDeleteDialog}
                disabled={deleting}
              >
                取消
              </button>
              <button
                ref={deleteConfirmRef}
                type="button"
                className="delete-confirm-button"
                onClick={confirmDelete}
                disabled={deleting}
              >
                {deleting ? "删除中..." : "确认删除"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
