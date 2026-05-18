# Kanda Robot — System Diagrams

> **Color convention across all diagrams**
> - 🟢 **Green** = Completed / Implemented
> - ⚫ **Gray** = Planned / Pending

---

## 1. High-Level Design (HLD)

Overall system architecture across all phases.

```mermaid
graph TB
    classDef done fill:#16A34A,color:#fff,stroke:#15803D
    classDef pending fill:#9CA3AF,color:#fff,stroke:#6B7280
    classDef layer fill:#EFF6FF,stroke:#2563EB,color:#1E3A5F

    subgraph P2["⚙️  Phase 2 — Embodiment Layer  ✅ COMPLETED"]
        S1["HC-SR04\nFront"]:::done
        S2["HC-SR04\nLeft"]:::done
        S3["HC-SR04\nRight"]:::done
        ESP["ESP32\nMicrocontroller"]:::done
        MD["TB6612FNG\nMotor Driver"]:::done
        OLED["SSD1306\nOLED Display"]:::done
        ML["Left Motor"]:::done
        MR["Right Motor"]:::done
        PWR["Battery → BMS\n→ Buck Converter"]:::done

        S1 -->|"distance (cm)"| ESP
        S2 -->|"distance (cm)"| ESP
        S3 -->|"distance (cm)"| ESP
        ESP -->|"AIN1/2 BIN1/2 PWM"| MD
        ESP -->|"I2C (GPIO 21/22)"| OLED
        MD --> ML
        MD --> MR
        PWR -->|"5V regulated"| ESP
        PWR -->|"7.4V direct"| MD
    end

    subgraph P3["🧠  Phase 3 — Intelligence Layer  ⏳ PLANNED"]
        CAM["Camera\nModule"]:::pending
        MIC["Microphone"]:::pending
        SPK["Speaker"]:::pending
        PI["Raspberry Pi\n(Brain)"]:::pending
        LLM["LLM API\n(GPT-4 / Gemini)"]:::pending
        SFT["Safety\nValidator"]:::pending

        CAM -->|"video frames"| PI
        MIC -->|"audio stream"| PI
        PI -->|"multimodal context"| LLM
        LLM -->|"JSON command"| SFT
        SFT -->|"validated action"| PI
        PI -->|"UART/Serial"| ESP
        ESP -->|"sensor telemetry"| PI
        SPK -.->|"TTS audio"| PI
    end
```

---

## 2. Low-Level Design (LLD)

Detailed pin-level wiring and signal flow.

```mermaid
graph LR
    classDef done fill:#16A34A,color:#fff,stroke:#15803D
    classDef pending fill:#9CA3AF,color:#fff,stroke:#6B7280
    classDef pin fill:#DBEAFE,color:#1E3A5F,stroke:#93C5FD

    subgraph POWER["Power Rail ✅"]
        BAT["LiPo 7.4V"]:::done
        BMS["BMS"]:::done
        SW["Switch"]:::done
        BUCK["Buck\nConverter\n→ 5V"]:::done
        BAT --> BMS --> SW
        SW -->|"7.4V"| BUCK
    end

    subgraph SENSORS["HC-SR04 Sensors ✅"]
        direction TB
        TF["TRIG_F → GPIO5\nECHO_F ← GPIO34"]:::done
        TL["TRIG_L → GPIO13\nECHO_L ← GPIO35"]:::done
        TR["TRIG_R → GPIO4\nECHO_R ← GPIO32"]:::done
        VD["Voltage Divider\n1kΩ + 1kΩ\n5V → 2.5V"]:::done
        TF --> VD
        TL --> VD
        TR --> VD
    end

    subgraph MCU["ESP32 ✅"]
        CPU["ESP32 DevKit\n(Arduino Core v3+)"]:::done
        OPIN["GPIO 21 SDA\nGPIO 22 SCL"]:::done
        MPIN["GPIO18 AIN1\nGPIO19 AIN2\nGPIO23 PWMA\nGPIO26 BIN1\nGPIO27 BIN2\nGPIO14 PWMB"]:::done
    end

    subgraph OLED_["SSD1306 OLED ✅"]
        OD["128×64\nI2C 0x3C"]:::done
    end

    subgraph MOTOR["TB6612FNG + Motors ✅"]
        DRV["TB6612FNG\nDriver"]:::done
        MA["Motor A\n(Left)"]:::done
        MB["Motor B\n(Right)"]:::done
        DRV --> MA
        DRV --> MB
    end

    subgraph FUTURE["Raspberry Pi Bridge ⏳"]
        RPI["Raspberry Pi\nUART RX/TX"]:::pending
    end

    BUCK -->|"5V"| CPU
    SW -->|"7.4V"| DRV
    VD -->|"2.5V safe"| CPU
    CPU --> OPIN --> OD
    CPU --> MPIN --> DRV
    CPU -.->|"UART GPIO1/3\n(planned)"| RPI
```

