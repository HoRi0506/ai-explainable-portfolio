from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from adapters.kis_adapter import KISAPIError, KISAdapter
from config.secrets import SecretManager
from schemas.models import (
    ApprovedOrderPlan,
    BrokerOrder,
    OrderSizing,
    Side,
    TradingMode,
)


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.request = httpx.Request("GET", "https://example.com")

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "http error", request=self.request, response=self
            )


@pytest.fixture
def secret_manager() -> SecretManager:
    manager = MagicMock(spec=SecretManager)
    manager.get.side_effect = lambda key: {
        "KIS_APP_KEY": "test-app-key",
        "KIS_APP_SECRET": "test-app-secret",
        "KIS_BASE_URL": "https://openapivts.koreainvestment.com:29443",
    }.get(key)
    return manager


@pytest.fixture
def sample_plan() -> ApprovedOrderPlan:
    return ApprovedOrderPlan(
        trace_id=uuid4(),
        mode=TradingMode.PAPER,
        sizing=OrderSizing(qty=10, notional=700000.0, weight_pct=7.0),
        risk_checks=[],
        order=BrokerOrder(symbol="005930", side=Side.BUY, qty=10, price=70000.0),
    )


def test_token_acquisition_is_cached(secret_manager: SecretManager) -> None:
    mock_client = MagicMock()
    mock_client.request.side_effect = [
        FakeResponse(
            {
                "access_token": "token-1",
                "access_token_token_expired": "2099-12-31 23:59:59",
                "expires_in": 86400,
            }
        ),
        FakeResponse({"rt_cd": "0", "output1": []}),
    ]

    with patch("adapters.kis_adapter.httpx.Client", return_value=mock_client):
        adapter = KISAdapter(secret_manager=secret_manager, account_no="1234567801")
        adapter.connect()
        _ = adapter.get_order_status()
        adapter.close()

    assert mock_client.request.call_count == 2


def test_token_refresh_when_expiring(secret_manager: SecretManager) -> None:
    mock_client = MagicMock()
    mock_client.request.side_effect = [
        FakeResponse(
            {
                "access_token": "token-1",
                "access_token_token_expired": "2099-12-31 23:59:59",
                "expires_in": 86400,
            }
        ),
        FakeResponse(
            {
                "access_token": "token-2",
                "access_token_token_expired": "2099-12-31 23:59:59",
                "expires_in": 86400,
            }
        ),
        FakeResponse({"rt_cd": "0", "output1": []}),
    ]

    with patch("adapters.kis_adapter.httpx.Client", return_value=mock_client):
        adapter = KISAdapter(secret_manager=secret_manager, account_no="1234567801")
        adapter.connect()
        adapter._token_expires_at = datetime.now(tz=UTC) + timedelta(minutes=4)
        _ = adapter.get_order_status()

    first_call = mock_client.request.call_args_list[0].kwargs
    second_call = mock_client.request.call_args_list[1].kwargs
    assert first_call["url"] == "/oauth2/tokenP"
    assert second_call["url"] == "/oauth2/tokenP"


@pytest.mark.parametrize(
    ("side", "expected_tr_id"),
    [(Side.BUY, "VTTC0012U"), (Side.SELL, "VTTC0011U")],
)
def test_submit_order_buy_sell(
    secret_manager: SecretManager,
    sample_plan: ApprovedOrderPlan,
    side: Side,
    expected_tr_id: str,
) -> None:
    plan_dict = sample_plan.model_dump()
    plan_dict["order"]["side"] = side
    plan = ApprovedOrderPlan(**plan_dict)

    mock_client = MagicMock()
    mock_client.request.side_effect = [
        FakeResponse({"access_token": "token-1", "expires_in": 86400}),
        FakeResponse({"HASH": "testhash123"}),
        FakeResponse(
            {
                "rt_cd": "0",
                "output": {
                    "ODNO": "0000117",
                    "KRX_FWDG_ORD_ORGNO": "91252",
                    "ORD_TMD": "093001",
                },
            }
        ),
    ]

    with patch("adapters.kis_adapter.httpx.Client", return_value=mock_client):
        adapter = KISAdapter(secret_manager=secret_manager, account_no="1234567801")
        adapter.connect()
        result = adapter.submit_order(plan)

    submit_call = mock_client.request.call_args_list[2].kwargs
    assert submit_call["headers"]["tr_id"] == expected_tr_id
    assert submit_call["headers"]["hashkey"] == "testhash123"
    assert submit_call["json"]["CANO"] == "12345678"
    assert submit_call["json"]["ACNT_PRDT_CD"] == "01"
    assert result["odno"] == "0000117"
    assert result["orgno"] == "91252"


