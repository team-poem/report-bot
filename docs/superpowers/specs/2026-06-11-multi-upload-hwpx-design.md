# 다중 업로드 + 취합 + HWPX 생성 — 설계 (Design Spec)

작성일: 2026-06-11
상태: 승인 대기 (검토 후 구현 계획 단계로)

## 1. 목적과 배경

부서 사무 업무의 핵심 고통은 단일 파일 이해가 아니라 **여러 파일을 모아 하나로
만드는 반복 취합 작업**이다. 현재 웹앱은 파일 1개를 올려 분석 리포트(HTML)를 받는
구조라서 이 흐름을 지원하지 못한다.

이 작업은 웹앱을 다음과 같이 확장한다:

- **다중 업로드:** 한글(HWP/HWPX) 등 지원 포맷 파일을 한 잡에 여러 개(4~5개 수준) 업로드.
- **산출물 종류 선택:** 제출 시 담당자가 명시적으로 선택.
  - `report` — 분석 리포트 (현행과 동일한 성격)
  - `merge` — 여러 파일 내용을 요청에 맞춰 하나의 새 문서로 취합
- **HWPX 다운로드:** 어느 쪽이든 결과를 HTML 미리보기 + **한글에서 열리는 HWPX 파일**로 제공.
- **양식 지원(2단계):** 자리표시자 규약이 들어간 양식 HWPX를 잡마다 함께 업로드하면,
  생성 내용이 양식의 지정 위치에 양식 서식으로 들어간다.

주의: 이 파이프라인이 만드는 것은 "원본들의 내용을 취합해 **새 문서를 생성**"하는
것이지, 원본 서식을 그대로 이어붙인 병합본이 아니다. 마크다운 경유는 손실 변환이며,
이는 의도된 트레이드오프다(들쭉날쭉한 원본 N개가 일관된 형식 하나로 정리됨).

## 2. 현재 상태

- `POST /jobs`: 단일 `UploadFile` + `request_text`. (`web/app.py`)
- `worker.run_job`: `queued → converting → analyzing → done/failed` 상태 머신.
- `pipeline_runner.convert`: 파일 1개 → `converted/<docid>/` (document.md, facts.json, CSV들).
- `codex_runner.run_codex`: `-C <doc_dir>` read-only 샌드박스, `-o report.md`.
- 리포트는 `GET /jobs/{id}/report`에서 HTML로 렌더.

순수 모듈 경계(`pipeline_runner` / `codex_runner` / `worker` / `job_manager`)는 유지하고
각 모듈을 확장한다. HWPX 생성은 신규 모듈로 추가한다.

## 3. 핵심 설계 결정

### 3.1 HWPX는 결정적(deterministic) Python 변환기가 만든다

Codex는 지금처럼 **마크다운만** 출력한다(리포트든 취합 문서든). 신규 모듈
`web/hwpx_writer.py`가 마크다운 → OWPML XML → zip(.hwpx) 변환을 담당한다.

기각한 대안:
- **Codex가 HWPX 직접 생성** — 비결정적이라 깨진 XML이 간헐 발생, 테스트 불가,
  read-only 샌드박스를 풀어야 함. 기각.
- **pandoc 경유(md → DOCX)** — .hwpx가 아니어서 수동 단계가 남고 양식 적용 불가.
  단, "HWPX 생성 실패 시에도 마크다운/HTML은 제공"이라는 폴백 정신만 흡수(§6).

### 3.2 양식은 자리표시자 규약 + 잡마다 업로드

임의 양식 HWPX에 본문을 꽂으려면 ①삽입 위치 ②적용 서식 ③표 칸 의미를 추론해야
하는데, 이는 불안정하다. 대신 **양식 안에 자리표시자 텍스트를 넣는 규약**을 요구한다.
자리표시자가 위치를 알려주고, 서식은 자리표시자에 입혀진 것을 상속하므로 추론이
사라진다. 업로드 시점에 규약을 검증해 실패를 앞당긴다.

### 3.3 자리표시자 규약 (확장 규약 전체, 2단계)

