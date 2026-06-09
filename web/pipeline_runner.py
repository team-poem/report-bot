"""기존 kordoc 변환 파이프라인(scripts/kordoc_pipeline.py) 래퍼."""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import kordoc_pipeline  # noqa: E402


class PipelineError(RuntimeError):
    pass


def _doc_id(upload_path: Path) -> str:
    # safe_doc_id(path, root): root.is_file() 이면 path.stem 기반 id 를 만든다.
    return kordoc_pipeline.safe_doc_id(upload_path, upload_path)


def convert(upload_path: Path, converted_root: Path) -> Path:
    """업로드 파일을 변환하고 변환 결과 폴더(converted_root/<docid>)를 돌려준다."""
    upload_path = Path(upload_path)
    converted_root = Path(converted_root)
    converted_root.mkdir(parents=True, exist_ok=True)

    kordoc_pipeline.process_file(upload_path, upload_path, converted_root, None)

    doc_dir = converted_root / _doc_id(upload_path)
    if not (doc_dir / "document.md").exists():
        raise PipelineError(
            f"변환 실패: {upload_path.name} 에서 document.md 를 만들지 못했습니다."
        )
    return doc_dir
