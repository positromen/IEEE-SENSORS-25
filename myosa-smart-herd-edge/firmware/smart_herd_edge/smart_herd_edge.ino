// Smart Herd Edge - MYOSA collar firmware (v3)
//
// What changed from v2: v2 streamed every raw reading to the cloud every ~2 s
// and left all interpretation to the dashboard. v3 validates every sample on
// the collar, samples the IMU at 20 Hz, classifies behaviour locally
// (edge_core.h) and sends one compact summary per minute, plus immediate
// alerts. Wi-Fi/MQTT and BLE both carry the same payloads; summaries are
// buffered while offline and replayed with their original timestamps.
//
// Hardware (existing MYOSA kit): ESP32 motherboard, MPU6050 accel/gyro,
// BMP180 pressure/temperature, APDS9960 light/proximity, 0.96" SSD1306 OLED.
//
// Board: "ESP32 Dev Module", Partition Scheme: "Huge APP (3MB No OTA)"
// Serial commands (115200): c = re-calibrate standing posture, r = toggle raw debug

#include <Wire.h>
#include <WiFi.h>
#include <time.h>
#include <PubSubClient.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_BMP085.h>
#include <SparkFun_APDS9960.h>
#include <Adafruit_SSD1306.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLE2902.h>

#include "config.h"
#include "edge_core.h"

// Nordic-UART-style service so any BLE terminal app can read the collar.
#define BLE_SERVICE_UUID "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
#define BLE_TX_UUID      "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

static Adafruit_MPU6050 mpu;
static Adafruit_BMP085 bmp;
static SparkFun_APDS9960 apds;
static Adafruit_SSD1306 oled(128, 64, &Wire, -1);
static WiFiClient wifi;
static PubSubClient mqtt(wifi);
static BLECharacteristic* bleTx = nullptr;
static bool bleConnected = false;

static herd::EdgeEngine engine;
static bool haveMpu = false, haveBmp = false, haveApds = false, haveOled = false;
static float lastTemp = NAN, lastPressure = NAN, lastProx = NAN, lastLight = NAN;
static uint8_t lastState = herd::kUnknown;
static float lastOdba = 0;
static uint8_t lastAlerts = 0;
static bool rawDebug = false;

// ---- store-and-forward ring buffer -------------------------------------
struct Pending { uint32_t t_ms; char json[200]; };
static Pending offline[OFFLINE_SLOTS];
static uint16_t offHead = 0, offCount = 0;

static void bufferOffline(uint32_t t_ms, const char* json) {
  Pending& p = offline[(offHead + offCount) % OFFLINE_SLOTS];
  p.t_ms = t_ms;
  strncpy(p.json, json, sizeof(p.json) - 1);
  p.json[sizeof(p.json) - 1] = 0;
  if (offCount < OFFLINE_SLOTS) offCount++;
  else offHead = (offHead + 1) % OFFLINE_SLOTS;  // drop oldest
}

// ---- BLE ----------------------------------------------------------------
class BleCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer*) override { bleConnected = true; }
  void onDisconnect(BLEServer* s) override {
    bleConnected = false;
    s->getAdvertising()->start();
  }
};

static void setupBle() {
  BLEDevice::init(COLLAR_ID);
  BLEServer* server = BLEDevice::createServer();
  server->setCallbacks(new BleCallbacks());
  BLEService* svc = server->createService(BLE_SERVICE_UUID);
  bleTx = svc->createCharacteristic(BLE_TX_UUID, BLECharacteristic::PROPERTY_NOTIFY);
  bleTx->addDescriptor(new BLE2902());
  svc->start();
  server->getAdvertising()->addServiceUUID(BLE_SERVICE_UUID);
  server->getAdvertising()->start();
}

static void bleSend(const char* msg) {
  if (!bleConnected || !bleTx) return;
  bleTx->setValue((uint8_t*)msg, strlen(msg));
  bleTx->notify();
}

