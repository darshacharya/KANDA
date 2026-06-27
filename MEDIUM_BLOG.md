# I Built a $77 Robot That Listens, Looks Around, and Finds Your Stuff

*A college student's journey from blinking motors to a robot that actually works.*

---

I was sitting on my hostel room floor. I looked at the little two-wheeled robot in front of me and said, "Hey Kanda, find my water bottle."

It turned. It started moving. It scanned the room slowly.

Two minutes later, it stopped right in front of the bottle and said, "Found it."

That moment felt bigger than it should have. But I'd spent months building up to it. A lot had to go right — at the same time — for it to work. And all of it ran on hardware that cost less than a meal out.

This is how I built KANDA.

![KANDA in the lab — camera on top, three distance sensors at the front, gold speaker on the side](https://raw.githubusercontent.com/darshacharya/KANDA/main/website/imgs/WhatsApp%20Image%202026-05-31%20at%2012.09.44%20PM.jpeg)

---

## What Is KANDA?

KANDA is a small wheeled robot. You talk to it. It listens, thinks, looks around, and responds.

It can move around a room on its own. It can find objects you describe. It can tell you what it sees through its camera. You can even send it commands over Telegram from across the house.

The total cost to build it: **$77.**

That includes everything — the body, the wheels, the camera, the sensors, the screen, the speaker, the microphone, the computer inside it. All of it.

---

## What's Inside

The robot has two main "brains."

The first is a small computer called a Raspberry Pi. This is where KANDA thinks. It listens for your voice, figures out what you want, and decides what to do next. It connects to AI services online to understand language and interpret camera images.

The second is a tiny chip called an ESP32. This controls the wheels and reads three distance sensors — one in front, one on the left, one on the right. It works on its own. Even if everything else fails, it will still stop the robot before it hits a wall.

There's also a small screen on the front that shows a pair of eyes. When KANDA wakes up, the eyes open. It's a small thing. It made the robot feel alive from day one.

![Top-down view showing the Raspberry Pi, OLED screen, and wiring](https://raw.githubusercontent.com/darshacharya/KANDA/main/website/imgs/WhatsApp%20Image%202026-05-31%20at%2012.09.44%20PM%20(1).jpeg)

---

## The Safety Problem Nobody Talks About

Here's the finding that surprised me most.

I tested what would happen if KANDA acted directly on whatever the AI said — no filter, no check, just raw AI output straight to the wheels.

**42% of the time, the AI's response was broken.**

Not dangerous in a dramatic way. Just wrong. Wrong word for the direction. Speed set to a number that doesn't exist. Instructions in a format the robot didn't understand. Garbled instructions that would have caused the robot to freeze or act randomly.

After adding a simple checking step — one that validates every instruction before it reaches the wheels — that number dropped to **zero.**

Not 5%. Not 1%. Zero.

This taught me something important: **AI systems that control physical things need a filter between the brain and the body.** The AI doesn't lie on purpose. It just gets the format wrong. A lot.

---

## Three Layers of Safety

KANDA uses three separate layers to stay safe. Each one works at a different speed.

**Layer 1 — The AI Brain (online, slow):** When you give a command, KANDA sends it to an AI service online. The AI reads the command along with KANDA's current situation: what the sensors see, what the camera sees, what it just did. It responds with a plan. This takes about 2 to 4 seconds.

**Layer 2 — The On-board Checker (instant):** Before any plan reaches the wheels, the Raspberry Pi checks it. Is the direction valid? Is the speed within range? If anything looks wrong, the instruction is replaced with one thing: **stop.** This check happens in less than 1 millisecond.

**Layer 3 — The Wheel Chip (hardware, 47 milliseconds):** The ESP32 reads the distance sensors 10 times per second. If anything comes within 15 centimeters of the front sensor, the wheels cut off. Immediately. It doesn't ask the AI. It doesn't wait for the Pi. It just stops. Even if the USB cable is unplugged, this layer keeps working.

These three layers don't slow each other down. The AI thinks slowly. The checker is instant. The hardware reflex is almost instant. They each handle what they're best at.

![The three-layer architecture — Cloud thinks, Pi checks, ESP32 reacts](https://raw.githubusercontent.com/darshacharya/KANDA/main/overleaf/images/kanda_architecture.png)

---

## The Journey

I didn't build all of this in one go.

**Step 1** was just getting the wheels to turn and the sensors to read. Boring, but necessary. I spent a lot of time staring at blinking lights.

**Step 2** was connecting the small wheel chip to the Raspberry Pi. They're built differently. They speak different languages. Getting them to talk to each other without freezing or crashing took weeks of patience.

**Step 3** was adding the AI. KANDA could now hear a command, think about it, and act. It worked — but barely. While the AI was thinking, the entire robot froze. No sensor updates. No new commands. Nothing. If a message came in during that 5-second window, it would just wait.

**Step 4** was a full rebuild. I rewrote the software so KANDA could do many things at once. Listen. Think. Move. Update the screen. Accept messages. All at the same time. Nothing blocks anything else. That's the version that found the water bottle.

![KANDA's seven internal states — from idle to listening to acting and back](https://raw.githubusercontent.com/darshacharya/KANDA/main/overleaf/paper/statemachine.png)

---

## What KANDA Can Do Right Now

**"Hey Kanda, go forward"** — it moves forward and confirms.

**"Hey Kanda, find my water bottle"** — it starts searching. It moves to parts of the room it hasn't checked yet. It looks through its camera. It checks if the target is there. It remembers where it's already been. 8 out of 10 times, it finds the object.

**"Hey Kanda, what do you see?"** — it describes whatever is in front of the camera. Good for checking on a room remotely.

**"Hey Kanda, move forward and turn left"** — it handles multi-step instructions, one action at a time.

**Telegram:** Send a voice note, a photo, or a text. KANDA handles all three the same way.

The numbers from real testing:
- Understands commands correctly: **94 out of 100**
- Finds objects in a room: **8 out of 10 tries**
- False wake-ups (activating without being called): **less than 2%**
- All 8 deliberate failure tests: **all ended safely**
- Emergency stop time: **47 milliseconds**

---

## What's Next

I'm writing a research paper about KANDA. It's going to a robotics journal published by Springer. The paper puts formal numbers behind everything described here.

On the hardware side, the biggest gap is mapping. KANDA knows roughly where it's been but doesn't build a real map of the room. Adding that would be the single biggest upgrade in what it can do.

There's also a web interface now — a control panel you can open on your phone. It has a direction pad, a text box for commands, and a live readout of what the sensors see. It made testing much easier, and it's a natural way to control the robot without using your voice.

---

## What I Actually Learned

The hardest part of building KANDA wasn't the AI.

Connecting to an AI service online is one function call. The hard part was everything around it. Getting the hardware to behave. Catching broken AI responses before they reached the wheels. Debugging problems that only appeared at midnight when the robot was navigating a dark room.

I also learned that a tight budget forces good decisions. When you can't afford expensive hardware, you figure out exactly what needs to happen where. You stop over-engineering. You build only what's necessary.

KANDA isn't a product. It's a working prototype with real test results, built by one person at a college in Bengaluru. If you're a student thinking about building something like this — the bar is lower than it looks.

The water bottle was already there. The robot just had to learn to find it.

---

*Source code, firmware, and the research paper draft are available on [GitHub](https://github.com/darshacharya/KANDA). Happy to talk if you're building something similar.*
