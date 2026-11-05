"""Versioned and auditable transaction cost profiles."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_UP, ROUND_UP
from hashlib import sha256
import json
import math
from typing import Literal


Basis = Literal["OFFICIAL", "BROKER_ACTUAL", "ASSUMED", "UNKNOWN"]
ApprovalStatus = Literal["APPROVED", "PENDING"]
_OLD_COMPONENTS = {
    "equity_commission_rate", "minimum_commission", "sell_tax_rate",
    "futures_fee_per_contract_per_side", "futures_transaction_tax_rate",
    "contract_multiplier", "tick_size", "slippage_ticks", "fee_rounding", "tax_rounding",
}
_OPTIONAL_COMPONENTS = {
    "equity_commission_per_share", "equity_slippage_bps", "half_spread_bps",
    "sell_regulatory_fee_rate", "volume_participation_cap",
    "commission_cap_rate", "exchange_fee_per_unit", "clearing_fee_per_unit", "regulatory_fee_per_unit",
}
_ALL_COMPONENTS = _OLD_COMPONENTS | _OPTIONAL_COMPONENTS


@dataclass(frozen=True)
class CostProfile:
    profile_id: str
    version: str
    instrument_type: Literal["EQUITY", "FUTURES"]
    approval_status: Literal["DRAFT", "APPROVED"]
    source_evidence: str
    basis: Basis
    equity_commission_rate: float
    minimum_commission: float
    sell_tax_rate: float
    futures_fee_per_contract_per_side: float
    futures_transaction_tax_rate: float
    contract_multiplier: float
    tick_size: float
    slippage_ticks: float
    fee_rounding: Literal["HALF_UP", "UP", "DOWN", "NONE", "FLOOR_INTEGER"]
    tax_rounding: Literal["HALF_UP", "UP", "DOWN", "NONE", "FLOOR_INTEGER"]
    component_basis: dict[str, Basis] = field(default_factory=dict)
    equity_commission_per_share: float | None = None
    equity_slippage_bps: float | None = None
    half_spread_bps: float | None = None
    sell_regulatory_fee_rate: float | None = None
    volume_participation_cap: float | None = None
    component_evidence: dict[str, str] = field(default_factory=dict)
    component_approval_status: dict[str, ApprovalStatus] = field(default_factory=dict)

    commission_cap_rate: float | None = None
    exchange_fee_per_unit: float | None = None
    clearing_fee_per_unit: float | None = None
    regulatory_fee_per_unit: float | None = None
    commission_scheme: str | None = None
    fee_mode: str | None = None
    configuration_metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.instrument_type not in {'EQUITY', 'FUTURES'} or self.approval_status not in {'DRAFT','APPROVED'}:
            raise ValueError('unsupported instrument / approval status')
        if self.basis not in {'ASSUMED','OFFICIAL','BROKER_ACTUAL','UNKNOWN'}:
            raise ValueError('unsupported basis')
        if self.commission_scheme not in (None, 'FIXED'):
            raise ValueError('unsupported commission scheme; TIERED needs an explicit implemented schedule')
        if self.fee_mode not in (None, 'ALL_IN', 'COMPONENTIZED'):
            raise ValueError('unsupported fee mode')
        if self.fee_mode == 'ALL_IN' and any((self.exchange_fee_per_unit, self.clearing_fee_per_unit,
                                             self.regulatory_fee_per_unit, self.sell_regulatory_fee_rate)):
            raise ValueError('ALL_IN excludes separate exchange / clearing / regulatory fees')
        if self.commission_cap_rate is not None and not 0 < self.commission_cap_rate <= 1:
            raise ValueError('commission cap must be in (0, 1]')
        if self.commission_cap_rate is not None and (self.instrument_type != 'EQUITY' or self.commission_scheme != 'FIXED'):
            raise ValueError('commission cap requires selected EQUITY FIXED scheme')
        if not self.profile_id or not self.version or not self.source_evidence:
            raise ValueError("profile_id, version and source_evidence are required")
        vals = (self.equity_commission_rate, self.minimum_commission, self.sell_tax_rate,
                self.futures_fee_per_contract_per_side, self.futures_transaction_tax_rate,
                self.contract_multiplier, self.tick_size, self.slippage_ticks)
        optional = (self.equity_commission_per_share, self.equity_slippage_bps,
                    self.half_spread_bps, self.sell_regulatory_fee_rate,
                    self.volume_participation_cap, self.commission_cap_rate, self.exchange_fee_per_unit,
                    self.clearing_fee_per_unit, self.regulatory_fee_per_unit)
        if any(not math.isfinite(v) or v < 0 for v in vals):
            raise ValueError("cost profile values must be finite and non-negative")
        if any(v is not None and (not math.isfinite(v) or v < 0) for v in optional):
            raise ValueError("optional cost profile values must be finite and non-negative")
        if set(self.component_basis) - _ALL_COMPONENTS or not _OLD_COMPONENTS.issubset(self.component_basis):
            raise ValueError(f"component_basis must include the legacy fields and only supported components")
        if set(self.component_evidence) - _ALL_COMPONENTS:
            raise ValueError("component_evidence contains unsupported cost components")
        if set(self.component_approval_status) - _ALL_COMPONENTS:
            raise ValueError("component_approval_status contains unsupported cost components")
        for name in _OPTIONAL_COMPONENTS:
            if getattr(self, name) is not None:
                if name not in self.component_basis:
                    raise ValueError(f"{name} requires an explicit basis")
                if not self.component_evidence.get(name):
                    raise ValueError(f"{name} requires component-specific source evidence")
                if name not in self.component_approval_status:
                    raise ValueError(f"{name} requires an explicit approval status")
        if self.instrument_type == "FUTURES" and (self.contract_multiplier <= 0 or self.tick_size <= 0):
            raise ValueError("futures profiles require positive multiplier and tick_size")
        if self.tick_size <= 0:
            raise ValueError('tick_size must be positive')
        if any(v not in {'ASSUMED','OFFICIAL','BROKER_ACTUAL','UNKNOWN'} for v in self.component_basis.values()):
            raise ValueError('unsupported component basis')
        if any(v not in {'APPROVED','PENDING'} for v in self.component_approval_status.values()):
            raise ValueError('unsupported component approval')
        if self.fee_rounding not in {'NONE','HALF_UP','UP','DOWN','FLOOR_INTEGER'} or self.tax_rounding not in {'NONE','HALF_UP','UP','DOWN','FLOOR_INTEGER'}:
            raise ValueError('unsupported rounding mode')
        if self.volume_participation_cap is not None and not 0 < self.volume_participation_cap <= 1:
            raise ValueError('volume_participation_cap must be in (0, 1]')
        if self.instrument_type == "EQUITY":
            if self.equity_commission_per_share is not None and self.equity_commission_rate > 0:
                raise ValueError("choose per-share or proportional equity commission, not both")
            if self.equity_slippage_bps is not None and self.slippage_ticks > 0:
                raise ValueError("choose basis-point or tick slippage, not both")
            if self.volume_participation_cap is not None and not 0 < self.volume_participation_cap <= 1:
                raise ValueError("volume_participation_cap must be in (0, 1]")
        elif any(v is not None for v in (self.equity_commission_per_share, self.equity_slippage_bps, self.half_spread_bps, self.sell_regulatory_fee_rate, self.commission_cap_rate)):
            raise ValueError("new per-share, equity bps and volume participation fields are EQUITY-only")

    @property
    def sha256(self) -> str:
        payload = asdict(self)
        # Preserve hashes for legacy profiles whose newly-added fields are unset.
        for name in _OPTIONAL_COMPONENTS:
            if payload[name] is None:
                payload.pop(name)
        if not payload["component_evidence"]:
            payload.pop("component_evidence")
        if not payload["component_approval_status"]:
            payload.pop("component_approval_status")
        for name in ('commission_scheme','fee_mode','configuration_metadata'):
            if not payload[name]:
                payload.pop(name)
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @property
    def components(self) -> dict[str, dict[str, str]]:
        legacy = {
            "commission": "equity_commission_rate", "minimum_commission": "minimum_commission",
            "sell_tax": "sell_tax_rate", "futures_fee": "futures_fee_per_contract_per_side",
            "futures_transaction_tax": "futures_transaction_tax_rate",
            "contract_multiplier": "contract_multiplier", "tick_size": "tick_size",
            "slippage": "slippage_ticks", "fee_rounding": "fee_rounding", "tax_rounding": "tax_rounding",
        }
        optional = {
            "commission_per_share": "equity_commission_per_share",
            "slippage_bps": "equity_slippage_bps", "half_spread_bps": "half_spread_bps",
            "sell_regulatory_fee": "sell_regulatory_fee_rate",
            "volume_participation_cap": "volume_participation_cap",
            "commission_cap": "commission_cap_rate", "exchange_fee": "exchange_fee_per_unit",
            "clearing_fee": "clearing_fee_per_unit", "regulatory_per_unit": "regulatory_fee_per_unit",
        }
        names = {**legacy, **{k: v for k, v in optional.items() if getattr(self, v) is not None}}
        return {
            name: {
                "basis": self.component_basis.get(field_name, self.basis),
                "source_evidence": self.component_evidence.get(field_name, self.source_evidence),
                "approval_status": self.component_approval_status.get(field_name, self.approval_status),
            }
            for name, field_name in names.items()
        }


def _round(value: Decimal | float, mode: str) -> Decimal:
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    if mode == "NONE":
        return amount
    if mode == "FLOOR_INTEGER":
        return amount.quantize(Decimal("1"), rounding=ROUND_FLOOR)
    rounding = {"HALF_UP": ROUND_HALF_UP, "UP": ROUND_UP, "DOWN": ROUND_DOWN}.get(mode)
    if rounding is None:
        raise ValueError(f"unsupported rounding mode: {mode}")
    return amount.quantize(Decimal("0.01"), rounding=rounding)


def _base_cost_components(
    price: float, quantity: float, side: str, profile: CostProfile
) -> tuple[Decimal, Decimal, Decimal]:
    """Return commission, sell tax, and sell regulatory fee separately."""
    price_value = Decimal(str(price))
    quantity_value = abs(Decimal(str(quantity)))
    multiplier = Decimal(str(profile.contract_multiplier if profile.instrument_type == "FUTURES" else 1.0))
    notional = abs(price_value * quantity_value * multiplier)
    side = side.upper()
    if side not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL")
    if profile.instrument_type == "FUTURES":
        fee = Decimal(str(profile.futures_fee_per_contract_per_side)) * quantity_value
        tax = notional * Decimal(str(profile.futures_transaction_tax_rate))
        regulatory_fee = Decimal("0")
    else:
        if profile.equity_commission_per_share is not None:
            proportional_fee = Decimal(str(profile.equity_commission_per_share)) * quantity_value
        else:
            proportional_fee = notional * Decimal(str(profile.equity_commission_rate))
        fee = max(Decimal(str(profile.minimum_commission)), proportional_fee)
        if profile.commission_cap_rate is not None:
            fee = min(fee, notional * Decimal(str(profile.commission_cap_rate)))
        tax = notional * Decimal(str(profile.sell_tax_rate)) if side == "SELL" else Decimal("0")
        regulatory_fee = (
            notional * Decimal(str(profile.sell_regulatory_fee_rate))
            if side == "SELL" and profile.sell_regulatory_fee_rate is not None
            else Decimal("0")
        )
    return (
        _round(fee, profile.fee_rounding),
        _round(tax, profile.tax_rounding),
        _round(regulatory_fee, profile.tax_rounding),
    )


def calculate_fill_costs(price: float, quantity: float, side: str, profile: CostProfile) -> dict[str, Decimal]:
    if not math.isfinite(price) or price <= 0 or not math.isfinite(quantity) or quantity == 0:
        raise ValueError('price/quantity must be finite, price positive and quantity nonzero')
    commission, tax, regulatory = _base_cost_components(price, quantity, side, profile)
    q = abs(Decimal(str(quantity)))
    return dict(commission=commission, transaction_tax=tax,
                exchange_fee=_round(q * Decimal(str(profile.exchange_fee_per_unit or 0)), profile.fee_rounding),
                clearing_fee=_round(q * Decimal(str(profile.clearing_fee_per_unit or 0)), profile.fee_rounding),
                regulatory_fee=regulatory + _round(q * Decimal(str(profile.regulatory_fee_per_unit or 0)), profile.tax_rounding))


def calculate_cost_components(price: float, quantity: float, side: str, profile: CostProfile) -> tuple[Decimal, Decimal, Decimal]:
    parts = calculate_fill_costs(price, quantity, side, profile)
    return (parts['commission'] + parts['exchange_fee'] + parts['clearing_fee'],
            parts['transaction_tax'], parts['regulatory_fee'])


def calculate_costs(price: float, quantity: float, side: str, profile: CostProfile) -> tuple[Decimal, Decimal]:
    """Backward-compatible (commission, combined sell taxes/fees) interface."""
    fee, tax, regulatory_fee = calculate_cost_components(price, quantity, side, profile)
    return fee, tax + regulatory_fee


def adverse_fill_price(price: float, side: str, profile: CostProfile, cost_multiplier: float = 1.0) -> float:
    if side.upper() not in {'BUY','SELL'} or not math.isfinite(price) or price <= 0:
        raise ValueError('invalid side / price')
    if not math.isfinite(cost_multiplier) or cost_multiplier < 0:
        raise ValueError("cost_multiplier must be finite and non-negative")
    if profile.equity_slippage_bps is not None:
        slip = Decimal(str(price)) * Decimal(str(profile.equity_slippage_bps)) / Decimal('10000')
    else:
        slip = Decimal(str(profile.slippage_ticks)) * Decimal(str(profile.tick_size))
    spread = Decimal(str(price)) * Decimal(str(profile.half_spread_bps or 0)) / Decimal('10000')
    move = float((slip + spread) * Decimal(str(cost_multiplier)))
    value = price + move if side.upper() == "BUY" else price - move
    tick = profile.tick_size
    if tick <= 0:
        return float(value)
    units = value / tick
    rounded_units = math.ceil(units - 1e-12) if side.upper() == "BUY" else math.floor(units + 1e-12)
    return float(round(rounded_units * tick, 10))