def test_get_order_status(secret_manager: SecretManager) -> None:
    mock_client = MagicMock()
    mock_client.request.side_effect = [
        FakeResponse({"access_token": "token-1", "expires_in": 86400}),
        FakeResponse(
            {
                "rt_cd": "0",
                "output1": [
                    {
                        "odno": "1001",
                        "pdno": "005930",
                        "sll_buy_dvsn_cd": "02",
                        "ord_qty": "10",
                        "ord_unpr": "70000",
                        "ccld_qty": "3",
                        "rmn_qty": "7",
                        "psbl_qty": "7",
                    }
                ],
            }
        ),
    ]

    with patch("adapters.kis_adapter.httpx.Client", return_value=mock_client):
        adapter = KISAdapter(secret_manager=secret_manager, account_no="1234567801")
        adapter.connect()
        rows = adapter.get_order_status("1001")

    assert len(rows) == 1
    assert rows[0]["side"] == "BUY"
    assert rows[0]["ccld_qty"] == 3


def test_get_fills(secret_manager: SecretManager) -> None:
    mock_client = MagicMock()
    mock_client.request.side_effect = [
        FakeResponse({"access_token": "token-1", "expires_in": 86400}),
        FakeResponse(
            {
                "rt_cd": "0",
                "output1": [
                    {
                        "odno": "1001",
                        "pdno": "005930",
                        "sll_buy_dvsn_cd": "01",
                        "ord_qty": "10",
                        "ccld_qty": "10",
                        "ccld_unpr": "71000",
                        "ccld_dttm": "20260302093001",
                    }
                ],
            }
        ),
    ]

    with patch("adapters.kis_adapter.httpx.Client", return_value=mock_client):
        adapter = KISAdapter(secret_manager=secret_manager, account_no="1234567801")
        adapter.connect()
        rows = adapter.get_fills("20260302")

    assert rows[0]["side"] == "SELL"
    assert rows[0]["ccld_qty"] == 10


def test_get_balance(secret_manager: SecretManager) -> None:
    mock_client = MagicMock()
    mock_client.request.side_effect = [
        FakeResponse({"access_token": "token-1", "expires_in": 86400}),
        FakeResponse(
            {
                "rt_cd": "0",
                "output1": [
                    {
                        "pdno": "005930",
                        "hldg_qty": "20",
                        "pchs_avg_pric": "69000",
                        "evlu_pfls_amt": "15000",
                    }
                ],
                "output2": [{"dnca_tot_amt": "1200000", "tot_evlu_amt": "2600000"}],
            }
        ),
    ]

    with patch("adapters.kis_adapter.httpx.Client", return_value=mock_client):
        adapter = KISAdapter(secret_manager=secret_manager, account_no="1234567801")
        adapter.connect()
        balance = adapter.get_balance()

    assert balance["cash"] == 1200000.0
    assert balance["total_value"] == 2600000.0
    assert balance["positions"][0]["qty"] == 20


