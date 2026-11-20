from pydantic import BaseModel, Field

NOTICE = "Research use only. Not a medical device and not a diagnosis."


class PredictResponse(BaseModel):
    label: int = Field(description="Class index of the top prediction (0-8).")
    tissue: str = Field(description="Tissue code of the top prediction, e.g. TUM.")
    confidence: float = Field(
        description="Top softmax probability. An uncalibrated model score, not the chance of being correct."
    )
    uncertain: bool = Field(description="True when confidence is below the configured threshold.")
    probabilities: dict[str, float] = Field(description="Softmax probability for each tissue code.")
    warnings: list[str] = Field(description="Input quirks that may reduce reliability.")
    model_version: str
    notice: str = NOTICE
