-- Question: For movies still in theaters, how much of each forecast have they earned so far?
--
-- These movies aren't scored yet: their gross is still growing, so comparing it to a full-run
-- forecast would make every forecast look too high. This tracks their progress instead.
--
-- SQL concepts:
--   arithmetic in SELECT   100.0 * gross_so_far / forecast gives a percentage (100.0, not 100,
--                          so SQLite does decimal division instead of whole-number division)
--   NULLIF(x, 0)           returns NULL instead of 0, so dividing by a zero forecast gives
--                          NULL rather than an error
--   CASE WHEN              flags movies that have already passed the forecast

WITH latest AS (
    SELECT *
    FROM movie_scores
    WHERE run_date = (SELECT MAX(run_date) FROM movie_scores)
      AND status = 'In theaters'
)
SELECT
    movie,
    forecaster,
    days_in_theaters,
    gross_so_far,
    forecast,
    ROUND(100.0 * gross_so_far / NULLIF(forecast, 0), 1)       AS pct_of_forecast_reached,
    CASE WHEN gross_so_far > forecast THEN 'Yes' ELSE '' END   AS already_passed_forecast
FROM latest
ORDER BY forecaster, pct_of_forecast_reached DESC;
