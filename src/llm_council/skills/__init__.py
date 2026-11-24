"""
Skills module for ADR-034 Agent Skills Integration.

Provides progressive disclosure loading for agent skills:
- Level 1: Metadata only (~100-200 tokens)
- Level 2: Full SKILL.md content
- Level 3: Resources on demand

Robustness features (ADR-034 v2.2):
- Path traversal protection (#292)
- Domain-specific exceptions (#293)
- Thread safety (#294)
- Cache invalidation (#295)
- Logging and observability (#296)
"""

from llm_council.skills.loader import (
    DEFAULT_SEARCH_PATHS,
    REFERENCES_DIR,
    SKILL_FILENAME,
    SkillError,
    SkillFull,
    SkillLoader,
    SkillMetadata,
    SkillNotFoundError,
    SkillParseError,
    load_skill_full,
    load_skill_metadata,
    load_skill_resource,
)

__all__ = [
    # Constants
    "SKILL_FILENAME",
    "REFERENCES_DIR",
    "DEFAULT_SEARCH_PATHS",
    # Exceptions
    "SkillError",
    "SkillNotFoundError",
    "SkillParseError",
    # Data classes
    "SkillMetadata",
    "SkillFull",
    # Functions
    "load_skill_metadata",
    "load_skill_full",
    "load_skill_resource",
    # Loader class
    "SkillLoader",
]
