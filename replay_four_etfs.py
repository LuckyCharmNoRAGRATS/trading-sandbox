import pandas as pd
import numpy as np
from pathlib import Path

symbols = ['spy','qqq','gld','tlt']
bars = {s: pd.read_csv(Path('ETFs') / f'{s}.us.txt', index_col='Date').sort_index() for s in symbols}
def panel(field):
    return pd.concat({s: b[field] for s,b in bars.items()},axis=1).dropna()
close = panel('Close')
opening = panel('Open').loc[close.index]
assert close.index.is_unique and (close > 0).all().all() and (opening > 0).all().all()
z = (close-close.rolling(20).mean())/close.rolling(20).std()
for mode,fee in [('strategy',0.0004),('hold',0.0004),('strategy',0)]:
    cash = 100000.
    qty = np.zeros(4)
    equity = [cash]
    entries = np.zeros(4)
    exits = np.zeros(4)
    for i in range(20,len(close)):
        op = opening.iloc[i].to_numpy()
        prev = close.iloc[i-1].to_numpy()
        score = z.iloc[i-1].to_numpy()
        trade = np.zeros(4)
        if mode == 'strategy':
            exit_signal = (qty != 0) & (abs(score)<0.5)
            entry = (qty == 0) & (abs(score)>=2)
            trade[exit_signal] = -qty[exit_signal]
            trade[entry] = -np.sign(score[entry])*np.floor(0.05*equity[-1]/prev[entry])
        elif i == 20:
            trade = np.floor(5000/(op*(1+fee)))
        entries += (qty==0)&(trade!=0)
        exits += (qty!=0)&(trade==-qty)
        cash -= trade@op + abs(trade)@op*fee
        qty += trade
        equity.append(cash+qty@close.iloc[i].to_numpy())
    e=pd.Series(equity)
    print(mode, fee,close.index[20],close.index[-1],len(e)-1,e.iloc[-1],(1-e/e.cummax()).max(),entries,exits,qty)
