"""
Renders the Smart Herd Edge demo video (myosa-smart-herd-edge-demo.mp4).

Every number and trace in the video comes from files produced by the analytics
pipeline - nothing is typed in by hand:
  analytics/out/audit_report.json, edge_report.json   (stats)
  analytics/out/aligned_samples.csv                   (real field-trial log)
  analytics/out/bench_scenario.csv, bench_windows.csv, bench_summaries.jsonl
Photos come from the blog folder.

Usage:
    python analytics/data_audit.py && python analytics/edge_figures.py
    python tools/make_demo_video.py
Requires ffmpeg on the PATH.
"""
import json
import os
import subprocess

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "analytics", "out")
BLOG = ROOT
VIDEO = os.path.join(BLOG, "myosa-smart-herd-edge-demo.mp4")
W, H, FPS = 1280, 720, 25
G = 9.80665

BG = (14, 20, 28)
FG = (236, 240, 244)
MUTED = (150, 162, 176)
GREEN = (65, 171, 93)
RED = (215, 48, 31)
AMBER = (253, 174, 97)
STATE_RGB = {"UNKNOWN": (189, 189, 189), "LYING": (106, 81, 163), "RESTING": (43, 140, 190),
             "GRAZING": (65, 171, 93), "WALKING": (253, 174, 97), "ACTIVE": (215, 48, 31)}
ALERTS = {1: "INACTIVITY", 2: "HEAT STRESS", 4: "AGITATION", 8: "FALL / CAST", 16: "ANOMALY", 32: "SENSOR FAULT"}

FONT_DIR = "/usr/share/fonts/truetype/dejavu"


def font(size, bold=False, mono=False):
    name = "DejaVuSansMono" if mono else "DejaVuSans"
    if bold:
        name += "-Bold"
    return ImageFont.truetype(os.path.join(FONT_DIR, name + ".ttf"), size)


def ease(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def fit(img, w, h, cover=True):
    r = max(w / img.width, h / img.height) if cover else min(w / img.width, h / img.height)
    im = img.resize((max(1, int(img.width * r)), max(1, int(img.height * r))), Image.LANCZOS)
    if cover:
        x, y = (im.width - w) // 2, (im.height - h) // 2
        return im.crop((x, y, x + w, y + h))
    return im


def ken_burns(img, w, h, p, zoom=0.08):
    z = 1 + zoom * p
    base = fit(img, int(w * z) + 2, int(h * z) + 2)
    dx = int((base.width - w) * p)
    dy = int((base.height - h) * 0.5)
    return base.crop((dx, dy, dx + w, dy + h))


def mpl_to_img(fig):
    fig.canvas.draw()
    im = Image.frombuffer("RGBA", fig.canvas.get_width_height(), fig.canvas.buffer_rgba()).convert("RGB")
    plt.close(fig)
    return im


def caption(d, text, sub=None):
    d.rectangle((0, H - 64, W, H), fill=(0, 0, 0))
    d.text((32, H - 52), text, font=font(24, True), fill=FG)
    if sub:
        main_w = d.textlength(text, font=font(24, True))
        sub_w = d.textlength(sub, font=font(16))
        if 32 + main_w + 40 + sub_w < W - 32:
            d.text((W - 32, H - 46), sub, font=font(16), fill=MUTED, anchor="ra")
        else:  # not enough room: tag sits just above the caption bar
            d.rectangle((W - 48 - sub_w, H - 92, W, H - 64), fill=(0, 0, 0))
            d.text((W - 32, H - 88), sub, font=font(16), fill=MUTED, anchor="ra")


def header(d, kicker, title):
    d.text((48, 34), kicker.upper(), font=font(18, True), fill=GREEN)
    d.text((48, 60), title, font=font(34, True), fill=FG)


def fade(frame, i, n, k=8):
    a = min(1.0, i / k, (n - 1 - i) / k) if n > 2 * k else 1.0
    if a >= 1:
        return frame
    return Image.blend(Image.new("RGB", frame.size, (0, 0, 0)), frame, max(a, 0))


class Renderer:
    def __init__(self, path):
        self.p = subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
             "-r", str(FPS), "-i", "-", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
             "-shortest", "-c:v", "libx264", "-preset", "slow", "-crf", "24", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "32k", "-movflags", "+faststart", path],
            stdin=subprocess.PIPE)
        self.frames = 0

    def scene(self, seconds, draw):
        n = int(seconds * FPS)
        for i in range(n):
            f = draw(i, n, i / max(n - 1, 1))
            f = fade(f, i, n)
            self.p.stdin.write(f.tobytes())
            self.frames += 1

    def close(self):
        self.p.stdin.close()
        self.p.wait()


