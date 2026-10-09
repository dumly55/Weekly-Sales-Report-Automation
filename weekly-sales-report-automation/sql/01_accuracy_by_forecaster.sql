-- Question: How accurate is each forecaster, counting only released movies in the latest run?
--
-- SQL concepts:
--   WITH ... AS (...)   names a smaller query ("latest") so the main query can use it like a table
--   subquery            (SELECT MAX(run_date) ...) finds the most recent run
--   GROUP BY            one result row per forecaster
--   COUNT / AVG / ABS   count rows, average them, and ignore the + or - sign of an error
--   SUM(CASE WHEN ...)  counts only the rows that meet a condition

WITH latest AS (
    SELECT *
    FROM movie_scores
    WHERE run_date = (SELECT MAX(run_date) FROM movie_scores)
      AND status = 'Released'
)
SELECT
    forecaster,
    COUNT(*)                                                AS movies_scored,
    ROUND(AVG(ABS(pct_error)), 1)                           AS avg_miss_pct,
    SUM(CASE WHEN ABS(pct_error) <= 25 THEN 1 ELSE 0 END)   AS within_25_pct,
    ROUND(100.0 * SUM(CASE WHEN ABS(pct_error) <= 25 THEN 1 ELSE 0 END) / COUNT(*), 1)
                                                            AS share_within_25_pct,
    SUM(CASE WHEN pct_error > 0 THEN 1 ELSE 0 END)          AS too_high,
    SUM(CASE WHEN pct_error < 0 THEN 1 ELSE 0 END)          AS too_low
FROM latest
GROUP BY forecaster
ORDER BY avg_miss_pct;
