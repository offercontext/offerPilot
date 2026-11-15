from __future__ import annotations

# 业务地图：docs/architecture/code-map/materials.md（投递材料包）。
# 材料包与简历是独立对象；生成/更新包不代表已向招聘方提交。

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import desc, select, text
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.models import ApplicationJDVersion, ApplicationMaterialKit


@dataclass
class MaterialKitCreate:
    application_id: int
    resume_id: Optional[int] = None
    jd_analysis_id: Optional[int] = None
    jd_snapshot: str = ""
    jd_version_id: Optional[int] = None
    status: str = "draft"
    content_json: str = "{}"


class MaterialKitSourceConflict(ValueError):
    """The kit write lost the current JD source race."""


class MaterialKitsRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    def create(self, data: MaterialKitCreate) -> ApplicationMaterialKit:
        kit = ApplicationMaterialKit(
            application_id=data.application_id,
            resume_id=data.resume_id,
            jd_analysis_id=data.jd_analysis_id,
            jd_snapshot=data.jd_snapshot,
            jd_version_id=data.jd_version_id,
            status=data.status or "draft",
            content_json=data.content_json or "{}",
        )
        with self._session_factory() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            _require_current_jd(session, data.application_id, data.jd_version_id)
            session.add(kit)
            session.commit()
            session.refresh(kit)
            return kit

    def get(self, kit_id: int) -> Optional[ApplicationMaterialKit]:
        with self._session_factory() as session:
            return session.get(ApplicationMaterialKit, kit_id)

    def get_by_application(self, application_id: int) -> Optional[ApplicationMaterialKit]:
        statement = select(ApplicationMaterialKit).where(
            ApplicationMaterialKit.application_id == application_id
        )
        with self._session_factory() as session:
            return session.scalar(statement)

    def update(self, kit_id: int, data: MaterialKitCreate) -> Optional[ApplicationMaterialKit]:
        with self._session_factory() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            kit = session.get(ApplicationMaterialKit, kit_id)
            if kit is None:
                return None
            _require_current_jd(session, data.application_id, data.jd_version_id)
            kit.resume_id = data.resume_id
            kit.jd_analysis_id = data.jd_analysis_id
            kit.jd_snapshot = data.jd_snapshot
            kit.jd_version_id = data.jd_version_id
            kit.status = data.status or "draft"
            kit.content_json = data.content_json or "{}"
            session.commit()
            session.refresh(kit)
            return kit


def _require_current_jd(session: Session, application_id: int, jd_version_id: int | None) -> None:
    # 携带 JD 版本的写入必须仍使用当前版本，防止模型生成期间 JD 更新后保存过期包。
    # 历史未绑定版本的记录在此返回，不据此宣称它已通过当前来源校验。
    if jd_version_id is None:
        return
    current_id = session.scalar(
        select(ApplicationJDVersion.id)
        .where(ApplicationJDVersion.application_id == application_id)
        .order_by(desc(ApplicationJDVersion.version_number))
        .limit(1)
    )
    if current_id != jd_version_id:
        raise MaterialKitSourceConflict("application JD source changed")
