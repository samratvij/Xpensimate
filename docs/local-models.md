# Running a local model instead of Claude

Xpensimate uses an AI model for sorting a merchant it has never seen (after your rules and history), reading
receipt photos and bank app screenshots, and the Ask chat. By default that's Claude. You can run open models on
your own server instead, so nothing leaves it.

Anything that speaks the OpenAI chat completions API works: [Ollama](https://ollama.com),
[llama.cpp](https://github.com/ggml-org/llama.cpp)'s `llama-server`, [vLLM](https://docs.vllm.ai),
[LM Studio](https://lmstudio.ai), or a hosted service with that API.

## The quick way: Ollama next to Xpensimate

```bash
cd Xpensimate/backend
docker compose -f docker-compose.yml -f compose.ollama.yml up -d --build
```

The overlay starts Ollama, downloads the models once (about 6 GB for receipts and categorizing, 5 GB more for
the chat), and points Xpensimate at it. On a VPS add
`-f compose.vps.yml` too. Check More → Health in the app: "AI model" should name your model server.

## Picking a model

| | Default | Notes |
|---|---|---|
| Categorizing (`CATEGORIZER_MODEL`) | `qwen2.5vl:7b` | A short classification: any decent 7B+ instruct model does it. |
| Receipts (`RECEIPT_MODEL`) | `qwen2.5vl:7b` | Must understand images (a "vision" or "VL" model). |
| Bank screenshots (`SCREENSHOT_MODEL`) | `qwen2.5vl:7b` | Also needs images. |
| Ask chat (`CHAT_MODEL`) | `qwen3:8b` | Must support tool calling; larger models plan better. |

One vision model for both keeps memory use down, since the server only holds one. To use others, set them
in `.env` (the overlay pulls whatever you name) and restart:

```bash
CATEGORIZER_MODEL=qwen3:8b
RECEIPT_MODEL=qwen2.5vl:7b
```

**Hardware.** A 7B model needs about 8 GB of free RAM. On a CPU, a new merchant takes several seconds and a
receipt a minute or two. A GPU or Apple Silicon is much faster. Bigger models read long or crumpled receipts
better; if receipts come back marked "needs review" often, try a larger vision model.

For an NVIDIA GPU, install the NVIDIA Container Toolkit and uncomment the `deploy:` block in
`compose.ollama.yml`.

## Using a model server you already run

Skip the overlay and set these in `backend/.env`:

```bash
LLM_PROVIDER=openai
LLM_BASE_URL=http://192.168.1.20:11434/v1    # Ollama elsewhere on your network
# LLM_BASE_URL=http://host.docker.internal:11434/v1   # Ollama on the same machine, outside Docker (see below)
# LLM_API_KEY=...                            # only if your server wants one
CATEGORIZER_MODEL=qwen2.5vl:7b
RECEIPT_MODEL=qwen2.5vl:7b
```

Then `docker compose up -d`. For `host.docker.internal` on Linux, add this under `api:` (and `gmail-worker:`)
in `docker-compose.yml`, and make Ollama listen beyond localhost (`OLLAMA_HOST=0.0.0.0`):

```yaml
    extra_hosts: ["host.docker.internal:host-gateway"]
```

`LLM_TIMEOUT` (seconds, default 180) is how long Xpensimate waits for one answer.

**The Ask chat** sends its tool definitions and your categories with every question, which is more than
Ollama's default context window holds. The overlay sets `OLLAMA_CONTEXT_LENGTH=16384`; on your own Ollama, set
the same environment variable. Local models answer without web search, and a question that needs several
lookups can take a minute on a CPU.

## How it works

Xpensimate sends the same prompt it sends Claude, plus a JSON schema whose category and "who" fields list
only your current choices. Servers that support structured output (all of the above) can only answer with
one of them. If the server is down or the answer doesn't fit, the charge stays Uncategorized (or the receipt
upload fails with a message) and nothing else is affected.
