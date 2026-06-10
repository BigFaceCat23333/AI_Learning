import type { QuerySource } from "../types/api";

interface SourceListProps {
  sources: QuerySource[];
}

export default function SourceList({ sources }: SourceListProps) {
  if (sources.length === 0) return null;

  return (
    <div className="source-list">
      <h4 className="source-list-title">📎 引用来源</h4>
      {sources.map((source, index) => (
        <div key={index} className="source-item">
          <div className="source-meta">
            <span className="source-filename">{source.filename}</span>
            <span className="source-chunk">chunk #{source.chunk_id}</span>
            <span className="source-score">
              相关度: {(source.score * 100).toFixed(1)}%
            </span>
          </div>
          <p className="source-text">{source.chunk_text}</p>
        </div>
      ))}
    </div>
  );
}
