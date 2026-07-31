# ai-learning

本项目包含：

- `backend/`：FastAPI 后端服务，提供基于 pgvector 的 RAG 文档上传和文档解读接口。
- `front/`：Vite + React + TypeScript 前端页面，提供文档上传和对话式解读界面。

目前仅支持 `.txt` 和 `.md` 格式的 UTF-8 文档。

## 后端启动

进入后端目录：

```bash
cd backend
```

安装依赖：

```bash
uv sync --extra dev
```

准备本地 PostgreSQL 数据库并初始化 pgvector 表结构：

```bash
# 创建数据库（如果尚未创建）
createdb -h localhost -p 5432 -U urpapa ai_learning

# 使用 SQL 文件初始化表结构（开发环境会重建表）
psql -h localhost -p 5432 -U urpapa -d ai_learning -f ../sql/initSqlTable.sql
```

启动后端服务：

```bash
uv run --extra dev python -m uvicorn ai_learning.main:app --reload
```

后端默认地址：

```text
http://localhost:8000
```

健康检查：

```bash
curl http://localhost:8000/api/health
```

如需真实调用文档解读接口，需要配置 LLM Key 和 Embedding Key：

```bash
export AI_LEARNING_LLM_API_KEY="你的 LLM API Key"
export AI_LEARNING_EMBEDDING_API_KEY="你的阿里云百炼 API Key"
export AI_LEARNING_EMBEDDING_BASE_URL="https://your-workspace-id.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
export AI_LEARNING_EMBEDDING_MODEL="text-embedding-v4"
```

## 环境变量说明

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `AI_LEARNING_DATABASE_URL` | PostgreSQL 连接串 | `postgresql+psycopg://urpapa:postgres@localhost:5432/ai_learning` |
| `AI_LEARNING_LLM_BASE_URL` | LLM API 地址 | `https://api.openai.com/v1` |
| `AI_LEARNING_LLM_API_KEY` | LLM API Key | - |
| `AI_LEARNING_LLM_MODEL` | LLM 模型名 | `gpt-4.1-mini` |
| `AI_LEARNING_EMBEDDING_PROVIDER` | Embedding 提供者 (`openai_compatible` 或 `mock`) | `openai_compatible` |
| `AI_LEARNING_EMBEDDING_BASE_URL` | 阿里云百炼 OpenAI 兼容 Embedding API 地址（需包含 `/compatible-mode/v1`） | - |
| `AI_LEARNING_EMBEDDING_API_KEY` | 阿里云百炼 API Key | - |
| `AI_LEARNING_EMBEDDING_MODEL` | Embedding 模型名 | `text-embedding-v4` |
| `AI_LEARNING_EMBEDDING_DIMENSIONS` | 向量维度 | `1536` |
| `AI_LEARNING_RETRIEVAL_MIN_SCORE` | 检索最低相关度阈值 | `0.35` |
| `AI_LEARNING_RETRIEVAL_TOP_K` | 返回结果数 | `5` |
| `AI_LEARNING_RETRIEVAL_CANDIDATE_K` | 候选召回数 | `20` |
| `AI_LEARNING_UPLOAD_MAX_BYTES` | 上传文件大小限制 | `5242880` (5MB) |
| `AI_LEARNING_AUTH_SECRET` | 认证 JWT 签名密钥（必填，至少 32 字符） | - |
| `AI_LEARNING_AUTH_TOKEN_TTL_SECONDS` | JWT 有效期（秒） | `28800` (8 小时) |
| `AI_LEARNING_AUTH_COOKIE_SECURE` | Cookie Secure 属性（公网 HTTPS 必须为 true） | `false` |

## 认证与用户隔离

### 初始登录凭证

部署后使用以下凭证登录：
- 用户名：`admin`
- 密码：由部署者在生成 `AI_LEARNING_AUTH_SECRET` 时同步生成，详见下方部署步骤。

### 现有部署增量升级（保留数据）

> 适用场景：已有运行中的部署，需要增加验证码登录和用户资料功能，**不丢失**现有文档和上传文件。

1. **备份数据库**：
   ```bash
   pg_dump -h localhost -p 5432 -U urpapa -d ai_learning > backup_$(date +%Y%m%d).sql
   ```

2. **执行增量迁移**（可重复安全执行）：
   ```bash
   psql -h localhost -p 5432 -U urpapa -d ai_learning -f sql/003_add_captcha_profile.sql
   ```

3. **头像持久化**：Docker 部署时，头像目录从 `AI_LEARNING_UPLOAD_DIR` 派生，位于 `${UPLOAD_DIR}/avatars/`。确保该目录在持久化卷中（默认 `backend_uploads` 卷已覆盖）。

4. **重启容器**：
   ```bash
   docker compose up --build -d
   ```

### 首次部署（全新数据库）

1. 准备 PostgreSQL 数据库。
2. 执行 `sql/initSqlTable.sql` 初始化所有表（含预置 admin 用户哈希和验证码表）。
3. 复制 `.env.example` → `.env`，填写 `AI_LEARNING_AUTH_SECRET`（至少 32 字符）及其他配置。
4. 启动后端和前端容器。
5. 使用交付密码登录，建议首次登录后立即修改密码。

### 现有 Docker 部署重新初始化（含旧知识库）

> ⚠️ 升级会清空现有知识库文档和分片数据。执行前确保所有业务访问已停止。

