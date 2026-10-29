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


class QuestionSource(Model):
    question_id: str = Field(pattern='^[a-f0-9]{24}$')
    question_version_id: str = Field(pattern='^[a-f0-9]{24}$')
    payload_hash: str = Field(pattern='^[a-f0-9]{64}$')


class Question(Model):
    id: str
    kind: Literal['single', 'multiple', 'text', 'number'] = 'single'
    title: str = Field(max_length=LIMITS['question_title'])
    required: bool = False
    validation: dict = Field(default_factory=dict)
    options: list[Option] = Field(default_factory=list, max_length=LIMITS['options'])
    rules: list[Rule] = Field(default_factory=list, max_length=LIMITS['rules'])
    source: QuestionSource | None = None
    _id = field_validator('id')(uuid_string)

    @model_validator(mode='before')
    @classmethod
    def no_null_source(cls, value):
        if isinstance(value, dict) and 'source' in value and value['source'] is None:
            raise ValueError('来源不能设为空')
        return value

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


class QuestionPayload(Model):
    kind: Literal['single', 'multiple', 'text', 'number'] = 'single'
    title: str = Field(max_length=LIMITS['question_title'])
    required: bool = False
    validation: dict = Field(default_factory=dict)
    options: list[Option] = Field(default_factory=list, max_length=LIMITS['options'])

    @model_validator(mode='after')
    def normalize(self):
        question = Question.model_validate({'id': '00000000-0000-4000-8000-000000000001', **self.model_dump()})
        self.validation = question.validation
        return self


class QuestionCreate(Model):
    label: str = Field(min_length=1, max_length=200)
    creation_key: str
    _key = field_validator('creation_key')(uuid_string)

    @field_validator('label')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('题目标签不能为空')
        return value.strip()


class VersionCreate(Model):
    creation_key: str
    operation: Literal['create', 'derive'] = 'create'
    parent_version_id: str | None = Field(default=None, pattern='^[a-f0-9]{24}$')
    restored_from_version_id: None = None
    payload: QuestionPayload | None = None
    _key = field_validator('creation_key')(uuid_string)

    @model_validator(mode='after')
    def creation_shape(self):
        if self.operation == 'create':
            if self.parent_version_id is not None or self.payload is None:
                raise ValueError('首版需要payload且不能指定父版本')
        elif self.parent_version_id is None or 'payload' in self.model_fields_set:
            raise ValueError('派生需指定父版本且不能提供payload')
        return self


class VersionDraft(Revision):
    payload: QuestionPayload


class QuestionImport(Revision):
    instance_id: str
    question_id: str = Field(pattern='^[a-f0-9]{24}$')
    question_version_id: str = Field(pattern='^[a-f0-9]{24}$')
    _instance = field_validator('instance_id')(uuid_string)
