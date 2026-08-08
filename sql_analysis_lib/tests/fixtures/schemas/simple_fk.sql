-- Simple parent-child FK for basic cascade tests

CREATE TABLE IF NOT EXISTS cg_departments (
    id      SERIAL PRIMARY KEY,
    name    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cg_employees (
    id      SERIAL PRIMARY KEY,
    dept_id INT REFERENCES cg_departments(id) ON DELETE CASCADE,
    name    TEXT NOT NULL,
    salary  NUMERIC(10, 2)
);
