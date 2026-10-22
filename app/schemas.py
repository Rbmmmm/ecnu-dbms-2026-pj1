from datetime import datetime
from typing import Literal, Annotated
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.decimal_values import canonical_decimal
from app.time_values import iso_deadline
from app.config import LIMITS


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


def uuid_string(value):
    try:
        if str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError('需要标准UUID')
    return value


class LoginCredentials(Model):
    username: str = Field(max_length=100)
    password: str = Field(max_length=128)


class Credentials(Model):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=128)


class Option(Model):
    id: str
    text: str = Field(max_length=LIMITS['option_text'])
    _id = field_validator('id')(uuid_string)


class SingleValidation(Model):
    pass


class MultipleValidation(Model):
    min_choices: int | None = Field(default=None, ge=0, le=LIMITS['options'])
    max_choices: int | None = Field(default=None, ge=0, le=LIMITS['options'])


class TextValidation(Model):
    min_length: int | None = Field(default=None, ge=0, le=LIMITS['text_answer'])
    max_length: int | None = Field(default=None, ge=0, le=LIMITS['text_answer'])


class NumberValidation(Model):
    min_value: str | None = Field(default=None, max_length=LIMITS['text_answer'])
    max_value: str | None = Field(default=None, max_length=LIMITS['text_answer'])
    integer_only: bool = False

    @field_validator('min_value', 'max_value')
    @classmethod
    def exact_decimal(cls, value):
        return canonical_decimal(value) if value is not None else None


class Rule(Model):
    op: Literal['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'any', 'all', 'exact']
    operand: str | Annotated[list[str], Field(max_length=LIMITS['options'])]
    target: str

    @field_validator('target')
    @classmethod
    def valid_target_format(cls, value):
        return value if value == 'END' else uuid_string(value)


class Question(Model):
    id: str
    kind: Literal['single', 'multiple', 'text', 'number'] = 'single'
    title: str = Field(max_length=LIMITS['question_title'])
    required: bool = False
    validation: dict = Field(default_factory=dict)
    options: list[Option] = Field(default_factory=list, max_length=LIMITS['options'])
    rules: list[Rule] = Field(default_factory=list, max_length=LIMITS['rules'])
    _id = field_validator('id')(uuid_string)

    @field_validator('options')
    @classmethod
    def unique_options(cls, value):
        if len({o.id for o in value}) != len(value):
            raise ValueError('选项ID重复')
        return value

    @model_validator(mode='after')
    def configuration_types(self):
        models = {'single': SingleValidation, 'multiple': MultipleValidation,
                  'text': TextValidation, 'number': NumberValidation}
        self.validation = models[self.kind].model_validate(self.validation).model_dump()
        for rule in self.rules:
            if self.kind == 'number':
                rule.operand = canonical_decimal(rule.operand)
            elif self.kind == 'single':
                rule.operand = uuid_string(rule.operand)
            elif self.kind == 'multiple':
                if not isinstance(rule.operand, list):
                    raise ValueError('多选规则操作数必须为选项ID数组')
                for item in rule.operand:
                    uuid_string(item)
        return self


class SurveyCreate(Model):
    title: str = Field(default='', max_length=LIMITS['survey_title'])
    description: str = Field(default='', max_length=LIMITS['description'])
    allow_anonymous: bool = False
    allow_repeat: bool = False
    deadline_at: datetime | None = None

    _deadline = field_validator('deadline_at', mode='before')(iso_deadline)


class Draft(SurveyCreate):
    revision: int = Field(ge=1, le=LIMITS['revision'])
    questions: list[Question] = Field(max_length=LIMITS['questions'])

    @field_validator('questions')
    @classmethod
    def unique_questions(cls, value):
        if len({q.id for q in value}) != len(value):
            raise ValueError('题目ID重复')
        return value


class Revision(Model):
    revision: int = Field(ge=1, le=LIMITS['revision'])


class DeadlineChange(Revision):
    deadline_at: datetime | None
    _deadline = field_validator('deadline_at', mode='before')(iso_deadline)

class Evaluate(Model):
    content_version: Literal[1]
    answers: dict[str, str | Annotated[list[str], Field(max_length=LIMITS['options'])] | None]

    @field_validator('content_version', mode='before')
    @classmethod
    def exact_version_type(cls, value):
        if type(value) is not int:
            raise ValueError('内容版本必须为整数')
        return value


class Submission(Evaluate):
    submission_key: str
    anonymous: bool = False
    _key = field_validator('submission_key')(uuid_string)
