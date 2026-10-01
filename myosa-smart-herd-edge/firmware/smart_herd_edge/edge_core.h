// edge_core.h - Smart Herd Edge: on-collar signal validation, behaviour
// classification and alerting.
//
// Platform-independent (no Arduino headers) so that the exact same code runs
// on the MYOSA ESP32 board, in the host unit tests (firmware/test) and in the
// replay of the field-trial dataset.
//
// Pipeline per sample:   validate -> gravity split -> window statistics
// Pipeline per window:   classify behaviour -> baseline z-score -> alert rules
// Pipeline per minute:   compact summary (replaces the v2 raw 2-second stream)
#pragma once

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace herd {

constexpr float kG = 9.80665f;

struct Sample {
  uint32_t t_ms;
  float ax, ay, az;     // m/s^2   (MPU6050)
  float gx, gy, gz;     // deg/s   (MPU6050)
  float temp_c;         // degC    (BMP180)
  float pressure_kpa;   // kPa     (BMP180)
  float proximity;      // 0..255  (APDS9960)
  float light;          // counts  (APDS9960)
};

// Bit flags describing why a sample was rejected.
enum Fault : uint8_t {
  kFaultNone = 0,
  kFaultAccelRange = 1 << 0,    // |axis| beyond physical limit (raw-count glitch)
  kFaultAccelDropout = 1 << 1,  // all axes exactly 0 or NaN (I2C dropout)
  kFaultGyroRange = 1 << 2,     // gyro saturated / NaN
  kFaultTemp = 1 << 3,
  kFaultPressure = 1 << 4,
  kFaultLight = 1 << 5,
};

enum State : uint8_t {
  kUnknown = 0,  // not enough valid data in the window
  kLying,        // low activity + collar rotated far from standing reference
  kResting,      // low activity, upright (standing / ruminating)
  kGrazing,      // moderate activity, head-down posture
  kWalking,      // moderate activity, head level
  kActive,       // high activity: running, agitation, mounting
  kStateCount
};

enum Alert : uint8_t {
  kAlertNone = 0,
  kAlertInactivity = 1 << 0,  // resting/lying longer than configured
  kAlertHeatStress = 1 << 1,  // collar temperature above limit for a sustained period
  kAlertAgitation = 1 << 2,   // sustained high activity
  kAlertFall = 1 << 3,        // impact followed by lying and no movement
  kAlertAnomaly = 1 << 4,     // activity far outside this animal's own baseline
  kAlertSensorFault = 1 << 5, // data quality too low to trust the collar
};

inline const char* stateName(uint8_t s) {
  static const char* n[] = {"UNKNOWN", "LYING", "RESTING", "GRAZING", "WALKING", "ACTIVE"};
  return s < kStateCount ? n[s] : "?";
}

struct Config {
  // --- validity gate (must match analytics/data_audit.py) ---
  float accel_limit = 4.0f * kG;
  float gyro_limit = 500.0f;
  float temp_min = -10.0f, temp_max = 50.0f;
  float pressure_min = 80.0f, pressure_max = 110.0f;
  float light_sentinel = 37889.0f;

  // --- windowing ---
  uint32_t window_ms = 10000;        // classification window
  float min_valid_fraction = 0.5f;   // below this the window is kUnknown
  float gravity_tau_s = 2.0f;        // low-pass time constant for gravity
  uint32_t max_gap_ms = 30000;       // a longer silence closes the window (link/power loss)

  // --- behaviour thresholds (dynamic acceleration, in g) ---
  float rest_odba_g = 0.05f;
  float active_odba_g = 0.35f;
  float head_down_deg = 25.0f;       // posture angle vs. standing reference
  float lying_deg = 60.0f;

  // --- alert rules ---
  uint32_t inactivity_ms = 4UL * 3600UL * 1000UL;  // 4 h continuous rest/lying
  uint32_t agitation_ms = 2UL * 60UL * 1000UL;     // 2 min continuous ACTIVE
  float heat_temp_c = 32.0f;
  uint32_t heat_ms = 10UL * 60UL * 1000UL;
  float impact_g = 2.5f;
  uint32_t fall_confirm_ms = 60UL * 1000UL;
  float anomaly_z = 4.0f;
  uint16_t baseline_warmup_windows = 30;
  float baseline_alpha = 0.02f;
  float fault_alert_fraction = 0.5f;
  uint8_t fault_alert_windows = 3;
};

struct WindowResult {
  uint32_t t_end_ms;
  uint16_t n_total, n_valid;
  uint8_t fault_mask;     // OR of all faults seen in the window
  float odba_g;           // mean overall dynamic body acceleration
  float peak_g;           // max |a| in window
  float posture_deg;      // angle between gravity and standing reference
  float temp_c;           // mean valid temperature (NaN if none)
  float pressure_kpa;
  float proximity;
  float z;                // baseline z-score of odba
  uint8_t state;
  uint8_t alerts;         // alerts raised (newly) at the end of this window
};

// Minute summary - the only thing sent over the radio in normal operation.
struct Summary {
  uint32_t t_end_ms;
  uint16_t seconds_in_state[kStateCount];
  float odba_g, peak_g, temp_c, pressure_kpa;
  float quality;         // valid samples / total samples
  uint8_t dominant;
  uint8_t alerts;        // OR of alerts raised during the minute
};

class EdgeEngine {
 public:
  explicit EdgeEngine(const Config& c = Config()) : cfg_(c) { reset(); }

  void reset() {
    have_gravity_ = false;
    ref_set_ = false;
    last_t_ = 0;
    last_push_t_ = 0;
    clearWindow();
    window_start_ = 0;
    window_open_ = false;
    mu_ = 0; var_ = 0; nbase_ = 0;
    state_since_ = 0; cur_state_ = kUnknown;
    heat_since_ = 0; heat_on_ = false;
    impact_t_ = 0; impact_pending_ = false;
    bad_windows_ = 0;
    latched_ = 0;
    clearSummary();
  }

  const Config& config() const { return cfg_; }

  // Fix the "standing" gravity direction (e.g. at collar fitting). If never
  // called, the first valid gravity estimate is used.
  void setReference(float gx, float gy, float gz) {
    float n = sqrtf(gx * gx + gy * gy + gz * gz);
    if (n < 1e-3f) return;
    ref_[0] = gx / n; ref_[1] = gy / n; ref_[2] = gz / n;
    ref_set_ = true;
  }

  uint8_t validate(const Sample& s) const {
    uint8_t f = kFaultNone;
    const float a[3] = {s.ax, s.ay, s.az};
    bool all_zero = true, any_nan = false;
    for (float v : a) {
      if (isnan(v)) { any_nan = true; continue; }
      if (fabsf(v) > cfg_.accel_limit) f |= kFaultAccelRange;
      if (v != 0.0f) all_zero = false;
    }
    if (any_nan || all_zero) f |= kFaultAccelDropout;
    const float g[3] = {s.gx, s.gy, s.gz};
    for (float v : g)
      if (isnan(v) || fabsf(v) >= cfg_.gyro_limit) f |= kFaultGyroRange;
    if (!isnan(s.temp_c) && (s.temp_c == 0.0f || s.temp_c < cfg_.temp_min || s.temp_c > cfg_.temp_max))
      f |= kFaultTemp;
    if (!isnan(s.pressure_kpa) && (s.pressure_kpa < cfg_.pressure_min || s.pressure_kpa > cfg_.pressure_max))
      f |= kFaultPressure;
    if (!isnan(s.light) && s.light == cfg_.light_sentinel) f |= kFaultLight;
    return f;
  }

  // Feed one sample. Returns true when a window closed; result in *out.
  bool push(const Sample& s, WindowResult* out) {
    bool closed = false;
    if (window_open_ && n_total_ > 0 && s.t_ms - last_push_t_ > cfg_.max_gap_ms) {
      // Data gap: close at the last real sample so the gap is not counted as
      // time spent in any behaviour, and re-acquire gravity afterwards.
      closeWindow(last_push_t_, out);
      closed = true;
      window_open_ = false;
      have_gravity_ = false;
    }
    last_push_t_ = s.t_ms;
    if (!window_open_) { window_start_ = s.t_ms; window_open_ = true; }
    if (s.t_ms - window_start_ >= cfg_.window_ms) {  // never true right after a gap close
      closeWindow(s.t_ms, out);
      closed = true;
      window_start_ = s.t_ms;
    }
    addSample(s);
    return closed;
  }

  // Force the current window to close (e.g. end of a replay).
  bool flush(uint32_t t_ms, WindowResult* out) {
    if (!window_open_ || n_total_ == 0) return false;
    closeWindow(t_ms, out);
    window_open_ = false;
    return true;
  }

  // Returns true once per minute of window time with a filled summary.
  bool popSummary(Summary* out, uint32_t period_ms = 60000) {
    if (!sum_open_ || sum_.t_end_ms - sum_start_ < period_ms) return false;
    finishSummary(out);
    return true;
  }

  // Compact JSON for MQTT/BLE. Returns bytes written (excl. NUL).
  static int summaryJson(const Summary& s, char* buf, size_t len) {
    int n = snprintf(buf, len,
        "{\"st\":\"%s\",\"act\":%.3f,\"pk\":%.2f,\"tC\":%.1f,\"p\":%.2f,\"q\":%.2f,"
        "\"ly\":%u,\"rs\":%u,\"gz\":%u,\"wk\":%u,\"ac\":%u,\"al\":%u}",
        stateName(s.dominant), s.odba_g, s.peak_g, s.temp_c, s.pressure_kpa, s.quality,
        s.seconds_in_state[kLying], s.seconds_in_state[kResting], s.seconds_in_state[kGrazing],
        s.seconds_in_state[kWalking], s.seconds_in_state[kActive], s.alerts);
    return n;
  }

 private:
  void clearWindow() {
    n_total_ = n_valid_ = 0;
    fault_mask_ = 0;
    odba_sum_ = 0; peak_ = 0;
    gsum_[0] = gsum_[1] = gsum_[2] = 0;
    temp_sum_ = 0; temp_n_ = 0;
    p_sum_ = 0; p_n_ = 0;
    prox_sum_ = 0; prox_n_ = 0;
  }

  void clearSummary() {
    memset(&sum_, 0, sizeof(sum_));
    sum_start_ = 0;
    sum_open_ = false;
    sum_odba_ = sum_temp_ = sum_p_ = 0;
    sum_n_ = sum_temp_n_ = sum_p_n_ = 0;
    sum_valid_ = sum_total_ = 0;
  }

  void addSample(const Sample& s) {
    n_total_++;
    uint8_t f = validate(s);
    fault_mask_ |= f;
    // Environmental channels are independent of the IMU.
    if (!isnan(s.temp_c) && !(f & kFaultTemp)) { temp_sum_ += s.temp_c; temp_n_++; }
    if (!isnan(s.pressure_kpa) && !(f & kFaultPressure)) { p_sum_ += s.pressure_kpa; p_n_++; }
    if (!isnan(s.proximity)) { prox_sum_ += s.proximity; prox_n_++; }
    if (f & (kFaultAccelRange | kFaultAccelDropout | kFaultGyroRange)) return;

    n_valid_++;
    float dt = have_gravity_ ? (s.t_ms - last_t_) / 1000.0f : 0.0f;
    last_t_ = s.t_ms;
    if (!have_gravity_) {
      grav_[0] = s.ax; grav_[1] = s.ay; grav_[2] = s.az;
      have_gravity_ = true;
    } else {
      float alpha = dt / (cfg_.gravity_tau_s + dt);
      grav_[0] += alpha * (s.ax - grav_[0]);
      grav_[1] += alpha * (s.ay - grav_[1]);
      grav_[2] += alpha * (s.az - grav_[2]);
    }
    float dx = s.ax - grav_[0], dy = s.ay - grav_[1], dz = s.az - grav_[2];
    odba_sum_ += (fabsf(dx) + fabsf(dy) + fabsf(dz)) / kG;
    float mag = sqrtf(s.ax * s.ax + s.ay * s.ay + s.az * s.az) / kG;
    if (mag > peak_) peak_ = mag;
    if (mag > cfg_.impact_g) { impact_t_ = s.t_ms; impact_pending_ = true; }
    gsum_[0] += grav_[0]; gsum_[1] += grav_[1]; gsum_[2] += grav_[2];
  }

  uint8_t classify(float odba, float posture) const {
    if (odba < cfg_.rest_odba_g) return posture >= cfg_.lying_deg ? kLying : kResting;
    if (odba < cfg_.active_odba_g) return posture >= cfg_.head_down_deg ? kGrazing : kWalking;
    return kActive;
  }

  void closeWindow(uint32_t t_end, WindowResult* r) {
    WindowResult w;
    memset(&w, 0, sizeof(w));
    w.t_end_ms = t_end;
    w.n_total = n_total_;
    w.n_valid = n_valid_;
    w.fault_mask = fault_mask_;
    w.temp_c = temp_n_ ? temp_sum_ / temp_n_ : NAN;
    w.pressure_kpa = p_n_ ? p_sum_ / p_n_ : NAN;
    w.proximity = prox_n_ ? prox_sum_ / prox_n_ : NAN;
    w.posture_deg = NAN;
    w.z = 0;
    uint32_t win_ms = t_end - window_start_;

    float valid_frac = n_total_ ? (float)n_valid_ / n_total_ : 0.0f;
    if (n_valid_ > 0 && valid_frac >= cfg_.min_valid_fraction) {
      w.odba_g = odba_sum_ / n_valid_;
      w.peak_g = peak_;
      float g[3] = {gsum_[0] / n_valid_, gsum_[1] / n_valid_, gsum_[2] / n_valid_};
      float gn = sqrtf(g[0] * g[0] + g[1] * g[1] + g[2] * g[2]);
      if (!ref_set_) setReference(g[0], g[1], g[2]);
      if (gn > 1e-3f) {
        float c = (g[0] * ref_[0] + g[1] * ref_[1] + g[2] * ref_[2]) / gn;
        c = c > 1 ? 1 : (c < -1 ? -1 : c);
        w.posture_deg = acosf(c) * 57.29578f;
      }
      w.state = classify(w.odba_g, isnan(w.posture_deg) ? 0 : w.posture_deg);
      // Per-animal baseline (EWMA mean/variance of activity).
      if (nbase_ >= cfg_.baseline_warmup_windows) {
        w.z = (w.odba_g - mu_) / sqrtf(var_ + 1e-6f);
      }
      if (nbase_ == 0) { mu_ = w.odba_g; var_ = 0.0025f; }
      float d = w.odba_g - mu_;
      mu_ += cfg_.baseline_alpha * d;
      var_ = (1 - cfg_.baseline_alpha) * (var_ + cfg_.baseline_alpha * d * d);
      if (nbase_ < 0xFFFF) nbase_++;
      bad_windows_ = 0;
    } else {
      w.state = kUnknown;
      if (1.0f - valid_frac >= cfg_.fault_alert_fraction) bad_windows_++;
    }

    w.alerts = evaluateAlerts(w, t_end);
    accumulateSummary(w, win_ms);
    clearWindow();
    if (r) *r = w;
  }

  uint8_t raiseOnce(uint8_t alert, bool condition) {
    if (condition && !(latched_ & alert)) { latched_ |= alert; return alert; }
    if (!condition) latched_ &= ~alert;
    return 0;
  }

  uint8_t evaluateAlerts(const WindowResult& w, uint32_t t) {
    uint8_t a = 0;
    if (w.state != cur_state_) {
      // Rest and lying count as one continuous inactive spell.
      bool both_inactive = (w.state == kResting || w.state == kLying) &&
                           (cur_state_ == kResting || cur_state_ == kLying);
      if (!both_inactive) state_since_ = t;
      cur_state_ = w.state;
    }
    uint32_t in_state = t - state_since_;
    bool inactive = (w.state == kResting || w.state == kLying);
    a |= raiseOnce(kAlertInactivity, inactive && in_state >= cfg_.inactivity_ms);
    a |= raiseOnce(kAlertAgitation, w.state == kActive && in_state >= cfg_.agitation_ms);

    bool hot = !isnan(w.temp_c) && w.temp_c >= cfg_.heat_temp_c;
    if (hot && !heat_on_) { heat_on_ = true; heat_since_ = t; }
    if (!hot) heat_on_ = false;
    a |= raiseOnce(kAlertHeatStress, heat_on_ && t - heat_since_ >= cfg_.heat_ms);

    bool fall = false;
    if (impact_pending_) {
      if (w.state == kLying && t - impact_t_ >= cfg_.fall_confirm_ms) { fall = true; impact_pending_ = false; }
      else if (w.state != kLying && w.state != kUnknown && t - impact_t_ >= cfg_.fall_confirm_ms) impact_pending_ = false;
    }
    if (fall) { a |= kAlertFall; }

    a |= raiseOnce(kAlertAnomaly, fabsf(w.z) >= cfg_.anomaly_z);
    a |= raiseOnce(kAlertSensorFault, bad_windows_ >= cfg_.fault_alert_windows);
    return a;
  }

  void accumulateSummary(const WindowResult& w, uint32_t win_ms) {
    if (!sum_open_) { sum_start_ = w.t_end_ms - win_ms; sum_open_ = true; }
    sum_.t_end_ms = w.t_end_ms;
    sum_.seconds_in_state[w.state] += (uint16_t)((win_ms + 500) / 1000);
    sum_.alerts |= w.alerts;
    sum_valid_ += w.n_valid;
    sum_total_ += w.n_total;
    if (w.state != kUnknown) {
      sum_odba_ += w.odba_g; sum_n_++;
      if (w.peak_g > sum_.peak_g) sum_.peak_g = w.peak_g;
    }
    if (!isnan(w.temp_c)) { sum_temp_ += w.temp_c; sum_temp_n_++; }
    if (!isnan(w.pressure_kpa)) { sum_p_ += w.pressure_kpa; sum_p_n_++; }
  }

  void finishSummary(Summary* out) {
    Summary s = sum_;
    s.odba_g = sum_n_ ? sum_odba_ / sum_n_ : NAN;
    s.temp_c = sum_temp_n_ ? sum_temp_ / sum_temp_n_ : NAN;
    s.pressure_kpa = sum_p_n_ ? sum_p_ / sum_p_n_ : NAN;
    s.quality = sum_total_ ? (float)sum_valid_ / sum_total_ : 0;
    uint8_t best = kUnknown;
    for (uint8_t i = 0; i < kStateCount; i++)
      if (s.seconds_in_state[i] > s.seconds_in_state[best]) best = i;
    s.dominant = best;
    *out = s;
    clearSummary();
  }

  Config cfg_;
  // gravity / posture
  bool have_gravity_, ref_set_;
  float grav_[3], ref_[3];
  uint32_t last_t_, last_push_t_;
  // current window
  bool window_open_;
  uint32_t window_start_;
  uint16_t n_total_, n_valid_;
  uint8_t fault_mask_;
  float odba_sum_, peak_, gsum_[3];
  float temp_sum_, p_sum_, prox_sum_;
  uint16_t temp_n_, p_n_, prox_n_;
  // baseline
  float mu_, var_;
  uint16_t nbase_;
  // alert state
  uint8_t cur_state_;
  uint32_t state_since_;
  bool heat_on_;
  uint32_t heat_since_;
  bool impact_pending_;
  uint32_t impact_t_;
  uint8_t bad_windows_;
  uint8_t latched_;
  // minute summary
  Summary sum_;
  bool sum_open_;
  uint32_t sum_start_;
  float sum_odba_, sum_temp_, sum_p_;
  uint16_t sum_n_, sum_temp_n_, sum_p_n_;
  uint32_t sum_valid_, sum_total_;
};

}  // namespace herd
