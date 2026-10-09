-- Question: Movie by movie, which forecaster came closer to the actual result?
--
-- SQL concepts:
--   self JOIN       the same table joined to itself ("p" = Predictions rows, "t" = Tracker rows),
--                   matched on the movie, so each movie's two forecasts sit side by side
--   CASE WHEN       picks a label from conditions
--   GROUP BY        then counts how many movies each forecaster won

WITH latest AS (
    SELECT *
    FROM movie_scores
    WHERE run_date = (SELECT MAX(run_date) FROM movie_scores)
      AND status = 'Released'
),
pairs AS (
    SELECT
        p.movie,
        CASE
            WHEN ABS(p.pct_error) < ABS(t.pct_error) THEN 'Predictions'
            WHEN ABS(t.pct_error) < ABS(p.pct_error) THEN 'Tracker Projection'
            ELSE 'Tie'
        END AS closer
    FROM latest AS p
    JOIN latest AS t
        ON t.movie = p.movie
    WHERE p.forecaster = 'Predictions'
      AND t.forecaster = 'Tracker Projection'
)
SELECT
    closer,
    COUNT(*)                   AS movies,
    GROUP_CONCAT(movie, ', ')  AS titles
FROM pairs
GROUP BY closer
ORDER BY movies DESC;
