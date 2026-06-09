# report-bot 확장 로드맵 (Scaling Roadmap)

작성일: 2026-06-09
상태: 참고 문서 (의사결정 기록)

이 문서는 "담당자들이 각자 문서를 업로드하면 변환(kordoc) + 분석(codex)이 잡 단위로
병렬 실행되는" 시스템을, **동시 사용자 규모가 커질 때 어디까지 어떻게 확장하는지**를
미리 정리한 것이다. 지금은 Tier 1을 선택했고, 나중에 신호가 보이면 Tier 2 → Tier 3로
올린다.

## 변하지 않는 핵심 (모든 Tier 공통)

확장은 "잡을 어떻게 받고/줄 세우고/워커에 분배하느냐(=dispatch·transport 계층)"만
바뀐다. 아래 순수 모듈들의 **인터페이스는 Tier가 올라가도 그대로** 재사용된다.

| 모듈 | 책임 | 비고 |
| --- | --- | --- |
| `pipeline_runner.convert(upload, out)` | 문서 → 변환 산출물 | kordoc 호출 |
| `codex_runner.run_codex(dir, req, report, on_event)` | 변환물 → 리포트 + 이벤트 | codex 호출 |
| `report_renderer.render_html(md)` | md → 자체완결 HTML | |
| `job_manager` | 잡 상태·산출물·이벤트 | 저장소만 교체 가능 |
| `worker.run_job(job, mgr, convert, codex)` | 한 잡 오케스트레이션 | dispatch와 무관 |

즉 확장은 "이 모듈들을 **어디서/몇 개나/어떻게 트리거해서** 돌리느냐"의 문제다.

---

## Tier 1 — 소규모 (동시 1~5명) · **현재 선택**

**언제:** 한 팀/한 사무실. 동시에 올려도 codex 프로세스 몇 개 수준.

**아키텍처:** 단일 FastAPI 컨테이너 1개.
- 잡 전용 `ThreadPoolExecutor(max_workers=N)` (기본 N=3)에 `run_job`을 submit.
- N개까지 동시 실행, 초과분은 자동으로 **queued 대기** → 슬롯 나면 시작.
- codex 동시 실행 수 = 워커 수라서 **API rate limit·RAM도 자연 통제**.
- 잡 상태는 인메모리(+`jobs/` 디스크). SSE 라이브 로그 그대로.

**필요 인프라:** 없음 (n8n·Redis·별도 워커 불필요).

**한계(감수):** 프로세스 재시작 시 인메모리 잡 레지스트리 소실(산출물은 디스크에 남음),
단일 머신 CPU/RAM 한도, 수평 확장 불가.

**바꾸는 것:** `app.py`의 fire-and-forget `run_in_executor(None, ...)` →
전용 바운디드 executor submit. env `REPORT_BOT_MAX_CONCURRENCY`로 N 조절. (약 30줄)

---

## Tier 2 — 중규모 (동시 5~20명, 여러 부서)

**올리는 신호:** 동시 업로드가 자주 N을 초과해 대기가 길어짐 / 재시작 때 진행 중 잡이
사라지는 게 문제됨 / 한 머신 RAM·CPU가 빡빡해짐 / 워커를 따로 늘리고 싶음.

**아키텍처:** **web 컨테이너 + worker 컨테이너 분리**, 사이에 경량 큐.
- web(FastAPI): 업로드 받고 잡을 큐에 넣음, 상태·리포트·SSE 제공.
- worker(N개): 큐에서 잡을 꺼내 `convert`→`run_codex` 실행. codex·kordoc·인증은
  **worker 이미지**에만 설치하면 됨(web 이미지는 가벼워짐).
- 큐/브로커: **Redis + RQ(또는 Celery)**. 잡이 Redis에 영속 → 재시작에도 보존.
- 산출물: 공용 볼륨 또는 오브젝트 스토리지(S3/MinIO) — 컨테이너가 나뉘므로 공유 필요.
- 잡 상태: Redis(또는 작은 DB)로 이동(`job_manager`의 저장소만 교체).
- SSE 라이브 로그: worker가 codex 이벤트를 **Redis pub/sub**으로 발행 → web의 SSE가 구독·중계.