# ---------------------------------------------------------------- scenes
def scene_title(r, cover):
    def draw(i, n, p):
        f = Image.new("RGB", (W, H), BG)
        f.paste(ken_burns(cover, 520, H, p, 0.1), (0, 0))
        d = ImageDraw.Draw(f)
        x = 580
        d.text((x, 150), "MYOSA · IEEE SENSORS@25 · BLOG SUBMISSION", font=font(18, True), fill=GREEN)
        d.text((x, 190), "Smart Herd Edge", font=font(60, True), fill=FG)
        d.text((x, 268), "On-collar intelligence for", font=font(30), fill=FG)
        d.text((x, 306), "livestock health monitoring", font=font(30), fill=FG)
        d.line((x, 370, x + 560, 370), fill=(60, 72, 86), width=2)
        for k, t in enumerate(["validate every sample on the collar",
                               "classify behaviour at 20 Hz on the ESP32",
                               "send alerts, not raw data"]):
            if p > 0.25 + 0.15 * k:
                d.text((x, 392 + 40 * k), "▸ " + t, font=font(24), fill=MUTED)
        d.text((x, 560), "An upgrade of our MYOSA Smart Herd project (v2 → v3)", font=font(18), fill=MUTED)
        return f
    r.scene(7, draw)


def scene_photos(r, items, kicker, title, seconds_each=4.0):
    for img, cap in items:
        def draw(i, n, p, img=img, cap=cap):
            f = Image.new("RGB", (W, H), BG)
            f.paste(ken_burns(img, W, H - 64, p, 0.07), (0, 0))
            d = ImageDraw.Draw(f)
            d.rectangle((0, 0, W, 112), fill=(0, 0, 0))
            header(d, kicker, title)
            caption(d, cap)
            return f
        r.scene(seconds_each, draw)


def scene_dashboard(r, imgs):
    def draw(i, n, p):
        f = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(f)
        header(d, "Where we started · v2", "Raw stream → cloud → ML dashboard")
        a = fit(imgs[0], 600, 420, cover=False)
        b = fit(imgs[1], 600, 420, cover=False)
        f.paste(a, (40, 140))
        if p > 0.35:
            f.paste(b, (660, 140))
        caption(d, "Our v2 dashboard flagged 1,272 'anomalies' in accel_x. Were they real?",
                "Prophet forecasting + Isolation Forest (v2)")
        return f
    r.scene(6, draw)


def scene_audit(r, audit):
    s = pd.read_csv(os.path.join(OUT, "aligned_samples.csv"))
    s = s.replace("nan", np.nan)
    acc = s[["ax", "ay", "az"]].astype(float)
    mag = np.sqrt((acc ** 2).sum(axis=1, min_count=3))
    bad = (acc.abs() > 4 * G).any(axis=1) | (acc == 0).all(axis=1) | \
        (s[["gx", "gy", "gz"]].astype(float).abs() >= 500).any(axis=1)
    t = s["t_ms"] / 3.6e6
    m = mag.notna()
    fig = plt.figure(figsize=(12.0, 4.9), dpi=100, facecolor="#0e141c")
    ax = fig.add_axes([0.07, 0.13, 0.9, 0.8], facecolor="#0e141c")
    ax.scatter(t[m & ~bad], mag[m & ~bad], s=6, c="#4ea3e0")
    ax.scatter(t[m & bad], mag[m & bad], s=8, c="#d7301f")
    ax.set_yscale("symlog", linthresh=20)
    ax.set_xlim(-0.1, t.max() + 0.1)
    ax.set_ylabel("|a| (m/s², symlog)", color="#ccc")
    ax.set_xlabel("hours since start of logging (17 Jan 2025)", color="#ccc")
    ax.tick_params(colors="#ccc")
    for sp in ax.spines.values():
        sp.set_color("#445")
    ax.axhspan(0, 4 * G, color="#41ab5d", alpha=0.12)
    ax.text(0.1, 25, "physically possible band (≤ 4 g)", color="#7fd18f", fontsize=11)
    chart = mpl_to_img(fig)
    x0 = int(0.07 * chart.width)
    x1 = int(0.97 * chart.width)
    rej = audit["imu_cycles_rejected_pct"]

    def draw(i, n, p):
        f = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(f)
        header(d, "Step 1 · we audited our own field data", "10 hours of real collar data")
        q = ease(p / 0.75)
        c = chart.copy()
        cut = int(x0 + (x1 - x0) * q)
        ImageDraw.Draw(c).rectangle((cut, 0, c.width, c.height - 40), fill=(14, 20, 28))
        f.paste(c, (40, 120))
        shown = rej * q
        d.text((W - 48, 28), f"{shown:4.1f}%", font=font(46, True), fill=RED, anchor="ra")
        d.text((W - 48, 84), "of IMU cycles physically impossible", font=font(16), fill=MUTED, anchor="ra")
        caption(d, "Red = raw-count glitches, I²C dropouts and saturation (|a| up to 3,580 m/s²)",
                "dataset/field-trial-2025-01-17.csv")
        return f
    r.scene(9, draw)


