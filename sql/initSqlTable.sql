-- ai-learning 数据库初始化 SQL
-- 使用方式：psql -h localhost -p 5432 -U urpapa -d ai_learning -f sql/initSqlTable.sql
-- 注意：本脚本会删除并重建所有表，仅适用于从零开始的开发环境。

-- 启用 pgvector 扩展
CREATE EXTENSION IF NOT EXISTS vector;

-- 按外键依赖顺序清理旧表
DROP TABLE IF EXISTS document_chunks;
DROP TABLE IF EXISTS documents;
DROP TABLE IF EXISTS captcha_challenges;
DROP TABLE IF EXISTS users;

-- 用户表
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(64) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    display_name VARCHAR(64),
    email VARCHAR(254),
    phone VARCHAR(32),
    bio VARCHAR(500),
    avatar_path VARCHAR(500),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- 文档表
CREATE TABLE documents (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename VARCHAR(255) NOT NULL,
    file_type VARCHAR(20) NOT NULL,
    saved_path VARCHAR(500) NOT NULL,
    raw_text TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_documents_user_id ON documents(user_id);

-- 文档分块表
CREATE TABLE document_chunks (
    id SERIAL PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding vector(1536) NOT NULL,
    chunk_metadata JSONB NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    char_count INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- 常规索引
CREATE INDEX idx_document_chunks_document_id ON document_chunks(document_id);
CREATE INDEX idx_document_chunks_content_hash ON document_chunks(content_hash);

-- 向量索引（HNSW，用于 cosine distance 高效召回）
CREATE INDEX idx_document_chunks_embedding ON document_chunks USING hnsw (embedding vector_cosine_ops);

-- 验证码挑战表
CREATE TABLE captcha_challenges (
    id VARCHAR(36) PRIMARY KEY,
    answer_digest VARCHAR(64) NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    consumed_at TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_captcha_challenges_expires_at ON captcha_challenges(expires_at);

-- 预置 admin 用户（Argon2id 哈希，密码由部署时安全随机源生成）
INSERT INTO users (username, password_hash, is_active)
VALUES ('admin', '$argon2id$v=19$m=65536,t=3,p=4$TMUPZFt/Qeww3wY7QiUG/w$P0DLODndkR/Ifyx2QyCgQDD7yujIE39tdstcsbtU504', TRUE);
