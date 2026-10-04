> Superseded behavior: see OPEN_ENDED_SMS_START_HERE.md for the current conversational controller and urgency classifier. Setup information below remains useful, but earlier restrictions on generated wording are historical.

# Local conversation model — start here

The original app is a small intent classifier with a scripted menu. Persisting acknowledgments did not turn it into a conversational language model. This build adds an optional Ollama connection with recent conversation history.

## Mac setup

1. Install and open Ollama: https://ollama.com/download/mac. Its current Mac app requires macOS 14 or newer; Apple M chips support GPU inference, Intel Macs use CPU inference. Python 3.11+ is also required for Compass.
2. In Terminal, download a local model once:

   ```bash
   ollama pull gemma3:4b
   ```

   The listed model download is approximately 3.3 GB; this is not total runtime RAM. Actual speed and memory need testing on your machine. Gemma 3 is not MedGemma.
3. Extract this complete build into a fresh folder. Open Terminal in its `compass_agent_build` folder and run:

   ```bash
   bash run_local_model_mac.command
   ```

   This verifies Ollama/model availability, installs Python dependencies, and runs a real inference smoke test before starting Compass. The first setup needs internet; subsequent inference goes only to localhost. No API key or cloud model is used.
4. Open http://127.0.0.1:8765/agent-demo. Enroll a routine fictional patient. Send an unfamiliar phrasing such as `The last bottle is empty and I cannot get another until next week`. Check the phone's Conversation engine indicator.

`Local model: gemma3:4b · responding` means the most recent local call returned valid structured output. It does not establish correct interpretation. `Rules fallback` or `unavailable_or_invalid` means the language model is not providing the interpretation; errors are not disguised as model answers.

To try a different downloaded local model:

```bash
export COMPASS_LOCAL_MODEL='your-local-model-name'
bash run_local_model_mac.command
```

To extend a slow first-load timeout:

```bash
export COMPASS_LOCAL_TIMEOUT=120
bash run_local_model_mac.command
```

## Behavior

The model receives eight recent messages and the current conversation state. It proposes an intent and short response. The controller owns database changes and escalation. Model-inferred adherence asks for confirmation rather than silently updating medication records. Symptoms, side effects, medication access, and visit requests get contextual fixed replies and human review. Flexible generated wording is restricted to nonclinical greetings and clarification of unknown messages; it is not unrestricted medical chat.

Known danger phrases bypass the model. A model-proposed urgent flag may also escalate. A negative flag never means a message is safe. Approved routines and menus continue to work if the model times out or returns invalid JSON.

Forgotten medication now gets an acknowledgment explicitly about forgetting and reminders. Access replies ask whether the obstacle is refill, cost, or transport. Side-effect replies ask about symptoms and timing. Visit requests are acknowledged without claiming a booking. `Ran out of food` is not automatically routed as a medication-refill problem.

## Verification limits

The connection code, payload/schema handling, invalid-output fallback, and controller integration were tested using mocked model responses. No Ollama binary or model weights were available in the execution environment; real inference was not run here. The included `check_local_model.py` makes this outstanding check explicit on your Mac. Generated reply quality, hallucination rate, medical safety, multilingual conversation performance, and model throughput remain unvalidated. This remains a synthetic local demo with simulated SMS and is not proof of 150,000 active conversations.

Technical references:
- https://docs.ollama.com/macos
- https://docs.ollama.com/quickstart
- https://github.com/ollama/ollama/blob/main/docs/capabilities/structured-outputs.mdx
- https://ollama.com/library/gemma3:4b
