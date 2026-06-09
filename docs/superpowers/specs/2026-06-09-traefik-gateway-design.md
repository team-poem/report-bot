# Traefik 게이트웨이 — 설계 (Design Spec)

작성일: 2026-06-09
상태: 승인 대기 (검토 후 구현 계획 단계로)

## 1. 목적

맥미니 한 대에서 여러 Docker 서비스(봇·웹앱)를 띄우고, **공인 IP 하나 + sslip.io**로
각 서비스를 호스트네임으로 접속하게 한다. Traefik 리버스 프록시가 `Host` 헤더 기준으로
라우팅하고, Let's Encrypt로 HTTPS를 자동 발급한다. k8s 없이 Docker Compose만으로 운영한다.

## 2. 배포 대상 (3개 서비스)

공인 IP를 `<IP>`(점 표기, 예: `1.2.3.4`)라 할 때:

| 서비스 | 호스트네임 | 컨테이너 내부 포트 | 위치 |
| --- | --- | ---: | --- |
| report-bot (이 repo) | `report-bot.<IP>.sslip.io` | 8000 | 이 저장소 |
| 학칙 챗봇 | `rules-bot.<IP>.sslip.io` | 8787 | 별도 폴더 |
| lms-chatbot | `lms-bot.<IP>.sslip.io` | 8080 | 별도 폴더 |

라벨(앞 이름)은 ASCII로 둔다(한글 라벨은 인증서 punycode 문제). 이름은 자유롭게 변경 가능.

## 3. 전제 / 제약

- 맥미니에 공인 IP가 있고, 공유기에서 **80, 443**이 맥미니로 포트포워딩돼 있음.
- 도메인은 sslip.io만 사용(`<라벨>.<IP>.sslip.io` → `<IP>`).
- 컨테이너 런타임은 OrbStack 또는 Docker Desktop(맥, ARM/arm64).

## 4. 아키텍처

```
인터넷 ─(80/443)─► 맥미니 ─► Traefik (게이트웨이)
                              ├─ Host(report-bot.<IP>.sslip.io) → report-bot:8000
                              ├─ Host(rules-bot.<IP>.sslip.io)  → rules-bot:8787
                              └─ Host(lms-bot.<IP>.sslip.io)    → lms-bot:8080
   모든 서비스는 공용 docker 네트워크 `proxy` 위에서 컨테이너 이름으로 통신.
   Traefik만 호스트에 80/443 공개. 각 앱은 ports: 공개 불필요.
```

핵심 원칙:
- **IP는 한 곳에서만 관리:** 각 compose의 `.env`에 `PUBLIC_IP=<IP>`. 라우팅 규칙은 `${PUBLIC_IP}` 참조.
  IP가 바뀌면 `.env`만 수정.
- **자동 HTTPS:** Let's Encrypt **TLS-ALPN-01(443 기반)** — ISP가 인바운드 80을 막아도 동작.
  80 → 443 자동 리다이렉트. 인증서는 `acme/acme.json`(권한 600)에 저장·자동 갱신.
- **Docker provider, `exposedByDefault=false`:** 라벨 `traefik.enable=true` 붙은 컨테이너만 노출.

## 5. 컴포넌트 / 파일

생성(이 repo):
- `deploy/traefik/docker-compose.yml` — Traefik 본체 서비스.
- `deploy/traefik/traefik.yml` — 정적 설정(엔트리포인트·ACME·docker provider).
- `deploy/traefik/.env.example` — `PUBLIC_IP` 예시. (ACME 이메일은 `traefik.yml`에 직접 적음)
- `deploy/README.md` — 맥미니 셋업 가이드 + "새 서비스 추가법" + 다른 두 서비스용 라벨 템플릿.

수정(이 repo):
- `docker-compose.yml`(report-bot) — Traefik 라벨 + 외부 `proxy` 네트워크 합류, 호스트 포트 공개 제거.

별도 폴더(이 repo 밖, 가이드로 안내):
- 학칙 챗봇 / lms-chatbot의 각 compose에 **라벨 4줄 + `proxy` 합류**를 추가(템플릿 제공).

### 5.1 `deploy/traefik/traefik.yml` (정적 설정)

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

### 5.2 `deploy/traefik/docker-compose.yml`

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

### 5.3 `deploy/traefik/.env.example`

```dotenv
PUBLIC_IP=1.2.3.4
```

