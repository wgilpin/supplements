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


@pytest.mark.anyio
async def test_extract_claims_async_normalization():
    mock_client = MagicMock()
    mock_response = MagicMock()
    
    # 1. Quote with punctuation/casing differences should be kept
    mock_response.parsed = [Claim(**_claim_dict(
        source_quote="taurine a key compound decreases anxiety"
    ))]
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
    
    with patch("skg.extract._get_client", return_value=mock_client):
        res = await extract_claims_async("Taurine, a key compound; decreases anxiety.")
        assert len(res) == 1

    # 2. Quote with actual word mismatch should be dropped
    mock_response.parsed = [Claim(**_claim_dict(
        source_quote="Taurine increases anxiety."
    ))]
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
    
    with patch("skg.extract._get_client", return_value=mock_client):
        res = await extract_claims_async("Taurine decreases anxiety.")
        assert len(res) == 0

    # 3. Quote with Greek letter mapped to name should be kept
    mock_response.parsed = [Claim(**_claim_dict(
        source_quote="Treatment with low-dose gamma-irradiation."
    ))]
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
    
    with patch("skg.extract._get_client", return_value=mock_client):
        res = await extract_claims_async("Treatment with low-dose γ-irradiation.")
        assert len(res) == 1

    # 4. Quote with minor symbol mismatches and dropped letters (fuzzy match) should be kept
    mock_response.parsed = [Claim(**_claim_dict(
        source_quote="Treatment with quercetin (DEN † QR) or low-dose -irradiation."
    ))]
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
    
    with patch("skg.extract._get_client", return_value=mock_client):
        res = await extract_claims_async("Treatment with quercetin (DEN + QR) or low-dose γ-irradiation.")
        assert len(res) == 1

