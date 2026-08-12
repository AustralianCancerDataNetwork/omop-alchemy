# analytics

Clinical-domain logic lives beside the concept sets and policies that give it meaning.
The domain-neutral `core` and `episodes` packages provide the underlying resolution and
traversal mechanisms.

## oncology

| API | Capability |
|---|---|
| `OncologyEpisode` | Classifies episode purpose and modality; traverses events linked to the episode and its direct children. |
| `structural_modalities` / `concept_modalities` | Preserve every evidenced modality so mixed treatment and SACT classification disagreements remain visible. |
| `structural_modality` / `concept_modality` | Select one deterministic modality in radiotherapy, surgery, diagnostic/staging, SACT priority order. |
| `OncologyProcedure` / `OncologyDrugExposure` | Add governed `is_radiotherapy`, `is_surgery`, `is_diagnostic_staging`, and `is_sact` questions to CDM facts. |
| `RTDoseSummary.from_procedures(...)` | Constructs one radiotherapy summary; `summarize_rt_procedures_by(...)` groups before construction. |
| `SACTDoseSummary.from_exposures(...)` | Constructs one SACT summary; `summarize_sact_exposures_by(...)` groups before construction. |
| `OncologyEpisodeEvent` | Resolves oncology-aware facts while retaining episode-event diagnostics. |

Governed membership has two access modes:

| Access form | Behaviour |
|---|---|
| Loaded instance property | Expands once per vocabulary identity, then uses cached O(1) membership. Initial classification requires a live session; a cached result remains the snapshot computed while the instance was attached. |
| Class-level hybrid expression | Emits a database subquery for each query and does not use the Python expansion cache. |

::: omop_alchemy.toolkit.analytics.oncology.OncologyEpisode
    options:
      members:
        - is_disease_episode
        - is_overarching
        - is_treatment_episode
        - is_treatment_regimen
        - is_treatment_cycle
        - primary_episode
        - child_treatment_episodes
        - structural_modalities
        - structural_modality
        - concept_modalities
        - concept_modality
        - child_treatment_episodes_by_modality
        - child_treatment_episodes_by_concept_modality
        - rt_procedures
        - rt_dose_summaries_by_site
        - rt_dose_summary
        - sact_exposures
        - sact_dose_summaries_by_drug_concept
        - sact_dose_summary
        - ctcae_weight_loss_grade
        - martin_weight_loss_grade
        - critical_weight_loss_grade
        - critical_weight_loss_summary

::: omop_alchemy.toolkit.analytics.oncology.RTDoseSummary
    options:
      members:
        - from_procedures

::: omop_alchemy.toolkit.analytics.oncology.SACTDoseSummary
    options:
      members:
        - from_exposures

## body_metrics

| API | Capability |
|---|---|
| `MeasurementReading.from_measurement(...)` | Reduces an OMOP measurement to the fields used by calculations and records its resolution source. |
| `MeasurementSeriesMixin` | Resolves normalized measurement series for an episode. |
| `WeightTrajectoryMixin` | Exposes normalized weight and height, BMI, BSA, windowed change, trajectories, and a dict-shaped typed summary. |
| `WeightChange` | Represents percentage change and whether it was evaluable; unevaluable change has `pct_change=None`. |
| `WeightTrajectorySummary` | Types the DataFrame- and JSON-friendly mapping returned by `weight_trajectory_summary()`. |

::: omop_alchemy.toolkit.analytics.body_metrics.MeasurementReading
    options:
      members:
        - from_measurement

::: omop_alchemy.toolkit.analytics.body_metrics.WeightTrajectoryMixin
    options:
      members:
        - weight_readings
        - height_readings_cm
        - height_m
        - baseline_weight
        - latest_weight
        - baseline_bmi
        - baseline_bsa_mosteller_m2
        - pct_change_from_baseline
        - pct_change_over
        - pct_change_trajectory
        - sustained_loss
        - weight_trajectory_summary

## adverse_events

| API | Policy |
|---|---|
| `ctcae_weight_loss_grade(...)` | Grades percentage weight loss against CTCAE-style bins. |
| `martin_weight_loss_grade(...)` | Applies the Martin et al. BMI-adjusted matrix. |
| `critical_weight_loss_grade(...)` | Uses the Martin matrix when BMI is available and otherwise falls back to CTCAE-style bins. |

::: omop_alchemy.toolkit.analytics.adverse_events
