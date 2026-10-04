# Real SMS demo with Twilio

This optional bridge takes a text from your own consenting test phone, feeds it into the existing local patient conversation, and submits the generated reply to Twilio. It is for fictional scenarios only. It does not establish clinical reliability. No real SMS was sent during development; automated tests mock the carrier API.

## Account prerequisites

Create a Twilio account at https://www.twilio.com/ and obtain an SMS-capable number that supports inbound and outbound messaging in your country. Complete any registration/verification Twilio requires. Confirm that the account can send custom message bodies before the demo.

As checked October 4, 2026, Twilio's current trial restricts outgoing bodies to predefined templates; this conversational bridge requires custom-body capability, generally an upgraded account. Older accounts may have different restrictions. Number verification/registration can take time; upgrading alone does not guarantee immediate delivery. Check Console before relying on this for a presentation.

Official references:
- https://www.twilio.com/docs/usage/trials/try-out-sms
- https://www.twilio.com/docs/messaging/tutorials/how-to-receive-and-reply/python
- https://www.twilio.com/docs/usage/security
- https://ngrok.com/docs/start

## Install into your existing project

Either use this complete build, or copy these files into your working project: agent.py, core.py, server.py, sms_bridge.py, launch_sms_bridge.py, requirements-sms.txt. Keep your own private_data folder and .venv. Stop the app before replacing files. Never copy private_data from another person's machine.

From the project folder:

```bash
.venv/bin/python -m pip install -r requirements-sms.txt
```

## Run in three Terminal tabs

**Tab 1 — main app** (retain your existing COMPASS_LOCAL_MODEL setting if using Ollama):

```bash
.venv/bin/python launch.py
```

Dashboard: http://127.0.0.1:8765/
Phone simulator / live conversation viewer: http://127.0.0.1:8765/agent-demo
Keep Ollama running if configured. This integration does not make inference faster.

**Tab 2 — public tunnel:** Install ngrok and connect your account using its official setup instructions. Then:

```bash
ngrok http 8766
```

Copy the HTTPS forwarding address and append `/sms`, for example `https://your-assigned-host.ngrok-free.app/sms`. Tunnel **8766 only**. The unauthenticated dashboard on 8765 must remain local.

**Tab 3 — SMS bridge:**

```bash
.venv/bin/python launch_sms_bridge.py
```

The launcher lists available fictional patients. Choose a routine English-speaking patient for a simple pain-history demo. Enter the Account SID, hidden Auth Token, Twilio number, exact public `/sms` URL, your consenting test phone, and fictional patient ID. Use international phone format starting with +country code. Type `SEND` to enable actual outgoing texts; otherwise the bridge only stores drafts. Enter a separate storage passphrase of at least 12 characters and reuse it on restarts. Credentials are entered locally, not saved in the project. Never paste your Auth Token into chat.

In Twilio Console, open the SMS number's messaging settings and set **A message comes in → Webhook → HTTP POST** to that same HTTPS `/sms` URL. Save. If using a Messaging Service, ensure its inbound routing uses this webhook. If the tunnel URL changes, update both Twilio and the bridge.

## Demonstrate the complete flow

1. In the web phone viewer select the same fictional patient. Enroll in the agent if you want its normal follow-up flow; the initial enrollment reminder is still simulated.
2. From your configured real phone, text the Twilio number: `My knee hurts`.
3. The bridge acknowledges Twilio immediately, then processes the message in a background queue. The phone receives the generated reply prefixed `Compass DEMO:` when provider delivery succeeds.
4. Reply naturally on the phone. Watch the conversation, pain history, urgency classification, and review tasks in the local dashboard. Refresh the main dashboard as needed.
5. Check Twilio Messaging Logs for the real message's delivery status. The dashboard's existing simulated-delivery labels are not carrier delivery receipts.

Use fictional symptoms only. This demo is not monitored emergency care.

## What is implemented

- Signed webhook verification against the exact configured public URL, account and destination checks, allowlisted test sender.
- Separate encrypted durable inbound queue; no public patient APIs.
- Message SID deduplication, including transactional receipts in the main app so a network retry does not repeat clinical workflow changes.
- Single sequential worker; slow local inference does not block webhook acknowledgment.
- Up to three local-processing attempts with the same message ID. Provider submission is attempted once. An ambiguous send is marked `send_unknown` and never automatically resent.
- STOP and related opt-out keywords suppress queued replies. START/UNSTOP clears the bridge block; carrier opt-out rules still apply. An already submitted message cannot be recalled.
- Text-only incoming messages up to 500 characters. Blank, overlong, and media messages are retained as unsupported without entering the clinical flow or sending a reply. Console/bridge monitoring is needed; this is not a production inbox.

## Scope and troubleshooting

Real transport covers inbound texts and their direct replies only. Scheduled reminders, enrollment messages, CHW notifications, and existing simulated outbox entries are not sent to phones. Provider accepted does not mean delivered; delivery callbacks are not implemented. Drafts are not automatically sent if you restart in SEND mode.

The laptop must stay awake with the app, bridge, internet connection, and tunnel running. The phone needs SMS coverage. Model inference remains local, but SMS content travels through Twilio, the carrier, and the tunnel service. Local encryption does not make that path offline or end-to-end encrypted.

- 403 webhook: check the exact URL, Auth Token, Account SID, To number, and allowed From number.
- Inbound arrives but no reply: confirm SEND mode, main app availability, model latency, no prior STOP, and Twilio's account/number restrictions. Look at the bridge terminal and Twilio Messaging Logs.
- Local processing failure: queue retries three times, then requires investigation; restarting does not automatically retry failed jobs.
- Uncertain send: check Twilio Messaging Logs before deciding whether any manual resend is appropriate.
- Restart with the same storage passphrase. Run only ONE bridge process and ONE main app process against their respective encrypted files. Do not open the same store in another process while running.
- After stopping, pending inbound messages resume on restart; in-flight provider sends become send_unknown. Stop the bridge when the demo is over and remove the webhook/tunnel.

## Verification

Install requirements-dev.txt and requirements-sms.txt, then run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q tests
```

Carrier delivery, your Twilio account configuration, and your Mac's model latency still require a real-phone acceptance test.
