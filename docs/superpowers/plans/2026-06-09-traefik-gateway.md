# Traefik 게이트웨이 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 맥미니 한 대에서 공인 IP + sslip.io로 report-bot/rules-bot/lms-bot 3개 서비스를 호스트네임 라우팅하는 Traefik 게이트웨이 구성(자동 Let's Encrypt HTTPS)을 설정 파일·가이드로 만든다.

**Architecture:** Traefik v3를 독립 Compose 스택으로 띄우고, 공용 외부 도커 네트워크 `proxy`에 모든 서비스가 합류한다. Traefik은 docker provider로 각 컨테이너의 라벨(`Host(...)` 규칙 + 내부 포트)을 읽어 라우팅하고, 80/443만 호스트에 공개한다. 공인 IP는 각 서비스 Compose의 `.env`(`PUBLIC_IP`)에서 라벨로 치환된다.

**Tech Stack:** Traefik v3, Docker Compose, sslip.io, Let's Encrypt(TLS-ALPN-01). 설정 검증 테스트는 pytest + PyYAML(`./venv/bin/python -m pytest`).

설계 spec: `docs/superpowers/specs/2026-06-09-traefik-gateway-design.md`

---

## 파일 구조

생성:
- `deploy/traefik/traefik.yml` — Traefik 정적 설정(엔트리포인트·ACME·docker provider).
- `deploy/traefik/docker-compose.yml` — Traefik 본체 서비스.
- `.env.example`(repo 루트) — `PUBLIC_IP` 예시(report-bot compose가 참조).
- `deploy/README.md` — 맥미니 셋업 가이드 + 새 서비스 추가법 + rules-bot/lms-bot 라벨 템플릿.
- `tests/test_deploy_config.py` — 설정 파일 구조 검증.

수정:
- `docker-compose.yml`(repo 루트, report-bot) — Traefik 라벨 + 외부 `proxy` 네트워크 합류, 호스트 포트 공개(`8000:8000`) 제거.
- `.gitignore` — `.env` 와 `deploy/traefik/acme/`(인증서·개인키) 무시.

> 설계 대비 보정: Traefik 본체 Compose는 `PUBLIC_IP`가 필요 없다(라벨은 각 **서비스** Compose가 자기 `.env`로 치환). 따라서 `.env.example`은 repo 루트(report-bot용)에 둔다. ACME 이메일은 `traefik.yml`에 직접 적는다(정적 파일은 env 치환 안 됨).

검증은 전부 오프라인(YAML 파싱 + 문자열 검사). 실제 인증서 발급·접속은 맥미니에서 수동(공인 IP·포트포워딩 필요).

---

## Task 1: Traefik 정적 설정 `traefik.yml`

**Files:**
- Create: `deploy/traefik/traefik.yml`
- Test: `tests/test_deploy_config.py`

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_deploy_config.py` (새 파일):

```python
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def _load_yaml(rel: str):
    return yaml.safe_load((REPO / rel).read_text(encoding="utf-8"))


def test_traefik_static_config():
    cfg = _load_yaml("deploy/traefik/traefik.yml")
    eps = cfg["entryPoints"]
    assert eps["web"]["address"] == ":80"
    redir = eps["web"]["http"]["redirections"]["entryPoint"]
    assert redir["to"] == "websecure"
    assert redir["scheme"] == "https"
    assert eps["websecure"]["address"] == ":443"
    acme = cfg["certificatesResolvers"]["le"]["acme"]
    assert "email" in acme
    assert acme["storage"] == "/acme/acme.json"
    assert "tlsChallenge" in acme
    docker = cfg["providers"]["docker"]
    assert docker["exposedByDefault"] is False
    assert docker["network"] == "proxy"
```

- [ ] **Step 2: 실패 확인**

Run: `./venv/bin/python -m pytest tests/test_deploy_config.py -q`
Expected: FAIL — `FileNotFoundError` (`deploy/traefik/traefik.yml` 없음)

- [ ] **Step 3: 구현**

`deploy/traefik/traefik.yml`:

```yaml
entryPoints:
  web:
    address: ":80"
    http:
      redirections:
        entryPoint:
          to: websecure
          scheme: https
  websecure:
    address: ":443"

certificatesResolvers:
  le:
    acme:
      email: "you@example.com"   # ← 본인 이메일로 직접 수정 (정적 파일은 env 치환 안 됨)
      storage: /acme/acme.json
      tlsChallenge: {}

providers:
  docker:
    exposedByDefault: false
    network: proxy

api:
  dashboard: false
```

- [ ] **Step 4: 통과 확인**

Run: `./venv/bin/python -m pytest tests/test_deploy_config.py -q`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add deploy/traefik/traefik.yml tests/test_deploy_config.py
git commit -m "feat: add Traefik static config (entrypoints, ACME, docker provider)"
```

