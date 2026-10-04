-- TWESE CHW AI database schema (PLAN.md section 4).
-- One SQLite file: data/chw.db. Create it with:
--   python data/make_db.py --db data/chw.db --schema data/schema.sql
-- Lists of codes (for example tests_performed) are stored as JSON text, like '["bp","glucose"]'.
-- Dates are ISO text: 'YYYY-MM-DD' for days, 'YYYY-MM-DDTHH:MM:SS' for times.

PRAGMA foreign_keys = ON;

-- One row per patient. Existing record fields keep their own names.
CREATE TABLE IF NOT EXISTS patients (
    id                 INTEGER PRIMARY KEY,
    sex                TEXT CHECK (sex IN ('male', 'female')),
    age                INTEGER,
    number_of_children INTEGER,               -- stored, not used yet
    smoker             TEXT CHECK (smoker IN ('yes', 'no')),
    height_cm          REAL,
    weight_kg          REAL,
    village            TEXT,
    phone_hash         TEXT,
    language           TEXT,                  -- en, fr, sw, rn
    enrolled_on        TEXT,
    condition_codes    TEXT,                  -- JSON list from positive results, for example '["hypertension"]'
    chw_id             INTEGER NOT NULL DEFAULT 1,  -- the CHW who follows this patient (one laptop, many CHWs)
    goal_override      TEXT CHECK (goal_override IS NULL OR goal_override = 'high_risk'),
                                              -- set only by data/import_records.py or a supervisor (PIN); never by
                                              -- the engine. high_risk: config.bp_goal_high_risk replaces config.bp_goal.
                                              -- An older chw.db gets this column by ALTER TABLE (engine/api.py).
    status             TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'former'))
                                              -- former: no longer followed (event, moved away); kept for the
                                              -- peer-trajectory history, left out of the dashboard and the plan
);

-- One row per contact: clinic visit, CHW visit, or a confirmed SMS.
CREATE TABLE IF NOT EXISTS encounters (
    id                          INTEGER PRIMARY KEY,
    patient_id                  INTEGER NOT NULL REFERENCES patients(id),
    date                        TEXT NOT NULL,
    source                      TEXT CHECK (source IN ('clinic', 'chw', 'sms')),
    complaint_text              TEXT,
    tests_performed             TEXT,         -- JSON list of codes
    positive_results            TEXT,         -- JSON list of codes
    referral                    TEXT,         -- JSON list of codes
    doctor_recommendation_text  TEXT,
    doctor_notes_text           TEXT,
    chw_notes_text              TEXT,         -- new field
    bp_text                     TEXT,         -- as recorded, for example '140/90'
    sbp                         INTEGER,      -- split from bp_text
    dbp                         INTEGER,      -- split from bp_text; empty if only one number was recorded
    blood_sugar                 REAL,         -- stored, not used in this version
    blood_sugar_unit            TEXT,
    meds_taken                  TEXT CHECK (meds_taken IN ('yes', 'no', 'partial')),
    symptom_codes               TEXT,         -- JSON list, extracted by layer 4 and confirmed by the CHW
    lang                        TEXT,
    context_codes_json          TEXT          -- PLAN.md 5b: [{code, quote}] from the notes or SMS (layer 4)
);

-- Medications pulled from the doctor's recommendation, confirmed by the CHW.
CREATE TABLE IF NOT EXISTS medications (
    id            INTEGER PRIMARY KEY,
    patient_id    INTEGER NOT NULL REFERENCES patients(id),
    encounter_id  INTEGER REFERENCES encounters(id),
    name          TEXT NOT NULL,              -- must be in config.medications
    dose_text     TEXT,
    source_text   TEXT                        -- the text the name was found in
);

-- Every SMS in and out. Outgoing messages wait as 'queued' until Sync marks them 'sent'.
CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY,
    patient_id    INTEGER REFERENCES patients(id),
    direction     TEXT CHECK (direction IN ('in', 'out')),
    kind          TEXT CHECK (kind IN ('report', 'reminder', 'follow_up', 'quiz', 'correction', 'other')),
    lang          TEXT,
    text          TEXT NOT NULL,
    received_at   TEXT,
    parsed_json   TEXT,                       -- fields extracted by sms.py, before confirmation
    encounter_id  INTEGER REFERENCES encounters(id),  -- set when the message becomes an encounter
    status        TEXT CHECK (status IN ('queued', 'sent'))
);

-- Flags raised by engine/signals.py, each with a reason and a guideline reference.
CREATE TABLE IF NOT EXISTS flags (
    id             INTEGER PRIMARY KEY,
    patient_id     INTEGER NOT NULL REFERENCES patients(id),
    date           TEXT NOT NULL,
    type           TEXT CHECK (type IN ('threshold', 'trend', 'symptom', 'who_risk', 'borderline')),
    value          TEXT,
    reason_text    TEXT,
    guideline_ref  TEXT,
    emergency      TEXT CHECK (emergency IN ('yes', 'no'))
);

