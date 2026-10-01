// Replays a recorded collar log through the on-collar EdgeEngine, sample by
// sample, exactly as the firmware would see it.
//
//   g++ -std=c++17 -O2 -I../smart_herd_edge replay.cpp -o replay
//   ./replay ../../analytics/out/aligned_samples.csv ../../analytics/out [window_ms] [tau_s] [prefix]
//
// Input : CSV  t_ms,ax,ay,az,gx,gy,gz,temp_c,pressure_kpa,proximity,light ("nan" = missing)
// Output: <outdir>/<prefix>_windows.csv, <outdir>/<prefix>_summaries.jsonl,
//         <outdir>/<prefix>_stats.txt (radio payload comparison v2 vs v3)
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <string>

#include "edge_core.h"

using namespace herd;

static float parseField(const char* s) {
  if (!s || !*s || strncmp(s, "nan", 3) == 0) return NAN;
  return strtof(s, nullptr);
}

// Size of the telemetry the v2 firmware published for one acquisition cycle:
// three separate MQTT JSON messages (IMU, environment, light/colour), each
// with full key names. Reconstructed from the v2 ThingsBoard keys.
static int v2PayloadBytes(const Sample& s) {
  char b[512];
  int n = 0;
  n += snprintf(b, sizeof b,
      "{\"accel_x\":%.2f,\"accel_y\":%.2f,\"accel_z\":%.2f,\"gyro_x\":%.2f,\"gyro_y\":%.2f,"
      "\"gyro_z\":%.2f,\"tilt_x\":%.2f,\"tilt_y\":%.2f,\"tilt_z\":%.2f}",
      s.ax, s.ay, s.az, s.gx, s.gy, s.gz, 0.0, 0.0, 0.0);
  n += snprintf(b, sizeof b,
      "{\"temperature_C\":%.1f,\"temperature_F\":%.2f,\"pressure_kPa\":%.2f,\"pressure_mmHg\":%.2f}",
      s.temp_c, s.temp_c * 1.8 + 32, s.pressure_kpa, s.pressure_kpa * 7.50062);
  n += snprintf(b, sizeof b,
      "{\"ambient_light\":%.0f,\"red_proportion\":%.0f,\"green_proportion\":%.0f,"
      "\"blue_proportion\":%.0f,\"proximity\":%.0f}",
      s.light, 0.0, 0.0, 0.0, s.proximity);
  return n;
}

int main(int argc, char** argv) {
  if (argc < 3) {
    fprintf(stderr, "usage: %s samples.csv outdir [window_ms] [gravity_tau_s] [prefix]\n", argv[0]);
    return 2;
  }
  Config cfg;
  // The v2 log has one full sample every ~6 s (the v3 firmware samples the IMU
  // at 20 Hz), so use 60 s windows for the replay to keep ~10 samples/window.
  cfg.window_ms = argc > 3 ? (uint32_t)atol(argv[3]) : 60000;
  cfg.gravity_tau_s = argc > 4 ? (float)atof(argv[4]) : 20.0f;
  EdgeEngine eng(cfg);
  std::string pre = argc > 5 ? argv[5] : "replay";

  FILE* in = fopen(argv[1], "r");
  if (!in) { perror(argv[1]); return 1; }
  std::string od = argv[2];
  FILE* fw = fopen((od + "/" + pre + "_windows.csv").c_str(), "w");
  FILE* fs = fopen((od + "/" + pre + "_summaries.jsonl").c_str(), "w");
  FILE* ft = fopen((od + "/" + pre + "_stats.txt").c_str(), "w");
  if (!fw || !fs || !ft) { perror("output"); return 1; }
  fprintf(fw, "t_end_s,n_total,n_valid,fault_mask,odba_g,peak_g,posture_deg,temp_c,pressure_kpa,z,state,alerts\n");

  char line[1024];
  if (!fgets(line, sizeof line, in)) { fprintf(stderr, "empty input\n"); return 1; }  // header
  long v2_bytes = 0, v2_msgs = 0, v3_bytes = 0, v3_msgs = 0, samples = 0;
  uint32_t last_t = 0;
  long windows = 0, unknown = 0, state_secs[kStateCount] = {0};
  uint8_t alerts_all = 0;
  long alert_count = 0;

  auto writeWindow = [&](const WindowResult& w) {
    fprintf(fw, "%.1f,%u,%u,%u,%.4f,%.3f,%.1f,%.2f,%.3f,%.2f,%s,%u\n", w.t_end_ms / 1000.0, w.n_total,
            w.n_valid, w.fault_mask, w.odba_g, w.peak_g, w.posture_deg, w.temp_c, w.pressure_kpa, w.z,
            stateName(w.state), w.alerts);
    windows++;
    if (w.state == kUnknown) unknown++;
    if (w.alerts) { alerts_all |= w.alerts; alert_count++; }
  };
  auto drainSummaries = [&]() {
    Summary s;
    while (eng.popSummary(&s)) {
      char buf[256];
      int n = EdgeEngine::summaryJson(s, buf, sizeof buf);
      fprintf(fs, "{\"t_s\":%.0f,\"summary\":%s}\n", s.t_end_ms / 1000.0, buf);
      v3_bytes += n; v3_msgs++;
      for (int i = 0; i < kStateCount; i++) state_secs[i] += s.seconds_in_state[i];
    }
  };

  while (fgets(line, sizeof line, in)) {
    float v[11];
    char* p = line;
    for (int i = 0; i < 11; i++) {
      char* c = strchr(p, ',');
      if (c) *c = 0;
      v[i] = parseField(p);
      p = c ? c + 1 : p + strlen(p);
    }
    Sample s{(uint32_t)v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7], v[8], v[9], v[10]};
    samples++;
    last_t = s.t_ms;
    v2_bytes += v2PayloadBytes(s);
    v2_msgs += 3;
    WindowResult w;
    if (eng.push(s, &w)) writeWindow(w);
    drainSummaries();
  }
  WindowResult w;
  if (eng.flush(last_t + 1, &w)) writeWindow(w);
  drainSummaries();

  fprintf(ft, "samples=%ld\nlog_seconds=%u\nwindows=%ld\nwindows_unknown=%ld\n", samples, last_t / 1000,
          windows, unknown);
  for (int i = 0; i < kStateCount; i++) fprintf(ft, "seconds_%s=%ld\n", stateName(i), state_secs[i]);
  fprintf(ft, "windows_with_alerts=%ld\nalert_mask=%u\n", alert_count, alerts_all);
  fprintf(ft, "v2_messages=%ld\nv2_bytes=%ld\nv3_messages=%ld\nv3_bytes=%ld\n", v2_msgs, v2_bytes, v3_msgs,
          v3_bytes);
  fprintf(ft, "payload_reduction_pct=%.1f\n", 100.0 * (1.0 - (double)v3_bytes / (double)v2_bytes));
  fclose(in); fclose(fw); fclose(fs); fclose(ft);
  printf("replayed %ld samples -> %ld windows, %ld summaries\n", samples, windows, v3_msgs);
  return 0;
}
