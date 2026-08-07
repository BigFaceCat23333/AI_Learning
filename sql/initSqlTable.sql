-- ai-learning 数据库初始化 SQL
-- 使用方式：psql -h localhost -p 5432 -U urpapa -d ai_learning -f sql/initSqlTable.sql
-- 注意：本脚本会删除并重建所有表，仅适用于从零开始的开发环境。

-- 启用 pgvector 扩展
CREATE EXTENSION IF NOT EXISTS vector;

-- 按外键依赖顺序清理旧表
DROP TABLE IF EXISTS conversation_messages;
DROP TABLE IF EXISTS conversations;
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

COMMENT ON TABLE users IS '用户表';
COMMENT ON COLUMN users.id IS '用户主键ID';
COMMENT ON COLUMN users.username IS '登录用户名';
COMMENT ON COLUMN users.password_hash IS '密码哈希值';
COMMENT ON COLUMN users.is_active IS '账号是否启用';
COMMENT ON COLUMN users.display_name IS '用户显示名称';
COMMENT ON COLUMN users.email IS '电子邮箱';
COMMENT ON COLUMN users.phone IS '手机号码';
COMMENT ON COLUMN users.bio IS '个人简介';
COMMENT ON COLUMN users.avatar_path IS '头像文件路径';
COMMENT ON COLUMN users.created_at IS '创建时间';
COMMENT ON COLUMN users.updated_at IS '更新时间';

-- 文档表
CREATE TABLE documents (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename VARCHAR(255) NOT NULL,
    file_type VARCHAR(20) NOT NULL,
    saved_path VARCHAR(500) NOT NULL,
    raw_text TEXT NOT NULL,
    deleted_at TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE documents IS '文档表';
COMMENT ON COLUMN documents.id IS '文档主键ID';
COMMENT ON COLUMN documents.user_id IS '所属用户ID';
COMMENT ON COLUMN documents.filename IS '原始文件名';
COMMENT ON COLUMN documents.file_type IS '文件类型';
COMMENT ON COLUMN documents.saved_path IS '文件保存路径';
COMMENT ON COLUMN documents.raw_text IS '文档原始文本内容';
COMMENT ON COLUMN documents.deleted_at IS '软删除时间';
COMMENT ON COLUMN documents.created_at IS '创建时间';

CREATE INDEX idx_documents_user_id ON documents(user_id);
CREATE INDEX idx_documents_user_deleted_at ON documents(user_id, deleted_at);

-- 文档分块表
CREATE TABLE document_chunks (
    id SERIAL PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding vector(1024) NOT NULL,
    chunk_metadata JSONB NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    char_count INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE document_chunks IS '文档分块表';
COMMENT ON COLUMN document_chunks.id IS '文档分块主键ID';
COMMENT ON COLUMN document_chunks.document_id IS '所属文档ID';
COMMENT ON COLUMN document_chunks.chunk_index IS '分块在文档中的序号';
COMMENT ON COLUMN document_chunks.chunk_text IS '分块文本内容';
COMMENT ON COLUMN document_chunks.embedding IS '分块文本向量';
COMMENT ON COLUMN document_chunks.chunk_metadata IS '分块元数据';
COMMENT ON COLUMN document_chunks.content_hash IS '分块内容哈希值';
COMMENT ON COLUMN document_chunks.char_count IS '分块字符数';
COMMENT ON COLUMN document_chunks.created_at IS '创建时间';

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

COMMENT ON TABLE captcha_challenges IS '验证码挑战表';
COMMENT ON COLUMN captcha_challenges.id IS '验证码挑战唯一标识';
COMMENT ON COLUMN captcha_challenges.answer_digest IS '验证码答案摘要';
COMMENT ON COLUMN captcha_challenges.expires_at IS '过期时间';
COMMENT ON COLUMN captcha_challenges.consumed_at IS '使用时间';
COMMENT ON COLUMN captcha_challenges.created_at IS '创建时间';

CREATE INDEX idx_captcha_challenges_expires_at ON captcha_challenges(expires_at);

-- 会话表
CREATE TABLE conversations (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(100) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    last_message_at TIMESTAMP NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMP NULL
);

COMMENT ON TABLE conversations IS '会话表';
COMMENT ON COLUMN conversations.id IS '会话主键ID';
COMMENT ON COLUMN conversations.user_id IS '所属用户ID';
COMMENT ON COLUMN conversations.title IS '会话标题';
COMMENT ON COLUMN conversations.created_at IS '创建时间';
COMMENT ON COLUMN conversations.last_message_at IS '最后一条消息时间';
COMMENT ON COLUMN conversations.deleted_at IS '软删除时间';

CREATE INDEX idx_conversations_user_list ON conversations(user_id, last_message_at DESC, id DESC);

-- 会话消息表
CREATE TABLE conversation_messages (
    id SERIAL PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role VARCHAR(16) NOT NULL,
    content TEXT NOT NULL,
    sources JSONB NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE conversation_messages IS '会话消息表';
COMMENT ON COLUMN conversation_messages.id IS '会话消息主键ID';
COMMENT ON COLUMN conversation_messages.conversation_id IS '所属会话ID';
COMMENT ON COLUMN conversation_messages.role IS '消息角色：user-用户，assistant-助手';
COMMENT ON COLUMN conversation_messages.content IS '消息内容';
COMMENT ON COLUMN conversation_messages.sources IS '消息引用来源';
COMMENT ON COLUMN conversation_messages.created_at IS '创建时间';

CREATE INDEX idx_conversation_messages_conv ON conversation_messages(conversation_id);
ALTER TABLE conversation_messages ADD CONSTRAINT chk_conversation_messages_role CHECK (role IN ('user', 'assistant'));

-- 预置 admin 用户（Argon2id 哈希，密码由部署时安全随机源生成）
INSERT INTO users (username, password_hash, is_active)
VALUES ('admin', '$argon2id$v=19$m=65536,t=3,p=4$TMUPZFt/Qeww3wY7QiUG/w$P0DLODndkR/Ifyx2QyCgQDD7yujIE39tdstcsbtU504', TRUE);