---

## Task 2: Traefik 본체 Compose

**Files:**
- Create: `deploy/traefik/docker-compose.yml`
- Test: `tests/test_deploy_config.py`

- [ ] **Step 1: 실패 테스트 추가**

`tests/test_deploy_config.py` 끝에 추가:

```python
def test_traefik_compose():
    c = _load_yaml("deploy/traefik/docker-compose.yml")
    svc = c["services"]["traefik"]
    assert svc["image"].startswith("traefik:v3")
    assert "80:80" in svc["ports"]
    assert "443:443" in svc["ports"]
    vols = svc["volumes"]
    assert any("/var/run/docker.sock" in v and v.endswith(":ro") for v in vols)
    assert any("traefik.yml" in v and v.endswith(":ro") for v in vols)
    assert any("/acme" in v for v in vols)
    assert svc["networks"] == ["proxy"]
    assert c["networks"]["proxy"]["external"] is True
```

- [ ] **Step 2: 실패 확인**

Run: `./venv/bin/python -m pytest tests/test_deploy_config.py::test_traefik_compose -q`
Expected: FAIL — `FileNotFoundError` (`deploy/traefik/docker-compose.yml` 없음)

- [ ] **Step 3: 구현**

`deploy/traefik/docker-compose.yml`:

```yaml
services:
  traefik:
    image: traefik:v3.1
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    networks:
      - proxy
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./traefik.yml:/etc/traefik/traefik.yml:ro
      - ./acme:/acme

networks:
  proxy:
    external: true
```

- [ ] **Step 4: 통과 확인**

Run: `./venv/bin/python -m pytest tests/test_deploy_config.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add deploy/traefik/docker-compose.yml tests/test_deploy_config.py
git commit -m "feat: add Traefik compose stack (80/443, docker.sock, acme volume)"
```

---

## Task 3: report-bot Compose 연결 + `.env.example` + `.gitignore`

**Files:**
- Modify: `docker-compose.yml` (repo 루트)
- Create: `.env.example` (repo 루트)
- Modify: `.gitignore`
- Test: `tests/test_deploy_config.py`

- [ ] **Step 1: 실패 테스트 추가**

`tests/test_deploy_config.py` 끝에 추가:

```python
def test_reportbot_compose_has_traefik_labels_and_no_host_port():
    c = _load_yaml("docker-compose.yml")
    svc = c["services"]["report-bot"]
    joined = "\n".join(svc["labels"])
    assert "traefik.enable=true" in joined
    assert "Host(`report-bot.${PUBLIC_IP}.sslip.io`)" in joined
    assert "entrypoints=websecure" in joined
    assert "tls.certresolver=le" in joined
    assert "loadbalancer.server.port=8000" in joined
    assert svc["networks"] == ["proxy"]
    assert c["networks"]["proxy"]["external"] is True
    # 호스트 포트 공개 제거: 8000:8000 매핑이 없어야 한다(Traefik 경유)
    assert "8000:8000" not in svc.get("ports", [])


def test_root_env_example_and_gitignore():
    env = (REPO / ".env.example").read_text(encoding="utf-8")
    assert "PUBLIC_IP=" in env
    gi = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gi
    assert "acme" in gi
```

- [ ] **Step 2: 실패 확인**

Run: `./venv/bin/python -m pytest tests/test_deploy_config.py -q`
Expected: FAIL — report-bot에 labels/networks 없음 + `.env.example` 없음

- [ ] **Step 3: 구현 — `docker-compose.yml`(루트) 전체를 아래로 교체**

```yaml
services:
  report-bot:
    build: .
    volumes:
      - ./jobs:/app/jobs                       # 산출물 영속
      - ${HOME}/.codex:/root/.codex:ro         # codex 인증(후속: 이미지에 codex 설치 시)
    environment:
      - CODEX_HOME=/root/.codex
      - REPORT_BOT_MAX_CONCURRENCY=3
    restart: unless-stopped
    labels:
      - traefik.enable=true
      - traefik.http.routers.reportbot.rule=Host(`report-bot.${PUBLIC_IP}.sslip.io`)
      - traefik.http.routers.reportbot.entrypoints=websecure
      - traefik.http.routers.reportbot.tls.certresolver=le
      - traefik.http.services.reportbot.loadbalancer.server.port=8000
    networks:
      - proxy

networks:
  proxy:
    external: true
```

- [ ] **Step 4: 구현 — `.env.example`(루트) 생성**

