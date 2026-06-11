# kordoc HWP → Markdown/Data Pipeline

`kordoc` CLI를 Python에서 호출해 HWP/HWPX/PDF/XLS/XLSX/DOCX 문서를 다음 산출물로 변환하는 프로토타입입니다.

- 에이전트 독해용 Markdown
- 원본 구조 JSON blocks
- 표 CSV/JSON
- 수치·날짜·연락처 후보 facts JSON/CSV
- 담당자 작업용 Excel

## 준비

Node.js/npm이 필요합니다. `kordoc`은 `npx -y kordoc`으로 실행되므로 별도 전역 설치는 필수는 아닙니다.

```bash
node --version
npx --version
python3 --version
```

Excel 파일 생성을 위해 `openpyxl`이 있으면 좋습니다.

```bash
python3 -m pip install openpyxl
```

없어도 CSV/JSON 산출물은 생성됩니다.

## 실행

```bash
python3 scripts/kordoc_pipeline.py ./input_hwp -o ./output
```

단일 파일도 가능합니다.

```bash
python3 scripts/kordoc_pipeline.py ./input_hwp/사업계획서.hwp -o ./output
```

페이지 범위를 제한하려면:

```bash
python3 scripts/kordoc_pipeline.py ./input_hwp -o ./output --pages 1-3
```

## 산출물 구조

```txt
output/
  문서ID/
    document.md          # 에이전트가 읽기 좋은 Markdown
    parse_result.json    # kordoc 원본 JSON 결과
    blocks.json          # IR blocks
    metadata.json
    warnings.json
    tables.json          # 표 구조 JSON
    table_01.csv         # 개별 표 CSV
    tables_long.csv      # Excel 친화 long-format 표 데이터
    facts.json           # 수치/날짜/key-value 후보
    facts.csv
    document_data.xlsx   # 문서별 Excel
  _aggregate/
    facts_all.json
    facts_all.csv
    tables_all.csv
    errors.json
    all_documents.xlsx   # 전체 문서 통합 Excel
```

## 현재 facts 추출 방식

초기 프로토타입은 LLM 없이 규칙 기반으로 다음 후보를 뽑습니다.

- 2열 key-value 표: `항목 | 내용`, `구분 | 값` 형태
- 금액: `원`, `천원`, `만원`, `백만원`, `천만원`, `억원`
- 퍼센트: `12.3%`
- 날짜: `2026.01.01`, `2026년 1월 1일` 등
- 전화번호, 이메일
- 수량 단위: `명`, `건`, `개`, `회`, `개월`, `년` 등

다음 단계에서는 문서 유형별 스키마와 LLM 추출을 붙여 `facts.json` 품질을 높이면 됩니다.

## 웹앱 (업로드 → Codex 분석 리포트 / 취합 문서)

담당자가 문서를 업로드하고 요청을 적으면 codex 가 분석 리포트 또는 취합 문서를 만들어 보여줍니다.

준비:

```bash
python3 -m pip install -r requirements.txt   # fastapi, uvicorn, python-multipart
codex login                                  # codex 인증(최초 1회)
```

실행:

```bash
python3 -m uvicorn web.app:app --reload --port 8000
```

브라우저에서 `http://127.0.0.1:8000` 접속 후:

1. **파일 선택** — HWP/HWPX/PDF/XLS/XLSX/DOCX 등 여러 파일을 동시에 선택할 수 있습니다.
2. **산출물 종류 선택** — 제출 전 원하는 산출물을 고릅니다.
   - **분석 리포트**: 업로드된 문서를 codex 가 분석해 리포트를 생성합니다.
   - **취합 문서**: 여러 문서의 내용을 하나의 새 문서로 취합합니다.
3. **결과 확인** — 완료되면 HTML 미리보기가 바로 표시되며, **한글 파일(.hwpx) 다운로드** 버튼으로 로컬에 저장할 수 있습니다. HWPX 생성이 실패하더라도 리포트 내용은 화면에서 계속 확인할 수 있습니다.

산출물은 `jobs/<job_id>/`(converted/, report.md, codex_log.jsonl, status.json)에 저장됩니다.

동시 실행 잡 수는 환경변수 `REPORT_BOT_MAX_CONCURRENCY`로 정합니다(기본 3). 초과분은 대기열에서 차례를 기다립니다.

테스트:

```bash
python3 -m pytest -q
```