def test_cancel_order(secret_manager: SecretManager) -> None:
    mock_client = MagicMock()
    mock_client.request.side_effect = [
        FakeResponse({"access_token": "token-1", "expires_in": 86400}),
        FakeResponse({"HASH": "testhash123"}),
        FakeResponse({"rt_cd": "0", "msg_cd": "APBK0013", "msg1": "OK"}),
    ]

    with patch("adapters.kis_adapter.httpx.Client", return_value=mock_client):
        adapter = KISAdapter(secret_manager=secret_manager, account_no="1234567801")
        adapter.connect()
        result = adapter.cancel_order("1001", "91252")

    call_args = mock_client.request.call_args_list[2].kwargs
    assert call_args["headers"]["tr_id"] == "VTTC0013U"
    assert call_args["headers"]["hashkey"] == "testhash123"
    assert call_args["json"]["ORGN_ODNO"] == "1001"
    assert result["raw"]["rt_cd"] == "0"


def test_error_handling_raises_kis_api_error(secret_manager: SecretManager) -> None:
    mock_client = MagicMock()
    mock_client.request.side_effect = [
        FakeResponse({"access_token": "token-1", "expires_in": 86400}),
        FakeResponse({"rt_cd": "1", "msg_cd": "ERR01", "msg1": "bad request"}),
    ]

    with patch("adapters.kis_adapter.httpx.Client", return_value=mock_client):
        adapter = KISAdapter(secret_manager=secret_manager, account_no="1234567801")
        adapter.connect()
        with pytest.raises(KISAPIError, match="ERR01"):
            _ = adapter.get_order_status()


def test_submit_order_timeout_no_retry(
    secret_manager: SecretManager,
    sample_plan: ApprovedOrderPlan,
) -> None:
    mock_client = MagicMock()
    mock_client.request.side_effect = [
        FakeResponse({"access_token": "token-1", "expires_in": 86400}),
        FakeResponse({"HASH": "testhash123"}),
        httpx.TimeoutException("timeout"),
    ]

    with patch("adapters.kis_adapter.httpx.Client", return_value=mock_client):
        adapter = KISAdapter(secret_manager=secret_manager, account_no="1234567801")
        adapter.connect()
        with pytest.raises(httpx.TimeoutException):
            _ = adapter.submit_order(sample_plan)

    assert mock_client.request.call_count == 3


def test_submit_order_requests_hashkey(
    secret_manager: SecretManager,
    sample_plan: ApprovedOrderPlan,
) -> None:
    mock_client = MagicMock()
    mock_client.request.side_effect = [
        FakeResponse({"access_token": "token-1", "expires_in": 86400}),
        FakeResponse({"HASH": "testhash123"}),
        FakeResponse(
            {
                "rt_cd": "0",
                "output": {
                    "ODNO": "0000117",
                    "KRX_FWDG_ORD_ORGNO": "91252",
                    "ORD_TMD": "093001",
                },
            }
        ),
    ]

    with patch("adapters.kis_adapter.httpx.Client", return_value=mock_client):
        adapter = KISAdapter(secret_manager=secret_manager, account_no="1234567801")
        adapter.connect()
        _ = adapter.submit_order(sample_plan)

    hashkey_call = mock_client.request.call_args_list[1].kwargs
    submit_call = mock_client.request.call_args_list[2].kwargs

    assert hashkey_call["url"] == "/uapi/hashkey"
    assert hashkey_call["json"] == submit_call["json"]
    assert submit_call["url"] == "/uapi/domestic-stock/v1/trading/order-cash"
    assert submit_call["headers"]["hashkey"] == "testhash123"


def test_rate_limit_sleep_called(secret_manager: SecretManager) -> None:
    mock_client = MagicMock()
    mock_client.request.side_effect = [
        FakeResponse({"access_token": "token-1", "expires_in": 86400}),
        FakeResponse({"rt_cd": "0", "output1": []}),
    ]

    with patch("adapters.kis_adapter.time.sleep") as mock_sleep:
        with patch("adapters.kis_adapter.httpx.Client", return_value=mock_client):
            adapter = KISAdapter(secret_manager=secret_manager, account_no="1234567801")
            adapter.connect()
            _ = adapter.get_order_status()

    assert mock_sleep.call_count == 2
