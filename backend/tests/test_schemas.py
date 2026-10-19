import pytest
from pydantic import ValidationError

from app.schemas import CreateTrackingCardArgs, QueryBillArgs


def test_strict_tool_args():
    with pytest.raises(ValidationError):
        QueryBillArgs.model_validate({'limit': 1, 'unexpected': True})


def test_date_range_and_integer_limit():
    with pytest.raises(ValidationError):
        QueryBillArgs.model_validate({'start_date': '2026-10-01', 'end_date': '2026-09-01'})
    with pytest.raises(ValidationError):
        QueryBillArgs.model_validate({'limit': '10'})


def test_write_tool_is_constrained():
    with pytest.raises(ValidationError):
        CreateTrackingCardArgs.model_validate({'category': 'x', 'purpose': '随便写'})
