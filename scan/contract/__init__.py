"""C13 Output contract: the published JSON schema and the builder that fills it."""

from scan.contract.build import build_result, measure, to_json
from scan.contract.schema import SCHEMA_VERSION, Result, json_schema

__all__ = ["SCHEMA_VERSION", "Result", "build_result", "json_schema", "measure", "to_json"]
