"""CSV ingestion pipeline.

Read a retailer CSV → drop commercial columns → normalise units →
produce a `ValidationReport` and a tuple of `NormalizedProduct`s.

No methodology classification is performed here. Classification is a
later phase. Ingestion only resolves *form* — units, types, required
fields, commercial-column drops.
"""

from altera_api.ingestion.column_filter import (
    FORBIDDEN_COLUMN_PATTERNS,
    filter_commercial_columns,
)
from altera_api.ingestion.csv_reader import (
    CSVReadConfig,
    CSVReadError,
    read_table_bytes,
)
from altera_api.ingestion.headers import normalise_header, normalise_row_headers
from altera_api.ingestion.normalizer import normalize_product
from altera_api.ingestion.parser import parse_row
from altera_api.ingestion.pipeline import IngestResult, ingest_csv_bytes
from altera_api.ingestion.units import (
    G_TO_KG,
    LB_TO_KG,
    OZ_TO_KG,
    normalise_protein_pct,
    normalise_weight_kg,
)

__all__ = [
    "CSVReadConfig",
    "CSVReadError",
    "FORBIDDEN_COLUMN_PATTERNS",
    "G_TO_KG",
    "IngestResult",
    "LB_TO_KG",
    "OZ_TO_KG",
    "filter_commercial_columns",
    "ingest_csv_bytes",
    "normalise_header",
    "normalise_protein_pct",
    "normalise_row_headers",
    "normalise_weight_kg",
    "normalize_product",
    "parse_row",
    "read_table_bytes",
]
