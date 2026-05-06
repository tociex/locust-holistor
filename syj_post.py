"""
syj_post.py - Helper untuk POST events ke SYJ GeneXus app.

Compatible dengan main.py user (HolistorWorkflows TaskSet).

Usage di main.py:
    from syj_post import s1_exportar_lsd_chain
    
    @task(1)
    def libro_sueldo_digital(self):
        s1_exportar_lsd_chain(self, http_pool, syj_host, liquidacion_id=212)
"""

import json
import re
import time
import logging

import urllib3

logger = logging.getLogger(__name__)


# ===================== Token Extraction =====================

SAVE_JSON_RE = re.compile(
    r"gx\.ajax\.saveJsonResponse\('(.+?)'\);",
    re.DOTALL,
)


def extract_tokens_from_html(html):
    """Extract semua token GeneXus dari response HTML."""
    match = SAVE_JSON_RE.search(html)
    if not match:
        return None
    
    raw = match.group(1)
    try:
        decoded = raw.encode().decode('unicode_escape')
        data = json.loads(decoded)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _extract_via_regex(raw)
    
    hiddens = data.get('gxHiddens', {})
    
    return {
        'AJAX_SECURITY_TOKEN': hiddens.get('AJAX_SECURITY_TOKEN', ''),
        'GX_AUTH_WPD_EXPORTAR_LSD': hiddens.get('GX_AUTH_WPD_EXPORTAR_LSD', ''),
        'GX_AJAX_KEY': hiddens.get('GX_AJAX_KEY', ''),
        'GX_AJAX_IV': hiddens.get('GX_AJAX_IV', ''),
        'EMPRESA_SYJ_ID': str(hiddens.get('EMPRESA_SYJ_ID', '')),
        'LIQUIDACION_CARATULA_ID': str(hiddens.get('LIQUIDACION_CARATULA_ID', '')),
        'gxhash_vPGMNAME': hiddens.get('gxhash_vPGMNAME', ''),
        'gxhash_vLIQUIDACIONID': hiddens.get('gxhash_vLIQUIDACIONID', ''),
        'gxhash_vEXIST': hiddens.get('gxhash_vEXIST', ''),
        'gxhash_vTOKEN_PROCESOBACKENDACTIVO': hiddens.get('gxhash_vTOKEN_PROCESOBACKENDACTIVO', ''),
        'gxhash_vPGMDESC': hiddens.get('gxhash_vPGMDESC', ''),
    }


def _extract_via_regex(raw):
    def find(p):
        m = re.search(p, raw)
        return m.group(1) if m else ''
    
    return {
        'AJAX_SECURITY_TOKEN': find(r'\\"AJAX_SECURITY_TOKEN\\":\\"([^\\]+)\\"'),
        'GX_AUTH_WPD_EXPORTAR_LSD': find(r'\\"GX_AUTH_WPD_EXPORTAR_LSD\\":\\"([^\\]+)\\"'),
        'GX_AJAX_KEY': find(r'\\"GX_AJAX_KEY\\":\\"([^\\]+)\\"'),
        'GX_AJAX_IV': find(r'\\"GX_AJAX_IV\\":\\"([^\\]+)\\"'),
        'EMPRESA_SYJ_ID': find(r'\\"EMPRESA_SYJ_ID\\":\\"([^\\]+)\\"'),
        'LIQUIDACION_CARATULA_ID': find(r'\\"LIQUIDACION_CARATULA_ID\\":\\"([^\\]+)\\"'),
        'gxhash_vPGMNAME': find(r'\\"gxhash_vPGMNAME\\":\\"([^\\]+)\\"'),
        'gxhash_vLIQUIDACIONID': find(r'\\"gxhash_vLIQUIDACIONID\\":\\"([^\\]+)\\"'),
        'gxhash_vEXIST': find(r'\\"gxhash_vEXIST\\":\\"([^\\]+)\\"'),
        'gxhash_vTOKEN_PROCESOBACKENDACTIVO': find(r'\\"gxhash_vTOKEN_PROCESOBACKENDACTIVO\\":\\"([^\\]+)\\"'),
        'gxhash_vPGMDESC': find(r'\\"gxhash_vPGMDESC\\":\\"([^\\]+)\\"'),
    }


