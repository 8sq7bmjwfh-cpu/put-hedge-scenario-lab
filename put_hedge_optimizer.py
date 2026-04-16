from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path


@dataclass
class MarketParams:
    spot_index: float = 8560.84
    risk_free_rate: float = 0.015
    dividend_yield: float = 0.0
    volatility: float = 0.20
    maturity_years: float = 30 / 365


@dataclass
class HedgeParams:
    portfolio_value: float = 10_000_000
    portfolio_beta: float = 1.0
    hedge_ratio: float = 1.0
    hedge_method: str = "notional"
    put_delta_abs: float | None = None
    contract_multiplier: float = 100.0


@dataclass
class TradingCostParams:
    fee_per_contract: float = 0.0
    slippage_per_contract: float = 0.0


@dataclass
class StrikeInfo:
    strike: float
    put_premium_points: float
    contracts: int
    total_entry_cost: float
    trigger_drop: float


def infer_exchange_strike_step(spot_index: float) -> float:
    if spot_index < 2000:
        return 25.0
    if spot_index < 5000:
        return 50.0
    return 100.0


def build_exchange_listed_strikes(spot_index: float, listing_depth: int) -> list[float]:
    if listing_depth < 1:
        raise ValueError("listing_depth must be >= 1")
    step = infer_exchange_strike_step(spot_index)
    atm = round(spot_index / step) * step
    strikes: list[float] = []
    for i in range(-listing_depth, listing_depth + 1):
        strike = atm + i * step
        if strike > 0:
            strikes.append(round(strike, 6))
    return sorted(set(strikes))


def frange(start: float, stop: float, step: float) -> list[float]:
    if step <= 0:
        raise ValueError("step must be > 0")
    n_steps = int(round((stop - start) / step))
    if n_steps < 0:
        raise ValueError("stop must be >= start")
    return [round(start + i * step, 6) for i in range(n_steps + 1)]


