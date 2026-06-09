# Codex 분석 리포트 웹앱 — 설계 (Design Spec)

작성일: 2026-06-09
상태: 승인됨 (구현 계획 단계로 진행)

## 1. 목적과 배경

담당자가 한글(HWP/HWPX)·엑셀 등 문서를 **업로드**하고 "무엇을 원하는지"를 자유롭게 적으면,
그 요청에 맞는 **분석 리포트**가 새 웹 페이지에 나오는 로컬 웹앱을 만든다.

이미 존재하는 것:
- `scripts/kordoc_pipeline.py` — `kordoc` CLI를 호출해 HWP/HWPX/PDF/XLS/XLSX/DOCX 를
  Markdown + 표 CSV/JSON + 규칙기반 facts(수치·날짜·연락처) + Excel 로 변환하는 배치 스크립트.
  `process_file(file_path, input_root, out_dir, pages)` 가 이미 호출 가능한 함수로 노출되어 있다.

이번에 추가하는 것: 위 파이프라인 위에 **업로드 → 변환 → Codex 분석 → 리포트 조회** 흐름을 얹는 웹 레이어.

## 2. 범위 (MVP 경계)

포함:
- 로컬 웹앱(브라우저, 단일 사용자 PoC)
- 파일 업로드 + 자유 요청문 입력
- 잡(Job) 기반 비동기 처리(A안) + `codex --json` 이벤트의 **SSE 라이브 진행 스트리밍**(C안)
- 완료된 리포트(Markdown)를 화면에 렌더 + 원본 다운로드
- 배포 대비 Dockerfile / docker-compose.yml

제외(다음 단계):
- 엑셀/한글 파일 **생성·수정** (이번엔 리포트 **조회까지만**)
- 다중 사용자 / 인증 / 영속 DB
- 리포트 버전 관리·재실행 히스토리 UI

## 3. 아키텍처

```
브라우저(단일 페이지, 의존성 없는 HTML+JS)
   │  ① POST /jobs        (파일 + 요청문)
   │  ② SSE  /jobs/{id}/events   (진행 로그 실시간)
   │  ③ GET  /jobs/{id}          (상태 폴링 백업)
   │  ④ GET  /jobs/{id}/report   (완료된 report.md)
   ▼
FastAPI 백엔드 (web/app.py)
   ├─ JobManager (메모리 dict + jobs/<id>/ 디스크)
   │     state: queued → converting → analyzing → done | failed
   ├─ 변환 단계: scripts/kordoc_pipeline.py 를 *모듈로 import* 해 process_file() 호출
   └─ 분석 단계: web/codex_runner.py 가 `codex exec` 호출
```

### 컴포넌트 (각각 독립 테스트 가능)

| 단위 | 책임 | 입력 → 출력 | 의존 |
| --- | --- | --- | --- |
| `pipeline` (기존, 거의 그대로) | 문서 → 변환 산출물 | 파일 → converted/ | kordoc(npx) |
| `web/codex_runner.py` | 프롬프트 조립 + codex 호출 + 이벤트 파싱 | (converted 경로, 요청문) → report.md, 이벤트 스트림 | codex CLI |
| `web/job_manager.py` | 잡 생명주기·상태·디스크 레이아웃·이벤트 큐 | 잡 생성/조회/상태전이 | 없음(표준 라이브러리) |
| `web/app.py` | HTTP/SSE 라우트 + 정적 HTML 1장 | HTTP | FastAPI |

> 경계 원칙: `codex_runner`·`job_manager`·`pipeline` 은 FastAPI 를 모름. `app.py` 만 HTTP 를 안다.
> 따라서 codex 호출 로직과 잡 상태머신은 웹서버 없이 단위테스트할 수 있다.

## 4. 데이터 흐름 / 디스크 레이아웃

```
jobs/<job_id>/
  request.txt          # 담당자가 적은 요청문
  upload/<원본파일>      # 업로드 원본
  converted/<docid>/   # pipeline 산출물 (document.md, facts.json, tables_long.csv, table_*.csv ...)
  report.md            # codex 가 쓴 최종 리포트  ← 화면에 렌더
  status.json          # {state, step, error, created_at, updated_at}
  codex_log.jsonl      # codex --json 원본 이벤트 (SSE 중계 + 디버그)
```

`job_id` 는 업로드 시각 기반 UUID. 단일 사용자 PoC 이므로 잡 메타는 메모리 dict 에 두되,
산출물·로그·상태는 디스크에 남겨 재시작/디버그가 가능하게 한다.

## 5. 처리 흐름 (상태머신)

1. **queued** — `POST /jobs` 수신. 업로드 저장, `request.txt` 기록, 백그라운드 태스크 시작.
2. **converting** — `process_file()` 로 변환. 실패 시 `failed` + error 기록.
3. **analyzing** — `codex_runner` 가 `codex exec` 실행. 표준출력 JSONL 을 한 줄씩 읽어
   `codex_log.jsonl` 에 적고 동시에 잡 이벤트 큐로 push (→ SSE 로 브라우저 전달).
4. **done** — `report.md` 생성 완료. SSE 로 완료 이벤트 전송, 연결 종료.
5. **failed** — 어느 단계든 예외 시. error 메시지를 단계 구분과 함께 노출.

