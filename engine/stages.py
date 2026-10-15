"""Stage registry.

Why: importing this module registers every implemented stage with the pipeline
(`engine.pipeline.register`). Stages are added here milestone by milestone.
"""

from __future__ import annotations
