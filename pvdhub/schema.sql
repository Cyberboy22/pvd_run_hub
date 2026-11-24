-- PVD Run Hub: one database for process, plant and coating data.
-- Written for SQLite; the same design maps 1:1 onto PostgreSQL
-- (process_signal would become a TimescaleDB hypertable there).

PRAGMA foreign_keys = ON;

-- ------------------------------------------------------------ master data
CREATE TABLE plant (
    plant_id    TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    process     TEXT NOT NULL CHECK (process IN ('MSIP', 'AIP', 'HS-PVD')),
    log_format  TEXT NOT NULL
);

CREATE TABLE target_set (
    target_id     TEXT PRIMARY KEY,
    plant_id      TEXT NOT NULL REFERENCES plant(plant_id),
    material      TEXT NOT NULL,
    installed_on  TEXT NOT NULL,
    removed_on    TEXT                     -- NULL = still installed
);

CREATE TABLE recipe (
    recipe_id        TEXT PRIMARY KEY,
    coating          TEXT NOT NULL,
    bias_v           REAL NOT NULL,
    power_kw         REAL NOT NULL,
    n2_ratio         REAL NOT NULL CHECK (n2_ratio BETWEEN 0 AND 1),
    temp_c           REAL NOT NULL,
    coat_h           REAL NOT NULL,
    etch_min         REAL NOT NULL,
    etch_bias_v      REAL NOT NULL,
    total_flow_sccm  REAL NOT NULL,
    is_reference     INTEGER NOT NULL DEFAULT 0
);

-- ----------------------------------------------------------- one row per run
CREATE TABLE coating_run (
    run_id       TEXT PRIMARY KEY,
    plant_id     TEXT NOT NULL REFERENCES plant(plant_id),
    recipe_id    TEXT NOT NULL REFERENCES recipe(recipe_id),
    target_id    TEXT NOT NULL REFERENCES target_set(target_id),
    started_at   TEXT NOT NULL,            -- local plant time, ISO 8601
    operator     TEXT,
    raw_file     TEXT NOT NULL,
    raw_sha256   TEXT NOT NULL,            -- provenance: which exact file produced this row
    status       TEXT NOT NULL CHECK (status IN ('ok', 'aborted')),
    energy_kwh   REAL NOT NULL
);

CREATE TABLE run_phase (
    run_id   TEXT NOT NULL REFERENCES coating_run(run_id),
    phase    TEXT NOT NULL CHECK (phase IN ('heating', 'etching', 'coating', 'cooling')),
    start_s  REAL NOT NULL,
    end_s    REAL NOT NULL,
    PRIMARY KEY (run_id, phase)
);

-- cleaned signals, stored at 30 s resolution (raw files keep the 10 s original)
CREATE TABLE process_signal (
    run_id       TEXT NOT NULL REFERENCES coating_run(run_id),
    t_s          REAL NOT NULL,
    bias_v       REAL,
    power_kw     REAL,
    cathode_v    REAL,
    pressure_pa  REAL,
    ar_sccm      REAL,
    n2_sccm      REAL,
    temp_c       REAL,
    PRIMARY KEY (run_id, t_s)
) WITHOUT ROWID;

CREATE TABLE quality_flag (
    flag_id   INTEGER PRIMARY KEY,
    run_id    TEXT NOT NULL REFERENCES coating_run(run_id),
    code      TEXT NOT NULL,
    severity  TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
    signal    TEXT,
    start_s   REAL,
    end_s     REAL,
    message   TEXT NOT NULL
);
CREATE INDEX ix_flag_run ON quality_flag(run_id);

-- ---------------------------------------------------------------- lab side
CREATE TABLE sample (
    sample_id  TEXT PRIMARY KEY,
    run_id     TEXT NOT NULL REFERENCES coating_run(run_id),
    substrate  TEXT NOT NULL,
    position   TEXT NOT NULL CHECK (position IN ('top', 'middle', 'bottom'))
);

CREATE TABLE measurement (
    measurement_id  INTEGER PRIMARY KEY,
    sample_id       TEXT NOT NULL REFERENCES sample(sample_id),
    quantity        TEXT NOT NULL CHECK (quantity IN ('thickness', 'hardness', 'youngs_modulus', 'adhesion_class')),
    value           REAL NOT NULL,
    unit            TEXT NOT NULL,
    method          TEXT NOT NULL,
    measured_on     TEXT NOT NULL
);
CREATE INDEX ix_meas_sample ON measurement(sample_id, quantity);

-- --------------------------------------------------------- derived results
CREATE TABLE run_features (
    run_id          TEXT PRIMARY KEY REFERENCES coating_run(run_id),
    coat_h          REAL,
    etch_min        REAL,
    etch_bias_v     REAL,
    bias_v          REAL,
    power_kw        REAL,
    cathode_v       REAL,
    pressure_pa     REAL,
    n2_ratio        REAL,
    temp_c          REAL,
    arc_rate_per_h  REAL,
    gap_min         REAL,
    target_age_kwh  REAL
);

CREATE TABLE drift_check (
    run_id          TEXT NOT NULL REFERENCES coating_run(run_id),
    golden_run_id   TEXT NOT NULL REFERENCES coating_run(run_id),
    metric          TEXT NOT NULL,
    value           REAL,
    golden_value    REAL,
    deviation_pct   REAL,
    status          TEXT NOT NULL CHECK (status IN ('ok', 'warning', 'alarm')),
    PRIMARY KEY (run_id, metric)
);

-- --------------------------------------------------------------------- views
-- How much energy had the target set seen BEFORE this run? (window function)
CREATE VIEW v_target_age AS
SELECT run_id, target_id, started_at,
       COALESCE(SUM(energy_kwh) OVER (
           PARTITION BY target_id ORDER BY started_at
           ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS target_age_kwh
FROM coating_run;

-- One row per run with the mean of each lab result (long -> wide)
CREATE VIEW v_run_properties AS
SELECT s.run_id,
       AVG(CASE WHEN m.quantity = 'thickness'      THEN m.value END) AS thickness_um,
       AVG(CASE WHEN m.quantity = 'hardness'       THEN m.value END) AS hardness_gpa,
       AVG(CASE WHEN m.quantity = 'youngs_modulus' THEN m.value END) AS youngs_modulus_gpa,
       MAX(CASE WHEN m.quantity = 'adhesion_class' THEN m.value END) AS adhesion_class_worst,
       COUNT(DISTINCT s.sample_id) AS n_samples
FROM sample s JOIN measurement m ON m.sample_id = s.sample_id
GROUP BY s.run_id;

-- The table a model trains on: measured process features + measured properties,
-- only for complete, non-aborted runs.
CREATE VIEW training_data AS
SELECT r.run_id, r.plant_id, rc.coating, rc.recipe_id, rc.is_reference, r.started_at,
       f.coat_h, f.etch_min, f.etch_bias_v, f.bias_v, f.power_kw, f.cathode_v, f.pressure_pa,
       f.n2_ratio, f.temp_c, f.arc_rate_per_h, f.target_age_kwh,
       p.thickness_um, p.hardness_gpa, p.youngs_modulus_gpa, p.adhesion_class_worst,
       ROUND(p.thickness_um / f.coat_h, 3) AS rate_um_per_h
FROM coating_run r
JOIN recipe rc           ON rc.recipe_id = r.recipe_id
JOIN run_features f      ON f.run_id = r.run_id
JOIN v_run_properties p  ON p.run_id = r.run_id
WHERE r.status = 'ok' AND p.hardness_gpa IS NOT NULL;