def month_str(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def add_months(d: date, months: int) -> date:
    year = d.year + (d.month - 1 + months) // 12
    month = (d.month - 1 + months) % 12 + 1
    return date(year, month, 1)


def quarter_months_after(d: date) -> list[date]:
    out: list[date] = []
    cursor = date(d.year, d.month, 1)
    while len(out) < 2:
        cursor = add_months(cursor, 1)
        if cursor.month in {3, 6, 9, 12}:
            out.append(cursor)
    return out


def default_exchange_expiry_months(valuation_date: date) -> list[str]:
    current_month = date(valuation_date.year, valuation_date.month, 1)
    next_month = add_months(current_month, 1)
    quarter_months = quarter_months_after(current_month)
    months = [current_month, next_month, *quarter_months]
    seen: set[str] = set()
    out: list[str] = []
    for m in months:
        s = month_str(m)
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def parse_ym(ym: str) -> tuple[int, int]:
    try:
        year_str, month_str_raw = ym.split("-")
        year = int(year_str)
        month = int(month_str_raw)
    except ValueError as exc:
        raise ValueError(f"Invalid expiry month format: {ym}, expected YYYY-MM") from exc
    if month < 1 or month > 12:
        raise ValueError(f"Invalid expiry month: {ym}")
    return year, month


def third_friday(year: int, month: int) -> date:
    first_day = date(year, month, 1)
    days_to_friday = (4 - first_day.weekday()) % 7
    first_friday = first_day + timedelta(days=days_to_friday)
    return first_friday + timedelta(days=14)


def maturity_years_from_exchange_expiry(
    valuation_date: date, expiry_month: str
) -> tuple[float, date]:
    year, month = parse_ym(expiry_month)
    expiry_date = third_friday(year, month)
    days = (expiry_date - valuation_date).days
    if days <= 0:
        raise ValueError(
            f"Expiry {expiry_month} third Friday ({expiry_date}) is not after valuation date {valuation_date}"
        )
    return days / 365.0, expiry_date


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black_scholes_put_points(
    spot: float,
    strike: float,
    risk_free_rate: float,
    dividend_yield: float,
    volatility: float,
    maturity_years: float,
) -> float:
    sqrt_t = math.sqrt(maturity_years)
    d1 = (
        math.log(spot / strike)
        + (risk_free_rate - dividend_yield + 0.5 * volatility * volatility) * maturity_years
    ) / (volatility * sqrt_t)
    d2 = d1 - volatility * sqrt_t
    return (
        strike * math.exp(-risk_free_rate * maturity_years) * norm_cdf(-d2)
        - spot * math.exp(-dividend_yield * maturity_years) * norm_cdf(-d1)
    )


def load_manual_premium_curve(csv_path: Path) -> dict[float, float]:
    if not csv_path.exists():
        raise FileNotFoundError(f"manual premium csv not found: {csv_path}")
    curve: dict[float, float] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("manual premium csv has no header")
        lower = {name.lower(): name for name in reader.fieldnames}
        strike_key = lower.get("strike")
        premium_key = lower.get("premium")
        if strike_key is None or premium_key is None:
            if len(reader.fieldnames) < 2:
                raise ValueError("manual premium csv must include at least two columns")
            strike_key = reader.fieldnames[0]
            premium_key = reader.fieldnames[1]
        for row in reader:
            strike = round(float(row[strike_key]), 6)
            premium_points = float(row[premium_key])
            if premium_points <= 0:
                raise ValueError(
                    f"manual premium points must be > 0, got strike={strike}, premium={premium_points}"
                )
            curve[strike] = premium_points
    if not curve:
        raise ValueError("manual premium csv is empty")
    return curve


def interpolate_manual_premium(strike: float, curve: dict[float, float]) -> float:
    keys = sorted(curve.keys())
    if strike in curve:
        return curve[strike]
    if strike < keys[0] or strike > keys[-1]:
        raise ValueError(
            f"strike {strike} outside manual premium curve range [{keys[0]}, {keys[-1]}]"
        )
    for left, right in zip(keys[:-1], keys[1:]):
        if left <= strike <= right:
            p_left = curve[left]
            p_right = curve[right]
            if right == left:
                return p_left
            ratio = (strike - left) / (right - left)
            return p_left + ratio * (p_right - p_left)
    raise RuntimeError(f"failed to interpolate premium for strike={strike}")


def get_put_premium_points(
    strike: float,
    market: MarketParams,
    pricing_mode: str,
    bs_premium_rate: float,
    manual_premium: float | None,
    manual_curve: dict[float, float] | None,
) -> float:
    if pricing_mode == "auto":
        bs_points = black_scholes_put_points(
            spot=market.spot_index,
            strike=strike,
            risk_free_rate=market.risk_free_rate,
            dividend_yield=market.dividend_yield,
            volatility=market.volatility,
            maturity_years=market.maturity_years,
        )
        return bs_points * (1.0 + bs_premium_rate)
    if pricing_mode == "manual_flat":
        if manual_premium is None or manual_premium <= 0:
            raise ValueError("manual_flat mode requires --manual-premium > 0")
        return manual_premium
    if pricing_mode == "manual_curve":
        if manual_curve is None:
            raise ValueError("manual_curve mode requires --manual-premium-csv")
        return interpolate_manual_premium(strike, manual_curve)
    raise ValueError("pricing_mode must be auto/manual_flat/manual_curve")


def calculate_put_contracts(strike: float, hedge: HedgeParams, market: MarketParams) -> int:
    target_exposure = hedge.portfolio_value * hedge.portfolio_beta * hedge.hedge_ratio
    if hedge.hedge_method == "notional":
        raw = target_exposure / (strike * hedge.contract_multiplier)
    else:
        raw = target_exposure / (
            market.spot_index * hedge.contract_multiplier * float(hedge.put_delta_abs)
        )
    return max(1, math.ceil(raw))


def build_strike_infos(
    strikes: list[float],
    market: MarketParams,
    hedge: HedgeParams,
    cost: TradingCostParams,
    pricing_mode: str,
    bs_premium_rate: float,
    manual_premium: float | None,
    manual_curve: dict[float, float] | None,
) -> list[StrikeInfo]:
    infos: list[StrikeInfo] = []
    for strike in strikes:
        put_premium_points = get_put_premium_points(
            strike=strike,
            market=market,
            pricing_mode=pricing_mode,
            bs_premium_rate=bs_premium_rate,
            manual_premium=manual_premium,
            manual_curve=manual_curve,
        )
        contracts = calculate_put_contracts(strike=strike, hedge=hedge, market=market)
        total_entry_cost = contracts * (
            put_premium_points * hedge.contract_multiplier
            + cost.fee_per_contract
            + cost.slippage_per_contract
        )
        infos.append(
            StrikeInfo(
                strike=strike,
                put_premium_points=put_premium_points,
                contracts=contracts,
                total_entry_cost=total_entry_cost,
                trigger_drop=strike / market.spot_index - 1,
            )
        )
    return infos


def build_matrix_cells(
    terminal_prices: list[float],
    strike_infos: list[StrikeInfo],
    market: MarketParams,
    hedge: HedgeParams,
) -> tuple[list[dict], float, float, float]:
    exposure = hedge.portfolio_value * hedge.portfolio_beta
    rows: list[dict] = []
    min_hedged_total_pnl = float("inf")
    max_hedged_total_pnl = float("-inf")

    for terminal_price in sorted(terminal_prices, reverse=True):
        scenario_return = terminal_price / market.spot_index - 1
        unhedged_total_pnl = exposure * scenario_return
        cells: list[dict] = []
        for info in strike_infos:
            put_payoff = max(info.strike - terminal_price, 0) * hedge.contract_multiplier * info.contracts
            option_pnl = put_payoff - info.total_entry_cost
            hedged_total_pnl = unhedged_total_pnl + option_pnl
            improvement_ratio = option_pnl / exposure if exposure > 0 else 0.0
            min_hedged_total_pnl = min(min_hedged_total_pnl, hedged_total_pnl)
            max_hedged_total_pnl = max(max_hedged_total_pnl, hedged_total_pnl)
            cells.append(
                {
                    "strike": info.strike,
                    "terminal_price": terminal_price,
                    "scenario_return": scenario_return,
                    "unhedged_total_pnl": unhedged_total_pnl,
                    "option_pnl": option_pnl,
                    "hedged_total_pnl": hedged_total_pnl,
                    "improvement_ratio": improvement_ratio,
                }
            )
        rows.append(
            {
                "terminal_price": terminal_price,
                "scenario_return": scenario_return,
                "unhedged_total_pnl": unhedged_total_pnl,
                "cells": cells,
            }
        )

    max_abs_hedged_total_pnl = max(
        1e-8,
        abs(min_hedged_total_pnl),
        abs(max_hedged_total_pnl),
    )
    return rows, min_hedged_total_pnl, max_hedged_total_pnl, max_abs_hedged_total_pnl


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    s = hex_color.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        raise ValueError(f"Invalid hex color: {hex_color}")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def rgb_to_hex(r: float, g: float, b: float) -> str:
    rr = max(0, min(255, int(round(r))))
    gg = max(0, min(255, int(round(g))))
    bb = max(0, min(255, int(round(b))))
    return f"#{rr:02x}{gg:02x}{bb:02x}"


def mix_color(from_hex: str, to_hex: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    fr, fg, fb = hex_to_rgb(from_hex)
    tr, tg, tb = hex_to_rgb(to_hex)
    return rgb_to_hex(
        fr + (tr - fr) * t,
        fg + (tg - fg) * t,
        fb + (tb - fb) * t,
    )


def heat_color(value: float, max_abs_value: float) -> str:
    neutral = "#f8f2e7"
    positive = "#c14e3f"
    negative = "#1a7d62"
    safe_max = max(max_abs_value, 1e-8)
    clamped = max(-safe_max, min(safe_max, value))
    if clamped >= 0:
        t = (clamped / safe_max) ** 0.7
        return mix_color(neutral, positive, t)
    t = ((-clamped) / safe_max) ** 0.7
    return mix_color(neutral, negative, t)


def validate_inputs(
    market: MarketParams,
    hedge: HedgeParams,
    cost: TradingCostParams,
    bs_premium_rate: float,
    listing_depth: int,
    terminal_start: float,
    terminal_stop: float,
    terminal_step: float,
) -> None:
    if market.spot_index <= 0:
        raise ValueError("spot_index must be > 0")
    if market.volatility <= 0:
        raise ValueError("volatility must be > 0")
    if market.maturity_years <= 0:
        raise ValueError("maturity_years must be > 0")
    if hedge.portfolio_value <= 0:
        raise ValueError("portfolio_value must be > 0")
    if hedge.portfolio_beta <= 0:
        raise ValueError("portfolio_beta must be > 0")
    if hedge.hedge_ratio <= 0:
        raise ValueError("hedge_ratio must be > 0")
    if hedge.contract_multiplier <= 0:
        raise ValueError("contract_multiplier must be > 0")
    if hedge.hedge_method not in {"notional", "delta"}:
        raise ValueError("hedge_method only supports notional or delta")
    if hedge.hedge_method == "delta":
        if hedge.put_delta_abs is None or not (0 < hedge.put_delta_abs <= 1):
            raise ValueError("put_delta_abs must be in (0, 1] when hedge_method=delta")
    if cost.fee_per_contract < 0 or cost.slippage_per_contract < 0:
        raise ValueError("fee_per_contract and slippage_per_contract must be >= 0")
    if bs_premium_rate <= -1:
        raise ValueError("bs_premium_rate must be > -1")
    if listing_depth < 1:
        raise ValueError("listing_depth must be >= 1")
    if terminal_step <= 0:
        raise ValueError("terminal_step must be > 0")
    if terminal_stop < terminal_start:
        raise ValueError("terminal_stop must be >= terminal_start")


def save_strike_summary_csv(strike_infos: list[StrikeInfo], output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "执行价K",
        "Put价格(点/张)",
        "合约张数",
        "总成本(元)",
        "保护触发跌幅",
    ]
    with output_file.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for info in strike_infos:
            writer.writerow(
                {
                    "执行价K": round(info.strike, 6),
                    "Put价格(点/张)": round(info.put_premium_points, 6),
                    "合约张数": info.contracts,
                    "总成本(元)": round(info.total_entry_cost, 6),
                    "保护触发跌幅": f"{info.trigger_drop:+.2%}",
                }
            )


def _matrix_headers(strike_infos: list[StrikeInfo]) -> list[str]:
    return ["到期标的价格S_T"] + [f"K={info.strike:.2f}" for info in strike_infos]


def save_hedged_pnl_matrix_csv(rows: list[dict], strike_infos: list[StrikeInfo], output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    headers = _matrix_headers(strike_infos)
    with output_file.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            line = {"到期标的价格S_T": round(row["terminal_price"], 6)}
            for cell in row["cells"]:
                line[f"K={cell['strike']:.2f}"] = round(cell["hedged_total_pnl"], 6)
            writer.writerow(line)


def save_improvement_ratio_matrix_csv(
    rows: list[dict], strike_infos: list[StrikeInfo], output_file: Path
) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    headers = _matrix_headers(strike_infos)
    with output_file.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            line = {"到期标的价格S_T": round(row["terminal_price"], 6)}
            for cell in row["cells"]:
                line[f"K={cell['strike']:.2f}"] = f"{cell['improvement_ratio']:.8f}"
            writer.writerow(line)


def save_hedged_pnl_color_matrix_csv(
    rows: list[dict],
    strike_infos: list[StrikeInfo],
    max_abs_hedged_total_pnl: float,
    output_file: Path,
) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    headers = _matrix_headers(strike_infos)
    with output_file.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            line = {"到期标的价格S_T": round(row["terminal_price"], 6)}
            for cell in row["cells"]:
                line[f"K={cell['strike']:.2f}"] = heat_color(
                    cell["hedged_total_pnl"], max_abs_hedged_total_pnl
                )
            writer.writerow(line)


def save_matrix_cells_long_csv(
    rows: list[dict], max_abs_hedged_total_pnl: float, output_file: Path
) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "到期标的价格S_T",
        "情景收益率",
        "执行价K",
        "未对冲总收益(元)",
        "期权损益(元)",
        "对冲后总收益(元)",
        "改善程度(占敞口)",
        "颜色(按对冲后总收益)",
    ]
    with output_file.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            for cell in row["cells"]:
                writer.writerow(
                    {
                        "到期标的价格S_T": round(cell["terminal_price"], 6),
                        "情景收益率": f"{cell['scenario_return']:+.4%}",
                        "执行价K": round(cell["strike"], 6),
                        "未对冲总收益(元)": round(cell["unhedged_total_pnl"], 6),
                        "期权损益(元)": round(cell["option_pnl"], 6),
                        "对冲后总收益(元)": round(cell["hedged_total_pnl"], 6),
                        "改善程度(占敞口)": f"{cell['improvement_ratio']:.8f}",
                        "颜色(按对冲后总收益)": heat_color(
                            cell["hedged_total_pnl"], max_abs_hedged_total_pnl
                        ),
                    }
                )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Put hedge matrix analysis (no optimization)."
    )
    parser.add_argument("--spot-index", type=float, default=8560.84)
    parser.add_argument("--portfolio-value", type=float, default=10_000_000)
    parser.add_argument("--portfolio-beta", type=float, default=1.0)
    parser.add_argument("--hedge-ratio", type=float, default=1.0)
    parser.add_argument("--hedge-method", choices=["notional", "delta"], default="notional")
    parser.add_argument("--put-delta-abs", type=float, default=None)
    parser.add_argument("--contract-multiplier", type=float, default=100.0)

    parser.add_argument("--valuation-date", type=str, default=None, help="YYYY-MM-DD, default today")
    parser.add_argument("--expiry-month", type=str, default=None, help="YYYY-MM")
    parser.add_argument("--listing-depth", type=int, default=12, help="ATM +/- N 档")

    parser.add_argument("--terminal-start", type=float, default=6800.0)
    parser.add_argument("--terminal-stop", type=float, default=9600.0)
    parser.add_argument("--terminal-step", type=float, default=100.0)

    parser.add_argument("--risk-free-rate", type=float, default=0.015)
    parser.add_argument("--dividend-yield", type=float, default=0.0)
    parser.add_argument("--volatility", type=float, default=0.20)
    parser.add_argument(
        "--bs-premium-rate",
        type=float,
        default=0.0,
        help="BS自动定价溢价率，最终Put价格=BS理论价*(1+溢价率)。例如 0.1 表示上浮10%%。",
    )

    parser.add_argument(
        "--pricing-mode", choices=["auto", "manual_flat", "manual_curve"], default="auto"
    )
    parser.add_argument(
        "--manual-premium",
        type=float,
        default=None,
        help="Manual flat put premium in points per contract.",
    )
    parser.add_argument("--manual-premium-csv", type=Path, default=None)

    parser.add_argument("--fee-per-contract", type=float, default=0.0)
    parser.add_argument("--slippage-per-contract", type=float, default=0.0)
    parser.add_argument("--out-dir", type=Path, default=Path("outputs"))
    return parser


def main() -> None:
    args = build_parser().parse_args()

    valuation_date = (
        datetime.strptime(args.valuation_date, "%Y-%m-%d").date()
        if args.valuation_date
        else date.today()
    )
    expiry_choices = default_exchange_expiry_months(valuation_date)
    selected_expiry_month = args.expiry_month or expiry_choices[0]
    maturity_years, expiry_date = maturity_years_from_exchange_expiry(
        valuation_date=valuation_date, expiry_month=selected_expiry_month
    )

    market = MarketParams(
        spot_index=args.spot_index,
        risk_free_rate=args.risk_free_rate,
        dividend_yield=args.dividend_yield,
        volatility=args.volatility,
        maturity_years=maturity_years,
    )
    hedge = HedgeParams(
        portfolio_value=args.portfolio_value,
        portfolio_beta=args.portfolio_beta,
        hedge_ratio=args.hedge_ratio,
        hedge_method=args.hedge_method,
        put_delta_abs=args.put_delta_abs,
        contract_multiplier=args.contract_multiplier,
    )
    cost = TradingCostParams(
        fee_per_contract=args.fee_per_contract,
        slippage_per_contract=args.slippage_per_contract,
    )

    validate_inputs(
        market=market,
        hedge=hedge,
        cost=cost,
        bs_premium_rate=args.bs_premium_rate,
        listing_depth=args.listing_depth,
        terminal_start=args.terminal_start,
        terminal_stop=args.terminal_stop,
        terminal_step=args.terminal_step,
    )

    manual_curve = (
        load_manual_premium_curve(args.manual_premium_csv)
        if args.pricing_mode == "manual_curve"
        else None
    )

    strikes = build_exchange_listed_strikes(
        spot_index=market.spot_index,
        listing_depth=args.listing_depth,
    )
    terminal_prices = frange(args.terminal_start, args.terminal_stop, args.terminal_step)
    strike_infos = build_strike_infos(
        strikes=strikes,
        market=market,
        hedge=hedge,
        cost=cost,
        pricing_mode=args.pricing_mode,
        bs_premium_rate=args.bs_premium_rate,
        manual_premium=args.manual_premium,
        manual_curve=manual_curve,
    )
    rows, min_hedged_total_pnl, max_hedged_total_pnl, max_abs_hedged_total_pnl = build_matrix_cells(
        terminal_prices=terminal_prices,
        strike_infos=strike_infos,
        market=market,
        hedge=hedge,
    )

    strike_summary_file = args.out_dir / "strike_summary.csv"
    hedged_matrix_file = args.out_dir / "hedged_pnl_matrix.csv"
    ratio_matrix_file = args.out_dir / "improvement_ratio_matrix.csv"
    color_matrix_file = args.out_dir / "hedged_pnl_color_matrix.csv"
    cells_long_file = args.out_dir / "matrix_cells_long.csv"

    save_strike_summary_csv(strike_infos, strike_summary_file)
    save_hedged_pnl_matrix_csv(rows, strike_infos, hedged_matrix_file)
    save_improvement_ratio_matrix_csv(rows, strike_infos, ratio_matrix_file)
    save_hedged_pnl_color_matrix_csv(
        rows, strike_infos, max_abs_hedged_total_pnl, color_matrix_file
    )
    save_matrix_cells_long_csv(rows, max_abs_hedged_total_pnl, cells_long_file)

    exposure = hedge.portfolio_value * hedge.portfolio_beta
    print("=" * 88)
    print("Put 对冲矩阵分析结果（无最优化）")
    print("=" * 88)
    print(
        f"估值日={valuation_date}, 到期月={selected_expiry_month}, 到期日={expiry_date}, 到期年限={market.maturity_years:.6f} 年"
    )
    print(
        f"执行价模式=交易所挂牌挡位, ATM±{args.listing_depth}档, 档位数={len(strike_infos)}"
    )
    print(
        f"到期价格网格: {args.terminal_start:.2f} ~ {args.terminal_stop:.2f}, 步长={args.terminal_step:.2f}, 情景数={len(rows)}"
    )
    print(
        f"定价模式={args.pricing_mode}, 单张交易成本(元)={cost.fee_per_contract + cost.slippage_per_contract:.2f}, 对冲敞口(元)={exposure:,.2f}"
    )
    if args.pricing_mode == "auto":
        print(
            f"BS溢价率={args.bs_premium_rate:.2%}，自动定价最终价格=BS理论价*(1+{args.bs_premium_rate:.6f})"
        )
    if selected_expiry_month not in expiry_choices:
        print(f"提示: 到期月 {selected_expiry_month} 不在常见挂牌月份 {expiry_choices}，已按输入月份计算。")
    if args.pricing_mode == "manual_flat":
        print("提示: manual_flat 对所有执行价使用同一价格，建议优先使用 manual_curve。")
    print(
        f"总收益色阶范围(元): {min_hedged_total_pnl:+,.2f} ~ {max_hedged_total_pnl:+,.2f}"
    )
    print("-" * 88)
    print(f"执行价成本摘要: {strike_summary_file.resolve()}")
    print(f"对冲后总收益矩阵: {hedged_matrix_file.resolve()}")
    print(f"改善比例矩阵: {ratio_matrix_file.resolve()}")
    print(f"总收益颜色矩阵: {color_matrix_file.resolve()}")
    print(f"矩阵长表(含颜色): {cells_long_file.resolve()}")


if __name__ == "__main__":
    main()
