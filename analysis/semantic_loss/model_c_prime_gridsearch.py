"""Model C' パラメータ探索（グリッドサーチ）

HA20結合データ (20件) を使い、5モデルのρを比較する再現可能スクリプト。
"""
from __future__ import annotations

import pandas as pd
import numpy as np
from scipy.stats import spearmanr
from itertools import product
from sklearn.linear_model import LinearRegression
import warnings

warnings.filterwarnings("ignore")

# --- Step 1: データ読み込みと損失変数 ---
df = pd.read_csv("analysis/semantic_loss/ha20_merged_for_model_c.csv")

L_P = 1 - df["system_hit_rate"].values       # 命題損失
L_struct = df["fail_max"].values              # 構造最大損失
L_R = df["delta_e_full"].values               # 参照損失（ΔE）
L_Q = np.maximum(df["f3_flag"].values, df["f4_flag"].values)  # 制約損失
human = df["human_score"].values

alphas = np.arange(0.1, 2.05, 0.1)
betas = np.arange(0.0, 2.05, 0.1)
gammas = np.arange(0.0, 2.05, 0.1)

# --- Step 2: Model C' (L_struct bottleneck) ---
best_rho_c_prime = -1
best_params_c_prime = None

for a, b, g in product(alphas, betas, gammas):
    L_linear = a * L_P + b * L_struct + g * L_R
    L_op = np.maximum(L_struct, L_linear)
    predicted = 5 - 4 * L_op
    rho, _ = spearmanr(predicted, human)
    if not np.isnan(rho) and rho > best_rho_c_prime:
        best_rho_c_prime = rho
        best_params_c_prime = (round(a, 1), round(b, 1), round(g, 1))

print(
    f"Model C': ρ={best_rho_c_prime:.4f}, "
    f"α={best_params_c_prime[0]}, β={best_params_c_prime[1]}, γ={best_params_c_prime[2]}"
)

# --- Step 3: Model C (L_Q bottleneck) ---
best_rho_c = -1
best_params_c = None

for a, b, g in product(alphas, betas, gammas):
    L_linear = a * L_P + b * L_Q + g * L_R
    L_op = np.maximum(L_Q, L_linear)
    predicted = 5 - 4 * L_op
    rho, _ = spearmanr(predicted, human)
    if not np.isnan(rho) and rho > best_rho_c:
        best_rho_c = rho
        best_params_c = (round(a, 1), round(b, 1), round(g, 1))

print(
    f"Model C: ρ={best_rho_c:.4f}, "
    f"α={best_params_c[0]}, β={best_params_c[1]}, γ={best_params_c[2]}"
)

# --- Step 4: Model D (multiplicative) ---
best_rho_d = -1
best_params_d = None

for a, b, g in product(alphas, betas, gammas):
    S = ((1 - L_P) ** a) * ((1 - L_struct) ** b) * ((1 - L_R) ** g)
    predicted = 1 + 4 * S
    rho, _ = spearmanr(predicted, human)
    if not np.isnan(rho) and rho > best_rho_d:
        best_rho_d = rho
        best_params_d = (round(a, 1), round(b, 1), round(g, 1))

print(
    f"Model D: ρ={best_rho_d:.4f}, "
    f"α={best_params_d[0]}, β={best_params_d[1]}, γ={best_params_d[2]}"
)

# --- Step 5: Model B' (linear regression) ---
X = np.column_stack([L_P, L_struct, L_R])
reg = LinearRegression().fit(X, human)
pred_b = reg.predict(X)
rho_b, _ = spearmanr(pred_b, human)

print(
    f"Model B': ρ={rho_b:.4f}, intercept={reg.intercept_:.4f}, "
    f"coefs={[round(c, 4) for c in reg.coef_]}"
)

# --- Step 6: Model A (baseline) ---
pred_a = 5 - 4 * (1 - df["system_hit_rate"].values)
rho_a, _ = spearmanr(pred_a, human)
print(f"Model A: ρ={rho_a:.4f}")

# --- Step 7: 比較テーブル ---
results = pd.DataFrame([
    {
        "model": "A (hit_rate only)",
        "rho": round(rho_a, 4),
        "alpha": "-",
        "beta": "-",
        "gamma": "-",
    },
    {
        "model": "B' (linear reg)",
        "rho": round(rho_b, 4),
        "alpha": round(reg.coef_[0], 4),
        "beta": round(reg.coef_[1], 4),
        "gamma": round(reg.coef_[2], 4),
    },
    {
        "model": "C (L_Q bottleneck)",
        "rho": round(best_rho_c, 4),
        "alpha": best_params_c[0],
        "beta": best_params_c[1],
        "gamma": best_params_c[2],
    },
    {
        "model": "C' (L_struct bottleneck)",
        "rho": round(best_rho_c_prime, 4),
        "alpha": best_params_c_prime[0],
        "beta": best_params_c_prime[1],
        "gamma": best_params_c_prime[2],
    },
    {
        "model": "D (multiplicative)",
        "rho": round(best_rho_d, 4),
        "alpha": best_params_d[0],
        "beta": best_params_d[1],
        "gamma": best_params_d[2],
    },
])

print("\n比較テーブル:")
print(results.to_string(index=False))

results.to_csv("analysis/semantic_loss/gridsearch_results_summary.csv", index=False)
print("\n保存完了: analysis/semantic_loss/gridsearch_results_summary.csv")
