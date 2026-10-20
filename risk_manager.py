import math

class RiskManager:
    def __init__(self, account_balance: float, max_risk_per_trade: float = 0.01):
        """
        account_balance: Total capital in the account.
        max_risk_per_trade: Maximum percentage of the account to risk on a single trade.
        """
        self.account_balance = account_balance
        self.max_risk = max_risk_per_trade

    def calculate_position_size(self, entry_price: float, stop_loss_price: float) -> int:
        """
        Calculates the maximum number of shares to purchase so that if the 
        stop-loss is triggered, the total loss does not exceed the max_risk percentage.
        """
        if entry_price <= 0 or stop_loss_price <= 0:
            raise ValueError("Prices must be greater than zero.")
            
        risk_amount = self.account_balance * self.max_risk
        risk_per_share = abs(entry_price - stop_loss_price)
        
        if risk_per_share == 0:
            print("⚠️ Stop loss cannot equal entry price.")
            return 0
            
        # Use math.floor to avoid buying fractional shares that exceed risk limits
        shares = math.floor(risk_amount / risk_per_share)
        return shares

if __name__ == "__main__":
    # Test the module using your Alpaca paper balance
    rm = RiskManager(account_balance=400000)
    
    entry = 150.00
    stop = 145.00
    
    shares_to_buy = rm.calculate_position_size(entry_price=entry, stop_loss_price=stop)
    
    print(f"--- RISK MANAGEMENT PLAN ---")
    print(f"Account Balance:      ${rm.account_balance:,.2f}")
    print(f"Max Risk Allowed:     ${rm.account_balance * rm.max_risk:,.2f} (1%)")
    print(f"Trade Entry Price:    ${entry:.2f}")
    print(f"Trade Stop Loss:      ${stop:.2f}")
    print(f"Risk per Share:       ${abs(entry - stop):.2f}")
    print(f"Target Position Size: {shares_to_buy} shares")