def update_tokens_from_response(response_text, current):
    """Re-extract tokens dari POST response (GeneXus rotate token setiap call)."""
    try:
        data = json.loads(response_text)
    except (json.JSONDecodeError, TypeError):
        return current
    
    hiddens = data.get('gxHiddens', {})
    if not hiddens:
        return current
    
    new = current.copy()
    for key in [
        'AJAX_SECURITY_TOKEN',
        'GX_AUTH_WPD_EXPORTAR_LSD',
        'gxhash_vPGMNAME',
        'gxhash_vLIQUIDACIONID',
        'gxhash_vEXIST',
        'gxhash_vTOKEN_PROCESOBACKENDACTIVO',
        'gxhash_vPGMDESC',
    ]:
        if key in hiddens and hiddens[key]:
            new[key] = hiddens[key]
    return new


# ===================== Cookie Helpers =====================

def build_genexus_cookie_value(value, length=9):
    """
    GeneXus pad cookie value dengan '+' di depan.
    Contoh: 6 → '++++++++6' (9 chars), 212 → '+++212' (6 chars)
    URL-encoded: '+' → '%2B'
    """
    s = str(value)
    if len(s) > length:
        # Value lebih panjang dari padding, tidak perlu pad
        return s
    padded = '+' * (length - len(s)) + s
    return padded.replace('+', '%2B')


def build_cookie_str_with_genexus(syj_session, empresa_syj_id, liquidacion_id):
    """
    Build cookie string termasuk EmpresaId/LiquidacionId dengan padding GeneXus.
    """
    parts = []
    
    # Cookies dari session login (skip EmpresaId & LiquidacionId yang akan di-override)
    for c in syj_session.cookies:
        if c.name not in ('EmpresaId', 'LiquidacionId', 'GxTZOffset'):
            parts.append(f"{c.name}={c.value}")
    
    # Tambah GeneXus-padded cookies
    parts.append(f"EmpresaId={build_genexus_cookie_value(empresa_syj_id, length=9)}")
    parts.append(f"LiquidacionId={build_genexus_cookie_value(liquidacion_id, length=6)}")
    parts.append("GxTZOffset=Asia/Jakarta")
    
    return "; ".join(parts)


# ===================== Payload Builders =====================

def build_e_exportar_payload(liquidacion_id, empresa_syj_id, tokens):
    """Body untuk POST event 'E_EXPORTAR'."""
    liq = int(liquidacion_id)
    emp = int(empresa_syj_id)
    
    return {
        "MPage": False,
        "cmpCtx": "",
        "parms": [
            False,
            f"[{liq}]",
            liq,
            0,
            emp,
            0,
            [liq],
            str(liq),
            False,
            [],
            False,
            "",
            False,
            9,
            {
                "s": "1",
                "v": [["1", "Liquidación y DJ"], ["2", "Solo DJ a Rectificar"]],
            },
            1,
        ],
        "hsh": [
            {"hsh": tokens.get('gxhash_vLIQUIDACIONID', ''), "row": ""},
            {"hsh": tokens.get('gxhash_vEXIST', ''), "row": ""},
        ],
        "objClass": "wpd_exportar_lsd",
        "pkgName": "GeneXus.Programs",
        "events": ["'E_EXPORTAR'"],
        "grids": {},
    }


def build_refresh_si_payload(cantidad_legajos, tokens):
    """Body untuk POST REFRESH dengan konfirmasi 'SI'."""
    return {
        "MPage": False,
        "cmpCtx": "",
        "parms": [
            "SI",
            cantidad_legajos,
            "NO, EMITIR",
            False,
            False,
            "WPD_Exportar_LSD",
            0,
            False,
            "00000000-0000-0000-0000-000000000000",
            "WPD Exportar Libro de Sueldos Digital",
        ],
        "hsh": [
            {"hsh": tokens.get('gxhash_vPGMNAME', ''), "row": ""},
            {"hsh": tokens.get('gxhash_vLIQUIDACIONID', ''), "row": ""},
            {"hsh": tokens.get('gxhash_vEXIST', ''), "row": ""},
            {"hsh": tokens.get('gxhash_vTOKEN_PROCESOBACKENDACTIVO', ''), "row": ""},
            {"hsh": tokens.get('gxhash_vPGMDESC', ''), "row": ""},
        ],
        "objClass": "wpd_exportar_lsd",
        "pkgName": "GeneXus.Programs",
        "events": ["REFRESH"],
        "grids": {},
    }


# ===================== Main Function untuk Locust Task =====================

