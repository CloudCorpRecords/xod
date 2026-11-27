# ⚡ Xod Infra

**Open-source Apple Silicon local LLM speed & efficiency engine** — built as a standalone companion project that plugs directly into **[Xod](https://github.com/CloudCorpRecords/xod)** (or any OpenAI-compatible client like Cursor, Continue, or Open WebUI).

## Why Xod Infra?

Apple Silicon Macs are memory-bandwidth bound, and default local setups routinely leave **30–70% of real-world token speed** on the table. Xod Infra fixes that in one clean package:

- **Apple Silicon Hardware Doctor (`xod-infra doctor`)** — detects your exact M1/M2/M3/M4 chip, unified RAM, memory bandwidth, Metal wired limit, and running backends (MLX, Ollama, LM Studio, llama.cpp).
- **1-Click Mac Tuning (`xod-infra tune --apply`)** — enables `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0` (cuts KV-cache RAM in half), `OLLAMA_KEEP_ALIVE=30m` (zero cold reloads), and prints the exact **MLX Speculative Decoding** (`--model` + `--draft-model`) command sized for your RAM tier (16 GB / 32 GB / 64 GB+).
- **OpenAI-Compatible Gateway + Live Dashboard (`xod-infra serve`)** — runs at `http://127.0.0.1:8790/v1`, auto-routes to your fastest running backend (`MLX → LM Studio → Ollama`), tracks live **TTFT (ms)**, **decode tok/s**, and **prefix-cache hit %**.
- **Live Tok/s Benchmarker (`xod-infra bench`)** — measures real TTFT and generation speed on your Mac.

## Quick Start

```bash
cd xod-infra
python3 -m xod_infra.cli doctor
python3 -m xod_infra.cli tune
python3 -m xod_infra.cli serve --port 8790
```

Then open **http://127.0.0.1:8790** for the live dashboard, or point **Xod Desktop** (Settings → AI Brain → Local OpenAI-compatible) to `http://127.0.0.1:8790/v1`.

## License

MIT
