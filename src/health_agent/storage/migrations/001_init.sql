-- 001_init.sql —— 初始 schema
-- 唯一事实来源是长表 records；宽表只在查询层由 pivot 派生，不落库。

CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    filename   TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

-- 指标注册表：由 src/health_agent/catalog.py 播种，业务代码不得绕过它写指标名
CREATE TABLE IF NOT EXISTS metrics_catalog (
    metric_name     TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    unit            TEXT NOT NULL,
    kind            TEXT NOT NULL,
    min_valid       REAL,
    max_valid       REAL,
    period_types    TEXT NOT NULL,
    tier            TEXT NOT NULL,
    in_report       INTEGER NOT NULL DEFAULT 0,
    review_required INTEGER NOT NULL DEFAULT 0,
    description     TEXT NOT NULL DEFAULT ''
);

-- 导入批次：批次是撤销的最小单位
CREATE TABLE IF NOT EXISTS import_batches (
    batch_id       TEXT PRIMARY KEY,
    imported_at    TEXT NOT NULL,
    file_name      TEXT,
    file_sha256    TEXT,
    source_kind    TEXT NOT NULL DEFAULT 'json',
    row_count      INTEGER NOT NULL DEFAULT 0,
    accepted_count INTEGER NOT NULL DEFAULT 0,
    pending_count  INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    conflict_count INTEGER NOT NULL DEFAULT 0,
    status         TEXT NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active', 'revoked')),
    revoked_at     TEXT,
    note           TEXT
);

-- 健康记录（长表）。value 允许 NULL：缺失就是缺失，绝不填 0
CREATE TABLE IF NOT EXISTS records (
    record_id           TEXT PRIMARY KEY,
    logical_key         TEXT NOT NULL,
    dedup_key           TEXT NOT NULL,
    record_date         TEXT NOT NULL,
    metric_name         TEXT NOT NULL REFERENCES metrics_catalog(metric_name),
    value               REAL,
    unit                TEXT NOT NULL,
    raw_value           TEXT,
    raw_unit            TEXT,
    period_type         TEXT NOT NULL
                        CHECK (period_type IN ('instant', 'day', 'week', 'month')),
    period_start        TEXT,
    period_end          TEXT,
    source_app          TEXT NOT NULL DEFAULT 'huawei_health',
    source_page         TEXT,
    extraction_method   TEXT,
    verification_status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (verification_status IN ('pending', 'confirmed', 'rejected', 'superseded')),
    batch_id            TEXT REFERENCES import_batches(batch_id),
    superseded_by       TEXT REFERENCES records(record_id),
    quality_flags       TEXT NOT NULL DEFAULT '[]',
    notes               TEXT,
    captured_at         TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    CHECK (period_type NOT IN ('week', 'month')
           OR (period_start IS NOT NULL AND period_end IS NOT NULL))
);

-- 同一逻辑键最多一条"已确认"：从数据库层面堵住静默覆盖。
-- pending 不受限制，因此冲突记录可以并存，等待人工裁决。
CREATE UNIQUE INDEX IF NOT EXISTS uq_records_confirmed
    ON records(logical_key) WHERE verification_status = 'confirmed';

CREATE INDEX IF NOT EXISTS ix_records_trend
    ON records(metric_name, period_type, record_date);
CREATE INDEX IF NOT EXISTS ix_records_status
    ON records(verification_status, record_date);
CREATE INDEX IF NOT EXISTS ix_records_batch
    ON records(batch_id);
CREATE INDEX IF NOT EXISTS ix_records_logical
    ON records(logical_key);

-- 冲突待裁决：新值与已有值并存，不擅自决定真值
CREATE TABLE IF NOT EXISTS conflicts (
    conflict_id        TEXT PRIMARY KEY,
    logical_key        TEXT NOT NULL,
    metric_name        TEXT NOT NULL,
    record_date        TEXT NOT NULL,
    existing_record_id TEXT REFERENCES records(record_id),
    incoming_record_id TEXT NOT NULL REFERENCES records(record_id),
    detected_at        TEXT NOT NULL,
    resolution         TEXT NOT NULL DEFAULT 'pending'
                       CHECK (resolution IN ('pending', 'keep_existing', 'take_incoming', 'keep_both')),
    resolved_at        TEXT,
    resolution_note    TEXT
);