-- Risk scores from engine/risk.py.
CREATE TABLE IF NOT EXISTS risk_scores (
    id               INTEGER PRIMARY KEY,
    patient_id       INTEGER NOT NULL REFERENCES patients(id),
    date             TEXT NOT NULL,
    score            REAL,
    tier             TEXT CHECK (tier IN ('low', 'medium', 'high')),
    components_json  TEXT,
    config_version   TEXT
);

-- The weekly plan from engine/plan_week.py. A person approves it.
CREATE TABLE IF NOT EXISTS plans (
    id           INTEGER PRIMARY KEY,
    chw_id       INTEGER NOT NULL,
    week_start   TEXT NOT NULL,
    patient_id   INTEGER NOT NULL REFERENCES patients(id),
    action       TEXT CHECK (action IN ('visit', 'sms', 'wait')),
    rank         INTEGER,
    est_minutes  REAL,
    reason_text  TEXT,
    confirmed    TEXT,                        -- set when the plan is approved
    done_on      TEXT
);

-- Every suggestion and what the CHW chose. This is the training data that grows by itself.
CREATE TABLE IF NOT EXISTS decisions (
    id                       INTEGER PRIMARY KEY,
    patient_id               INTEGER NOT NULL REFERENCES patients(id),
    date                     TEXT NOT NULL,
    state_json               TEXT,
    suggestion               TEXT,
    probs_json               TEXT,
    final_choice             TEXT,
    override_reason          TEXT,
    outcome_at_next_contact  TEXT,
    model_version            TEXT,
    config_version           TEXT
);

-- CHW visits, clinic appointments and SMS checks.
-- Missed means due_date is past and done_on is empty.
CREATE TABLE IF NOT EXISTS followups (
    id          INTEGER PRIMARY KEY,
    patient_id  INTEGER NOT NULL REFERENCES patients(id),
    due_date    TEXT NOT NULL,
    kind        TEXT CHECK (kind IN ('chw_visit', 'clinic_appointment', 'sms_check')),
    status      TEXT CHECK (status IN ('due', 'missed', 'done')),
    done_on     TEXT
);

-- Patient education answers (PLAN.md step 10).
CREATE TABLE IF NOT EXISTS quiz_results (
    id           INTEGER PRIMARY KEY,
    patient_id   INTEGER NOT NULL REFERENCES patients(id),
    question_id  TEXT NOT NULL,
    topic        TEXT,
    asked_at     TEXT,
    channel      TEXT CHECK (channel IN ('sms', 'visit')),
    answer       TEXT,
    correct      TEXT CHECK (correct IN ('yes', 'no', 'unclear')),
    lang         TEXT,
    next_due     TEXT
);

-- Hard outcomes only. The trajectory model (step 11b) learns from these.
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    patient_id  INTEGER NOT NULL REFERENCES patients(id),
    date        TEXT NOT NULL,
    type        TEXT CHECK (type IN ('stroke', 'hospital_admission', 'emergency_referral_confirmed', 'death')),
    source      TEXT CHECK (source IN ('referral', 'clinic', 'chw_note', 'followup')),
    note        TEXT
);

-- Suspected misses and over-calls, one row each (PLAN.md section 4). Filled by engine/errors.py;
-- a supervisor labels each row; the monthly review works from this table.
CREATE TABLE IF NOT EXISTS error_reviews (
    id                INTEGER PRIMARY KEY,
    patient_id        INTEGER REFERENCES patients(id),
    kind              TEXT CHECK (kind IN ('false_positive', 'false_negative', 'extraction_error')),
    source            TEXT CHECK (source IN ('override_down', 'override_up', 'event_without_flag',
                                             'referral_sent_home', 'field_corrected', 'trajectory_miss')),
    linked_id         INTEGER,                -- the flag, decision, event or message
    rule_id           TEXT,
    model_version     TEXT,
    config_version    TEXT,
    detected_on       TEXT,
    evidence_json     TEXT,
    supervisor_label  TEXT NOT NULL DEFAULT 'pending'
                      CHECK (supervisor_label IN ('confirmed_error', 'correct_call', 'unavoidable', 'pending')),
    cause_code        TEXT CHECK (cause_code IS NULL OR cause_code IN ('threshold', 'trend_rule', 'symptom_rule',
                                  'extraction', 'silence', 'model', 'chw_judgement', 'other')),
    reviewed_by       TEXT,
    reviewed_on       TEXT,
    UNIQUE (source, kind, linked_id)       -- a trajectory_miss links an event (false negative) or a score (false positive)
);

