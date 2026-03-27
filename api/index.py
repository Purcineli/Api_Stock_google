from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import httpx
from bs4 import BeautifulSoup
import re
from typing import Optional
import json
 
'''app = FastAPI()'''


app = FastAPI(
    title="Google Stock Scraper API",
    description="Extrai dados de ações diretamente da pesquisa do Google",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)
 
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
 
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
 
 
def clean_number(text: str) -> Optional[float]:
    """Converte string com vírgula/ponto para float."""
    if not text:
        return None
    cleaned = re.sub(r"[^\d,.\-]", "", text.strip())
    cleaned = cleaned.replace(",", ".")
    # Se houver mais de um ponto, considera que vírgula era separador decimal
    parts = cleaned.split(".")
    if len(parts) > 2:
        cleaned = "".join(parts[:-1]) + "." + parts[-1]
    try:
        return float(cleaned)
    except ValueError:
        return None
 
 
async def fetch_google_finance(ticker: str) -> dict:
    url = f"https://www.google.com/search?q={ticker}+stock&hl=pt-BR&gl=BR"
    
    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
        response = await client.get(url, headers=HEADERS)
        response.raise_for_status()
 
    soup = BeautifulSoup(response.text, "html.parser")
    data = {"ticker": ticker.upper(), "source": "Google Search", "raw_sections": []}
 
    # --- Preço atual ---
    price_el = soup.select_one("[data-last-price]")
    if price_el:
        data["price"] = clean_number(price_el.get("data-last-price", ""))
        data["currency"] = price_el.get("data-currency-code", "BRL")
    else:
        # fallback: procura elemento com classe típica de preço
        for sel in ["YMlKec fxKbKc", "IsqQVc fw-price-ltr", "zzDege"]:
            el = soup.find(class_=sel.split())
            if el:
                data["price"] = clean_number(el.get_text())
                break
 
    # --- Variação ---
    change_el = soup.select_one("[data-last-normal-change]")
    if change_el:
        data["change"] = clean_number(change_el.get("data-last-normal-change", ""))
        data["change_percent"] = clean_number(
            change_el.get("data-last-normal-change-percent", "")
        )
    
    # fallback variação por texto
    if "change" not in data:
        for el in soup.find_all(class_=re.compile(r"(IsqQVc|JwB6zf|V7M2Zf)")):
            text = el.get_text(strip=True)
            if "%" in text:
                num = clean_number(text.replace("%", ""))
                if num is not None:
                    data["change_percent"] = num
                    break
 
    # --- Nome da empresa ---
    name_candidates = [
        soup.select_one("div.PZPZlf"),
        soup.select_one("h3.r"),
        soup.select_one("span.WTP52d"),
        soup.select_one("div.oPhL2e"),
    ]
    for el in name_candidates:
        if el and el.get_text(strip=True):
            data["name"] = el.get_text(strip=True)
            break
 
    # --- Dados do painel de finanças (Knowledge Panel) ---
    # Tabelas com dados como Abertura, Máx, Mín, Volume, etc.
    kv_pairs = {}
    
    # Tenta selecionar pares chave-valor do knowledge panel
    rows = soup.select("table.sldiIf tr, div.WisKIc, div.iyjjgb")
    for row in rows:
        tds = row.find_all("td")
        if len(tds) >= 2:
            key = tds[0].get_text(strip=True).lower()
            val = tds[1].get_text(strip=True)
            kv_pairs[key] = val
 
    # Mapeamento PT/EN para campos padronizados
    field_map = {
        "abertura": "open",
        "open": "open",
        "máx.": "high",
        "high": "high",
        "mín.": "low",
        "low": "low",
        "vol.": "volume",
        "volume": "volume",
        "cap. de mercado": "market_cap",
        "market cap": "market_cap",
        "p/l": "pe_ratio",
        "p/e": "pe_ratio",
        "div. yield": "dividend_yield",
        "dividend yield": "dividend_yield",
        "máx. 52 sem.": "week_52_high",
        "52-week high": "week_52_high",
        "mín. 52 sem.": "week_52_low",
        "52-week low": "week_52_low",
        "vol. médio": "avg_volume",
        "avg volume": "avg_volume",
    }
 
    finance_data = {}
    for raw_key, val in kv_pairs.items():
        for pt_key, en_key in field_map.items():
            if pt_key in raw_key:
                finance_data[en_key] = val
                break
 
    if finance_data:
        data["details"] = finance_data
 
    # --- Scraping alternativo via spans com data-attrid ---
    for el in soup.find_all(attrs={"data-attrid": True}):
        attrid = el.get("data-attrid", "")
        text = el.get_text(separator=" ", strip=True)
        if attrid and text:
            data["raw_sections"].append({"attrid": attrid, "text": text[:200]})
 
    # Extrai blocos de resumo financeiro (divs com múltiplas linhas de dados)
    summary_blocks = soup.select("div.gyZGIc, div.HiIbD, div.EqCGIb")
    summary_texts = []
    for block in summary_blocks:
        t = block.get_text(separator="|", strip=True)
        if t:
            summary_texts.append(t)
    if summary_texts:
        data["summary_raw"] = summary_texts[:5]
 
    # Tenta extrair Exchange (bolsa)
    exchange_patterns = [
        r"\bB3\b", r"\bBOVESPA\b", r"\bNYSE\b", r"\bNASDAQ\b",
        r"\bBMFBOVESPA\b", r"\bSP500\b"
    ]
    full_text = soup.get_text()
    for pat in exchange_patterns:
        m = re.search(pat, full_text, re.IGNORECASE)
        if m:
            data["exchange"] = m.group().upper()
            break
 
    return data
 
 
@app.get("/", tags=["Info"])
async def root():
    return {
        "message": "Google Stock Scraper API",
        "docs": "/docs",
        "usage": "GET /stock/{ticker}",
        "examples": ["/stock/PETR4", "/stock/VALE3", "/stock/AAPL", "/stock/GOOGL"],
    }
 
 
@app.get("/stock/{ticker}", tags=["Stock"])
async def get_stock(
    ticker: str,
    raw: bool = Query(False, description="Incluir dados brutos na resposta"),
):
    """
    Busca dados de uma ação diretamente da pesquisa do Google.
 
    - **ticker**: Código da ação (ex: PETR4, VALE3, AAPL, GOOGL)
    - **raw**: Se True, inclui seções brutas extraídas da página
    """
    try:
        data = await fetch_google_finance(ticker.upper())
        if not raw:
            data.pop("raw_sections", None)
            data.pop("summary_raw", None)
        return data
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Erro ao acessar Google: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
 
 
@app.get("/stocks", tags=["Stock"])
async def get_multiple_stocks(
    tickers: str = Query(..., description="Tickers separados por vírgula. Ex: PETR4,VALE3,AAPL"),
):
    """
    Busca dados de múltiplas ações de uma vez.
    Máximo de 5 tickers por requisição.
    """
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    if len(ticker_list) > 5:
        raise HTTPException(status_code=400, detail="Máximo de 5 tickers por requisição.")
    
    results = {}
    for ticker in ticker_list:
        try:
            results[ticker] = await fetch_google_finance(ticker)
            results[ticker].pop("raw_sections", None)
            results[ticker].pop("summary_raw", None)
        except Exception as e:
            results[ticker] = {"error": str(e)}
    
    return results