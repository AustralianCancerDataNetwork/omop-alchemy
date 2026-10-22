from __future__ import annotations
from importlib.resources import files
from importlib.resources.abc import Traversable

CDM_VERSION: str = "5.4"

_SPEC_ROOT: Traversable = files("omop_alchemy.cdm.specification")

TABLE_LEVEL_CSV: Traversable = _SPEC_ROOT / "OMOP_CDMv5.4_Table_Level.csv"
FIELD_LEVEL_CSV: Traversable = _SPEC_ROOT / "OMOP_CDMv5.4_Field_Level.csv"