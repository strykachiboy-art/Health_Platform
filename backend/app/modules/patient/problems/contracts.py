"""Problem-list domain contracts; independent of HTTP and database transport."""
from datetime import date
from typing import Literal, Self
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

ClinicalStatus = Literal['active', 'inactive', 'resolved', 'remission', 'recurrence', 'relapse']
VerificationStatus = Literal['unconfirmed', 'provisional', 'differential', 'confirmed', 'refuted', 'entered_in_error']
Category = Literal['acute', 'chronic', 'symptom', 'other']
Severity = Literal['mild', 'moderate', 'severe', 'unknown']
CodingSystem = Literal['http://hl7.org/fhir/sid/icd-10', 'http://hl7.org/fhir/sid/icd-10-cm', 'http://snomed.info/sct']
TERMINAL = {'refuted', 'entered_in_error'}
CURRENT = {'active', 'recurrence', 'relapse'}
TRANSITIONS = {
    'active': {'inactive', 'resolved', 'remission'},
    'inactive': {'active', 'resolved', 'recurrence'},
    'resolved': {'recurrence'},
    'remission': {'resolved', 'relapse', 'inactive'},
    'recurrence': {'inactive', 'resolved', 'remission'},
    'relapse': {'inactive', 'resolved', 'remission'},
}

class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

class ProblemContent(Contract):
    name: str = Field(min_length=1, max_length=255)
    code: str | None = Field(default=None, min_length=1, max_length=64, pattern=r'^[A-Za-z0-9][A-Za-z0-9.\-]*$')
    coding_system: CodingSystem | None = None
    category: Category
    clinical_status: ClinicalStatus
    verification_status: VerificationStatus
    severity: Severity | None = None
    onset_date: date | None = None
    onset_description: str | None = Field(default=None, min_length=1, max_length=255)
    resolution_date: date | None = None
    notes: str | None = Field(default=None, max_length=4000)

    @model_validator(mode='after')
    def consistent(self) -> Self:
        if (self.code is None) != (self.coding_system is None):
            raise ValueError('Code and coding system must be supplied together')
        if self.code and self.coding_system == 'http://snomed.info/sct' and not self.code.isdigit():
            raise ValueError('SNOMED CT code must be numeric')
        if self.code and self.coding_system != 'http://snomed.info/sct':
            self.code = self.code.upper()
        if self.clinical_status == 'resolved' and self.resolution_date is None:
            raise ValueError('Resolved problems require a resolution date')
        if self.clinical_status != 'resolved' and self.resolution_date is not None:
            raise ValueError('Resolution date is only applicable to resolved status')
        if self.onset_date and self.resolution_date and self.resolution_date < self.onset_date:
            raise ValueError('Resolution cannot precede onset')
        return self

class ProblemCreate(ProblemContent):
    consultation_id: StrictInt | None = Field(default=None, gt=0)

class ProblemChange(Contract):
    expected_version: StrictInt = Field(ge=1)
    action: Literal['update', 'review', 'transition', 'correct', 'associate']
    reason: str = Field(min_length=1, max_length=1000)
    content: ProblemContent | None = None
    consultation_id: StrictInt | None = Field(default=None, gt=0)

    @model_validator(mode='after')
    def action_payload(self) -> Self:
        if self.action == 'associate':
            if self.consultation_id is None or self.content is not None:
                raise ValueError('Association requires only a consultation ID')
        elif self.action == 'review':
            if self.content is not None or self.consultation_id is not None:
                raise ValueError('Review requires no replacement content')
        elif self.content is None or self.consultation_id is not None:
            raise ValueError('Content change requires replacement content only')
        return self


def validate_create(content: ProblemContent, today: date) -> None:
    if content.verification_status in TERMINAL:
        raise ValueError('Cannot create a refuted or erroneous problem')
    validate_dates(content, today)


def validate_dates(content: ProblemContent, today: date) -> None:
    if any(d is not None and d > today for d in (content.onset_date, content.resolution_date)):
        raise ValueError('Clinical dates cannot be in the future')


def validate_change(old: ProblemContent, new: ProblemContent, action: str, today: date) -> None:
    validate_dates(new, today)
    if old.verification_status in TERMINAL:
        raise ValueError('Terminal verification records cannot be changed; create a separately reviewed problem')
    if old == new:
        raise ValueError('No change to problem content')
    if action == 'update':
        if (old.clinical_status, old.verification_status) != (new.clinical_status, new.verification_status):
            raise ValueError('Status changes require an explicit transition or correction')
        if (old.name, old.code, old.coding_system) != (new.name, new.code, new.coding_system):
            raise ValueError('Identity or coding changes require a correction')
    elif action == 'transition':
        allowed = {'clinical_status', 'verification_status', 'resolution_date'}
        if any(getattr(old, f) != getattr(new, f) for f in ProblemContent.model_fields if f not in allowed):
            raise ValueError('Transition cannot edit other clinical content')
        if new.verification_status == 'entered_in_error':
            raise ValueError('Entered in error requires correction')
        if old.clinical_status != new.clinical_status and new.clinical_status not in TRANSITIONS[old.clinical_status]:
            raise ValueError('Invalid clinical status transition')
    elif action != 'correct':
        raise ValueError('Invalid content-change operation')


def eligible_for_clinical_context(content: ProblemContent) -> bool:
    """No implicit diagnosis promotion; only explicit, confirmed current problems."""
    return content.verification_status == 'confirmed' and content.clinical_status in CURRENT
