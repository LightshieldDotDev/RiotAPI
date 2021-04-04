CREATE TABLE IF NOT EXISTS euw1.match
(
    match_id  BIGINT PRIMARY KEY,
    queue     SMALLINT,
    timestamp TIMESTAMP,
    duration  SMALLINT DEFAULT NULL,
    win       BOOLEAN  DEFAULT NULL, -- False: Blue | True: Red
    details   JSON,
    timeline  JSON,
    roleml    JSON
);


/*
 CREATE TABLE IF NOT EXISTS kr.match
(
    match_id  BIGINT PRIMARY KEY,
    queue     SMALLINT,
    timestamp TIMESTAMP,
    duration  SMALLINT DEFAULT NULL,
    win       BOOLEAN  DEFAULT NULL, -- False: Blue | True: Red
    details_pulled   BOOLEAN,
    timeline_pulled  BOOLEAN
);

 */