// ---- Wi-Fi / MQTT ---------------------------------------------------------
static bool wifiEnabled() { return strlen(WIFI_SSID) > 0; }

static bool mqttReady() {
  if (!wifiEnabled() || WiFi.status() != WL_CONNECTED) return false;
  if (mqtt.connected()) return true;
  static uint32_t lastTry = 0;
  if (millis() - lastTry < 5000) return false;
  lastTry = millis();
  return mqtt.connect(COLLAR_ID, TB_DEVICE_TOKEN, nullptr);
}

// Epoch milliseconds for a millis() timestamp, 0 if NTP time is unknown.
static uint64_t epochMs(uint32_t t_ms) {
  time_t now = time(nullptr);
  if (now < 1700000000) return 0;
  return (uint64_t)now * 1000ULL - (uint64_t)(millis() - t_ms);
}

static bool publishTelemetry(uint32_t t_ms, const char* valuesJson) {
  if (!mqttReady()) return false;
  char msg[300];
  uint64_t ts = epochMs(t_ms);
  if (ts) snprintf(msg, sizeof msg, "{\"ts\":%llu,\"values\":%s}", (unsigned long long)ts, valuesJson);
  else snprintf(msg, sizeof msg, "%s", valuesJson);
  return mqtt.publish("v1/devices/me/telemetry", msg);
}

static void flushOffline() {
  while (offCount && mqttReady()) {
    Pending& p = offline[offHead];
    if (!publishTelemetry(p.t_ms, p.json)) break;
    offHead = (offHead + 1) % OFFLINE_SLOTS;
    offCount--;
  }
}

// ---- OLED -----------------------------------------------------------------
static void drawOled() {
  if (!haveOled) return;
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  oled.setTextSize(1);
  oled.setCursor(0, 0);
  oled.print("SMART HERD EDGE ");
  oled.print(WiFi.status() == WL_CONNECTED ? "W" : "-");
  oled.print(bleConnected ? "B" : "-");
  oled.setTextSize(2);
  oled.setCursor(0, 14);
  oled.print(herd::stateName(lastState));
  oled.setTextSize(1);
  oled.setCursor(0, 36);
  oled.printf("act %.2fg  T %.1fC", lastOdba, isnan(lastTemp) ? 0.0f : lastTemp);
  oled.setCursor(0, 48);
  if (lastAlerts) oled.printf("ALERT 0x%02X", lastAlerts);
  else oled.printf("buf %u  ok", offCount);
  oled.display();
}

// ---- sensors --------------------------------------------------------------
static void calibrateStanding() {
  // Average gravity over 2 s while the animal stands still at fitting time.
  if (!haveMpu) return;
  float s[3] = {0, 0, 0};
  int n = 0;
  for (uint32_t t0 = millis(); millis() - t0 < 2000; n++) {
    sensors_event_t a, g, tmp;
    mpu.getEvent(&a, &g, &tmp);
    s[0] += a.acceleration.x; s[1] += a.acceleration.y; s[2] += a.acceleration.z;
    delay(1000 / IMU_HZ);
  }
  engine.setReference(s[0] / n, s[1] / n, s[2] / n);
  Serial.printf("[cal] standing reference set from %d samples\n", n);
}

static void readEnvironment() {
  if (haveBmp) {
    lastTemp = bmp.readTemperature();
    lastPressure = bmp.readPressure() / 1000.0f;  // Pa -> kPa
  }
  if (haveApds) {
    uint16_t light;
    uint8_t prox;
    lastLight = apds.readAmbientLight(light) ? light : NAN;
    lastProx = apds.readProximity(prox) ? prox : NAN;
  }
}

