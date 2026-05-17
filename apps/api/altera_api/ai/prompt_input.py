"""ClassifierPromptInput — strict allow-list dataclass.

This is the **only** type the prompt builder accepts. There is no
generic ``dict`` overload. Adding a new field to this class is the only
way a new field can appear in a prompt — and any such addition must
also be reflected in ``ALLOWED_PROMPT_FIELDS`` and the AI-inputs
policy docs.
"""
from __future__ import annotations

from typing import Self

from pydantic import ConfigDict, model_validator

from altera_api.ai.policy import ALLOWED_PROMPT_FIELDS
from altera_api.domain.common import Country, DomainBase, Language, NonEmptyStr
from altera_api.domain.product import NormalizedProduct


class ClassifierPromptInput(DomainBase):
    """Allowed-only fields for an LLM classifier prompt.

    Every field here must also appear in
    :data:`altera_api.ai.policy.ALLOWED_PROMPT_FIELDS`; a constructor
    validator double-checks this so the two cannot drift apart.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
        populate_by_name=True,
    )

    product_name: NonEmptyStr
    retailer_category: str | None = None
    retailer_subcategory: str | None = None
    brand: str | None = None
    ingredients_text: str | None = None
    labels: tuple[str, ...] = ()
    language: Language | None = None
    country: Country | None = None

    @model_validator(mode="after")
    def _fields_match_allow_list(self) -> Self:
        declared = set(type(self).model_fields.keys())
        if declared != ALLOWED_PROMPT_FIELDS:
            extra = declared - ALLOWED_PROMPT_FIELDS
            missing = ALLOWED_PROMPT_FIELDS - declared
            raise ValueError(
                "ClassifierPromptInput fields drifted from ALLOWED_PROMPT_FIELDS; "
                f"extra={sorted(extra)}, missing={sorted(missing)}."
            )
        return self

    @classmethod
    def from_product(cls, product: NormalizedProduct) -> Self:
        """Build a prompt input from a normalised product.

        Commercial fields are not copied **because they are not fields
        on this class** — there is no opt-in path that lets them slip
        in. The only way to leak them is to add a forbidden field to
        this class, which the policy validator rejects at construction.
        """
        return cls(
            product_name=product.product_name,
            retailer_category=product.retailer_category,
            retailer_subcategory=product.retailer_subcategory,
            brand=product.brand,
            ingredients_text=product.ingredients_text,
            labels=product.labels,
            language=product.language,
            country=product.country,
        )

    def to_payload(self) -> dict[str, object]:
        """Return a dict suitable for serialisation into the prompt.

        Empty strings collapse to ``None`` so they do not occupy
        slots in the prompt; ``labels`` is preserved as a list.
        """
        return {
            "product_name": self.product_name,
            "retailer_category": self.retailer_category,
            "retailer_subcategory": self.retailer_subcategory,
            "brand": self.brand,
            "ingredients_text": self.ingredients_text,
            "labels": list(self.labels),
            "language": self.language,
            "country": self.country,
        }