| 마커 | 의미 | 동작 |
| --- | --- | --- |
| `{{본문}}` | 단일 본문 슬롯 | 그 자리에 생성 본문 전체 삽입 |
| `{{추가: 지시}}` | 이름 있는 삽입 슬롯 | 그 자리에 지시에 맞는 새 내용 삽입 |
| `{{수정시작: 지시}} … {{수정끝}}` | 구간 교체 | 사이의 기존 내용을 추출해 Codex에 "원문 + 지시"로 주고, 다시 쓴 결과로 구간 전체를 교체 |

- 콜론 뒤 지시문은 Codex에게 주는 슬롯별 요구사항이다.
- 한글은 텍스트를 여러 run으로 쪼개 저장할 수 있으므로(`{{수정시작`이 두 run에 걸침),
  스캔은 **문단 단위로 run 텍스트를 합친 뒤** 패턴 매칭한다.
- 양식 문서 자체도 kordoc으로 마크다운 변환해 Codex에 맥락으로 제공한다
  (슬롯 주변 내용과 어울리는 결과를 쓰게 하기 위함).

양식은 **산출물 종류와 직교**한다: `report`든 `merge`든 양식을 첨부하면 결과가
양식 슬롯에 들어가고, 없으면 기본 서식으로 생성된다.

### 3.4 슬롯 식별자와 Codex 출력 구조 (2단계)

슬롯 식별자는 양식 내 **등장 순서로 자동 부여**한다(`본문-1`, `추가-2`, `수정-3` …).
콜론 뒤 지시문이 같은 마커가 여러 번 나와도 충돌하지 않는다. 프롬프트에는
"식별자 + 지시문 + (수정 구간이면 원문)"을 슬롯마다 제공한다.

양식이 있으면 Codex는 슬롯별 구분자가 있는 마크다운을 출력한다:

```
===SLOT: 추가-2===
(마크다운 내용)
===END===
```

시스템이 파싱 후 **모든 슬롯이 채워졌는지 검증**하고, 누락 시 잡 실패 처리한다.
양식이 없으면 구분자 없는 일반 마크다운(현행과 동일)을 받는다.

## 4. 아키텍처 / 컴포넌트

```
POST /jobs (files[], output_type, request_text, template?)
  ├─ 업로드 검증(확장자·파일명·양식 규약) ── 실패 시 400
  └─ JobRunner.submit(run_job, ...)
        ▼
  converting  : convert_many() — 파일별 kordoc 변환 → converted/<docid>/ × N
        ▼
  analyzing   : run_codex() — output_type별 프롬프트, -C converted/ 루트, → report.md
        ▼
  generating  : hwpx_writer — 기본 서식(markdown_to_hwpx) 또는 양식 치환(fill_template)
                → result.hwpx (zip/XML 정합성 자체 검증 통과 시에만 done)
        ▼
  done / failed
```

### 잡 디렉터리

```
jobs/<job_id>/
  upload/               # N개 원본 파일
  template/             # 양식 HWPX (선택, 2단계)
  converted/<docid>/…   # 파일별 변환 결과 × N
  request.txt
  report.md             # Codex 산출 마크다운 (산출물 종류 무관 동일 경로)
  result.hwpx           # 최종 한글 파일
  codex_log.jsonl / status.json
```

### 모듈별 변경

| 모듈 | 변경 |
| --- | --- |
| `app.py` | `POST /jobs`: `files: list[UploadFile]`, `output_type: report\|merge`, `template: UploadFile\|None`. 신규 `GET /jobs/{id}/hwpx` (Content-Disposition 다운로드). HTML 미리보기 라우트 유지. |
| `job_manager.py` | `Job.upload_paths: list[Path]`, `output_type`, `template_path` 추가. `JobState.GENERATING` 추가. |
| `pipeline_runner.py` | `convert_many(paths, root) -> list[Path]` — 기존 `process_file` 반복. 단일 변환은 N=1로 흡수. |
| `codex_runner.py` | 프롬프트 분기 2축: 산출물 종류(report 현행 / merge는 여러 `converted/<docid>/` 전체를 읽고 하나의 문서 작성) × 양식 유무(있으면 슬롯 구조 출력). `-C`는 `converted/` 루트. |
| **`hwpx_writer.py` (신설)** | `markdown_to_hwpx(md, out)` — 내장 스켈레톤 기반 기본 서식. `fill_template(template, slots, out)` — 자리표시자 스캔·치환(2단계). `scan_placeholders(template)` — 업로드 검증용. |
| `web/assets/skeleton.hwpx` (신설) | 기본 서식의 뼈대 HWPX (빈 문서 + 기본 스타일 정의). |
| `static/index.html` | 다중 파일 선택, 산출물 종류 라디오, 양식 첨부(2단계), 완료 시 HWPX 다운로드 버튼, `generating` 단계 라벨. |

