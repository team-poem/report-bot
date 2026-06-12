# 컨테이너 Codex 활성화 + PDF 의존성 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 맥미니 컨테이너에서 전체 파이프라인(업로드 → 변환 → Codex 분석 → HWPX)이 돌도록 이미지·compose·코드 노브를 갱신한다.

**Architecture:** Dockerfile에 Codex CLI(npm 전역)와 PDF 의존성(`npm ci`)을 넣고, ChatGPT OAuth 토큰 갱신을 위해 `~/.codex` 마운트를 rw로 바꾼다. codex 샌드박스 모드는 `REPORT_BOT_CODEX_SANDBOX` 환경변수(기본 `read-only`)로 빼서 Landlock 미지원 환경에서 재빌드 없이 전환 가능하게 한다. 스펙: `docs/superpowers/specs/2026-06-12-container-codex-deploy-design.md`

**Tech Stack:** Docker, docker-compose, npm(@openai/codex, kordoc, pdfjs-dist), pytest(yaml 설정 단정 테스트)

---

### Task 1: codex_runner 샌드박스 환경변수 노브

**Files:**
- Modify: `web/codex_runner.py` (cmd 조립부, `-s read-only` 고정값)
- Test: `tests/test_codex_runner.py`

- [ ] **Step 1: 실패하는 테스트 추가** — tests/test_codex_runner.py 에 append:

```python
def test_sandbox_mode_defaults_to_read_only(monkeypatch):
    from web.codex_runner import _sandbox_mode

    monkeypatch.delenv("REPORT_BOT_CODEX_SANDBOX", raising=False)
    assert _sandbox_mode() == "read-only"


def test_sandbox_mode_overridden_by_env(monkeypatch):
    from web.codex_runner import _sandbox_mode

    monkeypatch.setenv("REPORT_BOT_CODEX_SANDBOX", "danger-full-access")
    assert _sandbox_mode() == "danger-full-access"
```

- [ ] **Step 2: 실패 확인**

Run: `./venv/bin/python -m pytest tests/test_codex_runner.py -v`
Expected: 2 FAIL — `ImportError: cannot import name '_sandbox_mode'`

- [ ] **Step 3: 구현** — web/codex_runner.py 에 `import os` 추가(없으면), 함수 추가 및 cmd 조립부 수정:

```python
def _sandbox_mode() -> str:
    """codex -s 값. 컨테이너에서 Landlock 미지원이면 compose 에서
    REPORT_BOT_CODEX_SANDBOX=danger-full-access 로 전환(컨테이너가 격리 경계)."""
    return os.environ.get("REPORT_BOT_CODEX_SANDBOX", "read-only")
```

cmd 조립부의 `"-s", "read-only",` 를 `"-s", _sandbox_mode(),` 로 변경.

- [ ] **Step 4: 통과 확인**

Run: `./venv/bin/python -m pytest tests/ -v`
Expected: 전체 PASS (98 = 96 + 2)

- [ ] **Step 5: Commit**

```bash
git add web/codex_runner.py tests/test_codex_runner.py
git commit -m "feat: make codex sandbox mode configurable via env"
```

---

### Task 2: Dockerfile + compose 갱신 (+ 설정 단정 테스트)

**Files:**
- Modify: `Dockerfile`, `docker-compose.yml`
- Test: `tests/test_deploy_config.py`

- [ ] **Step 1: 실패하는 테스트 추가** — tests/test_deploy_config.py 에 append:

```python
def test_dockerfile_installs_codex_and_npm_deps():
    txt = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert "npm install -g @openai/codex" in txt
    assert "COPY package.json package-lock.json" in txt
    assert "npm ci" in txt
    # 레이어 캐싱: npm ci 가 소스 COPY 보다 앞서야 한다
    assert txt.index("npm ci") < txt.index("COPY scripts")


def test_reportbot_compose_codex_mount_is_writable_and_sandbox_env():
    c = _load_yaml("docker-compose.yml")
    svc = c["services"]["report-bot"]
    codex_mounts = [v for v in svc["volumes"] if "/.codex" in v]
    assert codex_mounts, ".codex 마운트가 없습니다"
    # OAuth 토큰 갱신이 써야 하므로 ro 금지
    assert all(not v.endswith(":ro") for v in codex_mounts)
    env = "\n".join(svc["environment"])
    assert "REPORT_BOT_CODEX_SANDBOX=${REPORT_BOT_CODEX_SANDBOX:-read-only}" in env
```

- [ ] **Step 2: 실패 확인**

Run: `./venv/bin/python -m pytest tests/test_deploy_config.py -v`
Expected: 신규 2건 FAIL

- [ ] **Step 3: Dockerfile 교체** — 전체를 다음으로:

```dockerfile
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
```

- [ ] **Step 4: docker-compose.yml 수정** — report-bot 서비스의 volumes/environment 를:

