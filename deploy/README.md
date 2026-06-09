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
