"""Altera AI domain models.

Strict Pydantic v2 models that represent the core business entities and
methodology-specific shapes. Calculation, AI classification, and Supabase
integration land in later phases — these models are the contract those
layers will use.
"""

from altera_api.domain.audit import AuditEvent, AuditEventType
from altera_api.domain.common import (
    ClassificationSource,
    Country,
    DomainBase,
    Language,
    Methodology,
    NonEmptyStr,
    Quantity,
    Role,
    Slug,
)
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.domain.product import (
    NormalizedProduct,
    ProteinSource,
    PTProductFields,
    RawProduct,
    RetailChannel,
    WWFProductFields,
)
from altera_api.domain.project import Project, PTValidationStatus
from altera_api.domain.protein_tracker import (
    ProteinTrackerCalculationRow,
    ProteinTrackerCalculationSummary,
    ProteinTrackerGroup,
    ProteinTrackerGroupAggregate,
    ProteinTrackerProductClassification,
)
from altera_api.domain.review import (
    ManualReviewDecision,
    ManualReviewDecisionType,
    ManualReviewItem,
    ManualReviewQueueReason,
    ManualReviewStatus,
)
from altera_api.domain.upload import Upload, UploadStatus
from altera_api.domain.validation import (
    ValidationError,
    ValidationReport,
    ValidationSeverity,
    ValidationWarning,
)
from altera_api.domain.versioning import (
    MethodologySourceEdition,
    MethodologyVersion,
    RulesVersion,
    SemverVersion,
    TaxonomyVersion,
)
from altera_api.domain.wwf import (
    WWFCalculationRow,
    WWFCalculationSummary,
    WWFCompositeIngredient,
    WWFCompositeStep1Bucket,
    WWFFG1Subgroup,
    WWFFG2Subgroup,
    WWFFG3Subgroup,
    WWFFG5GrainKind,
    WWFFG7SnackKind,
    WWFFoodGroup,
    WWFFoodGroupAggregate,
    WWFProductClassification,
)

__all__ = [
    # common
    "ClassificationSource",
    "Country",
    "DomainBase",
    "Language",
    "Methodology",
    "NonEmptyStr",
    "Quantity",
    "Role",
    "Slug",
    # versioning
    "MethodologySourceEdition",
    "MethodologyVersion",
    "RulesVersion",
    "SemverVersion",
    "TaxonomyVersion",
    # organisation / project / upload
    "Organisation",
    "Project",
    "PTValidationStatus",
    "Upload",
    "UploadStatus",
    "UserProfile",
    # product
    "NormalizedProduct",
    "PTProductFields",
    "ProteinSource",
    "RawProduct",
    "RetailChannel",
    "WWFProductFields",
    # validation
    "ValidationError",
    "ValidationReport",
    "ValidationSeverity",
    "ValidationWarning",
    # PT
    "ProteinTrackerCalculationRow",
    "ProteinTrackerCalculationSummary",
    "ProteinTrackerGroup",
    "ProteinTrackerGroupAggregate",
    "ProteinTrackerProductClassification",
    # WWF
    "WWFCalculationRow",
    "WWFCalculationSummary",
    "WWFCompositeIngredient",
    "WWFCompositeStep1Bucket",
    "WWFFG1Subgroup",
    "WWFFG2Subgroup",
    "WWFFG3Subgroup",
    "WWFFG5GrainKind",
    "WWFFG7SnackKind",
    "WWFFoodGroup",
    "WWFFoodGroupAggregate",
    "WWFProductClassification",
    # review
    "ManualReviewDecision",
    "ManualReviewDecisionType",
    "ManualReviewItem",
    "ManualReviewQueueReason",
    "ManualReviewStatus",
    # audit
    "AuditEvent",
    "AuditEventType",
]
