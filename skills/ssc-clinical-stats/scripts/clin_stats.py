"""
临床统计工具：生存分析(KM+logrank, Cox)、回归(logistic/linear)、Meta 分析合并计算。
适合 SSc 临床队列/随访/预后/系统综述。
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def km_survival(df_csv, time_col, event_col, group_col=None, out_png="km.png"):
    """Kaplan-Meier 生存曲线（可分组）+ logrank 检验。event: 1=事件, 0=删失。"""
    from lifelines import KaplanMeierFitter
    from lifelines.statistics import multivariate_logrank_test
    df = pd.read_csv(df_csv)
    fig, ax = plt.subplots(figsize=(6, 5))
    kmf = KaplanMeierFitter()
    if group_col:
        for name, g in df.groupby(group_col):
            kmf.fit(g[time_col], g[event_col], label=str(name))
            kmf.plot_survival_function(ax=ax)
        res = multivariate_logrank_test(df[time_col], df[group_col], df[event_col])
        msg = f"logrank p={res.p_value:.4f}"
    else:
        kmf.fit(df[time_col], df[event_col], label="All")
        kmf.plot_survival_function(ax=ax)
        msg = "（未分组）"
    ax.set_xlabel(time_col); ax.set_ylabel("Survival probability"); ax.set_title("Kaplan-Meier")
    fig.tight_layout(); fig.savefig(out_png, dpi=200, bbox_inches="tight"); plt.close(fig)
    return f"KM 曲线已保存：{out_png}；{msg}"


def cox_regression(df_csv, time_col, event_col, covariates, out_csv="cox.csv"):
    """Cox 比例风险回归。covariates: 协变量列名列表。输出 HR + 95%CI + p。"""
    from lifelines import CoxPHFitter
    df = pd.read_csv(df_csv)
    cols = [time_col, event_col] + list(covariates)
    d = df[cols].dropna()
    cph = CoxPHFitter()
    cph.fit(d, duration_col=time_col, event_col=event_col)
    summ = cph.summary[["exp(coef)", "exp(coef) lower 95%", "exp(coef) upper 95%", "p"]].copy()
    summ.columns = ["HR", "HR_lower95", "HR_upper95", "p"]
    summ.to_csv(out_csv)
    lines = [f"- {i}: HR={r.HR:.2f} [{r.HR_lower95:.2f}, {r.HR_upper95:.2f}], p={r.p:.3g}"
             for i, r in summ.iterrows()]
    return f"Cox 回归完成：{out_csv}\n" + "\n".join(lines)


def regression(df_csv, outcome_col, predictor_cols, kind="logistic", out_csv="reg.csv"):
    """logistic（二分类结局）或 linear（连续结局）回归。返回系数/OR + 95%CI + p。"""
    import statsmodels.api as sm
    df = pd.read_csv(df_csv)
    d = df[[outcome_col] + list(predictor_cols)].dropna()
    X = sm.add_constant(d[list(predictor_cols)].astype(float))
    y = d[outcome_col].astype(float)
    if kind == "logistic":
        model = sm.Logit(y, X).fit(disp=0)
        params = np.exp(model.params); ci = np.exp(model.conf_int())
        label = "OR"
    else:
        model = sm.OLS(y, X).fit()
        params = model.params; ci = model.conf_int()
        label = "coef"
    out = pd.DataFrame({label: params, f"{label}_low95": ci[0], f"{label}_high95": ci[1],
                        "p": model.pvalues})
    out.to_csv(out_csv)
    lines = [f"- {i}: {label}={r[label]:.3f} [{r[f'{label}_low95']:.3f}, {r[f'{label}_high95']:.3f}], p={r.p:.3g}"
             for i, r in out.iterrows() if i != "const"]
    return f"{kind} 回归完成：{out_csv}\n" + "\n".join(lines)


def meta_analysis(df_csv, effect_col="effect", se_col=None,
                  lower_col="lower", upper_col="upper",
                  study_col="study", log_scale=True, out_csv="meta.csv"):
    """Meta 分析合并（固定效应 + 随机效应 DerSimonian-Laird）。
    效应可给 se，或给 95%CI（自动反推 se）。log_scale=True 表示效应是比值型(OR/HR/RR)，会在 log 域合并。"""
    df = pd.read_csv(df_csv)
    eff = df[effect_col].astype(float).values
    if se_col and se_col in df.columns:
        se = df[se_col].astype(float).values
        yi = np.log(eff) if log_scale else eff
    else:
        lo = df[lower_col].astype(float).values
        hi = df[upper_col].astype(float).values
        if log_scale:
            yi = np.log(eff)
            se = (np.log(hi) - np.log(lo)) / (2 * 1.96)
        else:
            yi = eff
            se = (hi - lo) / (2 * 1.96)
    wi = 1.0 / se**2

    # 固定效应
    fe = np.sum(wi * yi) / np.sum(wi)
    fe_se = np.sqrt(1.0 / np.sum(wi))
    # 异质性
    Q = np.sum(wi * (yi - fe)**2)
    k = len(yi)
    I2 = max(0.0, (Q - (k - 1)) / Q) * 100 if Q > 0 else 0.0
    tau2 = max(0.0, (Q - (k - 1)) / (np.sum(wi) - np.sum(wi**2) / np.sum(wi))) if k > 1 else 0.0
    # 随机效应
    wi_re = 1.0 / (se**2 + tau2)
    re = np.sum(wi_re * yi) / np.sum(wi_re)
    re_se = np.sqrt(1.0 / np.sum(wi_re))

    def back(x): return np.exp(x) if log_scale else x
    pooled = pd.DataFrame({
        "model": ["Fixed", "Random(DL)"],
        "pooled_effect": [back(fe), back(re)],
        "ci_low": [back(fe - 1.96 * fe_se), back(re - 1.96 * re_se)],
        "ci_high": [back(fe + 1.96 * fe_se), back(re + 1.96 * re_se)],
    })
    pooled.to_csv(out_csv, index=False)
    return (f"Meta 分析完成：{out_csv}\n"
            f"- 固定效应：{back(fe):.3f} [{back(fe-1.96*fe_se):.3f}, {back(fe+1.96*fe_se):.3f}]\n"
            f"- 随机效应(DL)：{back(re):.3f} [{back(re-1.96*re_se):.3f}, {back(re+1.96*re_se):.3f}]\n"
            f"- 异质性 I2={I2:.1f}%，Q={Q:.2f}（k={k}）\n"
            f"可用 ssc-data-figure 的 forest_plot 画森林图。")
