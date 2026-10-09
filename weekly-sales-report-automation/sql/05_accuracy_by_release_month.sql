-- Question: Were the predictions better for movies released in some months than others?
--
-- SQL concepts:
--   strftime('%Y-%m', date)   turns a date like 2026-07-17 into its month, 2026-07
--   GROUP BY an expression    groups by that calculated month
--   AVG(pct_error)            without ABS, the average "lean": + means predictions ran high
--   IS NOT NULL               skips movies with no release date

WITH latest AS (
    SELECT *
    FROM movie_scores
    WHERE run_date = (SELECT MAX(run_date) FROM movie_scores)
      AND status = 'Released'
)
SELECT
    strftime('%Y-%m', release_date)  AS release_month,
    COUNT(*)                         AS movies,
    ROUND(AVG(ABS(pct_error)), 1)    AS avg_miss_pct,
    ROUND(AVG(pct_error), 1)         AS avg_lean_pct
FROM latest
WHERE forecaster = 'Predictions'
  AND release_date IS NOT NULL
GROUP BY release_month
ORDER BY release_month;
