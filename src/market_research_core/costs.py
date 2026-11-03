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

    def __post_init__(self) -> None:
        if not self.profile_id or not self.version or not self.source_evidence:
            raise ValueError("profile_id, version and source_evidence are required")
        vals = (self.equity_commission_rate, self.minimum_commission, self.sell_tax_rate,
                self.futures_fee_per_contract_per_side, self.futures_transaction_tax_rate,
                self.contract_multiplier, self.tick_size, self.slippage_ticks)
        optional = (self.equity_commission_per_share, self.equity_slippage_bps,
                    self.half_spread_bps, self.sell_regulatory_fee_rate,
                    self.volume_participation_cap)
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
        if self.instrument_type == "EQUITY":
            if self.equity_commission_per_share is not None and self.equity_commission_rate > 0:
                raise ValueError("choose per-share or proportional equity commission, not both")
            if self.equity_slippage_bps is not None and self.slippage_ticks > 0:
                raise ValueError("choose basis-point or tick slippage, not both")
            if self.volume_participation_cap is not None and not 0 < self.volume_participation_cap <= 1:
                raise ValueError("volume_participation_cap must be in (0, 1]")
        elif any(v is not None for v in optional):
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


def calculate_cost_components(
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


def calculate_costs(price: float, quantity: float, side: str, profile: CostProfile) -> tuple[Decimal, Decimal]:
    """Backward-compatible (commission, combined sell taxes/fees) interface."""
    fee, tax, regulatory_fee = calculate_cost_components(price, quantity, side, profile)
    return fee, tax + regulatory_fee


def adverse_fill_price(price: float, side: str, profile: CostProfile, cost_multiplier: float = 1.0) -> float:
    if not math.isfinite(cost_multiplier) or cost_multiplier < 0:
        raise ValueError("cost_multiplier must be finite and non-negative")
    if profile.equity_slippage_bps is not None or profile.half_spread_bps is not None:
        bps = Decimal(str(profile.equity_slippage_bps or 0)) + Decimal(str(profile.half_spread_bps or 0))
        move = float(Decimal(str(price)) * bps * Decimal(str(cost_multiplier)) / Decimal("10000"))
    else:
        move = profile.slippage_ticks * profile.tick_size * cost_multiplier
    value = price + move if side.upper() == "BUY" else price - move
    tick = profile.tick_size
    if tick <= 0:
        return float(value)
    units = value / tick
    rounded_units = math.ceil(units - 1e-12) if side.upper() == "BUY" else math.floor(units + 1e-12)
    return float(round(rounded_units * tick, 10))