**필요 인프라:** Redis 1개 + worker 컨테이너(스케일 가능) → 사실상 컨테이너 3종
(web, worker×k, redis).

**바꾸는 것:** dispatch 계층(executor submit → 큐 enqueue), `job_manager` 저장소
(인메모리 → Redis/DB), 이벤트 전송(인프로세스 큐 → pub/sub), 산출물 경로(로컬 → 공용/오브젝트).
**순수 모듈(convert/run_codex/render_html/worker)은 그대로.**

---

## Tier 3 — 대규모 / 확장성·운영 중시 (조직 전체·외부 공개 가능)

**올리는 신호:** 워커를 여러 머신/노드로 흩뿌려야 함 / 잡 재시도·우선순위·스케줄·감사 로그
필요 / 비개발자가 워크플로(알림·구글시트·분기)를 시각적으로 손대고 싶음 / SLA·모니터링 필요.

**선택지 A — 전용 잡 큐 스택(엔지니어링 친화):**
Celery + Redis/RabbitMQ + 다수 worker, Flower 등 모니터링, 자동 재시도/우선순위,
컨테이너 오케스트레이션(K8s 등)으로 worker 오토스케일.

**선택지 B — n8n(워크플로/통합 친화):**
잡 트리거를 n8n 웹훅으로 받고, n8n 워크플로가 convert→codex→후처리(슬랙 알림, 구글시트
기록, 조건 분기)를 노드로 구성. **장점:** 시각적 워크플로·실행 히스토리·통합이 공짜,
비개발자 참여 용이. **대가(주의):**
- codex·kordoc 바이너리 + 인증(~/.codex)을 **n8n 컨테이너 이미지에 직접 설치**해야 함
  (Execute Command 노드 활성화 필요) — "이미지에 codex 심기" 문제는 그대로 따라옴.
- n8n은 노드 종료 후 결과 반환형이라 **codex `--json` 실시간 스트리밍을 브라우저로 흘리기
  어려움** → 라이브 로그가 약해지거나 별도 배관 필요(이벤트를 외부 저장 후 web이 폴링 등).
- 진짜 병렬은 n8n **queue mode(main + Redis + worker)** 필요 → 컨테이너 수 더 늘어남.

**판단 기준:** "CLI 잡을 빠르고 단순하게 많이" 돌리는 게 핵심이면 A,
"여러 SaaS·알림·분기를 노코드로 엮고 운영자가 손대는 것"이 핵심이면 B.

---

## 요약 결정표

| 차원 | Tier 1 (지금) | Tier 2 | Tier 3 |
| --- | --- | --- | --- |
| 동시 사용자 | 1~5 | 5~20 | 20+ / 외부 |
| 컨테이너 | web 1개 | web + worker×k + redis | +오케스트레이션 / n8n 스택 |
| 동시성 제어 | 인프로세스 세마포어/풀 | 큐(RQ/Celery) | 분산 큐 + 오토스케일 |
| 잡 영속 | 인메모리(+디스크) | Redis/DB | 동일 + 감사·재시도 |
| 산출물 | 로컬 `jobs/` | 공용 볼륨/오브젝트 | 오브젝트 스토리지 |
| 라이브 로그(SSE) | 인프로세스 큐 | Redis pub/sub | pub/sub (n8n이면 별도 배관) |
| codex/kordoc 설치 | 단일 이미지 | worker 이미지 | worker/n8n 이미지 |
| 추가 인프라 | 없음 | Redis | Redis/브로커 + (n8n) |

**원칙:** 필요해지기 전에 올리지 않는다. Tier 1으로 시작하고, 위 "올리는 신호"가
실제로 보일 때 한 단계씩 올린다. 순수 모듈 인터페이스를 안정적으로 유지하면 각 Tier 전환은
dispatch/transport 계층 교체로 끝난다.