1. **停止业务访问**：
   ```bash
   docker compose down
   ```

2. **配置认证密钥**：
   在项目根目录 `.env` 中添加以下认证配置（密钥需至少 32 字符）：
   ```bash
   # 生成强随机密钥
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
   ```env
   AI_LEARNING_AUTH_SECRET=<生成的密钥>
   AI_LEARNING_AUTH_TOKEN_TTL_SECONDS=28800
   # 本地 HTTP：
   AI_LEARNING_AUTH_COOKIE_SECURE=false
   # 公网 HTTPS：
   # AI_LEARNING_AUTH_COOKIE_SECURE=true
   ```

3. **重新初始化数据库**（会删除并重建所有业务表）：
   ```bash
   psql -h localhost -p 5432 -U urpapa -d ai_learning -f sql/initSqlTable.sql
   ```

4. **清理旧上传文件**：
   ```bash
   docker volume rm ai-learning_backend_uploads
   ```

5. **重建并启动容器**：
   ```bash
   docker compose up --build -d
   ```

6. **生成新的 admin 密码**并替换 SQL 中的哈希（如使用全新部署则跳过）：
   ```bash
   cd backend
   uv run python -c "
   from ai_learning.auth import hash_password, generate_random_password
   pwd = generate_random_password()
   print(f'新 admin 密码: {pwd}')
   print(f'新 admin 哈希: {hash_password(pwd)}')
   # 将哈希更新到 sql/initSqlTable.sql 的 INSERT 语句中
   # 然后在数据库中执行 UPDATE users SET password_hash = '<新哈希>' WHERE username = 'admin';
   "
   ```

### 本地与公网 Cookie 配置

| 部署场景 | `AI_LEARNING_AUTH_COOKIE_SECURE` | 说明 |
|----------|----------------------------------|------|
| 本地 HTTP 开发 | `false` | Cookie 通过 HTTP 传输 |
| Docker 本地测试 | `false` | `localhost:3000` 访问 |
| 公网 HTTPS 部署 | `true` | Cookie 仅通过 HTTPS 传输 |

## 前端启动

进入前端目录：

```bash
cd front
```

使用项目指定 Node 版本：

```bash
nvm use
```

如果没有自动识别，可使用：

```bash
nvm use 21.7.3
```

安装依赖：

```bash
npm install
```

启动前端开发服务：

```bash
npm run dev
```

前端默认地址：

```text
http://localhost:5173
```

前端默认访问后端：

```text
http://localhost:8000/api
```

如需覆盖后端 API 地址：

```bash
VITE_API_BASE_URL=http://localhost:8000/api npm run dev
```

## 验证命令

前端构建：

```bash
cd front
nvm use
npm run build
```

后端测试：

```bash
cd backend
uv run --extra dev python -m pytest
```

## Docker 启动

当前 Docker 方案只运行前端和后端，PostgreSQL 继续使用本机已安装的数据库。

容器访问宿主机数据库时不能使用 `localhost`，需要使用 `host.docker.internal`：

```bash
cp .env.example .env
```

按需编辑 `.env`，至少确认数据库连接串、LLM API Key 和 Embedding API Key：

```env
AI_LEARNING_DATABASE_URL=postgresql+psycopg://urpapa:postgres@host.docker.internal:5432/ai_learning
AI_LEARNING_LLM_API_KEY=你的 API Key
AI_LEARNING_EMBEDDING_API_KEY=你的阿里云百炼 API Key
AI_LEARNING_EMBEDDING_BASE_URL=https://your-workspace-id.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
AI_LEARNING_EMBEDDING_MODEL=text-embedding-v4
```

其中 `your-workspace-id` 需要替换为阿里云百炼业务空间 ID；如果使用新加坡地域，按百炼控制台提供的地域域名替换整个 `AI_LEARNING_EMBEDDING_BASE_URL`。

启动 Docker 服务：

```bash
docker compose up --build
```

启动后访问：

```text
前端：http://localhost:3000
后端：http://localhost:8000
健康检查：http://localhost:8000/api/health
```

如果只想后台运行：

```bash
docker compose up --build -d
```

停止服务：

```bash
docker compose down
```

## 查看后端日志

Docker 运行时可查看后端运行日志：

```bash
# 实时跟踪后端日志
docker compose logs -f backend

# 查看最近 200 行
docker compose logs --tail=200 backend
```

如需查看模型调用链路（embedding / LLM）和 RAG 检索命中详情，可在 `.env` 中启用调试日志：

```env
AI_LEARNING_RAG_DEBUG_LOGS=true
AI_LEARNING_SQL_ECHO=true
```

修改后重建并重启后端：

```bash
docker compose up --build -d --force-recreate backend
```

> **注意**：`AI_LEARNING_SQL_ECHO=true` 会输出 SQLAlchemy 生成的 SQL 语句和参数，可能包含部分业务数据，不建议生产环境长期开启。

## 使用流程

1. 启动 PostgreSQL，并确认 `ai_learning` 数据库存在。
2. 使用 `sql/initSqlTable.sql` 初始化表结构（含 pgvector 扩展和向量索引）。
3. 启动后端服务：`http://localhost:8000`。
4. 启动前端服务：`http://localhost:5173`。
5. 在前端上传 UTF-8 编码的 `.txt` 或 `.md` 文件。
6. 上传成功后，在右侧对话框输入问题进行文档解读。
