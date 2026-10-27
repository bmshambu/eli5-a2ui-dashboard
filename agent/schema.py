"""Structured output the model writes around the computed facts (analysis.Fact)."""
from typing import Literal

from pydantic import BaseModel, Field

KINDS = ("trend", "driver", "risk", "opportunity", "anomaly", "pattern")


class Insight(BaseModel):
    kind: Literal["trend", "driver", "risk", "opportunity", "anomaly", "pattern"]
    title: str = Field(description="The point in at most 8 words, plain text")
    text: str = Field(description="1 or 2 sentences: the finding and what it means for the business. Every number copied exactly from the facts")
    evidence: list[str] = Field(description="Ids of the facts this insight rests on, e.g. ['F3', 'F12']")


class Narrative(BaseModel):
    headline: str = Field(description="The single most decision-relevant finding, one sentence under 110 characters")
    summary: str = Field(description="2 or 3 sentences that set the scene: overall picture, main driver, main watch-out")
    insights: list[Insight] = Field(description="3 to 5 insights of different kinds, most important first")
    actions: list[str] = Field(description="2 or 3 concrete next steps or checks, each one sentence")
    questions: list[str] = Field(description="3 follow-up questions the user could ask about this data, each under 70 characters")
    caveats: list[str] = Field(default_factory=list, description="Data-quality points that limit the conclusions, from the quality facts")
