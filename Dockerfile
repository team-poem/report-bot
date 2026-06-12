# Python + Node(npx kordoc 용) + Codex CLI. 인증(~/.codex)은 compose 에서 rw 마운트.
FROM python:3.12-slim

# Node.js (kordoc/codex 실행용)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Codex CLI (npm 이 컨테이너 플랫폼에 맞는 바이너리를 받는다)
RUN npm install -g @openai/codex

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# kordoc + pdfjs-dist@4 고정 설치 (PDF 변환에 필요; npx 가 로컬 판을 우선 사용)
COPY package.json package-lock.json ./
RUN npm ci

COPY scripts ./scripts
COPY web ./web

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8000"]
