import sqlalchemy as sa
import sqlalchemy.orm as so
from oa_configurator import Role
from typing import Optional
from orm_loader.helpers import Base
from omop_alchemy.cdm.base import (
    cdm_table,
    CDMTableBase,
    optional_concept_fk,
    required_concept_fk,
    merge_table_args,
    omop_index,
)

@cdm_table
class Cost(CDMTableBase, Base):
    __tablename__ = "cost"
    __table_args__ = merge_table_args(
        {"schema": Role.PRIMARY.value},
        omop_index(__tablename__, "cost_event_id"),
        omop_index(__tablename__, "cost_type_concept_id"),
    )

    cost_id: so.Mapped[int] = so.mapped_column(primary_key=True)
    cost_event_id: so.Mapped[int] = so.mapped_column(nullable=False)
    cost_domain_id: so.Mapped[str] = so.mapped_column(sa.String(20), nullable=False)
    cost_type_concept_id: so.Mapped[int] = required_concept_fk()

    currency_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()

    total_charge: so.Mapped[Optional[float]] = so.mapped_column(nullable=True)
    total_cost: so.Mapped[Optional[float]] = so.mapped_column(nullable=True)
    total_paid: so.Mapped[Optional[float]] = so.mapped_column(nullable=True)

    paid_by_payer: so.Mapped[Optional[float]] = so.mapped_column(nullable=True)
    paid_by_patient: so.Mapped[Optional[float]] = so.mapped_column(nullable=True)
    paid_patient_copay: so.Mapped[Optional[float]] = so.mapped_column(nullable=True)
    paid_patient_coinsurance: so.Mapped[Optional[float]] = so.mapped_column(nullable=True)
    paid_patient_deductible: so.Mapped[Optional[float]] = so.mapped_column(nullable=True)

    paid_by_primary: so.Mapped[Optional[float]] = so.mapped_column(nullable=True)
    paid_ingredient_cost: so.Mapped[Optional[float]] = so.mapped_column(nullable=True)
    paid_dispensing_fee: so.Mapped[Optional[float]] = so.mapped_column(nullable=True)

    payer_plan_period_id: so.Mapped[Optional[int]] = so.mapped_column(nullable=True)

    amount_allowed: so.Mapped[Optional[float]] = so.mapped_column(nullable=True)

    revenue_code_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
    revenue_code_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50), nullable=True)

    drg_concept_id: so.Mapped[Optional[int]] = optional_concept_fk()
    drg_source_value: so.Mapped[Optional[str]] = so.mapped_column(sa.String(3), nullable=True)
