import pytest
from pydantic import ValidationError
from app.schemas import ResearchRequest


def test_request_trims_question():
    request = ResearchRequest(question="  Explain market risks clearly  ")
    assert request.question == "Explain market risks clearly"


def test_request_rejects_blank_question():
    with pytest.raises(ValidationError):
        ResearchRequest(question="        ")
