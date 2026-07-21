# Version: 1.1-Alpine-Optimized
# --- 第一阶段：构建阶段 ---
FROM python:3.11-alpine as builder

WORKDIR /app

# 安装构建依赖 (编译 C 扩展需要的工具)
RUN apk add --no-cache gcc musl-dev linux-headers

# 安装 Python 依赖到特定文件夹
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# --- 第二阶段：运行阶段 ---
FROM python:3.11-alpine

WORKDIR /app

# 从构建阶段直接复制安装好的依赖包
COPY --from=builder /install /usr/local

# 复制源代码
COPY . .

# 设置环境变量，确保 Python 不产生 .pyc 文件且实时输出日志
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
