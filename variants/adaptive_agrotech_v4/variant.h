#pragma once

// Adaptive AgroTech Wireless Datalogger and Smart Controller v4.0.
#define CONFIG_ESP32_WROOM
#define USE_SX1276
#define USE_RF95 // Meshtastic's SX1276 driver is selected by USE_RF95.

// GPIOs confirmed by the user's continuity measurements on 2026-11-02.
#define LORA_SCK 18
#define LORA_MISO 19
#define LORA_MOSI 23
#define LORA_CS 5
#define LORA_DIO0 4
#define LORA_RESET 14
#define LORA_DIO1 RADIOLIB_NC
#define LORA_DIO2 RADIOLIB_NC

#define HAS_SCREEN 0
#define HAS_GPS 0

#if LORA_SCK < 0 || LORA_MISO < 0 || LORA_MOSI < 0 || LORA_CS < 0 || LORA_DIO0 < 0 || LORA_RESET < 0
#error "Measure and set all six Adaptive AgroTech LoRa GPIOs before building firmware for hardware."
#endif
