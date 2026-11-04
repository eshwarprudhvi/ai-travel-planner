from datetime import date
from typing import Optional
from pydantic import BaseModel, Field, model_validator


class TripCreate(BaseModel):
    origin: str = Field(min_length=2, max_length=100)
    destination: str = Field(min_length=2, max_length=100)
    start_date: date
    end_date: date
    budget: float = Field(gt=0)
    travelers: int = Field(default=1, ge=1)
    preferences: Optional[str] = None

    @model_validator(mode="after")
    def validate_dates(self):
        if self.end_date <= self.start_date:
            raise ValueError("End date must be after start date")
        if self.start_date < date.today():
            raise ValueError("Start date cannot be in the past")
        return self


class TripResponse(BaseModel):
    trip_id: str
    message: str
    trip: dict
