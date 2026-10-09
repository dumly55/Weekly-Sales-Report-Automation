-- Question: What is each forecaster's typical (median) miss in the latest run?
--
-- SQLite has no MEDIAN() function, so this builds one. A median is the middle value once the
-- values are sorted (or the average of the two middle values when there's an even count).
-- It's a better "typical miss" than the average, which one huge miss can drag up.
--
-- SQL concepts:
--   window functions   ROW_NUMBER() OVER (...) numbers each row within its group without
--                      collapsing the rows like GROUP BY does; COUNT(*) OVER (...) puts the
--                      group's size on every row
--   PARTITION BY       restarts the numbering for each forecaster
--   integer division   (total + 1) / 2 and (total + 2) / 2 are the middle position(s)

WITH latest AS (
    SELECT *
    FROM movie_scores
    WHERE run_date = (SELECT MAX(run_date) FROM movie_scores)
      AND status = 'Final'
),
ranked AS (
    SELECT
        forecaster,
        ABS(pct_error)                                                       AS miss,
        ROW_NUMBER() OVER (PARTITION BY forecaster ORDER BY ABS(pct_error))  AS position,
        COUNT(*)     OVER (PARTITION BY forecaster)                          AS total
    FROM latest
)
SELECT
    forecaster,
    ROUND(AVG(miss), 1) AS median_miss_pct
FROM ranked
WHERE position IN ((total + 1) / 2, (total + 2) / 2)
GROUP BY forecaster
ORDER BY median_miss_pct;
