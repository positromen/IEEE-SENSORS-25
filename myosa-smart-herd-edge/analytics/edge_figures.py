"""
Smart Herd Edge - runs the on-collar engine (firmware/smart_herd_edge/edge_core.h)
on (1) the real v2 field-trial log and (2) a scripted 20 Hz bench scenario, then
draws the figures used in the blog and the demo video.

Run analytics/data_audit.py first (it produces analytics/out/aligned_samples.csv).

Usage:
    python analytics/edge_figures.py
Requires g++ (C++17) on the PATH to build firmware/test/replay.cpp.
"""
import json
import os
import subprocess

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "analytics", "out")
FW = os.path.join(ROOT, "firmware")
BIN = os.path.join(OUT, "replay_bin")
G = 9.80665

STATE_COLORS = {"UNKNOWN": "#bdbdbd", "LYING": "#6a51a3", "RESTING": "#2b8cbe",
                "GRAZING": "#41ab5d", "WALKING": "#fdae61", "ACTIVE": "#d7301f"}
ALERTS = {1: "INACTIVITY", 2: "HEAT STRESS", 4: "AGITATION", 8: "FALL / CAST",
          16: "ANOMALY", 32: "SENSOR FAULT"}

plt.rcParams.update({
    "figure.dpi": 110, "font.size": 10, "axes.spines.top": False,
    "axes.spines.right": False, "axes.grid": True, "grid.alpha": 0.25,
})


def build_replay():
    src = os.path.join(FW, "test", "replay.cpp")
    subprocess.run(["g++", "-std=c++17", "-O2", "-I", os.path.join(FW, "smart_herd_edge"),
                    src, "-o", BIN], check=True)


def run_replay(csv, window_ms, tau_s, prefix):
    subprocess.run([BIN, csv, OUT, str(window_ms), str(tau_s), prefix], check=True)
    stats = dict(l.strip().split("=") for l in open(os.path.join(OUT, f"{prefix}_stats.txt")))
    win = pd.read_csv(os.path.join(OUT, f"{prefix}_windows.csv"))
    return {k: float(v) for k, v in stats.items()}, win


def bench_scenario(path, hz=20, seed=7):
    """Scripted 15-minute bench sequence with known ground truth. Each phase
    mimics the collar signal of one behaviour; a burst of the exact v2 raw-count
    glitch (accel_y = +/-1930) is injected to exercise the validity gate."""
    rng = np.random.default_rng(seed)
    phases = [  # (name, seconds)
        ("RESTING", 120), ("GRAZING", 150), ("GLITCH", 40), ("WALKING", 110),
        ("ACTIVE", 170), ("IMPACT", 0.3), ("LYING", 180), ("RESTING", 130),
    ]
    rows, truth = [], []
    t = 0.0
    dt = 1.0 / hz
    head = np.radians(40)
    for name, dur in phases:
        n = int(round(dur * hz))
        for i in range(n):
            ph = 2 * np.pi * t
            noise = rng.normal(0, 0.012 * G, 3)
            if name in ("RESTING", "GLITCH"):
                a = np.array([0.03 * G, -0.02 * G, G]) + noise
            elif name == "GRAZING":
                m = 0.12 * G * np.sin(1.4 * ph) + 0.05 * G * np.sin(0.37 * ph)
                a = np.array([G * np.sin(head) + m, 0.04 * G * np.cos(ph), G * np.cos(head) + m]) + noise
            elif name == "WALKING":
                a = np.array([0.12 * G * np.sin(1.1 * ph), 0.06 * G * np.cos(1.1 * ph),
                              G + 0.16 * G * np.sin(2.2 * ph)]) + noise
            elif name == "ACTIVE":
                a = np.array([0.55 * G * np.sin(2.6 * ph), 0.3 * G * np.cos(2.6 * ph),
                              G + 0.75 * G * np.sin(5.2 * ph)]) + 2 * noise
            elif name == "IMPACT":
                a = np.array([2.1 * G, 1.6 * G, 2.5 * G])
            else:  # LYING: collar rolled ~76 deg, motionless
                a = np.array([0.97 * G, 0.0, 0.24 * G]) + 0.5 * noise
            gyro = rng.normal(0, 1.5, 3) + (60 * np.sin(ph * 2.6) if name == "ACTIVE" else 0)
            if name == "GLITCH" and i % 3 != 0:
                a = a.copy()
                a[1] = 1930.0 * (1 if i % 2 else -1)
            temp = 24.0 + 0.002 * t + rng.normal(0, 0.05)
            rows.append([int(t * 1000), *a, *np.atleast_1d(gyro) * np.ones(3), temp,
                         100.95 + rng.normal(0, 0.01), 10, 140])
            truth.append(name)
            t += dt
    cols = ["t_ms", "ax", "ay", "az", "gx", "gy", "gz", "temp_c", "pressure_kpa", "proximity", "light"]
    df = pd.DataFrame(rows, columns=cols)
    df.to_csv(path, index=False, float_format="%.3f")
    return df, np.array(truth)


