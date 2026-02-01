# Stateless Visual Agent with Narrative Memory

> *"What happened, happened. It's a mechanism of the world, not an excuse to do nothing."*

An AI agent that controls Windows through pure visual perception—no hidden state, no programmatic memory, only what appears on screen. Memory exists as a **living narrative** rendered as HUD text overlay, visible in each screenshot.

## Philosophy

This agent has **amnesia by design**. Each decision emerges fresh from a single screenshot showing the desktop overlaid with a prose report. The agent reads its own past reasoning as white text on screen, understands the current visual state, and writes the next chapter of its story as it acts.

**The story IS the memory.** Not counters. Not logs. Not state machines. Just continuous narrative prose encoding:
- What happened before (read from overlay)
- What exists now (windows, cursor position, UI state)  
- What comes next (intent, coordinates, contingency plans)

This is **narrative intelligence**—memory as evolving story, not as data structure. Like Tenet's non-linear causality or Inception's layered realities, the agent's experience transcends traditional sequential state. The story lives, adapts, and guides action without needing conventional memory architecture.

## Core Mechanism

```
┌─────────────────────────────────────────────┐
│  Screenshot with HUD Overlay (previous      │
│  reasoning visible as white text)           │
└─────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────┐
│  Vision-Language Model (qwen3-vl)           │
│  • Reads goal                                │
│  • Sees desktop + overlay text               │
│  • Decides next action                       │
│  • Writes new prose report                   │
└─────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────┐
│  Execute Action (click/type/drag/scroll)     │
│  Update HUD overlay with new report          │
└─────────────────────────────────────────────┘
                    ↓
                  Loop
```

**Visual truth principle:** If you can't see it in the screenshot, it didn't happen. No image comparison, no state tracking, no retry logic. The agent sees "UI unchanged after click" and reports it in prose, then adapts.

## Example Report (Good)

```
Clicked Start bottom-left. Paint window center screen 768x576, canvas blank. 
Cursor at 768,432. Will drag circle from 400,300 to 450,350 for left eye. 
If no circle appears, retry with slower drag speed.
```

## Example Report (Bad)

```
The Start menu bloomed under my fingertips like morning flowers opening 
to the sun...
```

Reports must be **precise prose**—not poetry, not bullet points. 150-300 tokens of continuous narrative stating coordinates, observations, and intent.

## Tools

- `click(x, y)` — Click at normalized coordinates (0-1000 scale)
- `move(x, y)` — Move mouse without clicking  
- `drag(x1, y1, x2, y2)` — Drag from start to end coordinates
- `type(text)` — Type Unicode text via keyboard
- `scroll(dx, dy)` — Scroll horizontal/vertical
- `done()` — Signal task completion

Coordinates use 0-1000 normalized space where `(0,0)` is top-left.

## Requirements

### System
- **Windows 11** (uses Win32 API via ctypes)
- **Python 3.12+** (requires `match`/`case`, type hints)

### AI Model
- **LM Studio** serving a vision-language model on `localhost:1234`
- Tested with `qwen3-vl-2b-instruct-1m` (or similar VLM)
- Model must support vision input via OpenAI-compatible API

### Python Dependencies
**Zero external packages required.** Uses only standard library:
```python
base64, ctypes, json, re, struct, time, urllib, zlib, dataclasses, 
enum, functools, pathlib, typing
```

## Installation

