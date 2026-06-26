# I Built a $77 Robot That Understands Natural Language, Searches Rooms, and Has a Three-Layer Safety Brain

*How a college hobby project turned into a multimodal embodied AI agent — and what the numbers actually taught me.*

---

The first time KANDA successfully found my water bottle, I was sitting cross-legged on my hostel room floor at RV College of Engineering, watching a little two-wheeled robot scan the room in slow arcs. I said, "Hey Kanda, find my water bottle." It acknowledged, pivoted, started moving — and two minutes later it stopped in front of the bottle and said, "Found it."

That moment shouldn't have felt as significant as it did. But I'd spent months getting to it, and I knew exactly how many things had to go right at the same time for it to work: the wake word detection, the speech transcription, the language model planning, the vision model recognizing the bottle, the ESP32 firmware steering the motors, the safety validator letting the command through. All of that, running on hardware that cost less than a dinner for two.

This is the story of how I built KANDA.

---

## What KANDA Is

KANDA stands for Knowledge-driven Autonomous Navigation and Decision-making Agent. Underneath the acronym, it's a wheeled robot that you talk to like a person and that attempts to act like one — moving through space, looking at things, answering questions, following compound instructions.

The full hardware bill comes to just under ₹6,500, which is roughly $77. A Raspberry Pi 4 with 4GB of RAM is the main computer. An ESP32 microcontroller handles motors and proximity sensors. A Pi Camera v2.1 gives it eyes. Three HC-SR04 ultrasonic sensors watch front, left, and right. A USB microphone and Bluetooth speaker let it hear and speak. A small SSD1306 OLED display gives it a face — a pair of animated eyes that open when it wakes up.

The AI side runs almost entirely in the cloud. Groq's Llama 3.3 70B handles language reasoning and intent classification. NVIDIA NIM's Llama 3.2 11B Vision model describes what the camera sees. Groq Whisper converts speech to text. Google's gTTS converts responses back to audio. Wake word detection runs locally on the Pi using openWakeWord — the only part of the AI stack that doesn't call a remote API.

Why cloud LLMs? Because putting a capable GPU on a $77 robot is impossible. Groq and NVIDIA NIM both have generous free tiers. The latency — 1.5 to 4 seconds per inference — is acceptable for a robot that's already moving slowly through a room. The economics worked.

---

## The Architecture: Three Layers, Three Timescales

The most important design decision I made was separating intelligence from safety, and separating both from reflexes. I was influenced by Rodney Brooks' subsumption architecture — the idea that complex robot behavior emerges from layered simple behaviors, not a monolithic controller.

KANDA has three tiers.

**The cloud tier** (Deliberative) is where reasoning happens. When you give KANDA a command, it sends a structured prompt to Groq — not just your words, but a full snapshot of the robot's state: sensor distances in all three directions, the most recent visual scene description, the last few actions taken, the current task. The LLM proposes what to do next as a JSON response.

**The edge tier** (Critic) is the Raspberry Pi — the gatekeeper. Every single response from the cloud passes through a safety validator before any command is issued. The validator has two rules: the action must be one of seven allowed values (forward, backward, left, right, slight_left, slight_right, stop), and the speed must be an integer between 0 and 255. Anything that fails either rule is silently replaced with `{"action": "stop", "speed": 0}`. The Pi also runs the state machine, manages the Telegram bot, handles voice output, and coordinates the full async event loop.

**The device tier** (Reflex) is the ESP32. It runs independently of the Pi. It reads ultrasonic sensors at 10 Hz. If anything gets within 15 centimeters of the front sensor, it cuts motor power in 47 milliseconds — regardless of what the Pi is doing, regardless of what the cloud said. If you unplug the USB cable between the Pi and the ESP32 while KANDA is moving, it stops. Reflexes don't wait for permission.

The three tiers communicate vertically: HTTPS JSON between the cloud and the Pi, UART serial at 115,200 baud between the Pi and the ESP32. The tiers have no horizontal awareness of each other — the ESP32 doesn't know what the LLM said, and the LLM doesn't know the ESP32 exists.

---

## The Journey: What Actually Happened

I didn't sit down and design this architecture on day one. I built it in phases over several months, each one breaking the previous one in interesting ways.

Phase 1 was pure hardware: get motors turning, get sensors reading, get the ESP32 doing basic obstacle avoidance. Boring but essential. The OLED face was a morale feature — when the eyes blinked, it felt alive.

Phase 2 was connecting the Pi. I wrote a simple serial protocol so the Pi could send JSON commands to the ESP32 and receive telemetry back. This was where I first appreciated how different the debugging experience is when two systems are written in different languages and running on different clocks. C++ on the ESP32, Python on the Pi, talking over USB at 115,200 baud. A single malformed JSON packet — an extra newline, a truncated transmission — would hang one side waiting for a response that would never come. I added timeouts, checksums, and a lot of patience.

