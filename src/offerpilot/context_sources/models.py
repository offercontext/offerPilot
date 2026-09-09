from sqlalchemy import Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from offerpilot.models import Base
from .readiness import ConversationReadinessContext  # noqa: F401 - register readiness context
from .summary import ConversationSummary  # noqa: F401 - register the summary model


class ContextContributorSettings(Base):
    __tablename__ = "context_contributor_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    settings_json: Mapped[str] = mapped_column(Text, nullable=False)
