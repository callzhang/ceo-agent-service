from pydantic import BaseModel, Field


class OkrEvidence(BaseModel):
    source: str = Field(min_length=1)
    summary: str = Field(min_length=1)


class OkrReviewItem(BaseModel):
    objective_title: str
    objective_weight: float = Field(ge=0, le=1)
    kr_title: str
    kr_weight: float = Field(ge=0, le=1)
    self_progress: str
    kr_progress_update: str
    claim_text: str
    claim_completion_time: str
    deadline: str
    claim_base_score: float = Field(ge=0, le=100)
    claim_discount_factor: float = Field(ge=0.3, le=1.0)
    claim_discount_reason: str = Field(min_length=1)
    claim_score: float = Field(ge=0, le=100)
    verified_completion_time: str
    verified_base_score: float = Field(ge=0, le=100)
    verified_discount_factor: float = Field(ge=0.3, le=1.0)
    verified_discount_reason: str = Field(min_length=1)
    verified_score: float = Field(ge=0, le=100)
    evidence_used: list[OkrEvidence]
    evidence_gap: str
    review_comment: str
    suggested_follow_up: str


class OkrReviewPayload(BaseModel):
    person_name: str
    period_label: str
    summary: str = Field(min_length=1)
    items: list[OkrReviewItem] = Field(min_length=1)
