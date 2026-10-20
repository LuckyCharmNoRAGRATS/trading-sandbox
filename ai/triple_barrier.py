import numpy as np
import pandas as pd

class TripleBarrierLabeler:
    def __init__(self, profit_target_pct: float = 0.02, stop_loss_pct: float = 0.01, max_hold_bars: int = 10):
        """
        Initializes the three barriers:
        1. Upper Barrier (Profit Target)
        2. Lower Barrier (Stop Loss)
        3. Vertical Barrier (Maximum Holding Period)
        """
        self.pt = profit_target_pct
        self.sl = stop_loss_pct
        self.max_hold = max_hold_bars

    def apply_barriers(self, df: pd.DataFrame, signal_col: str = 'signal') -> pd.DataFrame:
        """
        Evaluates historical signals and assigns a class label:
         1: Hit profit target
        -1: Hit stop loss
         0: Hit vertical time barrier (expired)
        """
        data = df.copy()
        data['target_label'] = np.nan
        
        # Extract indices where the base strategy generated a trade
        trade_indices = data[data[signal_col] != 0].index
        
        for i in trade_indices:
            start_loc = data.index.get_loc(i)
            entry_price = float(data.iloc[start_loc]['close'])
            direction = int(data.iloc[start_loc][signal_col])
            
            # Define dynamic barriers based on entry price
            if direction == 1:  # Long Position
                upper = entry_price * (1 + self.pt)
                lower = entry_price * (1 - self.sl)
            else:               # Short Position
                upper = entry_price * (1 - self.sl)
                lower = entry_price * (1 + self.pt)
                
            # Define Vertical Time Barrier
            end_loc = min(start_loc + self.max_hold, len(data) - 1)
            path = data.iloc[start_loc + 1 : end_loc + 1]['close']
            
            # Default to time expiration
            label = 0 
            
            # Scan the price path for barrier collisions
            for current_price in path:
                if (direction == 1 and current_price >= upper) or (direction == -1 and current_price <= upper):
                    label = 1
                    break
                elif (direction == 1 and current_price <= lower) or (direction == -1 and current_price >= lower):
                    label = -1
                    break
                    
            data.loc[i, 'target_label'] = label
            
        return data