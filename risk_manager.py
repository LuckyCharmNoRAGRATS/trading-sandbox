import math
import os


class RiskManager:
    def __init__(
        self,
        account_balance: float,
        max_risk_per_trade: float = 0.01,
        max_position_pct: float = float(os.getenv("MAX_POSITION_SIZE", 0.05)),
    ):
        """
        account_balance: Total capital in the account.
        max_risk_per_trade: Maximum percentage of the account to risk on a single trade (default 1%).
        max_position_pct: Maximum notional concentration per position (default 5%).
        """
        self.account_balance = account_balance
        self.max_risk = max_risk_per_trade
        self.max_position_pct = max_position_pct

    def calculate_position_size(self, entry_price: float, stop_loss_price: float) -> int:
        """
        Calculates the maximum number of shares to purchase so that:
        1. If the stop-loss is triggered, the loss does not exceed max_risk_per_trade (1%).
        2. The total notional value of the position does not exceed max_position_pct (5%).
        """
        if entry_price <= 0 or stop_loss_price <= 0:
            raise ValueError("Prices must be greater than zero.")

        risk_per_share = abs(entry_price - stop_loss_price)
        if risk_per_share == 0:
            return 0

        risk_amount = self.account_balance * self.max_risk
        shares_by_risk = math.floor(risk_amount / risk_per_share)

        max_notional = self.account_balance * self.max_position_pct
        shares_by_notional = math.floor(max_notional / entry_price)

        return max(0, min(shares_by_risk, shares_by_notional))

    def size_position(self, entry_price: float, stop_loss_price: float) -> int:
        """Compatibility alias matching quant_engine.RiskEngine.size_position."""
        return self.calculate_position_size(entry_price=entry_price, stop_loss_price=stop_loss_price)


if __name__ == "__main__":
    rm = RiskManager(account_balance=400000)

    entry = 150.00
    stop = 145.00

    shares_to_buy = rm.calculate_position_size(entry_price=entry, stop_loss_price=stop)

    print("--- RISK MANAGEMENT PLAN ---")
    print(f"Account Balance:      ${rm.account_balance:,.2f}")
    print(f"Max Risk Allowed:     ${rm.account_balance * rm.max_risk:,.2f} ({rm.max_risk:.0%})")
    print(f"Max Notional Allowed: ${rm.account_balance * rm.max_position_pct:,.2f} ({rm.max_position_pct:.0%})")
    print(f"Trade Entry Price:    ${entry:.2f}")
    print(f"Trade Stop Loss:      ${stop:.2f}")
    print(f"Risk per Share:       ${abs(entry - stop):.2f}")
    print(f"Target Position Size: {shares_to_buy} shares")