Phase 3 was the AI layer — the first version, which I now call `ai_layer`. It worked. KANDA could hear a command, call Groq, get a plan, and execute it. But the code was synchronous. Each step blocked the next. While the LLM was thinking, the Pi was frozen — not reading sensors, not checking for new commands, not updating the OLED. If a Telegram message arrived mid-inference, it waited. If inference took five seconds, the robot was effectively offline for five seconds.

Phase 4, kanda_v2, was a complete rewrite. Everything became async. An event bus coordinates all modules — microphone, Telegram, web UI, LLM, motor executor — without any of them directly calling each other. The state machine transitions between seven states (IDLE, LISTENING, THINKING, ACTING, SEARCHING, SPEAKING, REPORTING) in response to events, not function calls. Nothing blocks. It's a meaningfully different program even though it does the same things.

---

## The Finding That Surprised Me Most

Here's the number I didn't expect: **42%**.

During evaluation, I ran the full system without the safety validator — just raw LLM outputs going directly to the motor driver. Of those outputs, 42% were structurally invalid. Not necessarily dangerous instructions, but malformed ones: wrong key names, speeds outside the valid range, action values that didn't match any known command, extra fields the firmware didn't know what to do with, responses that were valid JSON but not valid plans.

After adding the validator: **0%** safety invariant violations. Every single command that reached the ESP32 was well-formed and in-range.

This was empirical confirmation of something I'd only suspected: you cannot trust LLM outputs to be structurally correct, even when your prompt is very specific. The model doesn't hallucinate maliciously — it hallucinates by format. The validator isn't a nice-to-have; it's the difference between a system that works and one that randomly breaks in ways you can't predict.

The ablation study filled in the rest of the picture. Remove the scene context from the LLM prompt — the description of what the camera sees — and 15% of plans become unsafe. Remove the episodic memory of recent actions, and search efficiency collapses: 60% more redundant steps before finding the target. Remove the ESP32 reflex layer entirely, and 30% of trials have near-miss obstacle events that only luck avoids.

Each layer is earning its place.

---

## What KANDA Can Do Today

The demo commands I run most often:

"Hey Kanda, go forward" — validates the intent, issues a motor command, confirms execution.

"Hey Kanda, find my water bottle" — launches a frontier-based visual search. The robot moves to unexplored areas of the room, captures frames with the camera, sends them to NVIDIA NIM for description, checks if the target appears, and keeps a spatial grid of where it's already been. Success rate across ten trials: 8 out of 10.

"Hey Kanda, what do you see?" — captures a frame and returns a natural language description of the scene. This is NVIDIA NIM's Llama 3.2 11B Vision describing whatever is in front of the camera. Useful for remote access.

"Hey Kanda, move forward and turn left" — compound commands. The planner breaks this into a sequence and executes it step by step.

Telegram works the same way. Send a voice note, send a photo and ask a question about it, send a text command — the bot handles all three input types and routes them through the same pipeline.

Intent classification across 60 test utterances came out at an F1 score of 0.94. Wake word false positives run below 2%. Fault injection — eight different injected failure modes — all terminated safely. The firmware reflex latency is 47 milliseconds, plus or minus 21.

---

## What's Next

I'm currently writing up the system as a research paper for submission to Springer's Discover Robotics journal, with parallel conference submissions. The paper formalizes what the blog describes informally: the three-tier architecture, the safety validator design, the ablation results, the evaluation methodology.

On the hardware side, the most limiting factor is navigation. The current spatial memory is a grid — it knows roughly where it's been but has no map of walls or persistent landmarks. Adding proper SLAM would be the most significant capability jump. It's also the most expensive one to implement correctly on a Pi.

The v2 rewrite also opens a web UI at port 8080 on the Pi — a D-pad for manual control, a text box for commands, a live sensor readout, a state indicator. That interface made the system much easier to debug during development, and it's a natural interface for anyone who wants to control the robot without speaking to it.

---

## What I Took Away From This

Building KANDA taught me that the interesting problems in embodied AI aren't the AI problems. The AI is the easy part — you call an API and get a response. The hard part is everything around it: the latency management, the fault handling, the hardware that doesn't always behave the way the datasheet says, the firmware bugs that only appear at 3 AM when the robot is navigating a dark room, the 42% of LLM responses that need to be caught and sanitized before they reach the motors.

I also learned that budget constraints are actually clarifying. When you can't afford a GPU, you think more carefully about what needs to run locally versus remotely, and you design a system where those boundaries are explicit and clean. When you have ₹6,500 total, you don't waste components.

KANDA isn't a product. It's a working prototype with real numbers behind it, built by one person at a college in Bengaluru with an internet connection, a soldering iron, and too much free time. If you're a student thinking about building something like this: the barrier is lower than you think, and the learning curve is worth it.

The water bottle was already there. The robot just had to learn to find it.

---

*The full source code, firmware, and research paper draft are part of an ongoing project. If you're building something similar or want to discuss the architecture, I'm on [GitHub](https://github.com/sudarshan-rvce) and reachable via the contact in my profile.*