def s1_exportar_lsd_chain(task_self, http_pool, syj_host, liquidacion_id=212):
    """
    Full chain S1 Exportar LSD untuk Locust task.
    
    Args:
        task_self: self dari task (HolistorWorkflows instance)
        http_pool: urllib3 PoolManager (dari main.py)
        syj_host: e.g. "https://syj-qa.holistorsaas.com.ar"
        liquidacion_id: liquidacion target (default 212)
    
    Returns:
        bool: True kalau seluruh chain sukses
    """
    user = task_self.user
    environment = user.environment
    syj_session = user.syj_session
    empresa_syj_id = getattr(user, 'empresa_syj_id', '6')
    
    cookie_str = build_cookie_str_with_genexus(syj_session, empresa_syj_id, liquidacion_id)
    
    # ============ STEP 1: GET PAGE ============
    get_url = f"{syj_host}/wpd_exportar_lsd.aspx"
    get_headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:144.0) Gecko/20100101 Firefox/144.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'Cookie': cookie_str,
        'Referer': f"{syj_host}/home.aspx",
    }
    
    logger.info(f"🚀 S1: GET {get_url}")
    start = time.time()
    try:
        r = http_pool.request(
            'GET', get_url, headers=get_headers,
            timeout=urllib3.Timeout(connect=10, read=30),
            redirect=True,
        )
        elapsed = int((time.time() - start) * 1000)
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        environment.events.request.fire(
            request_type="GET", name="S1_LSD_Page",
            response_time=elapsed, response_length=0,
            exception=e, context={}, url=get_url, response=None,
        )
        logger.error(f"❌ S1 GET error: {e}")
        return False
    
    if r.status != 200:
        environment.events.request.fire(
            request_type="GET", name="S1_LSD_Page",
            response_time=elapsed, response_length=len(r.data) if r.data else 0,
            exception=Exception(f"HTTP {r.status}"), context={},
            url=get_url, response=None,
        )
        logger.error(f"❌ S1 GET status {r.status}")
        return False
    
    environment.events.request.fire(
        request_type="GET", name="S1_LSD_Page",
        response_time=elapsed, response_length=len(r.data),
        exception=None, context={}, url=get_url, response=None,
    )
    
    html = r.data.decode('utf-8', errors='replace')
    tokens = extract_tokens_from_html(html)
    
    if not tokens or not tokens.get('AJAX_SECURITY_TOKEN'):
        logger.error("❌ S1: Cannot extract tokens from response")
        # Save untuk debug
        try:
            with open('/tmp/s1_get_failed.html', 'w') as f:
                f.write(html)
            logger.error("   Saved response to /tmp/s1_get_failed.html")
        except Exception:
            pass
        return False
    
    logger.info(f"   ✓ tokens extracted: empresa={tokens['EMPRESA_SYJ_ID']}, liq={tokens['LIQUIDACION_CARATULA_ID']}")
    
    # Verify dan auto-correct empresa & liquidacion
    if tokens['EMPRESA_SYJ_ID'] and tokens['EMPRESA_SYJ_ID'] != str(empresa_syj_id):
        logger.warning(f"   ⚠️ EMPRESA_SYJ_ID mismatch: response={tokens['EMPRESA_SYJ_ID']}, config={empresa_syj_id}")
        empresa_syj_id = tokens['EMPRESA_SYJ_ID']
    
    if tokens['LIQUIDACION_CARATULA_ID'] and tokens['LIQUIDACION_CARATULA_ID'] != str(liquidacion_id):
        logger.warning(f"   ⚠️ LIQUIDACION_ID mismatch: response={tokens['LIQUIDACION_CARATULA_ID']}, config={liquidacion_id}")
        try:
            liquidacion_id = int(tokens['LIQUIDACION_CARATULA_ID'])
        except (ValueError, TypeError):
            pass
    
    # ============ STEP 2: POST E_EXPORTAR ============
    iv_lower = tokens['GX_AJAX_IV'].lower()
    timestamp_ms = int(time.time() * 1000)
    post_url = f"{syj_host}/wpd_exportar_lsd.aspx?{iv_lower},gx-no-cache={timestamp_ms}"
    
    post_headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:144.0) Gecko/20100101 Firefox/144.0',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'GxAjaxRequest': '1',
        'Content-Type': 'application/json',
        'AJAX_SECURITY_TOKEN': tokens['AJAX_SECURITY_TOKEN'],
        'X-GXAUTH-TOKEN': tokens['GX_AUTH_WPD_EXPORTAR_LSD'],
        'Origin': syj_host,
        'Referer': f"{syj_host}/wpd_exportar_lsd.aspx",
        'Cookie': cookie_str,
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
    }
    
    payload = build_e_exportar_payload(liquidacion_id, empresa_syj_id, tokens)
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    
    logger.info(f"🚀 S1: POST E_EXPORTAR")
    start = time.time()
    try:
        r = http_pool.request(
            'POST', post_url, body=body, headers=post_headers,
            timeout=urllib3.Timeout(connect=10, read=60),
        )
        elapsed = int((time.time() - start) * 1000)
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        environment.events.request.fire(
            request_type="POST", name="S1_POST_E_EXPORTAR",
            response_time=elapsed, response_length=0,
            exception=e, context={}, url=post_url, response=None,
        )
        logger.error(f"❌ S1 POST error: {e}")
        return False
    
    if r.status != 200:
        body_text = r.data.decode('utf-8', errors='replace') if r.data else ''
        environment.events.request.fire(
            request_type="POST", name="S1_POST_E_EXPORTAR",
            response_time=elapsed, response_length=len(r.data) if r.data else 0,
            exception=Exception(f"HTTP {r.status}"), context={},
            url=post_url, response=None,
        )
        logger.error(f"❌ S1 POST E_EXPORTAR status {r.status}")
        logger.error(f"   Body: {body_text[:1000]}")
        try:
            with open('/tmp/s1_post_failed.txt', 'w') as f:
                f.write(body_text)
        except Exception:
            pass
        return False
    
    environment.events.request.fire(
        request_type="POST", name="S1_POST_E_EXPORTAR",
        response_time=elapsed, response_length=len(r.data),
        exception=None, context={}, url=post_url, response=None,
    )
    logger.info(f"   ✓ E_EXPORTAR OK ({elapsed}ms)")
    
    resp_text = r.data.decode('utf-8', errors='replace')
    tokens = update_tokens_from_response(resp_text, tokens)
    
    has_confirm = (
        'W0138' in resp_text or
        'ConfirmMessage' in resp_text or
        'Actualizar Totalizadores' in resp_text
    )
    
    if not has_confirm:
        logger.info(f"✅ S1: No confirmation needed, done")
        return True
    
    logger.info(f"   → Got confirmation dialog, sending SI...")
    
    # ============ STEP 3: POST REFRESH SI ============
    cantidad_legajos = 9
    try:
        resp_data = json.loads(resp_text)
        for val_obj in resp_data.get('gxValues', []):
            if 'AV74ControlNum' in val_obj:
                cantidad_legajos = int(val_obj['AV74ControlNum'])
                break
    except Exception:
        pass
    
    timestamp_ms = int(time.time() * 1000)
    post_url = f"{syj_host}/wpd_exportar_lsd.aspx?{iv_lower},gx-no-cache={timestamp_ms}"
    
    post_headers['AJAX_SECURITY_TOKEN'] = tokens['AJAX_SECURITY_TOKEN']
    post_headers['X-GXAUTH-TOKEN'] = tokens['GX_AUTH_WPD_EXPORTAR_LSD']
    
    payload = build_refresh_si_payload(cantidad_legajos, tokens)
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    
    logger.info(f"🚀 S1: POST REFRESH SI (legajos={cantidad_legajos})")
    start = time.time()
    try:
        r = http_pool.request(
            'POST', post_url, body=body, headers=post_headers,
            timeout=urllib3.Timeout(connect=10, read=120),
        )
        elapsed = int((time.time() - start) * 1000)
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        environment.events.request.fire(
            request_type="POST", name="S1_POST_REFRESH_SI",
            response_time=elapsed, response_length=0,
            exception=e, context={}, url=post_url, response=None,
        )
        logger.error(f"❌ S1 REFRESH SI error: {e}")
        return False
    
    if r.status != 200:
        body_text = r.data.decode('utf-8', errors='replace') if r.data else ''
        environment.events.request.fire(
            request_type="POST", name="S1_POST_REFRESH_SI",
            response_time=elapsed, response_length=len(r.data) if r.data else 0,
            exception=Exception(f"HTTP {r.status}"), context={},
            url=post_url, response=None,
        )
        logger.error(f"❌ S1 REFRESH SI status {r.status}")
        logger.error(f"   Body: {body_text[:1000]}")
        return False
    
    environment.events.request.fire(
        request_type="POST", name="S1_POST_REFRESH_SI",
        response_time=elapsed, response_length=len(r.data),
        exception=None, context={}, url=post_url, response=None,
    )
    
    logger.info(f"✅ S1: All steps SUCCESS ({elapsed}ms)")
    return True