def fig_bench(df, truth, win, path):
    t = df["t_ms"].values / 60000
    mag = np.sqrt(df.ax ** 2 + df.ay ** 2 + df.az ** 2) / G
    fig, ax = plt.subplots(3, 1, figsize=(10.5, 6.4), sharex=True,
                           gridspec_kw={"height_ratios": [2.2, 0.7, 0.7]})
    ok = mag < 4
    ax[0].plot(t[ok], mag[ok], lw=0.4, color="#2b7bba")
    ax[0].scatter(t[~ok], np.full((~ok).sum(), 3.9), s=3, c="#d7301f", label="raw-count glitch (rejected)")
    ax[0].set_ylim(0, 4.2)
    ax[0].set_ylabel("|a| (g)")
    ax[0].set_title("Bench scenario at 20 Hz through the on-collar engine (edge_core.h)")
    ax[0].legend(loc="upper left", frameon=False, markerscale=3)
    # ground truth ribbon
    edges = np.flatnonzero(np.r_[True, truth[1:] != truth[:-1], True])
    for a, b in zip(edges[:-1], edges[1:]):
        name = truth[a]
        c = {"GLITCH": "#636363", "IMPACT": "#000000"}.get(name, STATE_COLORS.get(name, "#999"))
        ax[1].axvspan(t[a], t[min(b, len(t) - 1)], color=c, lw=0)
    ax[1].set_yticks([])
    ax[1].set_ylabel("scripted\ntruth", rotation=0, ha="right", va="center")
    # engine output ribbon
    te = win["t_end_s"].values / 60
    ts = np.r_[0, te[:-1]]
    for a, b, s in zip(ts, te, win["state"]):
        ax[2].axvspan(a, b, color=STATE_COLORS[s], lw=0)
    ax[2].set_yticks([])
    ax[2].set_ylabel("collar\noutput", rotation=0, ha="right", va="center")
    ax[2].set_xlabel("minutes")
    for _, r in win[win.alerts > 0].iterrows():
        for bit, name in ALERTS.items():
            if int(r.alerts) & bit:
                x = r.t_end_s / 60
                ax[0].axvline(x, color="k", lw=0.8, ls=":")
                ax[0].text(x, 4.05, name, rotation=0, ha="center", fontsize=8, fontweight="bold")
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in STATE_COLORS.values()]
    fig.legend(handles, list(STATE_COLORS), loc="lower center", ncol=6, frameon=False)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(path)
    plt.close(fig)


def bench_accuracy(df, truth, win):
    """Window-level agreement between the engine's state and the scripted truth
    (majority truth label inside each window; glitch/impact windows excluded)."""
    t_s = df["t_ms"].values / 1000
    ok = tot = 0
    starts = np.r_[0, win["t_end_s"].values[:-1]]
    for a, b, s in zip(starts, win["t_end_s"].values, win["state"]):
        m = (t_s >= a) & (t_s < b)
        if not m.any():
            continue
        lab, cnt = np.unique(truth[m], return_counts=True)
        lab = lab[cnt.argmax()]
        if lab in ("GLITCH", "IMPACT"):
            continue
        tot += 1
        ok += int(lab == s)
    return ok, tot


