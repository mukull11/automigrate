"""Ingestion of repository structure and dependency declarations."""

from automigrate.ingest.analyzer import find_usages
from automigrate.ingest.dependencies import (
    DependencyDeclaration,
    detect_declarations,
    find_declaration,
    import_module_roots,
    normalize_name,
)
from automigrate.ingest.scanner import ScanResult, find_python_files, scan_repository

__all__ = [
    "DependencyDeclaration",
    "ScanResult",
    "detect_declarations",
    "find_declaration",
    "find_python_files",
    "find_usages",
    "import_module_roots",
    "normalize_name",
    "scan_repository",
]
