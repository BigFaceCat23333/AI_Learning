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
