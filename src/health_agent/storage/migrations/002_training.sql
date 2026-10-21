-- 002_training.sql —— 训练日志与恢复上下文
--
-- 三条设计约束（与 001 的 records 严格分开）：
-- 1. 训练日志是用户主观记录，不是设备测量；本文件不接受任何"设备来源"字段。
-- 2. 缺失就是 NULL：时长、主观强度、精力、疲劳、酸痛程度均可为空，不以 0 填充。
-- 3. 恢复反馈的 source 由数据库约束固定为 subjective，从存储层堵住
--    "把主观感受写成设备测量"的路径。
--
-- 全部语句幂等（IF NOT EXISTS），重复执行安全。

-- 一次训练 = 一行。同一场训练练多个部位时用 body_parts 列表表示，不拆行。
CREATE TABLE IF NOT EXISTS training_sessions (
    session_id          TEXT PRIMARY KEY,
    session_date        TEXT NOT NULL,
    started_at          TEXT,
    activity_type       TEXT NOT NULL
                        CHECK (activity_type IN ('strength', 'cardio', 'mobility', 'sport', 'walking', 'other')),
    body_parts          TEXT NOT NULL DEFAULT '',
    content             TEXT NOT NULL DEFAULT '',
    duration_minutes    INTEGER
                        CHECK (duration_minutes IS NULL OR (duration_minutes BETWEEN 1 AND 600)),
    perceived_intensity INTEGER
                        CHECK (perceived_intensity IS NULL OR (perceived_intensity BETWEEN 1 AND 10)),
    completion          TEXT NOT NULL DEFAULT 'completed'
                        CHECK (completion IN ('completed', 'partial', 'skipped')),
    source              TEXT NOT NULL DEFAULT 'manual'
                        CHECK (source IN ('manual', 'import')),
    notes               TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_training_date
    ON training_sessions(session_date);
CREATE INDEX IF NOT EXISTS ix_training_type_date
    ON training_sessions(activity_type, session_date);

-- 每日恢复反馈：一天最多一条，重复提交 = 覆盖更新（由 UNIQUE + UPSERT 保证）。
CREATE TABLE IF NOT EXISTS recovery_feedback (
    feedback_id      TEXT PRIMARY KEY,
    feedback_date    TEXT NOT NULL UNIQUE,
    energy           INTEGER CHECK (energy IS NULL OR energy BETWEEN 1 AND 5),
    fatigue          INTEGER CHECK (fatigue IS NULL OR fatigue BETWEEN 1 AND 5),
    soreness_parts   TEXT NOT NULL DEFAULT '',
    soreness_level   INTEGER
                     CHECK (soreness_level IS NULL OR soreness_level BETWEEN 1 AND 5),
    discomfort       INTEGER NOT NULL DEFAULT 0 CHECK (discomfort IN (0, 1)),
    discomfort_note  TEXT,
    source           TEXT NOT NULL DEFAULT 'subjective'
                     CHECK (source = 'subjective'),
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_recovery_date
    ON recovery_feedback(feedback_date);

-- 训练偏好：单行表（id 恒为 1）。没有偏好时整行不存在，而不是塞一堆默认值。
CREATE TABLE IF NOT EXISTS training_preferences (
    id                 INTEGER PRIMARY KEY CHECK (id = 1),
    equipment_json     TEXT NOT NULL DEFAULT '[]',
    days_per_week      INTEGER
                       CHECK (days_per_week IS NULL OR days_per_week BETWEEN 1 AND 7),
    time_budget_minutes INTEGER
                       CHECK (time_budget_minutes IS NULL OR time_budget_minutes BETWEEN 5 AND 240),
    avoid_json         TEXT NOT NULL DEFAULT '[]',
    updated_at         TEXT NOT NULL
);