def fig_payload(field_stats, path):
    hours = field_stats["log_seconds"] / 3600
    v2b, v3b = field_stats["v2_bytes"] / 1024, field_stats["v3_bytes"] / 1024
    v2m, v3m = field_stats["v2_messages"], field_stats["v3_messages"]
    fig, ax = plt.subplots(1, 2, figsize=(9.5, 3.4))
    for a, vals, lab, fmt in ((ax[0], (v2b, v3b), "radio payload (KiB)", "{:.0f} KiB"),
                              (ax[1], (v2m, v3m), "MQTT messages", "{:.0f}")):
        bars = a.bar(["v2 raw stream", "v3 edge summaries"], vals, color=["#9e9ac8", "#41ab5d"], width=0.55)
        for b, v in zip(bars, vals):
            a.text(b.get_x() + b.get_width() / 2, v, fmt.format(v), ha="center", va="bottom")
        a.set_ylabel(lab)
        a.grid(axis="x", visible=False)
    fig.suptitle(f"Same {hours:.1f} h field-trial session: {field_stats['payload_reduction_pct']:.1f}% less radio payload")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def fig_architecture(path):
    fig, ax = plt.subplots(figsize=(11, 4.6))
    ax.set_xlim(0, 11)
    ax.set_ylim(0, 4.6)
    ax.axis("off")

    def box(x, y, w, h, text, fc, ec="#333", fs=9, bold=False):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.04,rounding_size=0.12",
                                    fc=fc, ec=ec, lw=1.1))
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs,
                fontweight="bold" if bold else "normal", wrap=True)

    def arrow(x0, y0, x1, y1, txt=""):
        ax.annotate("", (x1, y1), (x0, y0), arrowprops=dict(arrowstyle="-|>", color="#333", lw=1.2))
        if txt:
            ax.text((x0 + x1) / 2, (y0 + y1) / 2 + 0.13, txt, ha="center", fontsize=8, color="#444")

    ax.text(0.1, 4.35, "v2 (YESIST12): cloud does everything", fontsize=11, fontweight="bold", color="#6a51a3")
    box(0.1, 3.2, 2.0, 0.9, "MYOSA sensors\nMPU6050 · BMP180\nAPDS9960", "#efedf5", fs=8.5)
    box(3.0, 3.2, 2.2, 0.9, "raw values every ~2 s\n(3 MQTT msgs / cycle)", "#efedf5")
    box(6.1, 3.2, 2.2, 0.9, "ThingsBoard cloud", "#efedf5")
    box(9.0, 3.2, 1.9, 0.9, "Prophet + Isolation\nForest dashboard", "#efedf5")
    arrow(2.1, 3.65, 3.0, 3.65)
    arrow(5.2, 3.65, 6.1, 3.65, "Wi-Fi")
    arrow(8.3, 3.65, 9.0, 3.65)
    ax.text(5.5, 2.85, "no validation → 85% of IMU cycles unusable; 100% of ML 'anomalies' were sensor faults",
            ha="center", fontsize=8.5, color="#d7301f", style="italic")

    ax.text(0.1, 2.35, "v3 (SENSORS@25): Smart Herd Edge, intelligence on the collar", fontsize=11,
            fontweight="bold", color="#238b45")
    box(0.1, 0.25, 1.6, 1.75, "MYOSA sensors\n\nIMU @ 20 Hz\nenv @ 0.5 Hz", "#e5f5e0")
    box(2.0, 1.15, 1.5, 0.85, "validity gate\n6 fault classes", "#c7e9c0", bold=True)
    box(2.0, 0.25, 1.5, 0.75, "gravity split\nODBA + posture", "#c7e9c0", bold=True)
    box(3.8, 0.25, 1.7, 1.75, "behaviour\nclassifier\n\nlying · resting ·\ngrazing · walking ·\nactive", "#a1d99b", bold=True)
    box(5.8, 1.15, 1.6, 0.85, "per-animal\nbaseline (z-score)", "#c7e9c0", bold=True)
    box(5.8, 0.25, 1.6, 0.75, "alert rules\n6 alert types", "#c7e9c0", bold=True)
    box(7.75, 1.15, 1.45, 0.85, "1 summary / min\n+ instant alerts", "#e5f5e0")
    box(7.75, 0.25, 1.45, 0.75, "OLED status\non collar", "#e5f5e0")
    box(9.5, 1.15, 1.4, 0.85, "MQTT → cloud\n(store & forward)", "#e5f5e0")
    box(9.5, 0.25, 1.4, 0.75, "BLE → phone\n(offline)", "#e5f5e0")
    arrow(1.7, 1.57, 2.0, 1.57)
    arrow(2.75, 1.15, 2.75, 1.0)
    arrow(3.5, 0.62, 3.8, 0.9)
    arrow(5.5, 1.3, 5.8, 1.5)
    arrow(5.5, 0.9, 5.8, 0.62)
    arrow(7.4, 1.57, 7.75, 1.57)
    arrow(7.4, 0.62, 7.75, 0.62)
    arrow(9.2, 1.57, 9.5, 1.57)
    arrow(9.2, 1.35, 9.5, 0.62)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def main():
    os.makedirs(OUT, exist_ok=True)
    build_replay()

    field_stats, field_win = run_replay(os.path.join(OUT, "aligned_samples.csv"), 60000, 20, "replay")

    bench_csv = os.path.join(OUT, "bench_scenario.csv")
    df, truth = bench_scenario(bench_csv)
    bench_stats, bench_win = run_replay(bench_csv, 10000, 2, "bench")
    ok, tot = bench_accuracy(df, truth, bench_win)
    np.save(os.path.join(OUT, "bench_truth.npy"), truth)

    fig_bench(df, truth, bench_win, os.path.join(OUT, "fig_bench_timeline.png"))
    fig_payload(field_stats, os.path.join(OUT, "fig_radio_payload.png"))
    fig_architecture(os.path.join(OUT, "fig_architecture.png"))

    alerts = sorted({ALERTS[b] for a in bench_win.alerts for b in ALERTS if int(a) & b})
    summary = {
        "field_replay": {k: field_stats[k] for k in ("samples", "windows", "windows_unknown", "v2_bytes",
                                                      "v3_bytes", "v2_messages", "v3_messages",
                                                      "payload_reduction_pct")},
        "field_replay_alert_types": sorted({ALERTS[b] for a in field_win.alerts for b in ALERTS if int(a) & b}),
        "bench": {"windows_scored": tot, "windows_correct": ok,
                  "window_accuracy_pct": round(100 * ok / max(tot, 1), 1), "alerts_raised": alerts},
    }
    with open(os.path.join(OUT, "edge_report.json"), "w") as fh:
        json.dump(summary, fh, indent=2)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
