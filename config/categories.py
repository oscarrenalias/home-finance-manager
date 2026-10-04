"""Category taxonomy loader — reads config/categories.yaml and validates via Pydantic."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, field_validator

_YAML_PATH = Path(__file__).parent / "categories.yaml"


class Category(BaseModel):
    id: str
    name: str
    guidance: str
    examples: list[str]
    parent: Optional[str] = None

    @field_validator("id")
    @classmethod
    def id_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("category id must not be empty")
        return v

    @field_validator("guidance")
    @classmethod
    def guidance_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("guidance must not be empty")
        return v

    @field_validator("examples")
    @classmethod
    def examples_min_length(cls, v: list[str]) -> list[str]:
        if len(v) < 2:
            raise ValueError("examples must contain at least 2 entries")
        return v


def load_categories(path: Path = _YAML_PATH) -> list[Category]:
    """Load and validate the category taxonomy from a YAML file."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [Category.model_validate(item) for item in raw["categories"]]


# Module-level singleton — loaded once on first import.
CATEGORIES: list[Category] = load_categories()
CATEGORY_MAP: dict[str, Category] = {c.id: c for c in CATEGORIES}
