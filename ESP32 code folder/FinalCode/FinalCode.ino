#include <WiFi.h>
#include <WebServer.h>
#include <ESP32Servo.h>
#include <Adafruit_NeoPixel.h>

// --- WI-FI SOZLAMALARI ---
const char* WIFI_SSID = "iPhone 12 Pro";
const char* WIFI_PASS = "salom1213";


// --- PINLAR ---
#define PIN_NECK      4   // MG996R (Bo'yin)
#define PIN_ARM_L     5   // MG90S (Chap qo'l)
#define PIN_ARM_R     6   // MG90S (O'ng qo'l)
#define PIN_LEDS      7   // 2x WCMCU-2812B-8 parallel = 8 ta LED
#define NUM_LEDS      8

#define ENA           9   // Chap motor tezligi (PWM)
#define IN1           10
#define IN2           11
#define IN3           12
#define IN4           13
#define ENB           14  // O'ng motor tezligi (PWM)

WebServer server(80);
Servo neckServo;
Servo leftArm;
Servo rightArm;
Adafruit_NeoPixel eyes(NUM_LEDS, PIN_LEDS, NEO_GRB + NEO_KHZ800);

// --- HOLAT O'ZGARUVCHILARI ---
int motorSpeed = 140;
int eyeMode = 1;          // 0=OFF, 1=IDLE, 2=LISTEN, 3=THINK, 4=SPEAK, 5=RAINBOW, 6=CUSTOM RGB
int eyeBrightness = 70;
uint8_t customR = 0, customG = 180, customB = 255;
unsigned long lastAnimTime = 0;
int animStep = 0;
unsigned long motorStopTime = 0;

// --- MOTOR FUNKSIYALARI ---
void motorStop() {
  analogWrite(ENA, 0); analogWrite(ENB, 0);
  digitalWrite(IN1, LOW); digitalWrite(IN2, LOW);
  digitalWrite(IN3, LOW); digitalWrite(IN4, LOW);
}
void motorForward() {
  digitalWrite(IN1, HIGH); digitalWrite(IN2, LOW);
  digitalWrite(IN3, HIGH); digitalWrite(IN4, LOW);
  analogWrite(ENA, motorSpeed); analogWrite(ENB, motorSpeed);
}
void motorBack() {
  digitalWrite(IN1, LOW); digitalWrite(IN2, HIGH);
  digitalWrite(IN3, LOW); digitalWrite(IN4, HIGH);
  analogWrite(ENA, motorSpeed); analogWrite(ENB, motorSpeed);
}
void motorLeft() {
  digitalWrite(IN1, LOW); digitalWrite(IN2, HIGH);
  digitalWrite(IN3, HIGH); digitalWrite(IN4, LOW);
  analogWrite(ENA, motorSpeed); analogWrite(ENB, motorSpeed);
}
void motorRight() {
  digitalWrite(IN1, HIGH); digitalWrite(IN2, LOW);
  digitalWrite(IN3, LOW); digitalWrite(IN4, HIGH);
  analogWrite(ENA, motorSpeed); analogWrite(ENB, motorSpeed);
}

// --- QO'L HARAKATLARI ---
void armsRest() {
  leftArm.write(90);
  rightArm.write(90);
}
void armsUp() {
  leftArm.write(50);
  rightArm.write(130);
}
void waveHello() {
  for (int i = 0; i < 2; i++) {
    rightArm.write(145);
    delay(250);
    rightArm.write(110);
    delay(250);
  }
  rightArm.write(90);
}

// --- KO'Z ANIMATSIYALARI ---
void updateEyesAnimation() {
  unsigned long now = millis();
  if (now - lastAnimTime < 60) return;
  lastAnimTime = now;
  animStep++;

  eyes.setBrightness(eyeBrightness);

  if (eyeMode == 0) {
    eyes.clear();
  } else if (eyeMode == 1) {
    eyes.fill(eyes.Color(0, 180, 255));
  } else if (eyeMode == 2) {
    int pulse = 120 + (int)(135 * sin(animStep * 0.2));
    eyes.fill(eyes.Color(pulse, 15, 35));
  } else if (eyeMode == 3) {
    eyes.clear();
    int pos = animStep % NUM_LEDS;
    eyes.setPixelColor(pos, eyes.Color(180, 40, 255));
    eyes.setPixelColor((pos + NUM_LEDS - 1) % NUM_LEDS, eyes.Color(60, 10, 100));
  } else if (eyeMode == 4) {
    int val = random(80, 255);
    eyes.fill(eyes.Color(0, val, 110));
  } else if (eyeMode == 5) {
    for (int i = 0; i < NUM_LEDS; i++) {
      int hue = (animStep * 1500 + i * 65536 / NUM_LEDS) % 65536;
      eyes.setPixelColor(i, eyes.gamma32(eyes.ColorHSV(hue)));
    }
  } else if (eyeMode == 6) {
    eyes.fill(eyes.Color(customR, customG, customB));
  }
  eyes.show();
}

