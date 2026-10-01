// Host unit tests for edge_core.h - build & run:
//   g++ -std=c++17 -O2 -Wall -Wextra -I../smart_herd_edge test_edge_core.cpp -o test_edge_core && ./test_edge_core
#include <math.h>
#include <stdio.h>

#include <functional>
#include <vector>

#include "edge_core.h"

using namespace herd;

static int g_fail = 0, g_pass = 0;
#define CHECK(cond)                                                    \
  do {                                                                 \
    if (cond) { g_pass++; }                                            \
    else { g_fail++; printf("  FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond); } \
  } while (0)

// Generates samples at `hz` for `seconds`, signal given by f(t) -> Sample.
static std::vector<WindowResult> run(EdgeEngine& e, float seconds, float hz,
                                     std::function<Sample(float)> f, uint32_t t0_ms = 0,
                                     uint8_t* alerts_seen = nullptr) {
  std::vector<WindowResult> out;
  int n = (int)(seconds * hz);
  for (int i = 0; i < n; i++) {
    float t = i / hz;
    Sample s = f(t);
    s.t_ms = t0_ms + (uint32_t)(t * 1000);
    WindowResult w;
    if (e.push(s, &w)) {
      out.push_back(w);
      if (alerts_seen) *alerts_seen |= w.alerts;
    }
  }
  return out;
}

static Sample base(float ax, float ay, float az) {
  Sample s{};
  s.ax = ax; s.ay = ay; s.az = az;
  s.gx = s.gy = s.gz = 0.5f;
  s.temp_c = 24.0f; s.pressure_kpa = 100.9f; s.proximity = 10; s.light = 120;
  return s;
}

// Standing upright: gravity on +z.
static Sample standing(float) { return base(0.05f, -0.03f, kG); }

static void test_validity_gate() {
  printf("validity gate\n");
  EdgeEngine e;
  CHECK(e.validate(standing(0)) == kFaultNone);
  Sample s = standing(0); s.ay = -1930.0f;          // v2 raw-count glitch
  CHECK(e.validate(s) & kFaultAccelRange);
  s = base(0, 0, 0);                                // I2C dropout
  CHECK(e.validate(s) & kFaultAccelDropout);
  s = standing(0); s.gy = -500.0f;                  // gyro saturation
  CHECK(e.validate(s) & kFaultGyroRange);
  s = standing(0); s.temp_c = 0.0f;                 // BMP180 failed read
  CHECK(e.validate(s) & kFaultTemp);
  s = standing(0); s.pressure_kpa = 0.0f;
  CHECK(e.validate(s) & kFaultPressure);
  s = standing(0); s.light = 37889.0f;
  CHECK(e.validate(s) & kFaultLight);
  s = standing(0); s.ax = NAN;
  CHECK(e.validate(s) & kFaultAccelDropout);
}

static void test_resting() {
  printf("resting / standing still\n");
  EdgeEngine e;
  auto w = run(e, 60, 20, standing);
  CHECK(w.size() >= 5);
  CHECK(w.back().state == kResting);
  CHECK(w.back().odba_g < 0.02f);
  CHECK(w.back().posture_deg < 5.0f);
}

static void test_walking_and_grazing() {
  printf("walking / grazing\n");
  EdgeEngine e;
  run(e, 20, 20, standing);  // fix standing reference
  // ~1 Hz gait, 0.15 g vertical + 0.1 g lateral oscillation
  auto walk = [](float t) {
    return base(0.1f * kG * sinf(6.283f * 1.0f * t), 0.05f * kG * cosf(6.283f * 1.0f * t),
                kG + 0.15f * kG * sinf(6.283f * 2.0f * t));
  };
  auto w = run(e, 40, 20, walk, 20000);
  CHECK(w.back().state == kWalking);
  // head down ~40 deg, same moderate motion
  float th = 40.0f / 57.29578f;
  auto graze = [th](float t) {
    float m = 0.12f * kG * sinf(6.283f * 1.5f * t);
    return base(kG * sinf(th) + m, 0.03f * kG * cosf(6.283f * t), kG * cosf(th) + m);
  };
  w = run(e, 40, 20, graze, 60000);
  CHECK(w.back().state == kGrazing);
  CHECK(w.back().posture_deg > 25.0f && w.back().posture_deg < 60.0f);
}

static void test_lying() {
  printf("lying\n");
  EdgeEngine e;
  run(e, 20, 20, standing);
  auto lying = [](float) { return base(kG * 0.97f, 0.0f, kG * 0.24f); };  // ~76 deg rotated
  auto w = run(e, 40, 20, lying, 20000);
  CHECK(w.back().state == kLying);
  CHECK(w.back().posture_deg > 60.0f);
}

static void test_active_and_agitation_alert() {
  printf("active + agitation alert\n");
  EdgeEngine e;
  run(e, 20, 20, standing);
  auto run_fast = [](float t) {
    return base(0.6f * kG * sinf(6.283f * 2.5f * t), 0.3f * kG * cosf(6.283f * 2.5f * t),
                kG + 0.8f * kG * sinf(6.283f * 5.0f * t));
  };
  uint8_t seen = 0;
  auto w = run(e, 180, 20, run_fast, 20000, &seen);
  CHECK(w.back().state == kActive);
  CHECK(seen & kAlertAgitation);
}

static void test_inactivity_alert() {
  printf("inactivity alert (shortened to 5 min)\n");
  Config c;
  c.inactivity_ms = 5UL * 60UL * 1000UL;
  EdgeEngine e(c);
  uint8_t seen = 0;
  run(e, 4 * 60, 5, standing, 0, &seen);
  CHECK(!(seen & kAlertInactivity));
  run(e, 2 * 60, 5, standing, 4 * 60 * 1000, &seen);
  CHECK(seen & kAlertInactivity);
}

static void test_fall_alert() {
  printf("fall / cast detection\n");
  EdgeEngine e;
  run(e, 20, 20, standing);
  // 0.2 s impact of ~3.5 g, then lying motionless
  auto fall = [](float t) {
    if (t < 0.2f) return base(2.0f * kG, 1.5f * kG, 2.4f * kG);
    return base(kG * 0.97f, 0.0f, kG * 0.24f);
  };
  uint8_t seen = 0;
  run(e, 120, 20, fall, 20000, &seen);
  CHECK(seen & kAlertFall);
}

static void test_heat_alert() {
  printf("heat stress alert\n");
  EdgeEngine e;
  auto hot = [](float) { Sample s = standing(0); s.temp_c = 35.0f; return s; };
  uint8_t seen = 0;
  run(e, 15 * 60, 2, hot, 0, &seen);
  CHECK(seen & kAlertHeatStress);
}

static void test_sensor_fault_alert() {
  printf("sensor fault alert + unknown state\n");
  EdgeEngine e;
  auto glitch = [](float) { Sample s = standing(0); s.ay = 1937.0f; return s; };
  uint8_t seen = 0;
  auto w = run(e, 60, 20, glitch, 0, &seen);
  CHECK(w.back().state == kUnknown);
  CHECK(w.back().n_valid == 0);
  CHECK(seen & kAlertSensorFault);
}

static void test_anomaly_against_baseline() {
  printf("baseline anomaly (z-score)\n");
  EdgeEngine e;
  // long calm baseline with light motion
  auto calm = [](float t) { return base(0.02f * kG * sinf(3.0f * t), 0.0f, kG + 0.02f * kG * cosf(2.0f * t)); };
  run(e, 600, 10, calm);
  auto burst = [](float t) {
    return base(0.5f * kG * sinf(15.0f * t), 0.4f * kG * cosf(13.0f * t), kG + 0.5f * kG * sinf(11.0f * t));
  };
  uint8_t seen = 0;
  run(e, 20, 10, burst, 600000, &seen);
  CHECK(seen & kAlertAnomaly);
}

static void test_summary_payload() {
  printf("minute summary payload\n");
  EdgeEngine e;
  run(e, 125, 20, standing);
  Summary s{};
  CHECK(e.popSummary(&s));
  CHECK(s.dominant == kResting);
  CHECK(s.quality > 0.99f);
  char buf[256];
  int n = EdgeEngine::summaryJson(s, buf, sizeof(buf));
  printf("  payload (%d B): %s\n", n, buf);
  CHECK(n > 0 && n < 200);
}

int main() {
  test_validity_gate();
  test_resting();
  test_walking_and_grazing();
  test_lying();
  test_active_and_agitation_alert();
  test_inactivity_alert();
  test_fall_alert();
  test_heat_alert();
  test_sensor_fault_alert();
  test_anomaly_against_baseline();
  test_summary_payload();
  printf("\n%d passed, %d failed\n", g_pass, g_fail);
  return g_fail ? 1 : 0;
}
