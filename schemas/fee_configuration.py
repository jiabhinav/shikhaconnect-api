from decimal import Decimal
from typing import Annotated

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from schemas.fee_examples import (FEE_CONFIGURATION_EXAMPLES,
                                  FEE_CONFIGURATION_UPDATE_EXAMPLES,
                                  FEE_MONTH_PATCH_EXAMPLES)

Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2, allow_inf_nan=False)]
Month = Annotated[str, Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]


class FeeMonthInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    month_key: Month
    amount: Money
    enabled: bool = Field(strict=True)
    is_override: bool = True


class FeeMonthAmount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: Money
    enabled: bool = Field(strict=True)
    is_override: bool = True


class FeeConfigurationHeadWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fee_head_id: int = Field(gt=0)
    amount: Money = Field(validation_alias=AliasChoices("base_amount", "amount"))
    edit_months_wise: bool = False
    months: list[FeeMonthInput] | None = Field(
        default=None,
        description="Month-wise fee entries. Each item uses month_key (YYYY-MM), amount, enabled, and is_override.",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_month_list(cls, value):
        if not isinstance(value, dict):
            return value
        value = dict(value)
        if "amount" not in value and "base_amount" in value:
            value["amount"] = value.pop("base_amount")
        if "month_amounts" in value:
            if "months" in value:
                raise ValueError("Provide either months or month_amounts, not both")
            legacy_month_amounts = value.pop("month_amounts")
            if not isinstance(legacy_month_amounts, dict):
                raise ValueError("month_amounts must be an object")
            value["months"] = [
                {
                    "month_key": month_key,
                    "amount": month_value.get("amount") if isinstance(month_value, dict) else month_value,
                    "enabled": month_value.get("enabled", True) if isinstance(month_value, dict) else True,
                    "is_override": month_value.get("is_override", True) if isinstance(month_value, dict) else True,
                }
                for month_key, month_value in legacy_month_amounts.items()
            ]
        if "months" in value:
            if value["months"] is None:
                return value
            month_values = value["months"]
            if not isinstance(month_values, list):
                raise ValueError("months must be an array")
        return value

    @model_validator(mode="after")
    def validate_months(self):
        month_keys = [month.month_key for month in self.months or []]
        if len(month_keys) != len(set(month_keys)):
            raise ValueError("Month keys must be unique within a fee head")
        if self.edit_months_wise and not month_keys:
            raise ValueError("Month-wise editing requires values for every session month")
        return self

    @property
    def month_amounts(self):
        return {
            month.month_key: {
                "amount": month.amount,
                "enabled": month.enabled,
                "is_override": month.is_override,
            }
            for month in self.months or []
        }


class FeeConfigurationWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={
        "examples": [example["value"] for example in FEE_CONFIGURATION_EXAMPLES.values()]
    })
    fee_structure_id: int = Field(gt=0)
    fee_category_id: int = Field(gt=0)
    no_of_fee_heads: int | None = Field(default=None, ge=1, le=100)
    fee_heads: list[FeeConfigurationHeadWrite] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_heads(self):
        if self.no_of_fee_heads is not None and self.no_of_fee_heads != len(self.fee_heads):
            raise ValueError("no_of_fee_heads must match the number of fee_heads")
        if len({item.fee_head_id for item in self.fee_heads}) != len(self.fee_heads):
            raise ValueError("Fee heads must be unique within a configuration")
        return self


class FeeConfigurationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={
        "examples": [example["value"] for example in FEE_CONFIGURATION_UPDATE_EXAMPLES.values()]
    })
    fee_structure_id: int | None = Field(default=None, gt=0)
    fee_category_id: int | None = Field(default=None, gt=0)
    no_of_fee_heads: int | None = Field(default=None, ge=1, le=100)
    fee_heads: list[FeeConfigurationHeadWrite] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_heads(self):
        if self.no_of_fee_heads is not None and self.no_of_fee_heads != len(self.fee_heads):
            raise ValueError("no_of_fee_heads must match the number of fee_heads")
        if len({item.fee_head_id for item in self.fee_heads}) != len(self.fee_heads):
            raise ValueError("Fee heads must be unique within a configuration")
        return self


class FeeMonthDisplay(BaseModel):
    amount: Money | None
    enabled: bool


class FeeConfigurationHeadResponse(BaseModel):
    fee_head_id: int
    amount: Money
    edit_months_wise: bool
    month_amounts: dict[Month, Money | FeeMonthDisplay] = Field(
        validation_alias=AliasChoices("complete_month_amounts", "month_amounts"))
    april_fee: Money
    may_fee: Money
    june_fee: Money
    july_fee: Money
    august_fee: Money
    september_fee: Money
    october_fee: Money
    november_fee: Money
    december_fee: Money
    january_fee: Money
    february_fee: Money
    march_fee: Money
    model_config = ConfigDict(from_attributes=True)
    id: int


class FeeConfigurationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    school_id: int
    session_id: int
    fee_structure_id: int
    fee_category_id: int
    no_of_fee_heads: int
    fee_heads: list[FeeConfigurationHeadResponse]


class FeeConfigurationResult(BaseModel):
    status: str = "success"
    message: str
    data: FeeConfigurationResponse


class FeeConfigurationListResult(BaseModel):
    status: str = "success"
    message: str
    data: list[FeeConfigurationResponse]


class FeeMonthPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={
        "examples": [example["value"] for example in FEE_MONTH_PATCH_EXAMPLES.values()]
    })
    amount: Money | None = None
    # null resets a month to the base amount; omitted months remain unchanged.
    month_amounts: dict[Month, FeeMonthAmount | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_changes(self):
        if 'amount' in self.model_fields_set and self.amount is None:
            raise ValueError("Base amount cannot be null")
        if self.amount is None and not self.month_amounts:
            raise ValueError("Provide an amount or month changes")
        return self


class FeeScheduleMonth(FeeMonthDisplay):
    month: Month
    effective_amount: Money
    is_override: bool


class FeeScheduleHead(BaseModel):
    fee_head_id: int
    fee_head_name: str
    amount: Money
    months: list[FeeScheduleMonth]
    total: Decimal


class FeeScheduleResponse(BaseModel):
    configuration_id: int
    school_id: int
    session_id: int
    fee_heads: list[FeeScheduleHead]
    monthly_totals: dict[Month, Decimal]
    total: Decimal


class FeeScheduleResult(BaseModel):
    status: str = "success"
    message: str
    data: FeeScheduleResponse
