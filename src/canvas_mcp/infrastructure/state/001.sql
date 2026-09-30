CREATE TABLE state_schema (version INTEGER NOT NULL);
INSERT INTO state_schema (version) VALUES (1);
CREATE TABLE state_owners (
    owner TEXT PRIMARY KEY,
    initialized INTEGER NOT NULL DEFAULT 0 CHECK (initialized IN (0,1))
);
CREATE TABLE state_courses (
    owner TEXT NOT NULL REFERENCES state_owners(owner) ON DELETE CASCADE,
    course_id TEXT NOT NULL,
    PRIMARY KEY(owner, course_id)
);
CREATE TABLE grade_state (
    owner TEXT NOT NULL REFERENCES state_owners(owner) ON DELETE CASCADE,
    snapshot TEXT NOT NULL CHECK (snapshot IN ('latest','baseline')),
    course_id TEXT NOT NULL,
    assignment_id TEXT NOT NULL,
    attempt BIGINT,
    score DOUBLE PRECISION,
    grade TEXT,
    points_possible DOUBLE PRECISION,
    graded_at TEXT,
    posted_at TEXT,
    workflow TEXT NOT NULL,
    visibility TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    state_hash TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    PRIMARY KEY(owner, snapshot, course_id, assignment_id)
);
