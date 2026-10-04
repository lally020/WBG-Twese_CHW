# Build Instructions: TWESE CHW AI

Team-written product brief. PLAN.md is the technical blueprint; where the two differ, PLAN.md wins and its "Reconciled decisions" block says why.

Build a clean, functional, offline-first application for community health workers (CHWs) managing hypertension, diabetes and stroke prevention in Burundi.
Use the attached PLAN.md as the technical blueprint. Follow its existing architecture and core features rather than redesigning the project.
The goal: Help community health workers monitor more patients, identify concerning changes earlier, organize their time and provide continuous chronic disease care.

## 1. Language

- English must be the default language throughout the application.
- Add a language selector in the top-right corner.
- Include French, Swahili and Kirundi as translation options.
- All menus, buttons, instructions and patient-facing messages should support translation.
- Use reviewed translations where available. Clearly mark any language that does not yet have verified translations.
- Changing the language must not affect stored patient information.

## 2. Application Features

Build five main sections.

### A. Patient Dashboard

Display all registered patients, including:

- Patient ID and age
- Diagnosed conditions
- Most recent blood pressure and glucose measurements
- Blood pressure trends
- Upcoming and missed appointments
- Follow-up priority

Include search and filtering.

### B. AI-Assisted Patient Monitoring

Use the existing small-model architecture described in PLAN.md.
The application should:

- Import patient records.
- Process incoming SMS messages.
- Extract reported measurements, symptoms and medication information.
- Identify concerning changes using clinician-approved clinical guidelines.
- Present suggested next steps for CHW confirmation.

Always display the reason behind a clinical flag. Never allow AI to independently diagnose, prescribe medication or override clinical safety rules.

### C. SMS Communication

Create an SMS interface that allows patients and CHWs to communicate.
Include:

- Incoming messages
- AI-assisted message classification
- Automatic extraction of patient-reported measurements
- Appointment reminders
- Follow-up messages
- Patient education questions

CHWs must confirm extracted medical information before it updates patient records.
If a real SMS gateway is unavailable, create a fully functional simulated SMS interface.

### D. Weekly CHW Planner

Build a system that helps CHWs allocate their working hours.
It should:

- Identify patients requiring follow-up.
- Account for available working hours.
- Account for estimated travel and visit times.
- Group appointments geographically.
- Generate a suggested weekly schedule.
- Allow CHWs to modify and approve the schedule.

Patients requiring immediate clinical escalation must be handled separately, not placed in an ordinary weekly scheduling queue.

### E. Patient Education and Follow-Up

Use short, clinician-approved educational questions delivered through SMS.
Track:

- Patient responses
- Correct and incorrect answers
- Topics requiring additional education
- Follow-up completion

Provide simple visualizations that help CHWs understand which patients need additional support.

## 3. Offline Functionality

The core application must work without internet connectivity on an ordinary laptop with 8 GB of RAM or less.
Use the proposed architecture:

- Python
- Streamlit
- SQLite
- Julia-1 for structured decision support
- A lightweight local language model for information extraction

Preserve the fallbacks described in PLAN.md.
Allow patient records, measurements and CHW activities to be recorded offline. Queue SMS messages for delivery when connectivity or a gateway becomes available.

## 4. Design

Create a professional healthcare dashboard using a white background, navy-blue accents and a simple, intuitive layout.
Prioritize usability for CHWs with limited technical experience.
Use:

- Large, readable buttons
- Straightforward navigation
- Clear graphs for blood pressure trends
- Simple clinical flags and explanations
- Mobile-friendly layouts

Avoid unnecessarily complicated interfaces.

## 5. Clinical Safety

All clinical thresholds and instructions must come from the physician-reviewed configuration in PLAN.md.
Do not treat placeholder thresholds as approved clinical guidance.
Separate emergency escalation from routine patient prioritization. Any potential emergency must follow an approved clinical referral protocol.
Keep the AI's responsibilities limited to information extraction, classification and decision support. Require human confirmation of clinical actions.

## 6. Demonstration

Generate 60 synthetic patient records representing six villages, including patients with hypertension, diabetes, stable measurements, increasing blood pressure, missed medication and missed appointments.
Use synthetic information only. Do not include actual identifiable patient data.
Make the following demonstration functional:

1. Open the application offline.
2. Select a patient and review their blood pressure history.
3. Receive or simulate an incoming SMS.
4. Use AI to extract information from the message.
5. Display a clinical flag when an approved rule is triggered.
6. Generate a weekly CHW schedule.
7. Allow the CHW to review and approve the suggested actions.
8. Switch the application's language.

## 7. Final Deliverables

Provide:

- A working application
- Complete source code
- Installation and startup instructions
- Synthetic demonstration data
- A short README explaining the architecture
- A demonstration workflow suitable for the hackathon presentation

Important: Prioritize a complete, working prototype over additional features. Follow the existing PLAN.md and reuse existing repository code where available. Do not rebuild functioning components unnecessarily.
Start with the patient dashboard, clinical monitoring, SMS processing and weekly planner. Add patient education and additional translations after the core workflow works.
