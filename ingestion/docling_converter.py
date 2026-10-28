"""Docling-based document conversion."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import pickle
import tempfile
import threading
import time
from pathlib import Path

from ingestion.normalization import (
    DocumentElement,
    DocumentMetadata,
    NormalizedDocument,
    compute_content_hash,
)
from ingestion.validators import get_extension

logger = logging.getLogger(__name__)

# Cache for conversions (in-memory + optional disk cache)
_CONVERSION_CACHE: dict[str, NormalizedDocument] = {}
_CACHE_LOCK = threading.RLock()
_DISK_CACHE_DIR = Path(os.getenv("NEBULA_CACHE_DIR") or os.getenv("ASKBUDDY_CACHE_DIR") or "./.cache/nebula")
_CACHE_ENABLED = os.getenv("NEBULA_INGESTION_CACHE", "true").lower() in {"1", "true", "yes", "on"}
_CACHE_VERSION = "v1"


class DoclingConverter:
    _shared_converter = None
    _converter_lock = threading.Lock()
    
    def __init__(self, enable_ocr: bool = True):
        self.enable_ocr = enable_ocr
        self._converter = None

    def warm(self) -> None:
        """Initialize the converter (and its models) without converting anything."""
        self._get_converter()
    
    def _get_converter(self):
        if self._converter is not None:
            return self._converter
        with self._converter_lock:
            if DoclingConverter._shared_converter is not None:
                self._converter = DoclingConverter._shared_converter
                return self._converter
            try:
                from docling.datamodel.base_models import InputFormat
                from docling.datamodel.pipeline_options import PdfPipelineOptions
                from docling.document_converter import DocumentConverter, PdfFormatOption

                pipeline_options = PdfPipelineOptions()
                pipeline_options.do_ocr = self.enable_ocr
                try:
                    from docling.datamodel.pipeline_options import RapidOcrOptions
                    pipeline_options.ocr_options = RapidOcrOptions()
                except Exception:
                    pass

                # These options must be handed to the converter: building a
                # DocumentConverter() without them silently discarded the OCR
                # configuration and ran the defaults instead.
                self._converter = DocumentConverter(
                    format_options={
                        InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
                    }
                )
                DoclingConverter._shared_converter = self._converter
                logger.info("Docling converter initialized (shared)")
            except Exception as e:
                logger.warning(f"Failed to initialize Docling: {e}")
                raise
        return self._converter
    
    def _cache_key(self, content_hash: str, ext: str) -> str:
        key_str = f"{_CACHE_VERSION}|{content_hash}|{ext}|ocr:{self.enable_ocr}|docling"
        return hashlib.sha256(key_str.encode()).hexdigest()
    
    def _disk_cache_path(self, cache_key: str) -> Path:
        _DISK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        return _DISK_CACHE_DIR / f"{cache_key}.pkl"
    
    def _load_disk_cache(self, cache_key: str) -> NormalizedDocument | None:
        if not _CACHE_ENABLED:
            return None
        try:
            path = self._disk_cache_path(cache_key)
            if not path.exists():
                return None
            with open(path, 'rb') as f:
                cached = pickle.load(f)
            if isinstance(cached, NormalizedDocument):
                return cached
        except Exception as e:
            logger.debug(f"Disk cache load failed: {e}")
        return None
    
    def _save_disk_cache(self, cache_key: str, normalized: NormalizedDocument) -> None:
        if not _CACHE_ENABLED:
            return
        try:
            path = self._disk_cache_path(cache_key)
            fd, tmp_path = tempfile.mkstemp(prefix=".tmp_cache_", dir=str(_DISK_CACHE_DIR))
            os.close(fd)
            try:
                with open(tmp_path, 'wb') as f:
                    pickle.dump(normalized, f)
                os.replace(tmp_path, path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Disk cache save failed: {e}")
    
    @staticmethod
    def _normalize_columns(df):
        try:
            cols = list(df.columns)
            seen = {}
            new_cols = []
            for c in cols:
                name = str(c).strip() if c is not None else ""
                if name == "" or name.lower() == "nan":
                    name = "Column"
                if name in seen:
                    seen[name] += 1
                    new_cols.append(f"{name}_{seen[name]}")
                else:
                    seen[name] = 1
                    new_cols.append(name)
            df = df.copy()
            df.columns = new_cols
            return df
        except Exception:
            return df
    
    def convert(self, filename: str, content: bytes, document_id: str, **kwargs) -> NormalizedDocument:
        ext = get_extension(filename)
        content_hash = compute_content_hash(content)
        
        cache_key = self._cache_key(content_hash, ext)
        with _CACHE_LOCK:
            if cache_key in _CONVERSION_CACHE:
                cached = _CONVERSION_CACHE[cache_key]
                cached.metadata.document_id = document_id
                cached.metadata.filename = filename
                logger.info(f"Docling cache hit (memory) for {filename}")
                return cached
        
        disk_cached = self._load_disk_cache(cache_key)
        if disk_cached is not None:
            with _CACHE_LOCK:
                _CONVERSION_CACHE[cache_key] = disk_cached
            disk_cached.metadata.document_id = document_id
            disk_cached.metadata.filename = filename
            logger.info(f"Docling cache hit (disk) for {filename}")
            return disk_cached
        
        start_time = time.time()
        try:
            converter = self._get_converter()
            from docling.datamodel.base_models import DocumentStream
            
            stream = DocumentStream(name=filename, stream=io.BytesIO(content))
            result = converter.convert(stream)
            doc = result.document
            
            try:
                markdown = doc.export_to_markdown()
            except Exception:
                markdown = None
            
            elements = []
            page_count = None
            try:
                page_count = len(doc.pages) if hasattr(doc, 'pages') else None
            except Exception:
                pass
            
            elem_idx = 0
            try:
                if hasattr(doc, 'texts'):
                    for item in doc.texts:
                        text = getattr(item, 'text', '') or ''
                        if not text.strip():
                            continue
                        label = getattr(item, 'label', None)
                        element_type = str(label).split('.')[-1].lower() if label else 'paragraph'
                        prov = getattr(item, 'prov', [])
                        page_num = None
                        if prov:
                            try:
                                page_num = prov[0].page_no
                            except Exception:
                                pass
                        elements.append(DocumentElement(
                            element_id=f"elem_{elem_idx}",
                            text=text,
                            element_type=element_type,
                            page_number=page_num,
                            section=getattr(item, 'parent', None) and str(getattr(item.parent, 'label', '')) or None,
                        ))
                        elem_idx += 1
            except Exception as e:
                logger.debug(f"Could not extract elements from doc.texts: {e}")
            
            tables = []
            try:
                if hasattr(doc, 'tables'):
                    for table in doc.tables:
                        try:
                            table_df = table.export_to_dataframe(doc=doc)
                        except Exception:
                            try:
                                table_df = table.export_to_dataframe()
                            except Exception:
                                continue
                        try:
                            table_df = self._normalize_columns(table_df)
                        except Exception:
                            pass
                        try:
                            table_md = table.export_to_markdown(doc=doc)
                        except Exception:
                            try:
                                table_md = table.export_to_markdown()
                            except Exception:
                                table_md = None
                        try:
                            tables.append({
                                'data': table_df.to_dict(),
                                'markdown': table_md
                            })
                        except Exception:
                            pass
            except Exception as e:
                logger.debug(f"Could not extract tables: {e}")
            
            if not elements and markdown:
                elements.append(DocumentElement(
                    element_id="elem_0",
                    text=markdown,
                    element_type="document",
                    page_number=1,
                ))
            
            metadata = DocumentMetadata(
                document_id=document_id,
                filename=filename,
                file_type=ext,
                content_hash=content_hash,
                page_count=page_count,
            )
            
            full_text = "\n\n".join(e.text for e in elements if e.text.strip())
            if not full_text and markdown:
                full_text = markdown
            
            normalized = NormalizedDocument(
                metadata=metadata,
                elements=elements,
                text=full_text,
                markdown=markdown,
                tables=tables,
                provenance=[{"page": e.page_number, "type": e.element_type} for e in elements if e.page_number]
            )
            
            elapsed = time.time() - start_time
            logger.info(f"Docling converted {filename} in {elapsed:.2f}s, {len(elements)} elements")
            
            with _CACHE_LOCK:
                _CONVERSION_CACHE[cache_key] = normalized
            self._save_disk_cache(cache_key, normalized)
            return normalized
            
        except Exception as e:
            logger.error(f"Docling conversion failed for {filename}: {e}")
            raise


def clear_cache():
    global _CONVERSION_CACHE
    with _CACHE_LOCK:
        _CONVERSION_CACHE = {}
    try:
        if _DISK_CACHE_DIR.exists():
            for p in _DISK_CACHE_DIR.glob("*.pkl"):
                try:
                    p.unlink()
                except Exception:
                    pass
    except Exception:
        pass