CREATE INDEX IF NOT EXISTS ix_conflicts_pending
    ON conflicts(resolution, detected_at);

-- 个人基线快照：中位数 + MAD，样本不足时不建基线
CREATE TABLE IF NOT EXISTS baselines (
    baseline_id   TEXT PRIMARY KEY,
    metric_name   TEXT NOT NULL REFERENCES metrics_catalog(metric_name),
    window_days   INTEGER NOT NULL,
    sample_count  INTEGER NOT NULL,
    center_value  REAL NOT NULL,
    spread_value  REAL NOT NULL,
    mad_factor    REAL NOT NULL,
    method        TEXT NOT NULL DEFAULT 'median_mad',
    valid_from    TEXT NOT NULL,
    valid_until   TEXT NOT NULL,
    computed_at   TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'insufficient', 'superseded'))
);

CREATE INDEX IF NOT EXISTS ix_baselines_metric
    ON baselines(metric_name, status, computed_at);

-- 提醒实例：dedup_key 唯一，保证同一窗口同一规则只发一次
CREATE TABLE IF NOT EXISTS alerts (
    alert_id       TEXT PRIMARY KEY,
    rule_id        TEXT NOT NULL,
    dedup_key      TEXT NOT NULL UNIQUE,
    metric_name    TEXT,
    severity       TEXT NOT NULL DEFAULT 'info'
                   CHECK (severity IN ('info', 'warn')),
    window_start   TEXT NOT NULL,
    window_end     TEXT NOT NULL,
    trigger_reason TEXT NOT NULL,
    evidence_json  TEXT NOT NULL DEFAULT '[]',
    suggested_action TEXT,
    created_at     TEXT NOT NULL,
    cooldown_until TEXT,
    read_at        TEXT,
    feedback       TEXT NOT NULL DEFAULT 'none'
                   CHECK (feedback IN ('none', 'helpful', 'acted', 'irrelevant'))
);

CREATE INDEX IF NOT EXISTS ix_alerts_created
    ON alerts(created_at);

-- 用户设置（生活背景）：全部可空，为空时系统照常工作
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- AI 调用日志：费用拿不到就记 unavailable，绝不用官方标价冒充实际账单
CREATE TABLE IF NOT EXISTS ai_call_log (
    call_id       TEXT PRIMARY KEY,
    task_type     TEXT NOT NULL,
    model         TEXT NOT NULL,
    prompt_version TEXT,
    prompt_hash   TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    total_tokens  INTEGER,
    cost_value    REAL,
    cost_currency TEXT,
    cost_source   TEXT NOT NULL DEFAULT 'unavailable'
                  CHECK (cost_source IN ('billed', 'estimated', 'unavailable')),
    latency_ms    INTEGER,
    status        TEXT NOT NULL CHECK (status IN ('ok', 'error', 'timeout')),
    error_code    TEXT,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_ai_log_created
    ON ai_call_log(created_at);

-- 报告缓存：相同摘要直接复用，减少重复调用
CREATE TABLE IF NOT EXISTS report_cache (
    cache_key      TEXT PRIMARY KEY,
    report_type    TEXT NOT NULL,
    period_start   TEXT NOT NULL,
    period_end     TEXT NOT NULL,
    source_digest  TEXT NOT NULL,
    payload_json   TEXT NOT NULL,
    ai_model       TEXT,
    prompt_version TEXT,
    created_at     TEXT NOT NULL
);

-- 全量变更审计：谁在什么时候把什么改成了什么
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id   TEXT PRIMARY KEY,
    entity     TEXT NOT NULL,
    entity_id  TEXT NOT NULL,
    action     TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT,
    reason     TEXT,
    actor      TEXT NOT NULL DEFAULT 'local_user',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_audit_entity
    ON audit_log(entity, entity_id, created_at);
