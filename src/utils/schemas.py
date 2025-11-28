# src/utils/schemas.py
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from datetime import datetime

class TaskSchema(BaseModel):
    id: str
    type: str
    target: str
    scope: str
    priority: int
    expected_schema: Dict[str, Any]

class PlanOutput(BaseModel):
    query: str
    generated_at: datetime
    tasks: List[TaskSchema]

class Hypothesis(BaseModel):
    id: str
    hypothesis: str
    driver: Optional[str]
    initial_confidence: float = Field(ge=0.0, le=1.0)
    supporting_data_points: List[str] = []
    required_checks: List[str] = []

class ValidationResult(BaseModel):
    hypothesis_id: str
    validation: Dict[str, Any]
    confidence_final: float = Field(ge=0.0, le=1.0)
    status: str
    notes: Optional[str]
    evidence_refs: List[str] = []

class CreativeCandidate(BaseModel):
    campaign: str
    creative_id: str
    creative_type: str
    headline: str
    body: str
    cta: str
    rationale: Optional[str]
    inspiration_refs: List[str] = []
