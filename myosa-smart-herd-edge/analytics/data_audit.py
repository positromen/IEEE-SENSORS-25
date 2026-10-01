"""
Smart Herd Edge - data-quality audit of the v2 field-trial log.

Reads the raw ThingsBoard export (one sensor group per row, ';' separated),
re-assembles it into one sample per acquisition cycle, applies the same
physical-plausibility rules that the v3 collar firmware now runs on-device
(see firmware/smart_herd_edge/edge_core.h), and writes:

  analytics/out/aligned_samples.csv   - one row per cycle, used by the C++ replay
  analytics/out/audit_report.json     - fault statistics quoted in the blog
  analytics/out/fig_*.png             - figures used in the blog and video

Usage:
    python analytics/data_audit.py [--csv dataset/field-trial-2025-01-17.csv]
"""
import argparse
import json
import os

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.ensemble import IsolationForest

G = 9.80665

# Plausibility limits - must match herd::Config defaults in edge_core.h
ACCEL_LIMIT = 4.0 * G      # m/s^2, an animal collar never sustains > 4 g per axis
GYRO_LIMIT = 500.0         # deg/s, full-scale of the MPU6050 range used in v2
TEMP_RANGE = (-10.0, 50.0) # degC
PRESS_RANGE = (80.0, 110.0)  # kPa
LIGHT_SENTINEL = 37889.0   # value the APDS9960 driver returns on a failed read

COLS = ["accel_x", "accel_y", "accel_z", "gyro_x", "gyro_y", "gyro_z",
        "temperature_C", "pressure_kPa", "proximity", "ambient_light"]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "analytics", "out")

plt.rcParams.update({
    "figure.dpi": 110, "font.size": 10, "axes.spines.top": False,
    "axes.spines.right": False, "axes.grid": True, "grid.alpha": 0.25,
})


def load_raw(path):
    df = pd.read_csv(path, sep=";", parse_dates=["Timestamp"])
    return df.sort_values("Timestamp").reset_index(drop=True)


