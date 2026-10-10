from typing import List
from pydantic import BaseModel, Field

# Free slot per day
class FreeSlot(BaseModel):
    start_time: str = Field(description="Start time of the free slot")
    end_time: str = Field(description="End time of the free slot")
    duration_minutes: int = Field(description="Duration of the free slot in minutes")
    slot_type: str = Field(description="Type of the free slot")


# Day schedule summary of the weekday
class DayScheduleSummary(BaseModel):
    date: str = Field(description="Date of the schedule")
    weekday: str = Field(description="Weekday of the schedule")
    is_holiday: bool = Field(description="Is the day a holiday?")
    holiday_reason: str = Field(description="Reason for the holiday")
    classes: List[dict] = Field(description="Classes scheduled for the day")
    free_slots: List[FreeSlot] = Field(description="Free slots for the day")
    total_free_minutes: int = Field(description="Total free minutes in the day")