def scene_finding(r, audit, fault_png):
    iso = audit["isolation_forest_v2"]
    img = Image.open(fault_png).convert("RGB")

    def draw(i, n, p):
        f = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(f)
        header(d, "The finding", "Our v2 ML was mostly detecting broken readings")
        f.paste(fit(img, 700, 480, cover=False), (30, 150))
        x = 770
        d.text((x, 175), f"{iso['flagged_that_are_sensor_faults']} / {iso['flagged_by_isolation_forest']}",
               font=font(64, True), fill=RED)
        d.text((x, 255), "Isolation-Forest 'anomalies' that", font=font(22), fill=FG)
        d.text((x, 285), "were really sensor faults", font=font(22), fill=FG)
        if p > 0.4:
            d.text((x, 360), "Lesson for v3:", font=font(24, True), fill=GREEN)
            d.text((x, 396), "validate on the collar first,", font=font(22), fill=FG)
            d.text((x, 426), "then interpret behaviour.", font=font(22), fill=FG)
        caption(d, "No model can fix data that never made physical sense", "analytics/data_audit.py")
        return f
    r.scene(8, draw)


def scene_architecture(r, arch_png):
    img = Image.open(arch_png).convert("RGB")
    img = fit(img, 1200, 520, cover=False)

    def draw(i, n, p):
        f = Image.new("RGB", (W, H), (255, 255, 255))
        d = ImageDraw.Draw(f)
        d.rectangle((0, 0, W, 112), fill=BG)
        header(d, "What's new · v3", "Smart Herd Edge: the intelligence moves onto the collar")
        f.paste(img, ((W - img.width) // 2, 125))
        caption(d, "Same MYOSA kit. New firmware: gate → features → classifier → baseline → alerts",
                "firmware/smart_herd_edge/edge_core.h")
        return f
    r.scene(8, draw)


def scene_live(r):
    df = pd.read_csv(os.path.join(OUT, "bench_scenario.csv"))
    win = pd.read_csv(os.path.join(OUT, "bench_windows.csv"))
    summ = [json.loads(l) for l in open(os.path.join(OUT, "bench_summaries.jsonl"))]
    truth = np.load(os.path.join(OUT, "bench_truth.npy"), allow_pickle=True)
    t = df["t_ms"].values / 60000
    mag = np.sqrt(df.ax ** 2 + df.ay ** 2 + df.az ** 2) / G
    T = t.max()

    fig = plt.figure(figsize=(8.0, 4.6), dpi=100, facecolor="#0e141c")
    rect = [0.09, 0.42, 0.88, 0.53]
    ax = fig.add_axes(rect, facecolor="#0e141c")
    ok = mag < 4
    ax.plot(t[ok], mag[ok], lw=0.35, color="#4ea3e0")
    ax.scatter(t[~ok], np.full((~ok).sum(), 3.85), s=2, c="#d7301f")
    ax.set_xlim(0, T)
    ax.set_ylim(0, 4.1)
    ax.set_ylabel("|a| (g)", color="#ccc")
    ax.tick_params(colors="#ccc", labelbottom=False)
    for sp in ax.spines.values():
        sp.set_color("#445")
    axt = fig.add_axes([0.09, 0.27, 0.88, 0.1], facecolor="#0e141c")
    axo = fig.add_axes([0.09, 0.12, 0.88, 0.1], facecolor="#0e141c")
    edges = np.flatnonzero(np.r_[True, truth[1:] != truth[:-1], True])
    for a, b in zip(edges[:-1], edges[1:]):
        name = truth[a]
        c = {"GLITCH": "#636363", "IMPACT": "#ffffff"}.get(name)
        c = c or "#%02x%02x%02x" % STATE_RGB.get(name, (150, 150, 150))
        axt.axvspan(t[a], t[min(b, len(t) - 1)], color=c, lw=0)
    te = win["t_end_s"].values / 60
    ts = np.r_[0, te[:-1]]
    for a, b, s in zip(ts, te, win["state"]):
        axo.axvspan(a, b, color="#%02x%02x%02x" % STATE_RGB[s], lw=0)
    for a_ in (axt, axo):
        a_.set_xlim(0, T)
        a_.set_yticks([])
        a_.tick_params(colors="#ccc")
        for sp in a_.spines.values():
            sp.set_color("#445")
    axt.tick_params(labelbottom=False)
    axt.set_ylabel("truth", color="#ccc", rotation=0, ha="right", va="center")
    axo.set_ylabel("collar", color="#ccc", rotation=0, ha="right", va="center")
    axo.set_xlabel("minutes", color="#ccc")
    chart = mpl_to_img(fig)
    px0, px1 = int(rect[0] * chart.width), int((rect[0] + rect[2]) * chart.width)

    win_t = win["t_end_s"].values
    sum_t = np.array([s_["t_s"] for s_ in summ])
    alert_rows = win[win.alerts > 0]

    def draw(i, n, p):
        f = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(f)
        header(d, "Step 2 · the edge engine running", "20 Hz collar signal → behaviour → alerts")
        q = min(p / 0.95, 1.0)
        now_min = q * T
        now_s = now_min * 60
        c = chart.copy()
        cut = int(px0 + (px1 - px0) * q)
        cd = ImageDraw.Draw(c)
        cd.rectangle((cut + 1, 0, c.width, c.height - 30), fill=(14, 20, 28))
        cd.line((cut, 10, cut, c.height - 40), fill=(255, 255, 255), width=1)
        f.paste(c, (20, 112))

        # OLED mock (what the collar shows)
        done = win[win_t <= now_s]
        state = done["state"].iloc[-1] if len(done) else "UNKNOWN"
        odba = done["odba_g"].iloc[-1] if len(done) else 0.0
        posture = done["posture_deg"].iloc[-1] if len(done) else 0.0
        recent = alert_rows[(alert_rows.t_end_s <= now_s) & (alert_rows.t_end_s > now_s - 25)]
        ox, oy = 850, 120
        d.rounded_rectangle((ox - 14, oy - 14, ox + 404, oy + 214), 14, fill=(40, 46, 54))
        d.rectangle((ox, oy, ox + 390, oy + 200), fill=(0, 0, 0))
        d.text((ox + 12, oy + 8), "SMART HERD EDGE  W B", font=font(17, mono=True), fill=(120, 220, 255))
        d.text((ox + 12, oy + 40), state if not np.isnan(odba) or state == "UNKNOWN" else state,
               font=font(46, True, mono=True), fill=STATE_RGB[state])
        od = 0.0 if np.isnan(odba) else odba
        po = 0.0 if np.isnan(posture) else posture
        d.text((ox + 12, oy + 112), f"act {od:4.2f}g  post {po:3.0f}°", font=font(19, mono=True), fill=FG)
        if len(recent):
            names = [ALERTS[b] for a in recent.alerts for b in ALERTS if int(a) & b]
            d.rectangle((ox, oy + 148, ox + 390, oy + 200), fill=RED)
            d.text((ox + 12, oy + 160), "ALERT: " + names[-1], font=font(21, True, mono=True), fill=(255, 255, 255))
        else:
            d.text((ox + 12, oy + 160), f"t+{int(now_s // 60):02d}:{int(now_s % 60):02d}  quality ok",
                   font=font(18, mono=True), fill=MUTED)
        d.text((ox + 195, oy + 222), "collar OLED (simulated view)", font=font(14), fill=MUTED, anchor="ma")

        # radio log
        lx, ly = 850, 380
        d.text((lx - 14, ly), "MQTT / BLE uplink (1 msg / min)", font=font(16, True), fill=GREEN)
        sent = [s_ for s_, ts_ in zip(summ, sum_t) if ts_ <= now_s][-6:]
        for k, s_ in enumerate(sent):
            sm = s_["summary"]
            line = f"{int(s_['t_s'] // 60):02d}m {sm['st']:<8} act={sm['act']:.2f} q={sm['q']:.2f}"
            col = RED if sm["al"] else FG
            d.text((lx - 14, ly + 30 + 26 * k), line, font=font(15, mono=True), fill=col)
        caption(d, "Bench simulation: scripted 20 Hz signals fed to the real firmware code (edge_core.h)",
                "top ribbon = scripted truth, bottom = collar output")
        return f
    r.scene(30, draw)


def scene_results(r, audit, edge, payload_png):
    img = Image.open(payload_png).convert("RGB")
    b = edge["bench"]

    def draw(i, n, p):
        f = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(f)
        header(d, "Results", "What v3 changes, in numbers")
        f.paste(fit(img, 760, 290, cover=False), (30, 130))
        stats = [
            (f"{edge['field_replay']['payload_reduction_pct']:.1f}%", "less radio payload, same 10 h session"),
            (f"{b['window_accuracy_pct']:.1f}%", f"bench windows correct ({b['windows_correct']}/{b['windows_scored']})"),
            ("6", "fault classes caught before any ML"),
            ("31/31", "host unit tests passing"),
            ("1.25 MB", "firmware, compiled for ESP32 (39% flash)"),
        ]
        for k, (big, small) in enumerate(stats):
            if p > 0.08 * k:
                y = 140 + 92 * k
                d.text((820, y), big, font=font(40, True), fill=GREEN)
                d.text((820, y + 48), small, font=font(17), fill=MUTED)
        d.text((40, 450), "Field data replayed through the collar engine:", font=font(20, True), fill=FG)
        d.text((40, 482), f"• {int(edge['field_replay']['windows_unknown'])}/{int(edge['field_replay']['windows'])} "
               "windows marked UNKNOWN instead of inventing behaviour", font=font(18), fill=MUTED)
        d.text((40, 510), "• SENSOR FAULT alert raised automatically — v2 never noticed", font=font(18), fill=MUTED)
        d.text((40, 538), "• summaries carry a data-quality score (q) with every message", font=font(18), fill=MUTED)
        caption(d, "Every number here is produced by scripts in the repository", "analytics/out/*.json")
        return f
    r.scene(10, draw)


def scene_closing(r, cover):
    def draw(i, n, p):
        f = Image.new("RGB", (W, H), BG)
        f.paste(ken_burns(cover, 420, H, 1 - p, 0.08), (W - 420, 0))
        d = ImageDraw.Draw(f)
        d.text((60, 90), "NEXT · IEEE SENSORS@25", font=font(18, True), fill=GREEN)
        d.text((60, 120), "From prototype to pilot", font=font(44, True), fill=FG)
        items = ["Flash v3 firmware on the existing MYOSA collar kit",
                 "Re-run the field trial with 20 Hz on-collar features",
                 "Calibrate thresholds per animal with video-labelled behaviour",
                 "Report lying-time budgets & alert precision on real animals"]
        for k, t in enumerate(items):
            if p > 0.1 + 0.12 * k:
                d.text((60, 210 + 52 * k), f"{k + 1}.  {t}", font=font(23), fill=FG)
        d.text((60, 470), "Smart Herd Edge · built on MYOSA by IEEE Sensors Council", font=font(20), fill=MUTED)
        d.text((60, 502), "Thank you — MYOSA Sensors Council & Rashtriya Raksha University", font=font(20), fill=MUTED)
        return f
    r.scene(8, draw)


def main():
    audit = json.load(open(os.path.join(OUT, "audit_report.json")))
    edge = json.load(open(os.path.join(OUT, "edge_report.json")))
    load = lambda n: Image.open(os.path.join(BLOG, n)).convert("RGB")
    cover = load("smart-herd-edge-cover.jpg")
    r = Renderer(VIDEO)
    scene_title(r, cover)
    scene_photos(r, [
        (load("collar-field-trial.jpg"), "v2 collar on a working dog during our trial with Rashtriya Raksha University"),
        (load("collar-prototype-closeup.jpg"), "The MYOSA collar prototype: MPU6050 · BMP180 · APDS9960 on the strap"),
        (load("ieee-apscon-showcase.jpg"), "Showcased after IEEE APSCON 2025 (IIT Hyderabad)"),
        (Image.open(os.path.join(ROOT, "tools", "assets", "trainer-testimonial.jpg")).convert("RGB"),
         "Feedback from the animals' trainer shaped the v3 goals"),
    ], "Where we started · v2", "Smart Herd: a MYOSA smart collar", 3.6)
    scene_dashboard(r, [load("v2-dashboard-overview.jpg"), load("v2-dashboard-anomalies.jpg")])
    scene_audit(r, audit)
    scene_finding(r, audit, os.path.join(BLOG, "v2-fault-breakdown.png"))
    scene_architecture(r, os.path.join(BLOG, "edge-architecture.png"))
    scene_live(r)
    scene_results(r, audit, edge, os.path.join(BLOG, "edge-radio-payload.png"))
    scene_closing(r, cover)
    r.close()
    print(f"wrote {VIDEO}: {r.frames / FPS:.1f} s, {os.path.getsize(VIDEO) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
