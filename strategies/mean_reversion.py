import numpy as np
import pandas as pd

class BollingerMeanReversion:
    def __init__(self, window: int = 20, num_std: float = 2.0):
        self.window = window
        self.num_std = num_std

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Generates mean-reverting signals based on Bollinger Band (Z-Score) extremes.
        1 (Long)  = Price crosses below lower band
        -1 (Short) = Price crosses above upper band
        0 (Flat)  = Price crosses the mean (take profit)
        """
        data = df.copy()
        
        # Calculate Rolling Mean and Standard Deviation
        data['sma'] = data['close'].rolling(window=self.window).mean()
        data['std'] = data['close'].rolling(window=self.window).std()
        
        # Calculate Upper and Lower Bands
        data['upper_band'] = data['sma'] + (data['std'] * self.num_std)
        data['lower_band'] = data['sma'] - (data['std'] * self.num_std)
        
        # Initialize signal column
        data['signal'] = np.nan
        
        # Entry Logic
        data.loc[data['close'] < data['lower_band'], 'signal'] = 1   # Buy the dip
        data.loc[data['close'] > data['upper_band'], 'signal'] = -1  # Short the peak
        
        # Exit Logic (Revert to mean)
        data.loc[(data['close'] > data['sma']) & (data['close'].shift(1) <= data['sma'].shift(1)), 'signal'] = 0
        data.loc[(data['close'] < data['sma']) & (data['close'].shift(1) >= data['sma'].shift(1)), 'signal'] = 0
        
        # Forward fill signals to hold positions until an exit/flip occurs, then fill remaining NaNs with 0
        data['signal'] = data['signal'].ffill().fillna(0)
        
        return data
    