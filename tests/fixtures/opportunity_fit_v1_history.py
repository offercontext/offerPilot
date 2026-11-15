"""Fixed, read-only-era history seeded without restoring a production v1 writer."""
from datetime import datetime
import json
from pathlib import Path

from offerpilot.models import OpportunityFitReview


def seed_v1_history(factory, application_id: int, resume_id: int) -> OpportunityFitReview:
    values = json.loads(Path(__file__).with_suffix(".json").read_text())
    values.update(application_id=application_id, resume_id=resume_id)
    for key in ("created_at", "deep_reviewed_at"):
        values[key] = datetime.fromisoformat(values[key])
    with factory() as session:
        row = OpportunityFitReview(**values)
        session.add(row)
        session.commit()
        session.refresh(row)
        session.expunge(row)
        return row
