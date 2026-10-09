-- Question: How has each forecaster's accuracy changed from run to run, as more movies finish their run?
--
-- This is the query that needs history: it looks across every saved run, not just the latest.
-- With only one run saved, the change column is empty.
--
-- SQL concepts:
--   GROUP BY two columns   one row per run per forecaster
--   LAG() OVER (...)       reads the value from the previous row in order: here, the same
--                          forecaster's previous run, to show how much the average miss changed

WITH per_run AS (
    SELECT
        run_date,
        forecaster,
        COUNT(*)                       AS movies_scored,
        ROUND(AVG(ABS(pct_error)), 1)  AS avg_miss_pct
    FROM movie_scores
    WHERE status = 'Final'
    GROUP BY run_date, forecaster
)
SELECT
    run_date,
    forecaster,
    movies_scored,
    avg_miss_pct,
    ROUND(avg_miss_pct - LAG(avg_miss_pct) OVER (PARTITION BY forecaster ORDER BY run_date), 1)
        AS change_vs_previous_run
FROM per_run
ORDER BY forecaster, run_date;