```dotenv
# 공인 IP(외부 IP). 맥미니에서 `curl ifconfig.me`로 확인.
# 실제 값은 이 파일을 복사한 .env 에 적는다(.env 는 git 에 안 올라감).
PUBLIC_IP=1.2.3.4
```

- [ ] **Step 5: 구현 — `.gitignore`에 두 줄 추가**

`.gitignore` 파일 끝에 아래를 추가(기존 내용은 그대로 두고 추가):

```gitignore

# 배포 비밀/상태 (커밋 금지)
.env
deploy/traefik/acme/
```

- [ ] **Step 6: 통과 확인 + 전체 스위트 회귀**

Run: `./venv/bin/python -m pytest tests/test_deploy_config.py -q`
Expected: PASS (4 passed)

Run: `./venv/bin/python -m pytest -q`
Expected: 기존 23 + deploy 4 = 27 passed.

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml .env.example .gitignore tests/test_deploy_config.py
git commit -m "feat: wire report-bot behind Traefik (labels, proxy network, ignore secrets)"
```

---

## Task 4: 배포 가이드 `deploy/README.md`

**Files:**
- Create: `deploy/README.md`
- Test: `tests/test_deploy_config.py`

- [ ] **Step 1: 실패 테스트 추가**

`tests/test_deploy_config.py` 끝에 추가:

```python
def test_deploy_readme_has_guide_and_templates():
    txt = (REPO / "deploy/README.md").read_text(encoding="utf-8")
    assert "docker network create proxy" in txt
    assert "curl ifconfig.me" in txt
    # 다른 두 서비스 라벨 템플릿
    assert "rules-bot.${PUBLIC_IP}.sslip.io" in txt
    assert "loadbalancer.server.port=8787" in txt
    assert "lms-bot.${PUBLIC_IP}.sslip.io" in txt
    assert "loadbalancer.server.port=8080" in txt
```

- [ ] **Step 2: 실패 확인**

Run: `./venv/bin/python -m pytest tests/test_deploy_config.py::test_deploy_readme_has_guide_and_templates -q`
Expected: FAIL — `FileNotFoundError` (`deploy/README.md` 없음)

- [ ] **Step 3: 구현 — `deploy/README.md`**

````markdown
# 배포 가이드 (Traefik 게이트웨이 + sslip.io)

맥미니 한 대에서 여러 서비스를 공인 IP 하나로 호스트네임 라우팅한다. Traefik이 80/443을
받아 `Host` 헤더로 각 서비스에 분배하고, Let's Encrypt로 HTTPS를 자동 발급한다.

## 1. 처음 한 번만 (맥미니 준비)

1. **OrbStack**(권장) 또는 Docker Desktop 설치.
2. **잠자기 끄기** — 맥이 자면 서버도 죽는다. 시스템 설정 → 배터리/에너지 → "디스플레이 끔
   상태에서도 자동 잠자기 방지". 임시로는 `caffeinate -s &`.
3. **공용 네트워크 생성**(전 서비스 공유, 1회):
   ```bash
   docker network create proxy
   ```
4. **공인 IP 확인**:
   ```bash
   curl ifconfig.me
   ```
   나온 값을 각 서비스의 `.env`(`PUBLIC_IP=...`)와 아래 주소에 사용한다.
5. **공유기 포트포워딩** — 외부 80, 443 → 맥미니로 포워딩돼 있어야 한다(이미 설정됨).
6. **ACME 이메일** — `deploy/traefik/traefik.yml`의 `email:` 줄을 본인 이메일로 수정.
7. **인증서 저장 폴더** 생성(개인키 저장, git 에는 안 올라감):
   ```bash
   mkdir -p deploy/traefik/acme
   ```

## 2. 띄우는 순서

```bash
# (1) 게이트웨이 먼저
cd deploy/traefik
docker compose up -d

# (2) report-bot (이 저장소 루트)
cd ../..
echo "PUBLIC_IP=$(curl -s ifconfig.me)" > .env   # 또는 .env.example 복사 후 수정
docker compose up -d
```

접속 확인: 브라우저에서 `https://report-bot.<공인IP>.sslip.io`
(예: `https://report-bot.1.2.3.4.sslip.io`). 첫 접속 시 인증서 발급에 수십 초 걸릴 수 있다.

## 3. 새 서비스 추가하는 법 (학칙 챗봇 / lms-chatbot)

각 서비스는 **별도 폴더의 자기 docker-compose.yml**에 (a) `proxy` 네트워크 합류 +
(b) Traefik 라벨만 추가하면 된다. 그리고 그 폴더의 `.env`에 `PUBLIC_IP`를 적는다.

### 학칙 챗봇 (rules-bot, 내부 포트 8787)

