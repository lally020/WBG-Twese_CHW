# CHW Compass — teammate demo

This is the complete application. No earlier patches are needed.
Use Python 3.11 or 3.12. Internet is needed to install dependencies.
Extract the ZIP before running it. Keep the extracted folder intact.

## Mac / Linux
Put the extracted CHW_Compass_Teammate_Demo folder in Downloads, open Terminal, and run:

```bash
cd ~/Downloads/CHW_Compass_Teammate_Demo
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-eval.txt
.venv/bin/python launch.py
```

If python3 is not Python 3.11 or 3.12, use python3.11 or python3.12 in the venv command.

## Windows PowerShell
Extract the folder into Downloads, open PowerShell, and run:

```powershell
cd "$HOME\Downloads\CHW_Compass_Teammate_Demo"
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-eval.txt
.\.venv\Scripts\python.exe launch.py
```

Use `py -3.11` instead if Python 3.11 is installed.

## Passphrase and opening the demo
Choose your own passphrase of at least 12 characters. Typing at the password prompt may show no characters; this is normal. Save it and reuse the SAME passphrase every time. This package has no existing patient database or account credentials.

Wait for the server to start, then open:

- Main dashboard: http://127.0.0.1:8765/
- Patient messaging: http://127.0.0.1:8765/agent-demo
- Evaluation lab: http://127.0.0.1:8765/eval-demo

Keep Terminal open. These addresses work on the computer running the app.
No Twilio account, SMS number, ngrok, or API key is required for this demo.
Messages are simulated in the browser; this setup does not send phone texts.

## Short presentation walkthrough
1. Start the app at least 10 minutes before presenting. Open Evaluation lab: the automatic cycle starts at startup. Allow roughly 4–6 minutes, potentially longer on another laptop. Watch Run progress; do not launch duplicate runs.
2. Open the main dashboard and show the fictional patients and CHW workflow.
3. Open Patient messaging. Select a routine patient, ideally Fatuma S. (#3), and click **Enable fictional consent + agent**. If needed click **Advance agent now**.
4. Send: `I keep forgetting my medicine`. Read the response.
5. Demonstrate escalation last: `I have chest pain and cannot breathe`. Return to the dashboard and show the resulting human-review action.
6. Open Evaluation lab. Show the actual completed run, confusion matrix, precision, recall, F1, missed urgent cases, false alarms, and latency. The recorded example is labeled separately; do not present it as the live run.
7. Show the automatic improvement cycle: development failures produce candidate urgency classifiers; a separate frozen test set and workflow checks determine whether a candidate is activated. A rejected candidate leaves the existing classifier active. Rollback restores the previous version and pauses automatic updates.

## Explain accurately
The default conversation engine uses a rules fallback; the urgency classifier is a trained scikit-learn model. Optional local conversational model setup is described in LOCAL_MODEL_START_HERE.md, but is not needed for this demo. Automatic activation is blocked if the optional conversational model is configured, because these release checks cover rules mode.

The automatic loop improves the SMS urgency classifier when its gates pass. It also checks dashboard policies and conversation workflows for regressions; it does not automatically retrain the entire app or rewrite clinical rules. Evals do not guarantee improvement every run. The datasets are synthetic and not clinically validated; this is a fictional demonstration, not a patient-care system.

The automatic cycle runs at startup and after relevant code/dataset changes while the app stays open. It does not continuously retrain from patient messages. The review form records feedback separately; reviewed labels must be deliberately added to development data.

## Restart later
Mac/Linux: enter the same folder and run `.venv/bin/python launch.py`.
Windows: enter the same folder and run `.\.venv\Scripts\python.exe launch.py`.
Enter the original passphrase. Stop with Ctrl+C.

If the browser cannot connect, check Terminal for errors and keep the server running.
If port 8765 is already in use, stop the earlier Compass server instead of starting another.
If you see “Cannot unlock data”, retry with your original passphrase; do not delete your database.

For additional evaluation details, read EVALUATION_START_HERE.md. Its patch-install section applies to older installations; this complete package already contains those files.