static void handleWindow(const herd::WindowResult& w) {
  lastState = w.state;
  lastOdba = w.odba_g;
  if (w.alerts) {
    lastAlerts = w.alerts;
    char msg[160];
    snprintf(msg, sizeof msg, "{\"alert\":%u,\"st\":\"%s\",\"pk\":%.2f,\"tC\":%.1f}", w.alerts,
             herd::stateName(w.state), w.peak_g, w.temp_c);
    Serial.printf("[alert] %s\n", msg);
    bleSend(msg);
    if (!publishTelemetry(w.t_end_ms, msg)) bufferOffline(w.t_end_ms, msg);  // alerts are never dropped
  }
  Serial.printf("[win] %s odba=%.3fg posture=%.0fdeg valid=%u/%u z=%.1f\n", herd::stateName(w.state),
                w.odba_g, w.posture_deg, w.n_valid, w.n_total, w.z);
  drawOled();
}

void setup() {
  Serial.begin(115200);
  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(400000);

  haveMpu = mpu.begin(0x68) || mpu.begin(0x69);
  if (haveMpu) {
    mpu.setAccelerometerRange(MPU6050_RANGE_4_G);  // matches the 4 g validity limit
    mpu.setGyroRange(MPU6050_RANGE_500_DEG);
    mpu.setFilterBandwidth(MPU6050_BAND_21_HZ);    // anti-alias for 20 Hz sampling
  }
  haveBmp = bmp.begin();
  haveApds = apds.init() && apds.enableLightSensor(false) && apds.enableProximitySensor(false);
  haveOled = oled.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  Serial.printf("[boot] MPU6050=%d BMP180=%d APDS9960=%d OLED=%d\n", haveMpu, haveBmp, haveApds, haveOled);

  setupBle();
  if (wifiEnabled()) {
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    configTime(0, 0, "pool.ntp.org");
    mqtt.setServer(TB_HOST, TB_PORT);
    mqtt.setBufferSize(512);
  }
  readEnvironment();
  calibrateStanding();
  drawOled();
}

void loop() {
  static uint32_t nextImu = 0, nextEnv = 0;
  uint32_t now = millis();

  if (Serial.available()) {
    char c = Serial.read();
    if (c == 'c') calibrateStanding();
    if (c == 'r') rawDebug = !rawDebug;
  }

  if ((int32_t)(now - nextEnv) >= 0) {
    nextEnv = now + ENV_PERIOD_MS;
    readEnvironment();
  }

  if ((int32_t)(now - nextImu) >= 0) {
    nextImu = now + 1000 / IMU_HZ;
    herd::Sample s;
    s.t_ms = now;
    if (haveMpu) {
      sensors_event_t a, g, tmp;
      mpu.getEvent(&a, &g, &tmp);
      s.ax = a.acceleration.x; s.ay = a.acceleration.y; s.az = a.acceleration.z;
      s.gx = g.gyro.x * 57.29578f; s.gy = g.gyro.y * 57.29578f; s.gz = g.gyro.z * 57.29578f;  // rad/s -> deg/s
    } else {
      s.ax = s.ay = s.az = s.gx = s.gy = s.gz = NAN;  // engine flags this as a dropout
    }
    s.temp_c = lastTemp; s.pressure_kpa = lastPressure;
    s.proximity = lastProx; s.light = lastLight;
    if (rawDebug)
      Serial.printf("%lu,%.2f,%.2f,%.2f,%.1f,%.1f,%.1f,%.1f,%.2f,%.0f,%.0f\n", (unsigned long)now, s.ax, s.ay,
                    s.az, s.gx, s.gy, s.gz, s.temp_c, s.pressure_kpa, s.proximity, s.light);

    herd::WindowResult w;
    if (engine.push(s, &w)) handleWindow(w);

    herd::Summary sum;
    if (engine.popSummary(&sum, SUMMARY_PERIOD_MS)) {
      char json[200];
      herd::EdgeEngine::summaryJson(sum, json, sizeof json);
      Serial.printf("[summary] %s\n", json);
      bleSend(json);
      flushOffline();
      if (!publishTelemetry(sum.t_end_ms, json)) bufferOffline(sum.t_end_ms, json);
      lastAlerts = sum.alerts;
      drawOled();
    }
  }

  if (wifiEnabled()) mqtt.loop();
}
