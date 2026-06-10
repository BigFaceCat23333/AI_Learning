# ai-learning

本项目包含：

- `backend/`：FastAPI 后端服务，提供文档上传和文档解读接口。
- `front/`：Vite + React + TypeScript 前端页面，提供文档上传和对话式解读界面。

## 后端启动

进入后端目录：

```bash
cd backend
```

安装依赖：

```bash
uv sync --extra dev
```

准备本地 PostgreSQL 数据库：

```bash
createdb -h localhost -p 5432 -U urpapa ai_learning
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

如需真实调用文档解读接口，需要配置后端 LLM Key：

```bash
export AI_LEARNING_LLM_API_KEY="你的 API Key"
```

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

## 使用流程

1. 启动 PostgreSQL，并确认 `ai_learning` 数据库存在。
2. 启动后端服务：`http://localhost:8000`。
3. 启动前端服务：`http://localhost:5173`。
4. 在前端上传 UTF-8 编码的 `.txt` 或 `.md` 文件。
5. 上传成功后，在右侧对话框输入问题进行文档解读。
