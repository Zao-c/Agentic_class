import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.schemas import AlarmCodeRecord, Citation
from app.storage import Store


BRAND_ALIASES = {
    "发那科": "FANUC",
    "库卡": "KUKA",
    "安川": "YASKAWA",
}


def normalize_code(value: str) -> str:
    return re.sub(r"[\s:#：]", "", value).upper()


def normalize_brand(value: Optional[str]) -> str:
    compact = (value or "").strip().upper()
    return BRAND_ALIASES.get(compact, compact)


def normalize_model(value: Optional[str]) -> str:
    return re.sub(r"[^A-Z0-9*]", "", (value or "").upper())


def split_equipment(value: str) -> Tuple[str, str]:
    upper = value.upper().strip()
    brand = next(
        (candidate for candidate in ("ABB", "FANUC", "KUKA", "YASKAWA", "埃斯顿", "汇川", "新松") if candidate in upper),
        upper.split(" ", 1)[0] if upper else "",
    )
    remainder = upper.replace(brand, "", 1).strip()
    return normalize_brand(brand), normalize_model(remainder)


class AlarmCodeService:
    def __init__(self, store: Store):
        self.store = store

    @staticmethod
    def _canonical(record: AlarmCodeRecord) -> Dict[str, Any]:
        item = record.model_dump(mode="json")
        item["equipment_brand"] = normalize_brand(record.equipment_brand)
        item["equipment_models"] = [normalize_model(model) for model in record.equipment_models]
        item["controller_versions"] = [value.strip() for value in record.controller_versions]
        item["code"] = normalize_code(record.code)
        payload = json.dumps(
            {key: value for key, value in item.items() if key != "alarm_id"},
            ensure_ascii=False,
            sort_keys=True,
        )
        content_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        item["alarm_id"] = record.alarm_id or "alarm_" + content_hash[:24]
        item["content_hash"] = content_hash
        return item

    def import_records(self, records: Iterable[AlarmCodeRecord]) -> Dict[str, int]:
        return self.store.upsert_alarm_codes([self._canonical(record) for record in records])

    def import_file(self, path: Path) -> Dict[str, int]:
        path = Path(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_records = payload["records"] if isinstance(payload, dict) else payload
        return self.import_records(AlarmCodeRecord.model_validate(item) for item in raw_records)

    def lookup(
        self,
        code: str,
        equipment_brand: Optional[str] = None,
        equipment_model: Optional[str] = None,
        controller_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_code = normalize_code(code)
        brand = normalize_brand(equipment_brand)
        model = normalize_model(equipment_model)
        records = self.store.alarm_codes_by_code(normalized_code)
        if not records:
            return self._result("not_found", normalized_code, brand, model, [], [])

        brand_matches = [
            record for record in records if not brand or record["equipment_brand"] == brand
        ]
        if not brand_matches:
            return self._result(
                "brand_mismatch", normalized_code, brand, model, [], self._scopes(records)
            )

        exact_model = [
            record
            for record in brand_matches
            if model and model in {normalize_model(value) for value in record["equipment_models"]}
        ]
        wildcard_model = [
            record
            for record in brand_matches
            if "*" in {normalize_model(value) for value in record["equipment_models"]}
        ]
        if exact_model:
            selected = exact_model
            status = "exact_match"
        elif wildcard_model:
            selected = wildcard_model
            status = "brand_match_model_unverified"
        elif model:
            return self._result(
                "model_mismatch", normalized_code, brand, model, [], self._scopes(brand_matches)
            )
        else:
            selected = brand_matches
            status = "model_required" if len(self._scopes(selected)) > 1 else "brand_match_model_unverified"

        if controller_version:
            version_matches = [
                record
                for record in selected
                if not record["controller_versions"]
                or controller_version in record["controller_versions"]
            ]
            if version_matches:
                selected = version_matches

        meanings = {record["meaning"].strip() for record in selected}
        if len(meanings) > 1:
            status = "ambiguous"
        return self._result(status, normalized_code, brand, model, selected, self._scopes(records))

    @staticmethod
    def _scopes(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            {
                "equipment_brand": record["equipment_brand"],
                "equipment_models": record["equipment_models"],
                "controller_versions": record["controller_versions"],
            }
            for record in records
        ]

    @staticmethod
    def _result(
        status: str,
        code: str,
        brand: str,
        model: str,
        matches: List[Dict[str, Any]],
        available_scopes: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return {
            "status": status,
            "code": code,
            "equipment_brand": brand,
            "equipment_model": model,
            "matches": matches,
            "available_scopes": available_scopes,
        }

    @staticmethod
    def citation(record: Dict[str, Any], lookup_status: str) -> Citation:
        parts = [record["title"], record["meaning"]]
        if record["likely_causes"]:
            parts.append("可能原因：" + "；".join(record["likely_causes"]))
        if record["safe_checks"]:
            parts.append("安全核对：" + "；".join(record["safe_checks"]))
        return Citation(
            document_id="structured_alarm:" + record["alarm_id"],
            chunk_id=record["alarm_id"],
            title=record["source_title"],
            document_type="structured_alarm_code",
            chapter=record.get("source_locator"),
            excerpt="\n".join(parts)[:700],
            score=1.0,
            retrieval_method="structured_alarm_lookup",
            score_components={
                "exact_code": 1.0,
                "brand_match": 1.0,
                "model_verified": 1.0 if lookup_status == "exact_match" else 0.0,
                "source_verified": 1.0 if record["review_status"] != "draft" else 0.0,
            },
        )
