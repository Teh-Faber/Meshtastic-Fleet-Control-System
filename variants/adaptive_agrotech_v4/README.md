# Adaptive AgroTech v4.0 firmware bring-up

This is a **firmware overlay using the user's measured GPIO assignments**, not yet
validated by an on-device radio test.
This repository contains the ROS 2 bridge,
not Meshtastic firmware. The root
`platformio.ini` is a section to append to the real firmware configuration.

The requested ESP32-WROOM-32E and RFM96/SX1276 combination is assumed. The photos
show an ESP32 module, a LoRa module, and an STM32 processor. The user's continuity
measurements on 2026-11-02 supplied the six GPIO assignments below for the ESP32.
Continuity does not establish whether the STM32 shares the bus; check this before
powering both processors as potential SPI masters.

## Measure the radio connections

Disconnect USB, external power, and the CR2032 before resistance/continuity
measurements. Use the **top-view** pin diagram in the
[HopeRF datasheet, pages 10-11](https://www.hoperf.com/uploads/RFM96W-V2.0_1695351477.pdf).
Do not infer pad numbering from the orientation of the board in a photo.

Probe each radio pad against the ESP32 module's exposed castellated pads. Record
the destination module pad number and GPIO, or record that it goes to the STM32.
Near-zero resistance comparable to shorted probes is stronger evidence than a
continuity beep; semiconductor paths and series resistors can confuse the result.

| Signal | Radio module pad | ESP32 module pad | ESP32 GPIO |
| --- | ---: | --- | --- |
| SCK | 4 | 30 | 18 |
| MISO | 2 | 31 | 19 |
| MOSI | 3 | 37 | 23 |
| NSS / CS | 5 | 29 | 5 |
| DIO0 | 14 | 26 | 4 |
| RESET | 6 | 13 | 14 |

The user rechecked MOSI and confirmed GPIO23 / module pad **37**. Module pad **38**
is GND and must not be used for MOSI.

Also check radio GND (pads 1, 8, 10) and 3.3 V (pad 13). Optional DIO1 is pad 15;
record its connection if present. Use the
[Espressif module datasheet, section 3](https://documentation.espressif.com/esp32-wroom-32e_esp32-wroom-32ue_datasheet_en.html)
to translate **module pad numbers** to **GPIO numbers**. These are different.
Never use ESP32 flash pins GPIO6-11 for the radio; GPIO34-39 are input-only.

Confirm the radio's exact part number and frequency variant. HopeRF lists RFM96W
for 433/470 MHz and RFM95W for 868/915 MHz. Selecting a firmware region does not
change the module's RF matching network or antenna. The photo does not verify the
radio marking or antenna band.

## Install the overlay and build

The ignored `.firmware-validation` directory is a separate upstream checkout
at commit `4c89b6937cc5073fc71fa825dfcca7c9f18becee` on `develop`.
For a fresh checkout, run these commands from this repository's root (PowerShell):

```powershell
git clone --recurse-submodules https://github.com/meshtastic/firmware.git .firmware-validation
New-Item -ItemType Directory -Force .firmware-validation/variants/adaptive_agrotech_v4
Copy-Item variants/adaptive_agrotech_v4/variant.h .firmware-validation/variants/adaptive_agrotech_v4/
# Append ONCE; the validation checkout prepared here already has this section.
Get-Content platformio.ini | Add-Content .firmware-validation/platformio.ini
```

The source `variant.h` contains these measured GPIOs. Copy any later changes to
the header into the firmware checkout again. A guard rejects negative placeholder
GPIOs if the variant is adapted again. DIO1/DIO2 are explicitly disconnected;
update them if traced and required. No unverified display, I2C, GPS, LED, button,
sensor, or battery-measurement pin assignments are enabled by this overlay.

Install the version of PlatformIO required by the chosen firmware revision;
the checked revision requires **6.2.0**, installed locally in
`.firmware-validation/.venv`. Use that executable without changing your global
PlatformIO installation. Prepend the virtualenv's `Scripts` directory to `PATH`:
the platform's nested Arduino framework build calls `pio` by name, and an older
global PlatformIO on `PATH` fails the platform compatibility check.

```powershell
$env:PATH = (Resolve-Path .firmware-validation/.venv/Scripts).Path + ';' + $env:PATH
Remove-Item Env:PLATFORMIO_BUILD_FLAGS -ErrorAction SilentlyContinue
$pio = (Resolve-Path .firmware-validation/.venv/Scripts/pio.exe).Path
& $pio run -e adaptive_agrotech_v4 -d .firmware-validation
```

On 2026-11-02 this built the full Meshtastic 2.8.2 application at commit
`4c89b69` (about 27 minutes for the first build, which compiles a custom
ESP-IDF/Arduino framework first). Results: RAM 29.9 % (98,036 bytes), flash 73.4 %
(1,779,211 of 2,424,832 bytes in the `app` partition), with the RF95/SX127x
driver linked. Outputs in `.firmware-validation/.pio/build/adaptive_agrotech_v4/`:

| File | Use |
| --- | --- |
| `firmware-adaptive_agrotech_v4-2.8.2.4c89b69.factory.bin` | Full image for offset `0x0` (bootloader, partitions, app) |
| `firmware-adaptive_agrotech_v4-2.8.2.4c89b69.bin` | Application only, offset `0x10000` |
| `littlefs-adaptive_agrotech_v4-2.8.2.4c89b69.bin` | Filesystem image, `spiffs` partition at `0x300000` |

The partition table assumes **4 MB** flash. Compilation cannot establish radio
operation or frequency band.

The overlay preserves `${esp32_base.build_flags}` and uses `board = esp32dev`.
`USE_RF95` is necessary for the upstream SX127x driver; `USE_SX1276` and
`CONFIG_ESP32_WROOM` are retained as requested. `RF95Configuration.h` aliases
`LORA_DIO0`, `LORA_RESET`, and `LORA_DIO1` into the driver's pin macros. Unknown
ESP32 boards default to `PRIVATE_HW`; no existing board identity is borrowed.
Upstream normally places variants under `variants/esp32/<name>`; the requested
`variants/adaptive_agrotech_v4` path works through the explicit include flag and
the environment appended to the firmware root configuration.

## Before the first flash

With the measured GPIO map installed, identify the USB serial port and verify
that it reaches the ESP32 bootloader. The visible
reset button's wiring and automatic download circuitry are unverified. If manual
download mode is needed, ESP32 GPIO0 must be low during ESP32 EN reset; identify
those nets from the module datasheet before using any jumper.

Read and back up the ESP32's existing flash, confirm its actual flash size, and
check that the selected partition table fits. Connect an antenna matched to the
verified radio band. Build the measured-pin target before uploading, in the
same shell with the virtualenv `PATH` set as shown above:

```powershell
& $pio run -e adaptive_agrotech_v4 -d .firmware-validation -t upload --upload-port COM_PORT
& $pio device monitor --port COM_PORT --baud 115200
```

Replace `COM_PORT` with the verified ESP32 port. A first installation may also
need the firmware's filesystem installation procedure; follow the instructions
for the chosen firmware release. Verify radio initialization and bidirectional
packets with another node on the same supported band, region, modem preset, and
channel.

## First flash (2026-11-02)

One board (ESP32-D0WD-V3, 4 MB flash, MAC `c4:dd:57:b9:12:28`) enumerated as a
CH340 serial port and entered download mode automatically. Its original 4 MB
flash was backed up to `.firmware-validation/flash-backup/` first. The original
firmware contains `LoRa.begin(868E6)`, so the fitted radio was already driven at
868 MHz. The factory image (`0x0`) and LittleFS image (`0x300000`) were written
with esptool at 115200 baud; reads at 460800 baud failed with this adapter.

On boot the firmware reported `RF95 init success`. After
`meshtastic --port COM_PORT --set lora.region EU_868` it reports region EU_868,
LongFast, 869.525 MHz, 20 dBm output. The Arduino core also starts I2C on GPIO21/22
(SDA/SCL defaults); check those nets if the STM32 or sensors use them.

A broadcast text and position were transmitted and the node logged airtime
(`airUtilTx` > 0), which requires the DIO0 TX-done interrupt. No other node was
heard in 150 s, so **reception is unverified**; repeat the packet test with a
second EU_868 / LongFast node in range.

## Reflash, configure, and test

Run from this repository's root in PowerShell. esptool and pyserial come from
PlatformIO's own environment; the board appeared as `COM17` on the bring-up PC.

```powershell
$port = 'COM17'
$py = "$env:USERPROFILE\.platformio\penv\Scripts\python.exe"
$et = "$env:USERPROFILE\.platformio\packages\tool-esptoolpy\esptool.py"
$b = '.firmware-validation/.pio/build/adaptive_agrotech_v4'

# Identify the chip and flash size (read-only).
& $py $et --port $port flash-id

# Back up the whole 4 MB flash before the first write.
New-Item -ItemType Directory -Force .firmware-validation/flash-backup | Out-Null
& $py $et --port $port --baud 115200 read-flash 0 0x400000 .firmware-validation/flash-backup/original-4MB.bin

# Write firmware and filesystem (file names follow the built version).
& $py $et --port $port --baud 115200 write-flash --flash-mode dio --flash-freq 40m --flash-size 4MB `
  0x0 "$b/firmware-adaptive_agrotech_v4-2.8.2.4c89b69.factory.bin" `
  0x300000 "$b/littlefs-adaptive_agrotech_v4-2.8.2.4c89b69.bin"
```

For an application-only update that keeps settings, write only
`firmware-adaptive_agrotech_v4-*.bin` at `0x10000`.

Install the Meshtastic CLI in its own environment, set the region, and run the
packet test ([tools/packet_test.py](tools/packet_test.py)):

```powershell
py -m venv .firmware-validation/.cli-venv
& .firmware-validation/.cli-venv/Scripts/python.exe -m pip install meshtastic
$env:PYTHONIOENCODING = 'utf-8'
& .firmware-validation/.cli-venv/Scripts/meshtastic.exe --port $port --set lora.region EU_868
& .firmware-validation/.cli-venv/Scripts/meshtastic.exe --port $port --get lora.region --get lora.modem_preset
& .firmware-validation/.cli-venv/Scripts/python.exe variants/adaptive_agrotech_v4/tools/packet_test.py $port 150
```

The node reboots after the region change. Look for `RF95 init success` and the
EU_868 frequency in the serial log (115200 baud).

To restore the original firmware, write the backup back:

```powershell
& $py $et --port $port --baud 115200 write-flash 0x0 .firmware-validation/flash-backup/c4dd57b91228-original-4MB.bin
```

The flash backup is deliberately not committed: it is the vendor's firmware and
its NVS region may hold device configuration or credentials. Keep it outside git.
