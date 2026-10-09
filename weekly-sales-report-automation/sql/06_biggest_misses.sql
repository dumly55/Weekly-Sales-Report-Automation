-- Question: What were each forecaster's 5 biggest misses in the latest run?
--
-- SQL concepts:
--   RANK() OVER (...)        ranks rows within each forecaster, biggest miss = 1
--                            (tied misses share a rank)
--   filtering a window       window results can't go in WHERE directly, so they're computed
--                            in a WITH step ("ranked") and filtered in the main query

WITH latest AS (
    SELECT *
    FROM movie_scores
    WHERE run_date = (SELECT MAX(run_date) FROM movie_scores)
      AND status = 'Final'
),
ranked AS (
    SELECT
        forecaster,
        movie,
        ROUND(pct_error, 1)                                                    AS miss_pct,
        RANK() OVER (PARTITION BY forecaster ORDER BY ABS(pct_error) DESC)     AS miss_rank
    FROM latest
)
SELECT forecaster, miss_rank, movie, miss_pct
FROM ranked
WHERE miss_rank <= 5
ORDER BY forecaster, miss_rank;