---

## 3. Flow Diagram

Decision logic — current rule-based loop and planned LLM loop.

```mermaid
flowchart TD
    classDef done fill:#16A34A,color:#fff,stroke:#15803D
    classDef pending fill:#9CA3AF,color:#fff,stroke:#6B7280
    classDef decision fill:#DBEAFE,color:#1E3A5F,stroke:#2563EB

    START(["⚡ Power On"]):::done
    INIT["Initialize\nOLED · Pins · PWM"]:::done
    READ["Read Sensors\nF, L, R distances"]:::done
    CHK1{"Front < 20cm?"}:::done
    STOP["Stop Motors\n200ms"]:::done
    CHK2{"Left > Right?"}:::done
    TL["Turn LEFT\n400ms"]:::done
    TR["Turn RIGHT\n400ms"]:::done
    CHK3{"Left < 15cm?"}:::done
    CHK4{"Right < 15cm?"}:::done
    SR["Slight RIGHT\n(PWMA 50%)"]:::done
    SL["Slight LEFT\n(PWMB 50%)"]:::done
    FWD["FORWARD\nfull speed"]:::done
    DISP["Update OLED\n+ Serial log"]:::done
    WAIT["Delay 100ms"]:::done

    LLM_CHK{"LLM Mode\nEnabled?"}:::pending
    CTX["Build Context\n(sensors + camera + mic)"]:::pending
    LLM["LLM API Call\n(Raspberry Pi)"]:::pending
    SAFE{"Safety\nValidator"}:::pending
    CMD["Parse JSON\nCommand"]:::pending

    START --> INIT --> READ
    READ --> CHK1
    CHK1 -->|YES| STOP --> CHK2
    CHK2 -->|YES| TL
    CHK2 -->|NO| TR
    TL --> DISP
    TR --> DISP
    CHK1 -->|NO| CHK3
    CHK3 -->|YES| SR --> DISP
    CHK3 -->|NO| CHK4
    CHK4 -->|YES| SL --> DISP
    CHK4 -->|NO| FWD --> DISP
    DISP --> WAIT --> READ

    READ -.->|"Phase 3"| LLM_CHK
    LLM_CHK -.->|"YES"| CTX
    CTX -.-> LLM -.-> SAFE
    SAFE -.->|"PASS"| CMD -.->|"overrides rule logic"| CHK1
    SAFE -.->|"FAIL"| STOP
```

---

## 4. Use Case Diagram

Actor interactions with the system — current and planned.

```mermaid
graph LR
    classDef done fill:#16A34A,color:#fff,stroke:#15803D
    classDef pending fill:#9CA3AF,color:#fff,stroke:#6B7280
    classDef actor fill:#FEF3C7,color:#92400E,stroke:#F59E0B
    classDef sys fill:#EFF6FF,stroke:#2563EB,color:#1E3A5F

    USER(["👤 User /\nEngineer"]):::actor
    ENV(["🌍 Environment\n(Obstacles, Space)"]):::actor
    LLMACT(["🤖 LLM Service\n(GPT-4 / Gemini)"]):::pending

    subgraph SYS["Kanda Robot System"]
        UC1["Flash Firmware\nto ESP32"]:::done
        UC2["Monitor via\nSerial + OLED"]:::done
        UC3["Tune Speed /\nThreshold params"]:::done
        UC4["Autonomous\nObstacle Avoidance"]:::done
        UC5["Real-time Display\nof Sensor + Decision"]:::done
        UC6["Give Voice /\nText Command"]:::pending
        UC7["Camera-based\nScene Understanding"]:::pending
        UC8["LLM Reasoning\n& Decision"]:::pending
        UC9["Action via\nNatural Language"]:::pending
        UC10["Safety Override\n/ Validation"]:::pending
    end

    USER --> UC1
    USER --> UC2
    USER --> UC3
    USER --> UC6
    ENV -->|"distance triggers"| UC4
    UC4 --> UC5
    UC6 --> UC8
    UC7 --> UC8
    UC8 --> UC9
    UC8 --> UC10
    LLMACT -.-> UC8
    UC9 -.->|"Phase 3"| UC4
```
