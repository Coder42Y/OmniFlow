# OmniFlow 𝓥

> **One Prompt, Omni Deliver.** An open-source, multi-agent AI media workspace that transforms a single product concept into commercial copy, 8K studio photography posters, and cinematic 9:16 motion videos in seconds.

[![License: MIT](https://img.shields.io/badge/License-MIT-amber.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)
[![Architecture](https://img.shields.io/badge/Multi--Agent-Gemini%203.8%20%2B%20Agnes-emerald.svg)](#architecture)
[![UI Style](https://img.shields.io/badge/Design-Google%20M3%20%2B%20Codex-rose.svg)](#ui-and-ux)

---

## Why OmniFlow?

Traditional e-commerce content workflows are fragmented: copywriting, studio photoshoot, graphic layout, compliance auditing, and video editing require 4+ separate tools and hours of manual switching.

**OmniFlow eliminates the tool silos.** By combining a master reasoning agent (**Gemini 3.8 Flash**) with specialized domain engines (**Agnes Image & Agnes Video Engine**), creators can type one sentence to instantly receive a complete campaign package:
- **Hook & FABE Sales Script** audited against advertising compliance laws.
- **Commercial Studio Poster** rendered across customizable 5-layer photography setups.
- **9:16 Smooth Motion Video** rendered locally in sub-seconds using cinematic dolly-in cameras.

Everything runs on a single lightweight Python service with a zero-build, dependency-free Vue 3 web interface.

---

## Architecture

```
                       ┌─────────────────────────────────┐
                       │   Omni Hub (Master Interface)   │
                       │   "Powered by Multi-Agent"     │
                       └────────────────┬────────────────┘
                                        │
                         [Gemini 3.8 Flash Master Agent]
                     Intent Routing & Campaign Orchestrator
                                        │
         ┌──────────────────────────────┼──────────────────────────────┐
         ▼                              ▼                              ▼
 ┌───────────────┐              ┌───────────────┐              ┌───────────────┐
 │ Visual Poster │              │ Motion Video  │              │ Script & Copy │
 │   Workspace   │              │   Workspace   │              │   Workspace   │
 ├───────────────┤              ├───────────────┤              ├───────────────┤
 │ Agnes Image   │              │ Agnes Video   │              │ FABE Model    │
 │ 2.5 Flash     │              │ Engine        │              │ + Ad Law Risk │
 │ 5-Layer Cam   │              │ 9:16 H.264    │              │ 30+ Banned    │
 │ 6 Presets     │              │ Dolly-in FX   │              │ Words Filter  │
 └───────────────┘              └───────────────┘              └───────────────┘
```

---

## Features

- **⚡ Omni Hub Multi-Agent Orchestration**: Gemini 3.8 Flash acts as the master brain, intelligently dispatching copywriting, graphic rendering, and video synthesis tasks.
- **📜 Session History & New Chat**: Left-side collapsible session drawer, timeline grouping (Today, Last 7 Days, Older), instant `New Chat` creation, and persistent `localStorage` storage.
- **🎨 5-Layer Commercial Photography Assembly**: 6 industry presets (Hydro Luxe, Dark Obsidian, Geek Geometry, Starlight, Morning Dew, Warm Sun) with customizable podium, studio lighting, camera angle, and aspect ratios (1:1, 3:4, 9:16, 16:9).
- **🎬 Sub-Second Local Motion Video Engine**: Generates 9:16 (720x1280) H.264 MP4 videos with smooth camera push (Dolly-in) and automated animated kinetic typography overlay.
- **📝 FABE Copy & Compliance Radar**: Automated breakdown of Feature, Advantage, Benefit, and Evidence with real-time advertising law extreme word interception.
- **🔒 Enterprise-Grade Security (Zero Frontend Keys)**: All API credentials are securely managed server-side via environment variables; zero plaintext keys exposed to the browser.
- **🪟 Codex-Style Sleek Scrollbars**: Ultra-thin (5px) translucent scrollbars with smooth dampening, adapted for both light and dark surfaces.

---

## Quickstart

### 1. Clone & Setup

```bash
git clone https://github.com/your-username/OmniFlow.git
cd OmniFlow
```

### 2. Configure Environment

Copy the example environment file and provide your API credentials:

```bash
cp .env.example .env
```

Edit `.env`:

```ini
AGNES_API_KEY=your_agnes_api_key_here
AGNES_BASE_URL=https://api.agnes-ai.cn/v1
PORT=8080
```

> **Note**: `FFmpeg` and `FFprobe` are required for local video synthesis. Ensure they are installed and available in your system `PATH`:
> ```bash
> # Ubuntu / Debian
> sudo apt update && sudo apt install -y ffmpeg
> 
> # macOS
> brew install ffmpeg
> ```

### 3. Launch Server

Run with Python 3.8+ (standard library only, no bulky framework dependencies):

```bash
python3 server.py --port 8080
```

Open your browser and navigate to:
```
http://localhost:8080
```

---

## Usage Guide

| Workspace | Purpose | Primary Capabilities |
| :--- | :--- | :--- |
| **💬 Omni Hub** | Multi-Agent Command & Full Campaign | Type a product idea to get script + poster + 9:16 video in a single turn. Manage conversation sessions with `New Chat` and timeline history. |
| **🎨 Visual Poster** | Commercial Studio Photography | Switch between 6 industry presets, adjust podiums, lighting, angles, and ratios, with real-time Compiled Prompt live preview. |
| **🎬 Motion Video** | Dynamic Camera Movement | Convert any static poster into a 9:16 short video in 0.8s with animated headline and subtitle overlays. |
| **📝 Script & Copy** | Conversion Copy & Legal Safety | Generate 3-second hooks and FABE scripts while auditing against 30+ banned extreme promotional terms. |

---

## Automated Verification & Testing

OmniFlow ships with an end-to-end automated testing suite verifying UI contracts, session persistence, security isolation, and physical media outputs:

```bash
# Verify OmniFlow brand, session history, and Codex scrollbars
python3 tests/test_omniflow_sessions_e2e.py

# Verify Multi-Agent Gemini orchestration and Agnes video rendering
python3 tests/test_multi_agent_e2e.py

# Verify frontend contract, flow transitions, and zero-key security
python3 tests/test_frontend_e2e.py

# Verify commercial photography presets and multi-ratio physical rendering
python3 tests/test_visual_presets_e2e.py

# Verify backend AI endpoints
python3 tests/test_backend_e2e.py
```

---

## Contributing

Contributions are warmly welcomed! Please feel free to submit issues or pull requests.
1. Fork the repository.
2. Create your feature branch (`git checkout -b feature/amazing-feature`).
3. Commit your changes (`git commit -m 'Add some amazing feature'`).
4. Push to the branch (`git push origin feature/amazing-feature`).
5. Open a Pull Request.

---

## License

Distributed under the [MIT License](LICENSE). See `LICENSE` for more information.
