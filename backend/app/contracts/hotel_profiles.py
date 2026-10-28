"""Auditable synthetic-review profile returned in a hotel search result."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.contracts.identifiers import canonical_hotel_id

Aspect = Literal["children", "noise", "cleanliness", "food", "staff", "location", "wifi", "pets"]
Segment = Literal["family", "couple", "business", "pets"]


class ProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProfileEvidence(ProfileModel):
    review_id: str = Field(min_length=1)
    sentiment: Literal["positive", "negative"]
    quote: str = Field(min_length=1)


class AspectSummary(ProfileModel):
    positive_count: int = Field(strict=True, ge=0)
    negative_count: int = Field(strict=True, ge=0)
    mention_count: int = Field(strict=True, ge=0)
    review_count: int = Field(strict=True, ge=0)
    evidence: list[ProfileEvidence]

    @model_validator(mode="after")
    def counts_match_evidence(self):
        identities = {(e.review_id, e.sentiment, e.quote) for e in self.evidence}
        if len(identities) != len(self.evidence):
            raise ValueError("Duplicate profile evidence")
        if self.mention_count != len(self.evidence) or self.review_count != len({e.review_id for e in self.evidence}):
            raise ValueError("Profile counts disagree with evidence")
        if self.positive_count != sum(e.sentiment == "positive" for e in self.evidence):
            raise ValueError("Positive count disagrees with evidence")
        if self.negative_count != sum(e.sentiment == "negative" for e in self.evidence):
            raise ValueError("Negative count disagrees with evidence")
        return self


class AspectCount(ProfileModel):
    aspect: Aspect
    count: int = Field(strict=True, gt=0)


class GuestProfile(ProfileModel):
    review_count: int = Field(strict=True, ge=0)
    aspects: dict[Aspect, AspectSummary]
    pros: list[AspectCount]
    cons: list[AspectCount]

    @model_validator(mode="after")
    def summaries_match(self):
        for summaries, field in ((self.pros, "positive_count"), (self.cons, "negative_count")):
            expected = {a: getattr(row, field) for a, row in self.aspects.items() if getattr(row, field)}
            if len(summaries) != len(expected) or {v.aspect: v.count for v in summaries} != expected:
                raise ValueError("Guest pros/cons disagree with evidence")
        if any(row.review_count > self.review_count for row in self.aspects.values()):
            raise ValueError("Aspect refers to more reviews than the guest segment contains")
        return self


class ExtractionMetadata(ProfileModel):
    method: Literal["lexical-fixture-baseline-v1", "llm-literal-evidence-v1"]
    model: str | None
    live_model_run: bool

    @model_validator(mode="after")
    def provenance_is_consistent(self):
        if self.method == "lexical-fixture-baseline-v1" and (self.model is not None or self.live_model_run):
            raise ValueError("Lexical baseline is not a live model run")
        if self.method == "llm-literal-evidence-v1" and not self.model:
            raise ValueError("LLM extraction needs a model identifier")
        return self


class HotelReviewProfile(ProfileModel):
    schema_version: Literal[1]
    hotel_id: str
    is_synthetic: Literal[True]
    notice: str = Field(min_length=1)
    review_count: int = Field(strict=True, ge=20, le=50)
    corpus_generator: str = Field(min_length=1)
    corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    extraction: ExtractionMetadata
    aspects: dict[Aspect, AspectSummary]
    guest_profiles: dict[Segment, GuestProfile]

    @field_validator("hotel_id")
    @classmethod
    def canonical_identity(cls, value):
        return canonical_hotel_id(value)

    @model_validator(mode="after")
    def segments_match_totals(self):
        if set(self.guest_profiles) != {"family", "couple", "business", "pets"}:
            raise ValueError("All four guest segments must be explicit")
        if sum(g.review_count for g in self.guest_profiles.values()) != self.review_count:
            raise ValueError("Guest counts disagree with profile total")
        combined = {}
        for segment, guest in self.guest_profiles.items():
            for aspect, row in guest.aspects.items():
                for evidence in row.evidence:
                    prefix = f"synthetic-review/{self.hotel_id}/{segment}/"
                    if not evidence.review_id.startswith(prefix):
                        raise ValueError("Evidence belongs to another hotel or segment")
                    combined.setdefault(aspect, []).append(evidence.model_dump())
        if set(combined) != set(self.aspects):
            raise ValueError("Guest aspects disagree with profile aspects")
        for aspect, rows in combined.items():
            actual = [e.model_dump() for e in self.aspects[aspect].evidence]

            def key(e):
                return (e["review_id"], e["sentiment"], e["quote"])

            if sorted(actual, key=key) != sorted(rows, key=key):
                raise ValueError("Guest evidence disagrees with profile evidence")
        return self
