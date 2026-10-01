"""Typed constraint IR. The LLM (or rule parser) may only emit this closed schema -
it can never inject free-form math into the solver."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Kind = Literal["product_spec", "product_demand", "unit_capacity", "unit_status", "crude_limit"]


class ConstraintIR(BaseModel):
    kind: Kind
    entity: str = Field(description="Canonical product / unit / crude name from the registry")
    prop: Optional[Literal["sulfur", "ron"]] = Field(None, description="Quality property for product_spec")
    sense: Optional[Literal["<=", ">=", "=="]] = None
    value: Optional[float] = None
    unit: Optional[str] = Field(None, description="wt% | ppm | RON | kbbl/d | parcels")
    status: Optional[Literal["on", "off"]] = Field(None, description="Only for unit_status")

    def key(self) -> tuple:
        return (self.kind, self.entity, self.prop, self.sense, self.status,
                None if self.value is None else round(self.value, 9), self.unit)


class LLMParse(BaseModel):
    """What a language model must return (structured output)."""
    ir: Optional[ConstraintIR] = Field(None, description="null if the text is not a single supported constraint")
    restatement: str = Field(description="Plain-English restatement of what was understood")
    confidence: float = Field(ge=0.0, le=1.0)
    ambiguities: list[str] = Field(default_factory=list)