비동기 실행 방식: FastAPI `BackgroundTasks` 또는 `asyncio.create_task` 로 잡 워커를 띄우고,
codex 의 블로킹 subprocess 는 `asyncio.create_subprocess_exec` 로 비동기 스트리밍한다.

## 6. Codex 연동 상세

`codex exec` 비대화형 모드 사용. 검증된 옵션:

```
codex exec "<프롬프트>" \
  -C jobs/<id>/converted \          # 작업 루트 = 변환 산출물 폴더
  -s read-only \                    # 읽기 전용 (리포트만 받으므로 파일 수정 불필요)
  --skip-git-repo-check \           # 이 폴더는 git repo 아님
  --json \                          # 진행 이벤트 JSONL 로 stdout
  -o jobs/<id>/report.md            # 최종 메시지를 파일로 저장
```

프롬프트 = **고정 시스템 지침 + 담당자 요청문**:

> 너는 `converted/` 폴더의 한글 문서 변환 데이터(document.md, facts.json, tables_long.csv,
> table_*.csv)를 읽고 분석 리포트를 작성하는 어시스턴트다. 추측하지 말고 데이터 근거를
> 표·수치로 제시하라. 근거가 없으면 "데이터에서 확인 불가"라고 명시하라.
> 아래 담당자 요청에 맞춰 한국어 Markdown 리포트를 작성하라.
>
> [담당자 요청]
> {request.txt 내용}

facts.json·tables_long.csv 를 같이 물려주어 codex 가 본문(document.md)뿐 아니라
정규화된 수치 후보도 근거로 쓸 수 있게 한다.

## 7. SSE 라이브 스트리밍 (C)

- `GET /jobs/{id}/events` — `text/event-stream`. 잡의 이벤트 큐를 구독.
- 백엔드: codex stdout JSONL 한 줄 = 이벤트 하나. 잡별 `asyncio.Queue` 에 넣고 SSE 로 flush.
- 프론트: `EventSource` 로 연결, 진행 메시지를 화면 로그 영역에 append.
  `done` 이벤트 수신 시 `GET /jobs/{id}/report` 호출해 리포트 렌더.
- 폴백: SSE 끊겨도 `GET /jobs/{id}` 폴링으로 상태 확인 가능(이벤트 누락 시 최종 상태 보장).

## 8. 화면 (HTML 한 장)

- 상단: 파일 선택 + 요청문 textarea + "분석 시작" 버튼
- 제출 후 같은 페이지에서: 진행 단계(변환 중 / 분석 중) + SSE 라이브 로그
- 완료 시 하단에: 렌더된 리포트(Markdown→HTML, `marked.js` CDN) + `report.md` 다운로드 링크
- 의존성/빌드툴 없음. 정적 파일 1~2개.

## 9. 에러 처리

- 단계별 실패를 `status.json.error` 에 단계 구분과 함께 기록("변환 실패" vs "codex 실패").
- codex 비정상 종료(returncode≠0)·타임아웃·인증 미설정을 명시적으로 구분해 화면에 노출.
- 업로드 확장자/용량 검증, 지원 외 포맷 거부.

## 10. 테스트 전략

- `pipeline` — 기존 `output_test` 더미·소형 샘플로 회귀 확인(이미 동작).
- `codex_runner` — codex 를 가짜 스크립트로 모킹(고정 JSONL 출력 + report.md 작성).
  프롬프트 조립, 인자 구성, 이벤트 파싱, 에러(비정상 종료) 경로 검증.
- `job_manager` — 상태 전이(queued→converting→analyzing→done/failed) 단위테스트.
- `app` — TestClient 로 `POST /jobs` → 상태 조회 → 리포트 조회 happy path(파이프라인·codex 모킹).

## 11. 배포(Docker) — 다음 단계 대비, 설계만

- `Dockerfile`: Python 3 베이스 + Node.js(npx kordoc 용) + codex CLI 설치 + 앱.
- `docker-compose.yml`: 앱 서비스 1개, `jobs/` 볼륨 마운트, 포트 매핑, codex 인증
  (`CODEX_HOME`/토큰)을 환경변수·볼륨으로 주입.
- 이번 MVP 에서 파일은 작성하되 로컬 실행이 1차 검증 대상. 컨테이너 빌드/배포는 후속.

## 12. 스택

- 백엔드: **Python + FastAPI** (기존 파이프라인 재사용, codex/python/node 모두 설치 확인됨)
- 프론트: 순수 HTML + 약간의 JS, 마크다운 렌더는 `marked.js` CDN. 빌드 없음.
- 비동기: asyncio + `asyncio.create_subprocess_exec` 로 codex 스트리밍.

## 13. 결정 기록

- 오케스트레이션: **A(잡 기반 비동기) + C(SSE 라이브 스트리밍)** 둘 다 MVP 포함.
- 리포트 엔진: **Codex CLI**(`codex exec`). Claude API 미사용.
- 형태: 로컬 웹앱(브라우저), 단일 사용자.
- 1차 범위: 리포트 **조회까지**. 엑셀/한글 생성·수정은 다음 단계.
- 배포: Docker(파일은 이번에 작성, 검증은 후속).
