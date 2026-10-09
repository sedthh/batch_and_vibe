"""Selected-creature leash context, separate from permission to run inference."""
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class LeashContext:
    active: bool | None = None
    variant: Literal["learning", "compassion", "aggression"] | None = None
    attachment: Literal["unknown", "hand", "object", "none"] = "unknown"
    target_id: str | None = None
    target_kind: str | None = None

    def __post_init__(self):
        if self.active is not None and type(self.active) is not bool:
            raise ValueError("Invalid leash activity")
        if self.variant not in (None, "learning", "compassion", "aggression"):
            raise ValueError("Invalid leash variant")
        if self.attachment not in ("unknown", "hand", "object", "none"):
            raise ValueError("Invalid leash attachment")
        if self.active is not True:
            if self.variant is not None or self.target_id is not None or self.target_kind is not None:
                raise ValueError("Inactive or unknown leash cannot describe a variant or target")
            if self.attachment != ("none" if self.active is False else "unknown"):
                raise ValueError("Leash activity and attachment disagree")
        elif self.attachment == "none":
            raise ValueError("Active leash cannot have an absent attachment")
        for value in (self.target_id, self.target_kind):
            if value is not None and (not isinstance(value, str) or not 0 < len(value) <= 96):
                raise ValueError("Invalid leash target")
        if (self.target_id is not None or self.target_kind is not None) and self.attachment != "object":
            raise ValueError("Only object attachments describe an object target")