그 서비스 정의에 추가:
```yaml
    labels:
      - traefik.enable=true
      - traefik.http.routers.rulesbot.rule=Host(`rules-bot.${PUBLIC_IP}.sslip.io`)
      - traefik.http.routers.rulesbot.entrypoints=websecure
      - traefik.http.routers.rulesbot.tls.certresolver=le
      - traefik.http.services.rulesbot.loadbalancer.server.port=8787
    networks:
      - proxy

networks:
  proxy:
    external: true
```
주소: `https://rules-bot.<공인IP>.sslip.io`

### lms-chatbot (lms-bot, 내부 포트 8080)

```yaml
    labels:
      - traefik.enable=true
      - traefik.http.routers.lmsbot.rule=Host(`lms-bot.${PUBLIC_IP}.sslip.io`)
      - traefik.http.routers.lmsbot.entrypoints=websecure
      - traefik.http.routers.lmsbot.tls.certresolver=le
      - traefik.http.services.lmsbot.loadbalancer.server.port=8080
    networks:
      - proxy

networks:
  proxy:
    external: true
```
주소: `https://lms-bot.<공인IP>.sslip.io`

> 라우터 이름(`rulesbot`/`lmsbot`)과 서비스 이름은 **서비스마다 유일**해야 한다.
> 내부 포트(8787/8080/8000)는 컨테이너마다 독립이라 겹쳐도 무방하다.

## 4. 주의

- **호스트 포트 공개 불필요** — Traefik 뒤에 두면 각 앱은 `ports:`로 호스트에 포트를 열 필요가
  없다. 80/443만 Traefik이 공개한다.
- **라벨은 비밀이 아니다** — 주소를 아는 사람은 접속할 수 있다. 비공개가 필요하면 Traefik
  Basic Auth 미들웨어나 IP 화이트리스트를 추가한다.
- **공인 IP가 바뀌면 주소도 바뀐다**(IP가 sslip.io 이름에 포함). 유동 IP면 변경 시 각 `.env`의
  `PUBLIC_IP`를 갱신하고 사용자에게 새 주소를 알린다.
- `deploy/traefik/acme/`(인증서·개인키)와 `.env`는 git에 올리지 않는다.
- report-bot 컨테이너가 실제 codex 분석까지 하려면 이미지에 codex 설치 + 인증 주입이 필요하다
  (별도 후속 작업). 이 가이드는 게이트웨이/라우팅/TLS까지 다룬다.
````

- [ ] **Step 4: 통과 확인 + 전체 스위트**

Run: `./venv/bin/python -m pytest tests/test_deploy_config.py -q`
Expected: PASS (5 passed)

Run: `./venv/bin/python -m pytest -q`
Expected: 기존 23 + deploy 5 = 28 passed.

- [ ] **Step 5: Commit**

```bash
git add deploy/README.md tests/test_deploy_config.py
git commit -m "docs: add Traefik deploy guide and service label templates"
```

---

## Self-Review (작성자 점검 완료)

- **Spec 커버리지:** traefik.yml(Task 1) · Traefik compose(Task 2) · report-bot 라벨+proxy+포트제거(Task 3) · `.env.example`/IP 단일소스(Task 3) · 가이드+다른 두 서비스 라벨 템플릿(Task 4) · 자동 TLS(traefik.yml tlsChallenge + websecure) · 보안/주의 메모(Task 4 README) — spec 5~10절 대응. spec 9절 범위밖(codex-in-image·인증·모니터링·DDNS)은 계획에 미포함(정상, README에 명시).
- **Spec 대비 보정 2건(정상):** ① ACME 이메일은 traefik.yml에 직접(정적 파일 env 미치환) — spec 5.1에 이미 반영. ② `.env.example`은 repo 루트(report-bot용); Traefik 본체 compose는 PUBLIC_IP 불필요(라벨 치환은 각 서비스 compose가 수행). 계획 파일구조 노트에 명시.
- **플레이스홀더:** 모든 파일에 완전한 내용. `you@example.com`·`1.2.3.4`는 사용자가 채우는 설정값(플레이스홀더 아님, 가이드에 수정 지시 포함).
- **타입/문자열 일관성:** 라우터/서비스 이름 reportbot/rulesbot/lmsbot, 포트 8000/8787/8080, 호스트네임 `<name>.${PUBLIC_IP}.sslip.io`, certresolver `le`, entrypoint `websecure` — 테스트 단언과 파일 내용이 일치. 네트워크 `proxy`(external) 일관.
- **검증 한계:** 테스트는 YAML 파싱 + 문자열 검사(오프라인). 실제 인증서 발급·외부 접속은 맥미니에서 수동(공인 IP·포트포워딩 필요) — README 2절에 절차 명시.