> ACME 이메일은 `.env`가 아니라 `traefik.yml`의 `email:` 줄에 직접 적는다(정적 파일은 env 치환 안 됨).

### 5.4 report-bot `docker-compose.yml` 추가 라벨 (이 repo)

기존 `report-bot` 서비스에 다음을 추가(호스트 `ports:` 공개는 제거하고 Traefik 경유):

```yaml
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

그리고 이 compose의 `.env`에 `PUBLIC_IP=<IP>` 를 둔다.

### 5.5 다른 두 서비스용 라벨 템플릿 (가이드에 수록)

학칙 챗봇(compose의 해당 서비스에 추가):
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

lms-chatbot:
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

각 서비스 compose의 `.env`에도 `PUBLIC_IP=<IP>` 필요(라벨이 `${PUBLIC_IP}`를 치환하므로).

## 6. 맥미니에서 사용자가 할 일 (가이드 핵심)

1. **OrbStack**(권장) 또는 Docker Desktop 설치.
2. 맥 **잠자기 끄기**(서버가 자면 죽음): 시스템 설정 → 배터리/에너지 → 잠자기 안 함, 또는 `caffeinate -s`.
3. 공용 네트워크 1회 생성: `docker network create proxy`
4. 공인 IP 확인: `curl ifconfig.me` → `deploy/traefik/.env`의 `PUBLIC_IP`와 각 서비스 `.env`에 기입.
5. 공유기 **80/443 포트포워딩**이 맥미니로 돼 있는지 확인.
6. 띄우는 순서: ① `deploy/traefik`에서 `docker compose up -d` → ② 각 서비스 `docker compose up -d`.
7. 접속 확인: `https://report-bot.<IP>.sslip.io` 등. 첫 접속 시 인증서 자동 발급(수십 초).

## 7. 검증 방법

- `docker compose -f deploy/traefik/docker-compose.yml config` 로 compose 문법 검증.
- `traefik.yml`은 YAML 파싱 검증(파이프라인에서 `yaml.safe_load`).
- 라우터/포트 라벨이 표(2절)의 호스트네임·포트와 일치하는지 점검 테스트(문자열 검증).
- 실제 발급/접속은 맥미니에서 수동(공인 IP·포트포워딩 필요).

## 8. 보안 메모

- sslip.io 라벨은 **비밀이 아님** — 알면 누구나 접속. 비공개가 필요하면 Traefik **Basic Auth
  미들웨어**(또는 IP 화이트리스트)를 라우터에 추가. (이번 범위 밖, 후속 옵션)
- `acme/acme.json`은 **권한 600** 필수(Traefik이 거부함).
- docker.sock는 **read-only(`:ro`)** 로만 마운트.

## 9. 범위 밖 (다음 단계)

- **report-bot의 codex-in-image 선행작업**: report-bot 컨테이너가 실제 codex 분석까지 하려면
  이미지에 codex 설치 + 인증 주입이 필요(기존 미뤄둔 항목). 이 spec은 **게이트웨이/라우팅/TLS**까지.
- 학칙 챗봇·lms-chatbot의 내부(앱 코드·Dockerfile)는 이 repo 밖이라 다루지 않음 — 라벨/네트워크
  합류 가이드만 제공.
- Basic Auth/로그인, 모니터링 스택(Portainer/Dozzle/Uptime Kuma), DDNS(유동 IP 대응).

## 10. 주의(운영)

- **공인 IP가 바뀌면 sslip.io 주소도 바뀐다**(IP가 이름에 포함). 고정 IP면 안정, 유동이면 변경 시
  `.env`의 `PUBLIC_IP` 갱신 + 사용자에게 새 주소 공지. (후속: DDNS + 실도메인으로 보완)
- Traefik 뒤에 두면 각 앱의 **호스트 포트 공개 불필요** — 80/443만 공개. (앱 내부 포트가 8000/8080/8787로
  달라도, 같아도 무방 — 컨테이너 이름으로 구분.)

## 11. 결정 기록

- 게이트웨이: **Traefik v3**(docker provider, 라벨 기반 자동 발견). Caddy 대비 서비스 잦은 추가에 유리.
- TLS: **Let's Encrypt TLS-ALPN-01(443)** — 80 차단 환경에도 강함.
- IP 관리: 각 compose `.env`의 `PUBLIC_IP` 단일 소스.
- 범위: 게이트웨이 + 3개 서비스 라우팅 + 자동 TLS. codex-in-image·인증·모니터링은 분리.
