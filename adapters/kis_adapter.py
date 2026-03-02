from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_any,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config.secrets import SecretManager
from schemas.models import ApprovedOrderPlan, Side

_PAPER_BASE_URL = "https://openapivts.koreainvestment.com:29443"
_REAL_BASE_URL = "https://openapi.koreainvestment.com:9443"
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class KISAPIError(Exception):
    def __init__(self, msg_cd: str, msg1: str):
        self.msg_cd = msg_cd
        self.msg1 = msg1
        super().__init__(f"KIS API error ({msg_cd}): {msg1}")


def _is_retryable_http_status_error(exc: BaseException) -> bool:
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    return exc.response.status_code in _RETRYABLE_STATUS_CODES


class KISAdapter:
    def __init__(
        self,
        secret_manager: SecretManager,
        account_no: str,
        is_paper: bool = True,
    ) -> None:
        if len(account_no) < 8:
            raise ValueError("account_no는 최소 8자리여야 합니다")

        self._secret_manager = secret_manager
        self._account_no = account_no
        self._is_paper = is_paper

        self._cano = account_no[:8]
        self._acnt_prdt_cd = account_no[8:10] if len(account_no) >= 10 else "01"

        self._app_key = ""
        self._app_secret = ""
        self._base_url = ""
        self._client: httpx.Client | None = None

        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None
        self._token_lock = threading.Lock()

    def connect(self) -> None:
        app_key = self._secret_manager.get("KIS_APP_KEY")
        app_secret = self._secret_manager.get("KIS_APP_SECRET")
        if not app_key or not app_secret:
            raise ValueError("KIS_APP_KEY/KIS_APP_SECRET 시크릿이 필요합니다")

        env_base = self._secret_manager.get("KIS_BASE_URL")
        default_base = _PAPER_BASE_URL if self._is_paper else _REAL_BASE_URL

        self._app_key = app_key
        self._app_secret = app_secret
        self._base_url = env_base or default_base
        self._client = httpx.Client(base_url=self._base_url, timeout=10.0)

        self._ensure_access_token()

    def submit_order(
        self, plan: ApprovedOrderPlan, client_order_id: str | None = None
    ) -> dict[str, Any]:
        tr_id = (
            self._tr_id("VTTC0012U", "TTTC0802U")
            if plan.order.side == Side.BUY
            else self._tr_id("VTTC0011U", "TTTC0801U")
        )
        is_market = plan.order.order_type.upper() == "MARKET"
        quantity = str(int(plan.order.qty))
        if int(quantity) <= 0:
            raise ValueError("주문 수량은 양의 정수여야 합니다")

        price = (
            "0" if is_market or plan.order.price is None else str(int(plan.order.price))
        )
        payload = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "PDNO": plan.order.symbol,
            "ORD_DVSN": "01" if is_market else "00",
            "ORD_QTY": quantity,
            "ORD_UNPR": price,
            "EXCG_ID_DVSN_CD": "KRX",
            "SLL_TYPE": "",
            "CNDT_PRIC": "",
        }
        hashkey = self._get_hashkey(payload)
        data = self._request_json(
            method="POST",
            path="/uapi/domestic-stock/v1/trading/order-cash",
            tr_id=tr_id,
            json=payload,
            extra_headers={"hashkey": hashkey},
        )
        output = data.get("output", {})
        return {
            "odno": str(output.get("ODNO", "")),
            "orgno": str(output.get("KRX_FWDG_ORD_ORGNO", "")),
            "ord_tmd": str(output.get("ORD_TMD", "")),
            "client_order_id": client_order_id or "",
            "raw": data,
        }

    def get_order_status(self, odno: str | None = None) -> list[dict[str, Any]]:
        params = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
            "INQR_DVSN_1": "1",
            "INQR_DVSN_2": "0",
        }
        data = self._request_json(
            method="GET",
            path="/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl",
            tr_id=self._tr_id("VTTC8036R", "TTTC8036R"),
            params=params,
            retry_get=True,
        )
        rows = data.get("output1", [])
        result: list[dict[str, Any]] = []
        for row in rows:
            row_odno = str(row.get("odno", ""))
            if odno and row_odno != odno:
                continue
            side_code = str(row.get("sll_buy_dvsn_cd", ""))
            result.append(
                {
                    "odno": row_odno,
                    "symbol": str(row.get("pdno", "")),
                    "side": "SELL" if side_code == "01" else "BUY",
                    "ord_qty": int(row.get("ord_qty", 0) or 0),
                    "ord_unpr": float(row.get("ord_unpr", 0) or 0),
                    "ccld_qty": int(row.get("ccld_qty", 0) or 0),
                    "rmn_qty": int(row.get("rmn_qty", 0) or 0),
                    "psbl_qty": int(row.get("psbl_qty", 0) or 0),
                    "raw": row,
                }
            )
        return result

    def get_fills(self, date_str: str | None = None) -> list[dict[str, Any]]:
        query_date = date_str or datetime.now(tz=UTC).strftime("%Y%m%d")
        params = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "INQR_STRT_DT": query_date,
            "INQR_END_DT": query_date,
            "SLL_BUY_DVSN_CD": "00",
            "CCLD_DVSN": "01",
            "INQR_DVSN": "00",
            "EXCG_ID_DVSN_CD": "KRX",
        }
        data = self._request_json(
            method="GET",
            path="/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            tr_id=self._tr_id("VTTC0081R", "TTTC8001R"),
            params=params,
            retry_get=True,
        )
        rows = data.get("output1", [])
        result: list[dict[str, Any]] = []
        for row in rows:
            side_code = str(row.get("sll_buy_dvsn_cd", ""))
            result.append(
                {
                    "odno": str(row.get("odno", "")),
                    "symbol": str(row.get("pdno", "")),
                    "side": "SELL" if side_code == "01" else "BUY",
                    "ord_qty": int(row.get("ord_qty", 0) or 0),
                    "ccld_qty": int(row.get("ccld_qty", 0) or 0),
                    "ccld_unpr": float(row.get("ccld_unpr", 0) or 0),
                    "ccld_dttm": str(row.get("ccld_dttm", "")),
                    "raw": row,
                }
            )
        return result

    def get_balance(self) -> dict[str, Any]:
        params = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        data = self._request_json(
            method="GET",
            path="/uapi/domestic-stock/v1/trading/inquire-balance",
            tr_id=self._tr_id("VTTC8434R", "TTTC8434R"),
            params=params,
            retry_get=True,
        )
        positions = []
        for row in data.get("output1", []):
            positions.append(
                {
                    "symbol": str(row.get("pdno", "")),
                    "qty": int(row.get("hldg_qty", 0) or 0),
                    "avg_price": float(row.get("pchs_avg_pric", 0) or 0),
                    "unrealized_pnl": float(row.get("evlu_pfls_amt", 0) or 0),
                    "raw": row,
                }
            )
        raw_output2 = data.get("output2")
        account_row: dict[str, Any]
        if isinstance(raw_output2, list):
            account_row = raw_output2[0] if raw_output2 else {}
        elif isinstance(raw_output2, dict):
            account_row = raw_output2
        else:
            account_row = {}
        return {
            "positions": positions,
            "cash": float(account_row.get("dnca_tot_amt", 0) or 0),
            "total_value": float(account_row.get("tot_evlu_amt", 0) or 0),
            "raw": data,
        }

    def cancel_order(self, odno: str, orgno: str) -> dict[str, Any]:
        payload = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "KRX_FWDG_ORD_ORGNO": orgno,
            "ORGN_ODNO": odno,
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": "02",
            "ORD_QTY": "0",
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y",
            "EXCG_ID_DVSN_CD": "KRX",
        }
        hashkey = self._get_hashkey(payload)
        data = self._request_json(
            method="POST",
            path="/uapi/domestic-stock/v1/trading/order-rvsecncl",
            tr_id=self._tr_id("VTTC0013U", "TTTC0803U"),
            json=payload,
            extra_headers={"hashkey": hashkey},
        )
        return {"raw": data}

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _ensure_access_token(self) -> str:
        if not self._token_needs_refresh():
            return self._access_token or ""

        with self._token_lock:
            if not self._token_needs_refresh():
                return self._access_token or ""

            payload = {
                "grant_type": "client_credentials",
                "appkey": self._app_key,
                "appsecret": self._app_secret,
            }
            response = self._send_once(
                method="POST",
                path="/oauth2/tokenP",
                headers={"Content-Type": "application/json; charset=utf-8"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

            access_token = data.get("access_token")
            if not access_token:
                raise RuntimeError("KIS access_token 응답이 비어 있습니다")

            expires_at = self._parse_token_expiration(data)
            self._access_token = str(access_token)
            self._token_expires_at = expires_at
            return self._access_token

    def _token_needs_refresh(self) -> bool:
        if not self._access_token or not self._token_expires_at:
            return True
        return self._token_expires_at - datetime.now(tz=UTC) < timedelta(minutes=5)

    def _parse_token_expiration(self, data: dict[str, Any]) -> datetime:
        expired_str = data.get("access_token_token_expired")
        if isinstance(expired_str, str) and expired_str:
            try:
                dt = datetime.strptime(expired_str, "%Y-%m-%d %H:%M:%S")
                return dt.replace(tzinfo=UTC)
            except ValueError:
                pass

        expires_in = int(data.get("expires_in", 86400) or 86400)
        return datetime.now(tz=UTC) + timedelta(seconds=expires_in)

    def _headers(self, tr_id: str) -> dict[str, str]:
        token = self._ensure_access_token()
        return {
            "Content-Type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def _tr_id(self, paper_id: str, real_id: str) -> str:
        """모의투자/실거래 TR_ID 선택.

        Args:
            paper_id: 모의투자 TR_ID.
            real_id: 실거래 TR_ID.

        Returns:
            현재 모드에 맞는 TR_ID.
        """
        return paper_id if self._is_paper else real_id

    def _get_hashkey(self, payload: dict[str, Any]) -> str:
        """KIS POST 요청용 hashkey 생성.

        KIS API는 POST 요청 시 요청 본문의 해시키를 헤더에 포함해야 한다.
        /uapi/hashkey 엔드포인트로 해시키를 발급받는다.
        """
        response = self._send_once(
            method="POST",
            path="/uapi/hashkey",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "appkey": self._app_key,
                "appsecret": self._app_secret,
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        return str(data.get("HASH", ""))

    @retry(
        retry=retry_any(
            retry_if_exception_type(httpx.RequestError),
            retry_if_exception(_is_retryable_http_status_error),
        ),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _send_get_with_retry(
        self,
        path: str,
        headers: dict[str, str],
        params: dict[str, Any] | None,
    ) -> httpx.Response:
        response = self._send_once(
            method="GET",
            path=path,
            headers=headers,
            params=params,
        )
        if response.status_code in _RETRYABLE_STATUS_CODES:
            raise httpx.HTTPStatusError(
                "retryable status",
                request=response.request,
                response=response,
            )
        return response

    def _request_json(
        self,
        method: str,
        path: str,
        tr_id: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        retry_get: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = self._headers(tr_id)
        if extra_headers:
            headers.update(extra_headers)
        if method == "GET" and retry_get:
            response = self._send_get_with_retry(
                path=path, headers=headers, params=params
            )
        else:
            response = self._send_once(
                method=method,
                path=path,
                headers=headers,
                params=params,
                json=json,
            )

        response.raise_for_status()
        data = response.json()
        if str(data.get("rt_cd", "")) != "0":
            raise KISAPIError(str(data.get("msg_cd", "")), str(data.get("msg1", "")))
        return data

    def _send_once(
        self,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        if self._client is None:
            raise RuntimeError("KISAdapter.connect()가 먼저 호출되어야 합니다")
        try:
            return self._client.request(
                method=method,
                url=path,
                headers=headers,
                params=params,
                json=json,
            )
        finally:
            time.sleep(0.5)