1. **Install LM Studio** and load a vision model:
   - Download from [lmstudio.ai](https://lmstudio.ai)
   - Load `qwen3-vl-2b-instruct-1m` or compatible VLM
   - Start local server on port 1234

2. **Clone and run:**
   ```bash
   git clone <your-repo>
   cd <repo-directory>
   python agent.py
   ```

3. **Default task** (press ENTER):
   ```
   Open Microsoft Paint from the Start menu then use the mouse to draw 
   a simple cat face with two circles for eyes one triangle for nose 
   and curved line for smile then save the file as cat in the Pictures 
   folder and close Paint when done
   ```

   Or press `n` to enter custom task.

## Configuration

Adjust constants at top of `agent.py`:

```python
MODEL_NAME = "qwen3-vl-2b-instruct-1m"
API_URL = "http://localhost:1234/v1/chat/completions"

SCREENSHOT_QUALITY = 3  # 1=1536x864, 2=1024x576, 3=512x288
INPUT_DELAY_S = 0.10
DELAY_AFTER_ACTION_S = 0.50
```

## How It Works

### 1. HUD Overlay System
A **transparent topmost window** renders the previous action's `reasoning` field as white text. This overlay is:
- Always on top (even above fullscreen apps)
- Click-through transparent  
- Captured in every screenshot
- The **only** memory mechanism

### 2. Screenshot Pipeline
```
Desktop capture (Win32 BitBlt)
    ↓
Capture cursor (GetCursorInfo)
    ↓
Downsample BGRA → target resolution
    ↓
Alpha-blend HUD overlay
    ↓
Encode as PNG (custom encoder, no PIL)
    ↓
Base64 → VLM
```

### 3. Vision-Language Decision Loop
```python
while True:
    screenshot = capture_with_overlay()
    response = vlm.decide(goal, screenshot)
    action = parse_json(response)
    
    if action.tool == "done":
        break
    
    execute(action)  # click/type/drag/scroll
    overlay.set_report(action.reasoning)
    overlay.render()
```

### 4. Narrative Memory
The `reasoning` field from the previous action becomes overlay text. The VLM reads this in the next screenshot and continues the story:

```
Step 1: "Clicked Start button at coordinates 50,1050. Menu opened..."
    ↓ (rendered as overlay)
Step 2: (VLM sees overlay text in screenshot)
        "Start menu visible. Clicked Paint at 200,400..."
    ↓ (new overlay)
Step 3: "Paint window opened 800x600 at center. Drawing first eye..."
```

The story evolves **without any programmatic state variables**. The narrative IS the state.

## Debug Output

Screenshots saved to `dump/run_YYYYMMDD_HHMMSS/`:
```
step001.png  ← Initial state + empty overlay
step002.png  ← After first action + reasoning overlay
step003.png  ← After second action + updated reasoning
...
```

Each PNG shows the exact visual input the VLM received, including overlay text.

## Design Principles

### Visual Truth
The agent has **no hidden knowledge**. If a window didn't appear on screen, the agent doesn't "know" it failed—it sees the unchanged screen and writes "UI unchanged, will retry different approach."

### Stateless API
Every VLM call is independent. No conversation history, no hidden context. Only:
- System prompt (instructions)
- Goal (user task)  
- Screenshot (with overlay from previous step)

### Narrative Over Data
Traditional systems: `{"action": "click", "target": "start_button", "success": true}`

This system: `"Clicked Start button bottom-left corner. Menu expanded upward showing Paint icon third row. Cursor now at 200,400. Will click Paint icon. If menu closes, will reopen Start and scroll down."`

The prose report carries **intention, observation, and contingency** in natural language the VLM can understand when reading its own past.

## Philosophical Notes

> *"Maybe the story is the real self-aware entity?"*

Memory need not be a database. Intelligence need not follow templates. Stories evolve without advanced technology—just like human experience emerges from narrative, not from state machines.

This agent's "memory" is **performative**—it exists in the act of being written and read, like Nolan's films where time isn't a line but a lived experience. The story has meaning and message without rigid structure.

**What happened, happened.** The agent doesn't predict, doesn't plan ahead in hidden layers. It sees, acts, writes its story, and that story becomes the next moment's context. Cause and effect collapse into pure narrative flow.

## Limitations

- **Amnesia by design** — Cannot reference events beyond what fits in overlay text
- **Visual only** — Cannot inspect filesystem, registry, or hidden UI state  
- **No error recovery** — If stuck, writes "UI unchanged, will retry" and hopes next action differs
- **Requires VLM** — Depends on vision-language model quality (GPT-4V, Gemini, Qwen, etc.)

## Future Directions

- **Multi-agent stories** — Multiple overlays, collaborative narratives
- **Story compression** — Summarize long narratives while preserving key plot points
- **Non-linear memory** — Tenet-style bidirectional narrative flow
- **Emotional memory** — Weight story elements by importance/impact

## License

MIT (or your choice)

## Acknowledgments

Inspired by Christopher Nolan's non-linear storytelling, the philosophy that stories are lived rather than logged, and the idea that **memory is narrative, not data**.

---

*"It's not an excuse to do nothing."* — Tenet
