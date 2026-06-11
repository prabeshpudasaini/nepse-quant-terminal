from __future__ import annotations

import re
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

import requests as _requests
from bs4 import BeautifulSoup

from backend.quant_pro.data_scrapers.gold_silver_ingestion import (
    store_nepal_metals_prices,
    get_latest_nepal_metals,
)

# ── Nepali → English translation cache ──────────────────────────────────────
_translation_cache: dict[str, str] = {}


def _translate_nepali(text: str) -> str:
    """Translate Nepali text to English using Google Translate. Cached."""
    if not text or not any(ord(c) > 127 for c in text[:10]):
        return text
    if text in _translation_cache:
        return _translation_cache[text]
    try:
        from deep_translator import GoogleTranslator
        result = GoogleTranslator(source='ne', target='en').translate(text[:200])
        if result:
            _translation_cache[text] = result
            return result
    except Exception:
        pass
    return text

def _translate_batch(texts: list[str]) -> list[str]:
    """Translate a batch of Nepali texts. Uses single API call where possible."""
    to_translate = []
    indices = []
    results = list(texts)
    for i, t in enumerate(texts):
        if t and any(ord(c) > 127 for c in t[:10]) and t not in _translation_cache:
            to_translate.append(t[:200])
            indices.append(i)
        elif t in _translation_cache:
            results[i] = _translation_cache[t]
    if to_translate:
        try:
            from deep_translator import GoogleTranslator
            translated = GoogleTranslator(source='ne', target='en').translate_batch(to_translate)
            for idx, orig, trans in zip(indices, to_translate, translated):
                if trans:
                    _translation_cache[texts[idx]] = trans
                    results[idx] = trans
        except Exception:
            pass
    return results


def _extract_decimal_price(text: str) -> Optional[Decimal]:
    cleaned = re.sub(r"[^\d]", "", str(text or ""))
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except Exception:
        return None