```yaml
    volumes:
      - ./jobs:/app/jobs                       # 산출물 영속
      - ${HOME}/.codex:/root/.codex            # codex 인증 (OAuth 토큰 갱신 때문에 rw)
    environment:
      - CODEX_HOME=/root/.codex
      - REPORT_BOT_MAX_CONCURRENCY=3
      - REPORT_BOT_CODEX_SANDBOX=${REPORT_BOT_CODEX_SANDBOX:-read-only}
```
(labels/networks 등 나머지는 그대로)

- [ ] **Step 5: 통과 확인 + 로컬 빌드 스모크**

Run: `./venv/bin/python -m pytest tests/ -v` → 전체 PASS (100)

빌드 스모크 (수 분 소요):
```bash
docker compose build 2>&1 | tail -3
docker compose run --rm --no-deps --entrypoint sh report-bot -c "codex --version && npx kordoc --help >/dev/null 2>&1 && echo kordoc-OK && node -e \"require('pdfjs-dist/package.json')\" 2>/dev/null || ls node_modules/pdfjs-dist/package.json"
```
Expected: codex 버전 문자열 + `kordoc-OK` + pdfjs-dist 존재 확인. (docker 데몬이 없으면 이 스모크는 맥미니에서 수행하도록 보고에 명시)

- [ ] **Step 6: Commit**

```bash
git add Dockerfile docker-compose.yml tests/test_deploy_config.py
git commit -m "feat: install codex CLI and PDF deps in image, rw codex auth mount"
```

---

### Task 3: deploy/README 갱신

**Files:**
- Modify: `deploy/README.md`
- Test: `tests/test_deploy_config.py`

- [ ] **Step 1: 실패하는 테스트 추가** — tests/test_deploy_config.py 에 append:

```python
def test_deploy_readme_covers_codex_setup_and_sandbox_fallback():
    txt = (REPO / "deploy/README.md").read_text(encoding="utf-8")
    assert "codex login" in txt
    assert "@openai/codex" in txt
    assert "REPORT_BOT_CODEX_SANDBOX" in txt
    assert "danger-full-access" in txt
```

Run: `./venv/bin/python -m pytest tests/test_deploy_config.py -v` → 신규 1건 FAIL

- [ ] **Step 2: README 내용 추가** — deploy/README.md 의 "1. 처음 한 번만" 섹션에 항목 추가:

```markdown
8. **Codex 인증(1회)** — 분석 단계가 ChatGPT 계정 인증을 사용한다. 맥미니 호스트에서:
   ```bash
   npm install -g @openai/codex
   codex login          # 브라우저 로그인. ~/.codex 에 인증이 저장된다
   ```
   컨테이너는 이 폴더를 읽기·쓰기로 마운트한다(토큰 자동 갱신).
```

"2. 띄우는 순서" 다음에 섹션 추가:

```markdown
## 2.5 재배포 (코드 갱신 시)

```bash
git pull
docker compose build
docker compose up -d
```

배포 후 검증:
```bash
# 컨테이너 안에서 codex 가 인증·실행되는지
docker compose exec report-bot codex exec "1+1을 계산해" -s read-only --skip-git-repo-check
```

**분석 단계가 "샌드박스" 관련 에러로 실패하면** (컨테이너 커널이 Landlock 미지원):
`.env` 에 `REPORT_BOT_CODEX_SANDBOX=danger-full-access` 를 추가하고
`docker compose up -d` 로 재기동한다. 컨테이너 자체가 격리 경계라 허용 가능한 설정이다.
```

- [ ] **Step 3: 통과 확인**

Run: `./venv/bin/python -m pytest tests/ -v` → 전체 PASS (101)

- [ ] **Step 4: Commit**

```bash
git add deploy/README.md tests/test_deploy_config.py
git commit -m "docs: codex auth setup, redeploy procedure, sandbox fallback"
```

---

## 맥미니 수동 게이트 (계획 범위 밖, 배포 시 수행)

1. 맥미니에서 `codex login` (사전 준비 8번) 후 `git pull && docker compose build && up -d`
2. `https://report-bot.<공인IP>.sslip.io` 에서 실제 파일 업로드 → done 도달 + HWPX 다운로드 확인
3. 샌드박스 에러 시 README 의 fallback 적용

## Self-Review 결과

- **스펙 커버리지:** §3.1 codex npm 설치(Task 2), §3.2 rw 마운트(Task 2), §3.3 샌드박스 노브(Task 1), §4 README(Task 3), §5 검증(각 Task 테스트 + Task 2 빌드 스모크 + 수동 게이트 명시). 누락 없음.
- **타입 일관성:** `_sandbox_mode()` 이름이 Task 1 테스트·구현에서 일치. compose env 보간 문자열이 Task 2 테스트·yaml 에서 동일.
- **주의:** Task 2 빌드 스모크는 로컬 docker 가용 시에만; 불가하면 맥미니 게이트로 이월.
