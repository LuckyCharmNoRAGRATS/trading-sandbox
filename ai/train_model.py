import os
import joblib
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, classification_report

# Import your local AI modules
from triple_barrier import TripleBarrierLabeler
from meta_labeler import MetaLabelFilter

# Setup institutional directories
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT_DIR / "data" / "SPY_1Day_365d.parquet"
MODEL_DIR = ROOT_DIR / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

def train_meta_model():
    if not DATA_PATH.exists():
        print(f"❌ Missing data cache: {DATA_PATH}. Run data_fetcher.py first.")
        return

    print("1. Loading historical Parquet data...")
    df = pd.read_parquet(DATA_PATH)
    
    # Placeholder: Generate dummy signals to represent your base strategy firing
    # (1 = Long, -1 = Short, 0 = Flat)
    df['sma_20'] = df['close'].rolling(20).mean()
    df['sma_50'] = df['close'].rolling(50).mean()
    df['signal'] = np.where(df['sma_20'] > df['sma_50'], 1, -1)
    df['signal'] = df['signal'].shift(1).fillna(0) # Prevent look-ahead bias
    
    print("2. Engineering Features and Triple Barrier Labels...")
    ai_filter = MetaLabelFilter()
    features_df = ai_filter.generate_features(df)
    
    labeler = TripleBarrierLabeler(profit_target_pct=0.02, stop_loss_pct=0.01, max_hold_bars=10)
    labeled_df = labeler.apply_barriers(df, signal_col='signal')
    
    # Merge features and labels
    dataset = features_df.join(labeled_df['target_label']).dropna()
    
    # Filter dataset strictly to rows where the base strategy signaled a trade
    train_data = dataset[dataset['signal'] != 0].copy()
    
    # Binary Classification Task: 1 (Hit Profit Target) vs 0 (Hit Stop Loss or Time Expired)
    train_data['binary_target'] = np.where(train_data['target_label'] == 1, 1, 0)
    
    # Define strictly contextual features (exclude raw price to prevent overfitting)
    feature_cols = ['returns_1d', 'returns_5d', 'volatility_20d', 'price_to_sma', 'hurst']
    X = train_data[feature_cols]
    y = train_data['binary_target']
    
    if len(X) < 50:
        print("⚠️ Not enough labeled trades to train. Increase your data timeframe.")
        return

    print("3. Executing Purged Time-Series Cross-Validation...")
    # gap=10 strictly drops the overlapping hold period between train and test sets
    tscv = TimeSeriesSplit(n_splits=5, gap=10)
    model = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42, class_weight='balanced')
    
    fold = 1
    for train_index, test_index in tscv.split(X):
        X_train, X_test = X.iloc[train_index], X.iloc[test_index]
        y_train, y_test = y.iloc[train_index], y.iloc[test_index]
        
        model.fit(X_train, y_train)
        preds = model.predict(X_test)
        acc = accuracy_score(y_test, preds)
        print(f"   Fold {fold} OOS Accuracy: {acc:.2%}")
        fold += 1
        
    print("\n4. Training Final Production Model...")
    model.fit(X, y)
    
    model_path = MODEL_DIR / "rf_meta_model.pkl"
    joblib.dump(model, model_path)
    print(f"✅ Institutional Meta-Labeler successfully trained and saved to {model_path}")

if __name__ == "__main__":
    train_meta_model()