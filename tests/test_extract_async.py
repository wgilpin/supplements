import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from google.genai.errors import APIError
from skg.extract import extract_claims_async, extract_claims_batch
from skg.schema import Claim

# Mock Claim response structure
def _claim_dict(**kw):
    base = dict(compound="taurine", target=None, effect="anxiety",
               direction="decreases", dose_text="", cohort_text="",
               model="human RCT", source_quote="Taurine decreases anxiety.")
    base.update(kw)
    return base

@pytest.mark.anyio
async def test_extract_claims_async_success():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.parsed = [Claim(**_claim_dict())]
    
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
    
    with patch("skg.extract._get_client", return_value=mock_client):
        res = await extract_claims_async("Taurine decreases anxiety.")
        assert len(res) == 1
        assert res[0].compound == "taurine"
        assert res[0].effect == "anxiety"

@pytest.mark.anyio
async def test_extract_claims_async_retry_on_429():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.parsed = [Claim(**_claim_dict())]
    
    api_error = APIError(code=429, response_json={"error": {"message": "Resource exhausted"}})
    
    mock_generate = AsyncMock()
    mock_generate.side_effect = [api_error, mock_response]
    mock_client.aio.models.generate_content = mock_generate
    
    with patch("skg.extract._get_client", return_value=mock_client), \
         patch("asyncio.sleep", AsyncMock()) as mock_sleep:
        res = await extract_claims_async("Taurine decreases anxiety.")
        assert len(res) == 1
        assert mock_generate.call_count == 2
        mock_sleep.assert_called_once_with(2)  # (2**0) + 1 = 2

@pytest.mark.anyio
async def test_extract_claims_batch_pacing():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.parsed = [Claim(**_claim_dict())]
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
    
    records = [
        {"pmid": "1", "abstract": "Taurine decreases anxiety."},
        {"pmid": "2", "abstract": "Taurine decreases anxiety."},
        {"pmid": "3", "abstract": "Taurine decreases anxiety."}
    ]
    
    with patch("skg.extract._get_client", return_value=mock_client), \
         patch("asyncio.sleep", AsyncMock()) as mock_sleep:
        res = await extract_claims_batch(records)
        assert len(res) == 3
        
        sleep_args = [call.args[0] for call in mock_sleep.call_args_list]
        assert 1.0 in sleep_args
        assert 2.0 in sleep_args
        assert len(sleep_args) == 2
