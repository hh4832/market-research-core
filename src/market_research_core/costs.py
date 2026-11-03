"""Versioned and auditable transaction cost profiles."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import json
import math
from typing import Literal


Basis = Literal["OFFICIAL", "BROKER_ACTUAL", "ASSUMED"]


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
    fee_rounding: Literal["HALF_UP", "UP", "DOWN"]
    tax_rounding: Literal["HALF_UP", "UP", "DOWN"]
    component_basis: dict[str, Basis] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.profile_id or not self.version or not self.source_evidence:
            raise ValueError("profile_id, version and source_evidence are required")
        vals = (self.equity_commission_rate, self.minimum_commission, self.sell_tax_rate,
                self.futures_fee_per_contract_per_side, self.futures_transaction_tax_rate,
                self.contract_multiplier, self.tick_size, self.slippage_ticks)
        if any(not math.isfinite(v) or v < 0 for v in vals):
            raise ValueError("cost profile values must be finite and non-negative")
        required = {"equity_commission_rate", "minimum_commission", "sell_tax_rate",
                    "futures_fee_per_contract_per_side", "futures_transaction_tax_rate",
                    "contract_multiplier", "tick_size", "slippage_ticks", "fee_rounding", "tax_rounding"}
        if set(self.component_basis) != required:
            raise ValueError(f"component_basis must classify every cost field: {sorted(required)}")
        if self.instrument_type == "FUTURES" and (self.contract_multiplier <= 0 or self.tick_size <= 0):
            raise ValueError("futures profiles require positive multiplier and tick_size")

    @property
    def sha256(self) -> str:
        payload = asdict(self)
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @property
    def components(self) -> dict[str, dict[str, str]]:
        return {name: {"basis": self.component_basis[field], "source_evidence": self.source_evidence}
                for name, field in {
                    "commission": "equity_commission_rate", "minimum_commission": "minimum_commission",
                    "sell_tax": "sell_tax_rate", "futures_fee": "futures_fee_per_contract_per_side",
                    "futures_transaction_tax": "futures_transaction_tax_rate",
                    "contract_multiplier": "contract_multiplier", "tick_size": "tick_size",
                    "slippage": "slippage_ticks", "fee_rounding": "fee_rounding", "tax_rounding": "tax_rounding",
                }.items()}


def _round(value: float, mode: str) -> float:
    rounding = {"HALF_UP": ROUND_HALF_UP, "UP": "ROUND_UP", "DOWN": "ROUND_DOWN"}[mode]
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=rounding))


def calculate_costs(price: float, quantity: float, side: str, profile: CostProfile) -> tuple[float, float]:
    notional = abs(price * quantity * (profile.contract_multiplier if profile.instrument_type == "FUTURES" else 1.0))
    if profile.instrument_type == "FUTURES":
        fee = profile.futures_fee_per_contract_per_side * quantity
        tax = notional * profile.futures_transaction_tax_rate
    else:
        fee = max(profile.minimum_commission, notional * profile.equity_commission_rate)
        tax = notional * profile.sell_tax_rate if side == "SELL" else 0.0
    return _round(fee, profile.fee_rounding), _round(tax, profile.tax_rounding)


def adverse_fill_price(price: float, side: str, profile: CostProfile) -> float:
    move = profile.slippage_ticks * profile.tick_size
    value = price + move if side == "BUY" else price - move
    tick = profile.tick_size
    # Round against the trader to a valid tick.
    units = value / tick
    rounded_units = math.ceil(units - 1e-12) if side == "BUY" else math.floor(units + 1e-12)
    return float(round(rounded_units * tick, 10))
