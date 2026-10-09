-- Question: How many movies landed in each accuracy band, for each forecaster, and which ones?
--
-- Bands: Nailed it (within 10%), Close (10-25% off), Off (25-50% off), Way off (more than 50% off).
--
-- SQL concepts:
--   GROUP BY two columns      one row per forecaster-and-band combination
--   GROUP_CONCAT              joins the movie titles in each group into one text value
--   ORDER BY CASE ...         sorts the bands in a custom order instead of alphabetically

WITH latest AS (
    SELECT *
    FROM movie_scores
    WHERE run_date = (SELECT MAX(run_date) FROM movie_scores)
      AND status = 'Released'
)
SELECT
    forecaster,
    accuracy_band,
    COUNT(*)                   AS movies,
    GROUP_CONCAT(movie, ', ')  AS titles
FROM latest
GROUP BY forecaster, accuracy_band
ORDER BY
    forecaster,
    CASE accuracy_band
        WHEN 'Nailed it' THEN 1
        WHEN 'Close'     THEN 2
        WHEN 'Off'       THEN 3
        ELSE 4
    END;