### 지원 마크다운 요소 (hwpx_writer)

제목(h1–h3), 문단, 표, 순서/비순서 목록, 굵게/기울임. 이 범위를 벗어나는 요소
(이미지, 각주 등)는 일반 텍스트로 강등하되 내용은 보존한다.

## 5. 범위

**1단계 — 다중 업로드 + 기본 서식 HWPX** (이것만으로 배포 가치 있음):
- 다중 업로드, `output_type` 선택, `convert_many`, 취합/리포트 프롬프트 분기,
  `generating` 상태, `markdown_to_hwpx`(스켈레톤 기반), 다운로드 라우트, 프런트 변경.

**2단계 — 양식 + 확장 자리표시자 규약**:
- 양식 업로드·검증, 자리표시자 스캔(run 합치기 포함), 마커 3종, 슬롯 구조 Codex
  출력·누락 검증, `fill_template`, 양식 맥락 프롬프트.

각 단계는 별도 구현 계획으로 진행한다. 1단계 완료 시 실제 부서 파일로 검증 후
2단계에 들어간다.

제외(이번 아님):
- 원본 서식 보존 병합(§1 주의 참고), 이미지·각주의 서식 재현.
- 슬롯 누락 시 Codex 자동 재시도 (필요해지면 추가).
- 서버 사전 등록 템플릿 라이브러리 (잡마다 업로드로 시작, 수요 확인 후).
- 바이너리 .hwp 출력 (한글이 HWPX를 네이티브로 열므로 불필요).

## 6. 에러 처리

**업로드 시점 (400, 메시지에 해결 방법 명시):**
- 파일 0개 / 지원하지 않는 확장자 / 파일명 경로 탈출(기존 검증을 N개 파일에 적용).
- `output_type`이 `report`/`merge` 외의 값.
- 양식 제공 시: 자리표시자 0개("양식에 {{본문}} 또는 {{추가: …}}를 넣어 주세요"),
  `{{수정시작}}`/`{{수정끝}}` 짝 불일치, 양식이 HWPX가 아님.

**변환 단계:** 한 파일이라도 실패하면 **실패한 파일명을 담아** 잡 실패. 부분 성공으로
계속 가지 않는다 — 취합 결과에서 일부 파일이 조용히 빠지는 것이 더 위험하다.

**분석 단계:** 슬롯 파싱 후 누락 슬롯이 있으면 슬롯 이름을 담아 실패.

**생성 단계 (핵심 폴백):** HWPX 생성이 실패해도 `report.md`는 존재하므로 HTML
미리보기는 계속 열람 가능하다. 잡은 실패로 표시하되 에러 메시지에 "리포트 내용은
화면에서 확인할 수 있습니다"를 포함한다. 생성 직후 zip 구조·XML 정합성 자체 검증을
통과해야 `done`이 된다.

## 7. 테스트

기존 방식대로 모듈별 단위 테스트, 실제 의존성(kordoc·codex)은 페이크로 대체:

- **`hwpx_writer`:** 마크다운 요소별(제목/문단/표/목록/굵게) XML 매핑, zip 구조 검증.
  자리표시자 스캔은 run이 쪼개진 fixture XML로 문단 합치기 로직 검증. 수정 구간 추출,
  서식 상속, 슬롯 치환 각각 테스트.
- **`codex_runner`:** output_type별 프롬프트 조립, 슬롯 출력 파싱·누락 검증.
- **`app`:** 다중 파일 업로드, 양식 규약 위반별 400, 다운로드 라우트(Content-Disposition).
- **`worker`:** `generating` 상태 전이, 생성 실패 시 미리보기 폴백.
- **라운드트립 스모크(자동):** 생성한 HWPX를 kordoc 파이프라인에 다시 넣어 텍스트가
  보존되는지 확인.
- **수동 검증(각 단계 완료 조건):** 생성된 HWPX를 실제 한글(또는 한컴독스)에서 열어
  서식·표가 깨지지 않는지 확인. 자동화 불가하므로 완료 조건에 명시한다.