def _fetch_nrb_forex_rates(codes: tuple[str, ...] = ("USD", "EUR", "GBP", "INR", "CNY", "JPY")) -> list[dict]:
    """Fetch a compact set of NRB forex rows."""
    api_url = "https://www.nrb.org.np/api/forex/v1/rates"
    code_set = {c.upper() for c in codes}
    try:
        today = datetime.utcnow().date()
        from_date = today - timedelta(days=7)
        response = _requests.get(
            api_url,
            params={
                "from": from_date.strftime("%Y-%m-%d"),
                "to": today.strftime("%Y-%m-%d"),
                "per_page": 50,
                "page": 1,
            },
            headers={
                "Accept": "application/json",
                "User-Agent": "Nepse-TUI/1.0",
            },
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("status", {}).get("code") != 200:
            return []

        latest_by_code: dict[str, dict] = {}
        previous_by_code: dict[str, dict] = {}
        for rate_data in data.get("data", {}).get("payload", []):
            date_str = rate_data.get("date")
            try:
                rate_date = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
            except Exception:
                rate_date = datetime.utcnow()
            for rate in rate_data.get("rates", []):
                currency = rate.get("currency", {})
                code = str(currency.get("iso3") or "").upper()
                if code not in code_set:
                    continue
                candidate = {
                    "currency_code": code,
                    "currency_name": currency.get("name", code),
                    "buy_rate": float(rate.get("buy", 0) or 0),
                    "sell_rate": float(rate.get("sell", 0) or 0),
                    "unit": int(currency.get("unit", 1) or 1),
                    "date": rate_date,
                    "source": "NRB",
                }
                current = latest_by_code.get(code)
                if current is None or candidate["date"] > current["date"]:
                    if current is not None and current["date"] != candidate["date"]:
                        previous = previous_by_code.get(code)
                        if previous is None or current["date"] > previous["date"]:
                            previous_by_code[code] = current
                    latest_by_code[code] = candidate
                elif candidate["date"] != current["date"]:
                    previous = previous_by_code.get(code)
                    if previous is None or candidate["date"] > previous["date"]:
                        previous_by_code[code] = candidate
        rows = []
        for code in codes:
            row = latest_by_code.get(code)
            if not row:
                continue
            previous = previous_by_code.get(code) or {}
            prev_buy = float(previous.get("buy_rate") or 0)
            change_rate = (row["buy_rate"] - prev_buy) if prev_buy > 0 else None
            change_pct = ((row["buy_rate"] - prev_buy) / prev_buy * 100) if prev_buy > 0 else None
            enriched = dict(row)
            enriched["previous_buy_rate"] = prev_buy if prev_buy > 0 else None
            enriched["change_rate"] = change_rate
            enriched["change_pct"] = change_pct
            rows.append(enriched)
        return rows
    except Exception:
        return []


def _fetch_gold_silver_prices() -> Optional[dict]:
    """Fetch Nepal gold and silver prices from FENEGOSIDA."""
    url = "https://fenegosida.org/"
    try:
        response = _requests.get(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "Nepse-TUI/1.0",
            },
            timeout=20,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        text = soup.get_text("\n")

        gold_tola = None
        silver_tola = None
        date_bs = None

        date_pattern = (
            r"(\d{1,2}\s+"
            r"(?:Baishakh|Jestha|Ashadh|Shrawan|Bhadra|Ashwin|Kartik|Mangsir|Poush|Magh|Falgun|Chaitra)"
            r"\s+\d{4})"
        )
        date_match = re.search(date_pattern, text, re.IGNORECASE)
        if date_match:
            date_bs = re.sub(r"\s+", " ", date_match.group(1)).strip()

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        current_section = None

        for i, line in enumerate(lines):
            line_lower = line.lower()
            window = lines[i:i + 6]
            window_text = " ".join(window).lower()
            if "fine gold" in line_lower and "per 1 tola" in window_text and not gold_tola:
                for candidate in window:
                    price = _extract_decimal_price(candidate)
                    if price and price > 100000:
                        gold_tola = price
                        break
            if line_lower == "silver" and "per 1 tola" in window_text and not silver_tola:
                for candidate in window:
                    price = _extract_decimal_price(candidate)
                    if price and 1000 < price < 10000:
                        silver_tola = price
                        break

        for i, line in enumerate(lines):
            line_lower = line.lower()
            if "gold" in line_lower and "silver" not in line_lower:
                current_section = "gold"
            elif "silver" in line_lower:
                current_section = "silver"

            price_matches = re.findall(r"रु\s*([\d,]+)", line) or re.findall(r"([\d,]{5,})", line)
            for price_str in price_matches:
                price = _extract_decimal_price(price_str)
                if not price or price <= 1000:
                    continue
                context = " ".join(lines[max(0, i - 2): i + 3]).lower()
                if "tola" in context or "तोला" in context:
                    if current_section == "gold" and not gold_tola:
                        gold_tola = price
                    elif current_section == "silver" and not silver_tola:
                        silver_tola = price

        if not gold_tola and not silver_tola:
            for table in soup.find_all("table"):
                for row in table.find_all("tr"):
                    cells = row.find_all(["td", "th"])
                    row_text = " ".join(cell.get_text(" ", strip=True) for cell in cells).lower()
                    if "gold" in row_text or "सुन" in row_text:
                        for cell in cells:
                            price = _extract_decimal_price(cell.get_text(" ", strip=True))
                            if price and price > 100000 and not gold_tola:
                                gold_tola = price
                    if "silver" in row_text or "चाँदी" in row_text:
                        for cell in cells:
                            price = _extract_decimal_price(cell.get_text(" ", strip=True))
                            if price and price > 1000 and not silver_tola:
                                silver_tola = price

        if not gold_tola and not silver_tola:
            return None

        gold_val = float(gold_tola or 0)
        silver_val = float(silver_tola or 0)

        # ── Persist to macro_indicators so CHG% can be computed ──────────────
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        try:
            from backend.quant_pro.database import get_db_path as _get_db_path
            store_nepal_metals_prices(
                gold_npr_per_tola=gold_val,
                silver_npr_per_tola=silver_val,
                date_str=today_str,
                db_path=str(_get_db_path()),
            )
        except Exception:
            pass  # persistence failure must not break display

        # ── Load CHG% from stored history ─────────────────────────────────────
        gold_chg_pct = None
        silver_chg_pct = None
        gold_chg_abs = None
        silver_chg_abs = None
        try:
            from backend.quant_pro.database import get_db_path as _get_db_path2
            hist = get_latest_nepal_metals(db_path=str(_get_db_path2()))
            if hist.get("date") == today_str:
                gold_chg_pct = hist.get("gold_chg_pct")
                silver_chg_pct = hist.get("silver_chg_pct")
                gold_chg_abs = hist.get("gold_chg_abs")
                silver_chg_abs = hist.get("silver_chg_abs")
        except Exception:
            pass

        return {
            "gold_per_tola": gold_val,
            "silver_per_tola": silver_val,
            "gold_chg_pct": gold_chg_pct,
            "silver_chg_pct": silver_chg_pct,
            "gold_chg_abs": gold_chg_abs,
            "silver_chg_abs": silver_chg_abs,
            "date": datetime.utcnow(),
            "date_bs": date_bs,
            "source": "FENEGOSIDA",
        }
    except Exception:
        return None


def _fetch_yahoo_futures_price(symbol: str, label: str) -> Optional[dict]:
    """Fetch a lightweight global futures quote from Yahoo Finance."""
    try:
        response = _requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": "5d", "interval": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        result = (payload.get("chart", {}).get("result") or [None])[0]
        if not result:
            return None
        meta = result.get("meta") or {}
        price = float(meta.get("regularMarketPrice") or 0)
        prev = meta.get("previousClose")
        if prev is None:
            prev = meta.get("chartPreviousClose")
        prev = float(prev or 0)
        pct = ((price - prev) / prev * 100) if prev > 0 else None
        change = (price - prev) if prev > 0 else None
        return {
            "label": label,
            "value": price,
            "unit": meta.get("currency", "USD"),
            "change": change,
            "change_pct": pct,
            "source": "Yahoo Finance",
        }
    except Exception:
        return None


def _fetch_noc_fuel_prices() -> Optional[dict]:
    """Fetch latest Nepal Oil Corporation retail prices."""
    try:
        response = _requests.get(
            "https://noc.org.np/retailprice",
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "Mozilla/5.0",
            },
            timeout=20,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        table = soup.find("table", class_="table")
        if not table:
            return None

        headers = [th.get_text(" ", strip=True).lower() for th in table.find("tr").find_all(["th", "td"])]
        col_map: dict[str, int] = {}
        for idx, header in enumerate(headers):
            if "date" in header and "time" not in header:
                col_map["date"] = idx
            elif "petrol" in header or "ms" in header:
                col_map["petrol"] = idx
            elif "diesel" in header or "hsd" in header:
                col_map["diesel"] = idx
            elif "kerosene" in header or "sko" in header:
                col_map["kerosene"] = idx
            elif "lpg" in header:
                col_map["lpg"] = idx

        best_cells = None
        best_key = None
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) < 5:
                continue
            first_cell = cells[0].get_text(" ", strip=True)
            if "प्रेस" in first_cell.lower():
                continue
            match = re.search(r"(\d{4})\.(\d{1,2})\.(\d{1,2})", first_cell)
            if not match:
                continue
            sort_key = tuple(int(x) for x in match.groups())
            if best_key is None or sort_key > best_key:
                best_key = sort_key
                best_cells = cells

        if not best_cells:
            return None

        def _cell_price(key: str) -> float:
            idx = col_map.get(key)
            if idx is None or idx >= len(best_cells):
                return 0.0
            raw = re.sub(r"[^\d.]", "", best_cells[idx].get_text(" ", strip=True))
            try:
                return float(raw) if raw else 0.0
            except Exception:
                return 0.0

        return {
            "date_bs": best_cells[col_map["date"]].get_text(" ", strip=True) if "date" in col_map else "",
            "petrol": _cell_price("petrol"),
            "diesel": _cell_price("diesel"),
            "kerosene": _cell_price("kerosene"),
            "lpg": _cell_price("lpg"),
            "source": "NOC",
        }
    except Exception:
        return None
