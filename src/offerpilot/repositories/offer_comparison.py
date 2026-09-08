from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.models import Offer, OfferComparisonDimension, OfferComparisonValue


@dataclass
class OfferComparisonError(Exception):
    code: str
    message: str
    status_code: int


class OfferComparisonRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    def list_dimensions(self, *, active_only: bool = True) -> list[OfferComparisonDimension]:
        statement = select(OfferComparisonDimension).order_by(OfferComparisonDimension.id.asc())
        if active_only:
            statement = statement.where(OfferComparisonDimension.archived_at.is_(None))
        with self._session_factory() as session:
            return list(session.scalars(statement))

    def get_dimension(self, dimension_id: int) -> Optional[OfferComparisonDimension]:
        with self._session_factory() as session:
            return session.get(OfferComparisonDimension, dimension_id)

    def create_dimension(self, label: str) -> OfferComparisonDimension:
        with self._session_factory() as session:
            dimension = OfferComparisonDimension(label=label)
            session.add(dimension)
            session.commit()
            session.refresh(dimension)
            return dimension

    def update_dimension(
        self,
        dimension_id: int,
        *,
        label: str | None = None,
        archived: bool | None = None,
    ) -> Optional[OfferComparisonDimension]:
        with self._session_factory() as session:
            dimension = session.get(OfferComparisonDimension, dimension_id)
            if dimension is None:
                return None
            if label is not None:
                dimension.label = label
            if archived is not None:
                dimension.archived_at = datetime.now(timezone.utc) if archived else None
            session.commit()
            session.refresh(dimension)
            return dimension

    def _get_visible_offer(self, session: Session, offer_id: int) -> Offer:
        offer = session.get(Offer, offer_id)
        if offer is None:
            raise OfferComparisonError(
                "offer_comparison_offer_not_found", "offer not found", 404
            )
        return offer

    def _get_active_dimension(self, session: Session, dimension_id: int) -> OfferComparisonDimension:
        dimension = session.get(OfferComparisonDimension, dimension_id)
        if dimension is None or dimension.archived_at is not None:
            raise OfferComparisonError(
                "offer_comparison_dimension_not_found", "comparison dimension not found", 404
            )
        return dimension

    def get_values(self, offer_id: int) -> list[OfferComparisonValue]:
        with self._session_factory() as session:
            self._get_visible_offer(session, offer_id)
            statement = select(OfferComparisonValue).where(
                OfferComparisonValue.offer_id == offer_id
            ).order_by(OfferComparisonValue.dimension_id.asc())
            return list(session.scalars(statement))

    def upsert_value(self, offer_id: int, dimension_id: int, value_text: str) -> OfferComparisonValue:
        with self._session_factory() as session:
            self._get_visible_offer(session, offer_id)
            self._get_active_dimension(session, dimension_id)
            value = session.scalar(
                select(OfferComparisonValue).where(
                    OfferComparisonValue.offer_id == offer_id,
                    OfferComparisonValue.dimension_id == dimension_id,
                )
            )
            if value is None:
                value = OfferComparisonValue(
                    offer_id=offer_id,
                    dimension_id=dimension_id,
                    value_text=value_text,
                )
                session.add(value)
            else:
                value.value_text = value_text
            session.commit()
            session.refresh(value)
            return value

    def clear_value(self, offer_id: int, dimension_id: int) -> Optional[OfferComparisonValue]:
        with self._session_factory() as session:
            self._get_visible_offer(session, offer_id)
            self._get_dimension_for_read(session, dimension_id)
            value = session.scalar(
                select(OfferComparisonValue).where(
                    OfferComparisonValue.offer_id == offer_id,
                    OfferComparisonValue.dimension_id == dimension_id,
                )
            )
            if value is not None:
                session.delete(value)
                session.commit()
            return value

    def _get_dimension_for_read(self, session: Session, dimension_id: int) -> OfferComparisonDimension:
        dimension = session.get(OfferComparisonDimension, dimension_id)
        if dimension is None:
            raise OfferComparisonError(
                "offer_comparison_dimension_not_found", "comparison dimension not found", 404
            )
        return dimension

    def comparison_payload(self, offer_ids: list[int], dimension_ids: list[int]) -> dict[str, Any]:
        with self._session_factory() as session:
            offers = [self._get_visible_offer(session, offer_id) for offer_id in offer_ids]
            dimensions: list[OfferComparisonDimension] = []
            for dimension_id in sorted(dimension_ids):
                dimensions.append(self._get_active_dimension(session, dimension_id))
            values = list(
                session.scalars(
                    select(OfferComparisonValue).where(
                        OfferComparisonValue.offer_id.in_(offer_ids),
                        OfferComparisonValue.dimension_id.in_(dimension_ids),
                    )
                )
            )
            values_by_key = {(value.offer_id, value.dimension_id): value.value_text for value in values}
            dimensions_payload = []
            missing = []
            for dimension in dimensions:
                dimension_values = []
                for offer in sorted(offers, key=lambda item: item.id):
                    value_text = values_by_key.get((offer.id, dimension.id))
                    dimension_values.append({"offer_id": offer.id, "value_text": value_text})
                    if value_text is None:
                        missing.append(
                            {
                                "offer_id": offer.id,
                                "path": f"offer_snapshot/dimensions/{dimension.id}/value_text",
                                "label": dimension.label,
                            }
                        )
                dimensions_payload.append(
                    {"id": dimension.id, "label": dimension.label, "values": dimension_values}
                )
            return {
                "offers": [self._offer_payload(offer) for offer in offers],
                "dimensions": dimensions_payload,
                "missing": missing,
            }

    @staticmethod
    def _offer_payload(offer: Offer) -> dict[str, Any]:
        return {
            "id": offer.id,
            "application_id": offer.application_id,
            "company_name": offer.company_name,
            "position_name": offer.position_name,
            "status": offer.status,
            "base_monthly": offer.base_monthly,
            "months_per_year": offer.months_per_year,
            "signing_bonus": offer.signing_bonus,
            "equity": offer.equity,
            "perks": offer.perks,
            "deadline": offer.deadline,
            "notes": offer.notes,
            "assessment": offer.assessment,
            "total_cash": offer.total_cash,
            "created_at": offer.created_at.isoformat() if offer.created_at else None,
            "updated_at": offer.updated_at.isoformat() if offer.updated_at else None,
        }