def align_cycles(raw, cycle_s=6):
    """The v2 firmware published each sensor group as its own MQTT message
    roughly every 2 s, so one full reading is spread over ~3 rows. Bin into
    fixed cycles and keep the first valid value of every column."""
    t0 = raw["Timestamp"].iloc[0]
    sec = (raw["Timestamp"] - t0).dt.total_seconds()
    raw = raw.assign(cycle=(sec // cycle_s).astype(int))
    agg = raw.groupby("cycle")[COLS].first()
    agg["t_s"] = agg.index * cycle_s
    agg["timestamp"] = t0 + pd.to_timedelta(agg["t_s"], unit="s")
    return agg.reset_index(drop=True)


def fault_flags(s):
    f = pd.DataFrame(index=s.index)
    acc = s[["accel_x", "accel_y", "accel_z"]]
    f["accel_range"] = (acc.abs() > ACCEL_LIMIT).any(axis=1)
    f["accel_dropout"] = (acc == 0).all(axis=1)          # gravity cannot vanish
    f["gyro_range"] = (s[["gyro_x", "gyro_y", "gyro_z"]].abs() >= GYRO_LIMIT).any(axis=1)
    f["temp"] = ~s["temperature_C"].between(*TEMP_RANGE) & s["temperature_C"].notna() \
        | (s["temperature_C"] == 0)
    f["pressure"] = ~s["pressure_kPa"].between(*PRESS_RANGE) & s["pressure_kPa"].notna()
    f["light"] = s["ambient_light"] == LIGHT_SENTINEL
    f["any_imu"] = f[["accel_range", "accel_dropout", "gyro_range"]].any(axis=1)
    return f


def write_replay_csv(s, path):
    """Flat CSV consumed by firmware/test/replay.cpp (empty field = missing)."""
    r = pd.DataFrame({
        "t_ms": (s["t_s"] * 1000).astype(np.int64),
        "ax": s["accel_x"], "ay": s["accel_y"], "az": s["accel_z"],
        "gx": s["gyro_x"], "gy": s["gyro_y"], "gz": s["gyro_z"],
        "temp_c": s["temperature_C"], "pressure_kpa": s["pressure_kPa"],
        "proximity": s["proximity"], "light": s["ambient_light"],
    })
    r.to_csv(path, index=False, na_rep="nan", float_format="%.3f")


def isolation_forest_check(s, flags):
    """Re-run the v2 dashboard's anomaly detector (Isolation Forest on accel_x,
    contamination=0.1) and measure how many of its 'anomalies' are simply
    acquisition faults that the v3 validity gate removes before any ML runs."""
    m = s["accel_x"].notna()
    x = s.loc[m, "accel_x"].values.reshape(-1, 1)
    yhat = IsolationForest(contamination=0.1, random_state=42).fit_predict(x)
    anom = pd.Series(yhat == -1, index=s.index[m])
    # judge each flag against accel_x's own validity, not the whole IMU sample
    faulty = s.loc[m, "accel_x"].abs() > ACCEL_LIMIT
    return {
        "samples": int(m.sum()),
        "flagged_by_isolation_forest": int(anom.sum()),
        "flagged_that_are_sensor_faults": int((anom & faulty).sum()),
        "accel_x_fault_rate": round(float(faulty.mean()), 3),
        "share_of_flags_that_are_faults": round(float((anom & faulty).sum() / max(anom.sum(), 1)), 3),
    }


def fig_raw_vs_clean(s, flags, path):
    t = (s["timestamp"] - s["timestamp"].iloc[0]).dt.total_seconds() / 3600
    mag = np.sqrt((s[["accel_x", "accel_y", "accel_z"]] ** 2).sum(axis=1, min_count=3))
    bad = flags["any_imu"]
    fig, ax = plt.subplots(2, 1, figsize=(10, 5.2), sharex=True)
    ax[0].scatter(t[~bad], mag[~bad], s=4, c="#2b7bba", label="plausible")
    ax[0].scatter(t[bad], mag[bad], s=7, c="#d7301f", label="rejected by validity gate")
    ax[0].set_yscale("symlog", linthresh=20)
    ax[0].set_ylabel("|a| (m/s², symlog)")
    ax[0].set_title("v2 raw collar log: acceleration magnitude (field trial, 17 Jan 2025)")
    ax[0].legend(loc="upper center", frameon=False, markerscale=2)
    ax[1].scatter(t[~bad], mag[~bad] / G, s=4, c="#2b7bba")
    ax[1].axhline(1.0, color="k", lw=0.8, ls="--")
    ax[1].set_ylabel("|a| (g), clean")
    ax[1].set_xlabel("hours since start of logging")
    ax[1].set_title("same signal after on-collar validation: physically meaningful 0–4 g band")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def fig_fault_breakdown(report, path):
    labels = {"accel_range": "accel out of range\n(raw-count glitch)",
              "accel_dropout": "accel stuck at 0\n(I²C dropout)",
              "gyro_range": "gyro saturated",
              "temp": "temperature\nread failure",
              "pressure": "pressure\nread failure",
              "light": "light sensor\nsentinel"}
    k = list(labels)
    v = [report["fault_rate_pct"][x] for x in k]
    fig, ax = plt.subplots(figsize=(9, 3.6))
    bars = ax.barh([labels[x] for x in k], v, color=["#d7301f", "#fc8d59", "#fdbb84", "#9ecae1", "#6baed6", "#c6dbef"])
    for b, val in zip(bars, v):
        ax.text(b.get_width() + 0.2, b.get_y() + b.get_height() / 2, f"{val:.1f}%", va="center")
    ax.invert_yaxis()
    ax.set_xlabel("% of acquisition cycles affected")
    ax.set_title("Where the v2 data went wrong: fault classes detected by the v3 validity gate")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=os.path.join(ROOT, "dataset", "field-trial-2025-01-17.csv"))
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)

    raw = load_raw(args.csv)
    s = align_cycles(raw)
    flags = fault_flags(s)
    write_replay_csv(s, os.path.join(OUT, "aligned_samples.csv"))

    has_imu = s["accel_x"].notna() | s["accel_y"].notna()
    report = {
        "raw_rows": int(len(raw)),
        "log_start": str(raw["Timestamp"].min()),
        "log_end": str(raw["Timestamp"].max()),
        "log_hours": round((raw["Timestamp"].max() - raw["Timestamp"].min()).total_seconds() / 3600, 2),
        "cycles": int(len(s)),
        "cycles_with_imu": int(has_imu.sum()),
        "fault_rate_pct": {c: round(100 * float(flags.loc[has_imu, c].mean()), 2)
                           for c in ["accel_range", "accel_dropout", "gyro_range", "temp", "pressure", "light"]},
        "axis_out_of_range_pct": {c: round(100 * float((s.loc[has_imu, c].abs() > ACCEL_LIMIT).mean()), 2)
                                  for c in ["accel_x", "accel_y", "accel_z"]},
        "imu_cycles_rejected_pct": round(100 * float(flags.loc[has_imu, "any_imu"].mean()), 2),
        "isolation_forest_v2": isolation_forest_check(s, flags),
    }
    with open(os.path.join(OUT, "audit_report.json"), "w") as fh:
        json.dump(report, fh, indent=2)

    fig_raw_vs_clean(s, flags, os.path.join(OUT, "fig_raw_vs_clean.png"))
    fig_fault_breakdown(report, os.path.join(OUT, "fig_fault_breakdown.png"))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
