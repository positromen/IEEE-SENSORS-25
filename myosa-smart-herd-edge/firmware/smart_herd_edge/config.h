// config.h - per-deployment settings for the Smart Herd Edge collar.
// Fill these in before flashing. Never commit real credentials.
#pragma once

// ---- Wi-Fi / ThingsBoard (leave WIFI_SSID empty for BLE-only operation) ----
#define WIFI_SSID          ""
#define WIFI_PASSWORD      ""
#define TB_HOST            "thingsboard.cloud"
#define TB_PORT            1883
#define TB_DEVICE_TOKEN    ""            // ThingsBoard device access token

// ---- Identity ----
#define COLLAR_ID          "herd-collar-01"

// ---- Sampling ----
#define IMU_HZ             20            // MPU6050 sampling rate fed to the edge engine
#define ENV_PERIOD_MS      2000          // BMP180 + APDS9960 read period
#define SUMMARY_PERIOD_MS  60000         // one radio message per minute

// ---- I2C (MYOSA motherboard, ESP32 default bus) ----
#define I2C_SDA            21
#define I2C_SCL            22

// ---- Store-and-forward buffer (minute summaries kept while offline) ----
#define OFFLINE_SLOTS      180           // 3 hours