-- Peer-trajectory results (PLAN.md step 11b).
CREATE TABLE IF NOT EXISTS trajectory_scores (
    id                      INTEGER PRIMARY KEY,
    patient_id              INTEGER NOT NULL REFERENCES patients(id),
    date                    TEXT NOT NULL,
    share_peers_with_event  REAL,             -- empty when there are not enough local events yet
    peer_ids_json           TEXT,
    flag                    TEXT CHECK (flag IN ('yes', 'no')),
    reason_text             TEXT,
    model_version           TEXT
);

-- Thumbs up or down from the CHW or the patient.
CREATE TABLE IF NOT EXISTS feedback (
    id      INTEGER PRIMARY KEY,
    who     TEXT CHECK (who IN ('chw', 'patient')),
    target  TEXT CHECK (target IN ('suggestion', 'plan')),
    value   TEXT,
    note    TEXT
);

-- Where the CHW's hours go. Feeds the weekly plan with real minutes per visit and village.
CREATE TABLE IF NOT EXISTS time_log (
    id          INTEGER PRIMARY KEY,
    chw_id      INTEGER NOT NULL,
    activity    TEXT CHECK (activity IN ('visit', 'sms', 'travel', 'admin')),
    patient_id  INTEGER REFERENCES patients(id),
    village     TEXT,
    start       TEXT,
    "end"       TEXT                          -- quoted because END is an SQL word
);

-- Every call to the local LLM (layer 4), valid or not.
CREATE TABLE IF NOT EXISTS llm_log (
    id             INTEGER PRIMARY KEY,
    message_id     INTEGER REFERENCES messages(id),
    job            TEXT CHECK (job IN ('extract', 'medications', 'summary')),
    model          TEXT,
    prompt_text    TEXT,
    response_text  TEXT,
    valid          TEXT CHECK (valid IN ('yes', 'no')),
    reason         TEXT,
    at             TEXT
);

-- People who use the app, with a PIN per person. Roles: chw, supervisor. Only the PIN hash is stored.
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    role       TEXT NOT NULL CHECK (role IN ('chw', 'supervisor')),
    pin_salt   TEXT NOT NULL,
    pin_hash   TEXT NOT NULL,              -- PBKDF2-SHA256 of the PIN with the salt
    created_at TEXT
);

-- Who changed what, and when.
CREATE TABLE IF NOT EXISTS audit (
    id          INTEGER PRIMARY KEY,
    user_id     TEXT,
    action      TEXT,
    table_name  TEXT,
    row_id      INTEGER,
    at          TEXT
);

-- Key and value pairs: synthetic = true, schema_version, config_version.
CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

-- Hidden trajectory of each synthetic patient (drifting, stable, deteriorating).
-- Filled by make_synthetic.py (step 2), used by evaluate.py only. The engine never reads it.
CREATE TABLE IF NOT EXISTS synthetic_truth (
    patient_id                 INTEGER PRIMARY KEY REFERENCES patients(id),
    trajectory                 TEXT,          -- drifting, stable, deteriorating
    deteriorates_within_30d    TEXT CHECK (deteriorates_within_30d IN ('yes', 'no')),
    groups_json                TEXT,          -- for example '["smoker_high_bmi","silent"]'
    text_modifier              TEXT CHECK (text_modifier IN ('up', 'down', 'none')),
                                              -- a note or message that should move the action one step
    text_modifier_reason       TEXT,
    text_modifier_action       TEXT,          -- the intended action for that encounter
    text_modifier_encounter    INTEGER,
    text_modifier_date         TEXT,
    text_modifier_codes_json   TEXT           -- PLAN.md 5b: gold context codes with quotes for that case
);

-- Speeds up the per-patient lookups the engine does all the time.
CREATE INDEX IF NOT EXISTS idx_encounters_patient ON encounters(patient_id, date);
CREATE INDEX IF NOT EXISTS idx_messages_patient   ON messages(patient_id, received_at);
CREATE INDEX IF NOT EXISTS idx_flags_patient      ON flags(patient_id, date);
CREATE INDEX IF NOT EXISTS idx_risk_patient       ON risk_scores(patient_id, date);
CREATE INDEX IF NOT EXISTS idx_followups_patient  ON followups(patient_id, due_date);
CREATE INDEX IF NOT EXISTS idx_quiz_patient       ON quiz_results(patient_id, question_id);
CREATE INDEX IF NOT EXISTS idx_decisions_patient  ON decisions(patient_id, date);
CREATE INDEX IF NOT EXISTS idx_traj_patient       ON trajectory_scores(patient_id, date);
CREATE INDEX IF NOT EXISTS idx_plans_week         ON plans(chw_id, week_start);
CREATE INDEX IF NOT EXISTS idx_patients_chw       ON patients(chw_id, status);
CREATE INDEX IF NOT EXISTS idx_events_patient     ON events(patient_id, date);
CREATE INDEX IF NOT EXISTS idx_time_log_chw       ON time_log(chw_id, activity);