// --- ZAXIRA WEB DASHBOARD ---
const char DASHBOARD_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<title>LexRobot Dashboard</title>
<style>
  body { background:#0d1117; color:#e6edf3; font-family:system-ui,sans-serif; margin:0; padding:14px; text-align:center; }
  h2 { margin:6px 0 14px; color:#58a6ff; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(290px, 1fr)); gap:14px; max-width:960px; margin:auto; }
  .card { background:#161b22; border:1px solid #30363d; border-radius:16px; padding:16px; }
  h3 { margin:0 0 12px; font-size:16px; color:#8b949e; text-transform:uppercase; letter-spacing:1px; }
  .dpad { display:grid; grid-template-columns:repeat(3, 72px); gap:10px; justify-content:center; margin:10px 0; }
  button { background:#21262d; color:#fff; border:1px solid #30363d; border-radius:12px; padding:14px 10px; font-size:15px; font-weight:600; cursor:pointer; touch-action:manipulation; }
  button:active { background:#1f6feb; transform:scale(0.96); }
  .stop { background:#da3633; border-color:#f85149; }
  .btn-row { display:flex; flex-wrap:wrap; gap:8px; justify-content:center; margin-top:10px; }
  .btn-row button { flex:1 1 42%; padding:10px; font-size:13px; }
  .slider-box { text-align:left; margin:12px 0; font-size:14px; }
  input[type=range] { width:100%; accent-color:#58a6ff; margin-top:6px; }
  .val { float:right; color:#58a6ff; font-weight:bold; }
</style>
</head>
<body>
  <h2>🤖 LexRobot Boshqaruv Paneli</h2>
  <div class="grid">
    <div class="card">
      <h3>🛞 Harakat (Oyoqlar)</h3>
      <div class="dpad">
        <div></div>
        <button onclick="send('/motor?dir=fwd&ms=350')">⬆️</button>
        <div></div>
        <button onclick="send('/motor?dir=left&ms=350')">⬅️</button>
        <button class="stop" onclick="send('/motor?dir=stop')">⏹</button>
        <button onclick="send('/motor?dir=right&ms=350')">➡️</button>
        <div></div>
        <button onclick="send('/motor?dir=back&ms=350')">⬇️</button>
        <div></div>
      </div>
      <div class="slider-box">
        Tezlik (PWM): <span id="spVal" class="val">140</span>
        <input type="range" min="80" max="255" value="140" oninput="spVal.innerText=this.value; send('/speed?v='+this.value)">
      </div>
    </div>

    <div class="card">
      <h3>🦾 Bo'yin va Qo'llar</h3>
      <div class="slider-box">
        Bo'yin (30°–150°): <span id="nkVal" class="val">90°</span>
        <input type="range" min="30" max="150" value="90" oninput="nkVal.innerText=this.value+'°'; send('/servo?id=neck&a='+this.value)">
      </div>
      <div class="slider-box">
        Chap qo'l: <span id="laVal" class="val">90°</span>
        <input type="range" min="0" max="180" value="90" oninput="laVal.innerText=this.value+'°'; send('/servo?id=left&a='+this.value)">
      </div>
      <div class="slider-box">
        O'ng qo'l: <span id="raVal" class="val">90°</span>
        <input type="range" min="0" max="180" value="90" oninput="raVal.innerText=this.value+'°'; send('/servo?id=right&a='+this.value)">
      </div>
      <div class="btn-row">
        <button onclick="send('/action?cmd=wave')">👋 Salom berish</button>
        <button onclick="send('/action?cmd=up')">🙌 Qo'llar oldinga</button>
        <button onclick="send('/action?cmd=rest')">🧘 Tinch holat</button>
      </div>
    </div>

    <div class="card">
      <h3>👀 Ko'zlar (LED Animatsiya)</h3>
      <div class="btn-row">
        <button onclick="send('/eye?m=1')" style="border-color:#00b4ff">🔵 Tinch</button>
        <button onclick="send('/eye?m=2')" style="border-color:#ff3b5c">🔴 Eshitish</button>
        <button onclick="send('/eye?m=3')" style="border-color:#b46cff">🟣 O'ylash</button>
        <button onclick="send('/eye?m=4')" style="border-color:#00ff88">🟢 Gapirish</button>
        <button onclick="send('/eye?m=5')" style="border-color:#f59e0b">🌈 Kamalak</button>
        <button onclick="send('/eye?m=0')" class="stop">⚫ O'chirish</button>
      </div>
      <div class="slider-box" style="margin-top:16px">
        Yorug'lik: <span id="brVal" class="val">70</span>
        <input type="range" min="10" max="200" value="70" oninput="brVal.innerText=this.value; send('/eye?b='+this.value)">
      </div>
    </div>
  </div>
<script>
  function send(url) { fetch(url).catch(() => {}); }
</script>
</body>
</html>
)rawliteral";

// --- WEB SERVER YO'NALISHLARI ---
void setupWebRoutes() {
  server.on("/", []() { server.send_P(200, "text/html", DASHBOARD_HTML); });

  server.on("/state", []() {
    String s = server.arg("s");
    bool arms = (server.arg("arms") == "1");

    if (s == "idle") {
      eyeMode = 1;
      neckServo.write(90);
      if (arms) armsRest();
    } else if (s == "listen") {
      eyeMode = 2;
      neckServo.write(95);
    } else if (s == "think") {
      eyeMode = 3;
      neckServo.write(75);
    } else if (s == "speak") {
      eyeMode = 4;
      neckServo.write(90);
      if (arms) {
        leftArm.write(55);
        rightArm.write(125);
      }
    }
    server.send(200, "text/plain", "OK");
  });

  server.on("/rgb", []() {
    customR = constrain(server.arg("r").toInt(), 0, 255);
    customG = constrain(server.arg("g").toInt(), 0, 255);
    customB = constrain(server.arg("b").toInt(), 0, 255);
    eyeMode = 6;
    server.send(200, "text/plain", "OK");
  });

  server.on("/motor", []() {
    String d = server.arg("dir");
    int ms = server.hasArg("ms") ? server.arg("ms").toInt() : 0;

    if (d == "fwd") motorForward();
    else if (d == "back") motorBack();
    else if (d == "left") motorLeft();
    else if (d == "right") motorRight();
    else {
      motorStop();
      motorStopTime = 0;
      server.send(200, "text/plain", "OK");
      return;
    }

    motorStopTime = (ms > 0) ? (millis() + ms) : 0;
    server.send(200, "text/plain", "OK");
  });

  server.on("/speed", []() {
    motorSpeed = constrain(server.arg("v").toInt(), 0, 255);
    server.send(200, "text/plain", "OK");
  });

  server.on("/servo", []() {
    String id = server.arg("id");
    int a = constrain(server.arg("a").toInt(), 0, 180);
    if (id == "neck") neckServo.write(constrain(a, 30, 150));
    else if (id == "left") leftArm.write(a);
    else if (id == "right") rightArm.write(a);
    server.send(200, "text/plain", "OK");
  });

  server.on("/action", []() {
    String cmd = server.arg("cmd");
    if (cmd == "wave") waveHello();
    else if (cmd == "up") armsUp();
    else if (cmd == "rest") armsRest();
    server.send(200, "text/plain", "OK");
  });

  server.on("/eye", []() {
    if (server.hasArg("m")) eyeMode = server.arg("m").toInt();
    if (server.hasArg("b")) eyeBrightness = constrain(server.arg("b").toInt(), 5, 255);
    server.send(200, "text/plain", "OK");
  });
}

void setup() {
  Serial.begin(115200);

  pinMode(ENA, OUTPUT); pinMode(ENB, OUTPUT);
  pinMode(IN1, OUTPUT); pinMode(IN2, OUTPUT);
  pinMode(IN3, OUTPUT); pinMode(IN4, OUTPUT);
  motorStop();

  eyes.begin();
  eyes.setBrightness(eyeBrightness);

  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  neckServo.setPeriodHertz(50);
  leftArm.setPeriodHertz(50);
  rightArm.setPeriodHertz(50);

  neckServo.attach(PIN_NECK, 700, 2300);
  leftArm.attach(PIN_ARM_L, 700, 2300);
  rightArm.attach(PIN_ARM_R, 700, 2300);

  neckServo.write(90);
  armsRest();

  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Wi-Fi ga ulanmoqda");
  while (WiFi.status() != WL_CONNECTED) {
    delay(400);
    Serial.print(".");
  }
  Serial.println("\nUlandi! ESP32 IP manzili:");
  Serial.println(WiFi.localIP());

  setupWebRoutes();
  server.begin();
}

void loop() {
  server.handleClient();
  updateEyesAnimation();

  if (motorStopTime > 0 && millis() >= motorStopTime) {
    motorStop();
    motorStopTime = 0;
  }

  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd == "IDLE") eyeMode = 1;
    else if (cmd == "LISTEN") eyeMode = 2;
    else if (cmd == "THINK") eyeMode = 3;
    else if (cmd == "SPEAK") eyeMode = 4;
    else if (cmd == "OFF") eyeMode = 0;
    else if (cmd == "WAVE") waveHello();
    else if (cmd == "UP") armsUp();
    else if (cmd == "REST") armsRest();
    else if (cmd.startsWith("NECK:")) neckServo.write(constrain(cmd.substring(5).toInt(), 30, 150));
    else if (cmd.startsWith("SPEED:")) motorSpeed = constrain(cmd.substring(6).toInt(), 0, 255);
    else if (cmd == "FWD") { motorForward(); motorStopTime = millis() + 350; }
    else if (cmd == "BACK") { motorBack(); motorStopTime = millis() + 350; }
    else if (cmd == "LEFT") { motorLeft(); motorStopTime = millis() + 350; }
    else if (cmd == "RIGHT") { motorRight(); motorStopTime = millis() + 350; }
    else if (cmd == "STOP") { motorStop(); motorStopTime = 0; }
  }
}