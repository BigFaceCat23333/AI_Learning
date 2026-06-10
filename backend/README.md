# ai-learning

A small FastAPI project skeleton for AI learning workflows, including basic RAG and agent modules.

## Run

进入项目目录：

```bash
cd /Users/urpapa/code/proj/pyProj/ai-learning
```

初始化依赖和数据库：

```bash
uv sync --extra dev
createdb -h localhost -p 5432 -U urpapa ai_learning
```

启动项目：

```bash
uv run --extra dev python -m uvicorn ai_learning.main:app --reload
```

启动后服务地址：

```bash
http://localhost:8000
```

健康检查：

```bash
curl http://localhost:8000/api/health
```

Default local database:

```bash
postgresql+psycopg://urpapa:postgres@localhost:5432/ai_learning
```

Upload a document:

```bash
curl -F "file=@README.md" http://localhost:8000/api/documents/upload
```

Ask a question:

```bash
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{"question":"What is this project?", "top_k":3}'
```

## Test

```bash
uv run --extra dev python -m pytest
```

## 验证

### 1. 运行测试

测试使用独立的 PostgreSQL 测试库，避免清理业务库数据：

```bash
createdb -h localhost -p 5432 -U urpapa ai_learning_test
```

```bash
uv run --extra dev python -m pytest
```

### 2. 启动服务

```bash
uv run --extra dev python -m uvicorn ai_learning.main:app --reload
```

### 3. 上传文档

```bash
curl -F "file=@README.md" http://localhost:8000/api/documents/upload
```

### 4. 查询

**有 API key 时（真实 LLM 回答）：**

```bash
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{"question":"What is this project?", "top_k":3}'
```

**无 API key 时（显式错误）：**

如果未配置 `AI_LEARNING_LLM_API_KEY`，查询接口会返回 `503` 错误，提示需要配置 API key，不再返回 mock 回答。

### application.yml

项目默认从根目录的 `application.yml` 读取应用配置，环境变量仍然可以覆盖同名配置。
启动时会根据 SQLAlchemy 模型自动创建业务表，不需要手动维护建表 SQL。

```yaml
ai_learning:
  app_name: ai-learning
  database_url: postgresql+psycopg://urpapa:postgres@localhost:5432/ai_learning
  llm_base_url: https://api.openai.com/v1
  llm_api_key:
  llm_model: gpt-4.1-mini
  upload_dir: uploads
  upload_max_bytes: 5242880
```

### 环境变量覆盖

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `AI_LEARNING_DATABASE_URL` | 数据库连接串 | `postgresql+psycopg://urpapa:postgres@localhost:5432/ai_learning` |
| `AI_LEARNING_LLM_BASE_URL` | LLM API 地址 | `https://api.openai.com/v1` |
| `AI_LEARNING_LLM_API_KEY` | LLM API 密钥 | 无（未配置时查询返回 503） |
| `AI_LEARNING_LLM_MODEL` | LLM 模型名 | `gpt-4.1-mini` |
| `AI_LEARNING_UPLOAD_DIR` | 上传文件目录 | `uploads` |
