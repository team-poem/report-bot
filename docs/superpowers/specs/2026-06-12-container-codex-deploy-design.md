# 컨테이너 Codex 활성화 + PDF 의존성 — 설계 (Design Spec)

작성일: 2026-06-12
상태: 승인됨 (구현 계획 단계로)

## 1. 목적과 배경

맥미니 컨테이너 배포본은 현재 업로드·변환·화면까지만 동작한다. Codex CLI가 이미지에
없어 분석 단계가 실패하고, PDF 의존성(kordoc + pdfjs-dist)도 빠져 있다. 이 작업으로
컨테이너에서 전체 파이프라인(업로드 → 변환 → Codex 분석 → HWPX 생성)이 돌게 하여
부서 담당자가 `https://report-bot.<공인IP>.sslip.io` 에서 1·2단계 기능을 쓸 수 있게 한다.

## 2. 현재 상태

- `Dockerfile`: python:3.12-slim + Node 20. codex 없음, npm 의존성 없음.
- `docker-compose.yml`: `~/.codex:/root/.codex:ro` 마운트와 `CODEX_HOME` 은 미리
  준비돼 있으나, **ro 마운트는 ChatGPT(OAuth) 인증의 토큰 갱신 쓰기를 막는다.**
- 로컬에서 `npm i kordoc pdfjs-dist@^4.10.38` 로 PDF 변환을 고쳤고
  `package.json`/`package-lock.json` 이 저장소에 있다 — 이미지에는 아직 미반영.

## 3. 핵심 결정

### 3.1 Codex CLI 는 이미지에 npm 전역 설치 (A안)

`npm install -g @openai/codex`. npm 이 컨테이너 플랫폼(linux)에 맞는 바이너리를
받으므로 재현 가능하고 자기완결적이다.

기각한 대안: 호스트 codex 바이너리 마운트(macOS 바이너리는 리눅스에서 실행 불가),
호스트 실행 + 컨테이너 호출(경계 복잡).

### 3.2 인증은 호스트 `~/.codex` 를 rw 마운트

확정된 인증 방식은 ChatGPT 계정 로그인(OAuth)이며 토큰 갱신이 폴더에 써야 하므로
`:ro` 를 제거한다. 맥미니 호스트에서 최초 1회 `codex login` 이 사전 조건이다.

### 3.3 샌드박스 모드 환경변수 노브

codex 의 `-s read-only` 는 리눅스에서 Landlock 커널 기능에 의존하는데 컨테이너
환경에 따라 미지원일 수 있다. `codex_runner` 의 `-s` 값을
`REPORT_BOT_CODEX_SANDBOX`(기본 `read-only`)로 빼서, Landlock 실패 시 재빌드 없이
compose 에서 `danger-full-access` 로 전환할 수 있게 한다(컨테이너 자체가 격리 경계).

## 4. 변경 내용

| 파일 | 변경 |
| --- | --- |
| `Dockerfile` | ① `npm install -g @openai/codex` ② `COPY package.json package-lock.json` + `RUN npm ci` (소스 COPY 앞, 레이어 캐싱) |
| `docker-compose.yml` | ① `~/.codex` 마운트의 `:ro` 제거 ② `REPORT_BOT_CODEX_SANDBOX` 환경변수 전달(기본값 유지 시 생략 가능 형태) |
| `web/codex_runner.py` | `-s` 값: `os.environ.get("REPORT_BOT_CODEX_SANDBOX", "read-only")` |
| `deploy/README.md` | 사전 준비(호스트 `npm i -g @openai/codex` + `codex login` 1회), 재배포 절차, 컨테이너 내 codex 스모크 검증, Landlock 실패 증상과 대응 |
| `tests/test_deploy_config.py` 등 | Dockerfile 라인 존재, compose 마운트 비-ro, env 전달, codex_runner 샌드박스 노브 단위 테스트 |

## 5. 검증

- 자동: 설정 파일 단정 테스트 + codex_runner 노브 단위 테스트 (전체 스위트 통과).
- 로컬 빌드 스모크: `docker compose build` 성공, 컨테이너에서 `codex --version`,
  `npx kordoc --help` 동작.
- 맥미니 수동 게이트: 재배포 후 실제 파일 업로드 1건이 done 까지 도달하고 HWPX
  다운로드가 되는 것 (이건 맥미니에서 사용자가 수행).

## 6. 범위 제외

- Basic Auth 등 접근 제어(별도 선택 과제 유지), 학칙 챗봇 배포, Traefik 설정 변경.
