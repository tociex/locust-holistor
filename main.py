# main.py — FINAL VERSION dengan POST S1 yang benar (no more 500)
from gevent import monkey
monkey.patch_all()

import os
import csv
import logging
import re
import requests
import urllib3
import time
import json
import xml.etree.ElementTree as ET
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from locust import HttpUser, task, between, TaskSet, tag
from dotenv import load_dotenv
from queue import Queue, Empty

from syj_post import s1_exportar_lsd_chain  # ← helper untuk POST chain

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
load_dotenv()

# Pool urllib3 untuk request SYJ — pool besar untuk concurrent users
http_pool = urllib3.PoolManager(
    timeout=urllib3.Timeout(connect=10, read=30),
    retries=Retry(total=1, connect=1, read=0),
    num_pools=50,
    maxsize=100,
    block=False
)


class EnvConfig:
    BASE_PLATFORM = "https://plataforma-saas-host-qa.azurewebsites.net"
    PORTAL_URL    = "https://plataforma-saas-qa.azurewebsites.net"
    LOGIN_URL     = "https://plataforma-saas-qa.azurewebsites.net/account/login"
    SYJ_URLS = {
        "STG":  "https://syj-stg.holistorsaas.com.ar",
        "QA":   "https://syj-app-qa-gx18-websession.azurewebsites.net",
        "UAT":  "https://syj-uat.holistorsaas.com.ar",
        "PROD": "https://syj.holistorsaas.com.ar"
    }

    @classmethod
    def get_syj_domain(cls):
        env = os.getenv("TARGET_ENV", "QA").upper()
        return cls.SYJ_URLS.get(env, cls.SYJ_URLS["QA"])


def load_accounts_to_queue():
    user_queue = Queue()
    try:
        with open("accounts.csv", mode="r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                user_queue.put(row)
        logger.info(f"✅ Loaded {user_queue.qsize()} accounts from CSV.")
    except FileNotFoundError:
        logger.error("❌ CRITICAL ERROR: accounts.csv not found!")
    return user_queue


USER_DATA_QUEUE = load_accounts_to_queue()


def clean_empresa_id(raw, default="132313"):
    if not raw:
        return default
    raw_str = str(raw).strip()
    if raw_str.isdigit():
        return raw_str
    digits = re.sub(r'\D', '', raw_str)
    if digits:
        logger.warning(f"   empresa '{raw_str}' extracted to '{digits}'")
        return digits
    logger.warning(f"   empresa '{raw_str}' is not an ID, use default {default}")
    return default


def fire_event(environment, name, req_type, elapsed, length, exception=None, url=""):
    try:
        environment.events.request.fire(
            request_type=req_type,
            name=name,
            response_time=elapsed,
            response_length=length,
            exception=exception,
            context={},
            url=url,
            response=None,
        )
    except Exception as e:
        logger.error(f"   fire_event error: {e}")


def soap_call(session, syj_host, token, environment):
    soap_body = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <PInitSYJ.Execute xmlns="Holistor_SYJ">
      <Token>{token}</Token>
    </PInitSYJ.Execute>
  </soap:Body>
</soap:Envelope>"""

    url   = f"{syj_host}/apinitsyj.aspx"
    start = time.time()
    try:
        r = session.post(
            url,
            data=soap_body.encode("utf-8"),
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction":   "Holistor_SYJaction/APINITSYJ.Execute",
            },
            timeout=20,
            allow_redirects=True
        )
        elapsed = int((time.time() - start) * 1000)

        root = ET.fromstring(r.text)
        isok_val      = ""
        errmsg_val    = ""
        urlaccess_val = ""

        for elem in root.iter():
            tag_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            val = (elem.text or "").strip()
            if tag_name == "Isok":
                isok_val = val
            elif tag_name == "Errmessage":
                errmsg_val = val
            elif tag_name == "Urlaccess":
                urlaccess_val = val

        if isok_val.lower() == "true":
            if urlaccess_val and not urlaccess_val.startswith("http"):
                urlaccess_val = f"{syj_host}/{urlaccess_val}"
            fire_event(environment, "SOAP_PInitSYJ", "POST", elapsed, len(r.content), url=url)
            logger.info(f"✅ SOAP OK")
            return urlaccess_val if urlaccess_val else None
        else:
            fire_event(environment, "SOAP_PInitSYJ", "POST", elapsed, len(r.content),
                       Exception(f"Isok=false: {errmsg_val}"), url=url)
            logger.error(f"❌ SOAP Isok=false: {errmsg_val}")
            return None

    except ET.ParseError as e:
        elapsed = int((time.time() - start) * 1000)
        fire_event(environment, "SOAP_PInitSYJ", "POST", elapsed, 0, e, url=url)
        return None
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        fire_event(environment, "SOAP_PInitSYJ", "POST", elapsed, 0, e, url=url)
        return None


class FakeResp:
    def __init__(self, urllib3_resp, url):
        self.status_code = urllib3_resp.status
        self.content     = urllib3_resp.data
        self.text        = urllib3_resp.data.decode("utf-8", errors="ignore")
        self.url         = url
        self.headers     = dict(urllib3_resp.headers)


class HolistorWorkflows(TaskSet):

    def on_start(self):
        for attempt in range(5):
            if getattr(self.user, "is_logged_in", False):
                logger.info(f"🎯 TaskSet ready: {self.user.account_info.get('username')}")
                return
            logger.warning(f"⚠️ Waiting login... attempt {attempt+1}/5")
            time.sleep(2)
        logger.error("❌ Login timeout, task canceled.")
        self.interrupt()

    def get_task_headers(self):
        return {
            "Authorization":    getattr(self.user, "bearer_token", ""),
            "Content-Type":     "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Referer":          getattr(self.user, "syj_url_access",
                                        EnvConfig.get_syj_domain()),
        }

    def get_ajax_headers(self, referer_page="home.aspx"):
        syj_host = EnvConfig.get_syj_domain()
        return {
            "GxAjaxRequest":    "1",
            "Content-Type":     "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Referer":          f"{syj_host}/{referer_page}",
        }

    def _build_cookie_str(self):
        syj_session = getattr(self.user, "syj_session", requests.Session())
        return "; ".join([f"{k}={v}" for k, v in syj_session.cookies.items()])

    def syj_get(self, url, name, timeout=30):
        start = time.time()
        logger.info(f"   [GET] {name}")

        headers = self.get_task_headers()
        headers["Cookie"] = self._build_cookie_str()

        try:
            r = http_pool.request(
                "GET", url, headers=headers,
                timeout=urllib3.Timeout(connect=10, read=timeout),
                redirect=True
            )
            elapsed = int((time.time() - start) * 1000)
            resp = FakeResp(r, url)

            exc = None if resp.status_code == 200 else Exception(f"HTTP {resp.status_code}")
            fire_event(self.user.environment, name, "GET", elapsed,
                       len(resp.content), exc, url=url)
            logger.info(f"   {name} → {resp.status_code} ({elapsed}ms)")
            if resp.status_code != 200:
                logger.error(f"   Body: {resp.text[:300]}")
            return resp
        except Exception as e:
            elapsed = int((time.time() - start) * 1000)
            fire_event(self.user.environment, name, "GET", elapsed, 0, e, url=url)
            logger.error(f"❌ GET {name} ({type(e).__name__}): {e}")
            return None

    def syj_post_ajax(self, url, name, payload, timeout=60,
                      referer_page="home.aspx"):
        start = time.time()
        logger.info(f"   [POST] {name}")

        headers = self.get_ajax_headers(referer_page)
        headers["Cookie"] = self._build_cookie_str()

        try:
            body = json.dumps(payload).encode("utf-8")
            r = http_pool.request(
                "POST", url, body=body, headers=headers,
                timeout=urllib3.Timeout(connect=10, read=timeout),
                redirect=True
            )
            elapsed = int((time.time() - start) * 1000)
            resp = FakeResp(r, url)

            exc = None if resp.status_code == 200 else Exception(f"HTTP {resp.status_code}")
            fire_event(self.user.environment, name, "POST", elapsed,
                       len(resp.content), exc, url=url)
            logger.info(f"   {name} → {resp.status_code} ({elapsed}ms)")
            if resp.status_code != 200:
                logger.error(f"   Response body: {resp.text[:500]}")
            return resp
        except Exception as e:
            elapsed = int((time.time() - start) * 1000)
            fire_event(self.user.environment, name, "POST", elapsed, 0, e, url=url)
            logger.error(f"❌ POST {name} ({type(e).__name__}): {e}")
            return None

    # ================================================================ #
    # SKENARIO 1 — Libro de Sueldos Digital (FIXED: pakai full POST chain)
    # ================================================================ #
    @tag('skenario_1')
    @task(1)
    def libro_sueldo_digital(self):
        """S1: Libro de Sueldos Digital - Full POST chain (GET → E_EXPORTAR → REFRESH SI)."""
        syj_host = EnvConfig.get_syj_domain()
        liquidacion_id = getattr(self.user, 'liquidacion_id', 212)
        
        logger.info(f"🚀 S1: Libro Sueldo Digital (liq={liquidacion_id})")
        
        success = s1_exportar_lsd_chain(
            task_self=self,
            http_pool=http_pool,
            syj_host=syj_host,
            liquidacion_id=liquidacion_id,
        )
        
        if success:
            logger.info(f"✅ S1 chain SUCCESS")
        else:
            logger.error(f"❌ S1 chain FAILED")

    # ================================================================ #
    # SKENARIO 2 — Recibos PDF por legajo (ZIP)
    # ================================================================ #
    @tag('skenario_2')
    @task(5)
    def recibos_pdf_por_legajo(self):
        syj_host = EnvConfig.get_syj_domain()
        url      = f"{syj_host}/empresamodelorecibo_v2.aspx"

        logger.info(f"🚀 S2: Recibos PDF por legajo (ZIP)")
        r_page = self.syj_get(url, "S2_Recibos_Page", timeout=15)
        if not r_page or r_page.status_code != 200:
            return
        if "wwtempresaabm" in r_page.url:
            logger.error("❌ S2: Redirect to wwtempresaabm")
            return
        # NOTE: POST untuk S2 belum di-implement dengan AJAX_SECURITY_TOKEN.
        # Saat ini hanya GET. Untuk full POST chain butuh capture browser curl S2.
        logger.info(f"✅ S2 GET only (POST chain not yet implemented)")

    # ================================================================ #
    # SKENARIO 3 — Recibos SIN PDF por legajo
    # ================================================================ #
    @tag('skenario_3')
    @task(3)
    def recibos_sin_pdf_por_legajo(self):
        syj_host = EnvConfig.get_syj_domain()
        url      = f"{syj_host}/empresamodelorecibo_v2.aspx"

        logger.info(f"🚀 S3: Recibos SIN PDF por legajo")
        r_page = self.syj_get(url, "S3_Recibos_SIN_Page", timeout=15)
        if not r_page or r_page.status_code != 200:
            return
        if "wwtempresaabm" in r_page.url:
            logger.error("❌ S3: Redirect to wwtempresaabm")
            return
        # NOTE: POST untuk S3 belum di-implement (sama dengan S2)
        logger.info(f"✅ S3 GET only (POST chain not yet implemented)")

    # ================================================================ #
    # SKENARIO 4 — Ganancias 4ta Categoría
    # ================================================================ #
    @tag('skenario_4')
    @task(1)
    def ganancias_anual_4ta_categoria(self):
        syj_host = EnvConfig.get_syj_domain()
        url      = f"{syj_host}/wpd_ganancias4tacateg.aspx"

        logger.info(f"🚀 S4: Ganancias Anual 4ta Categoría")
        r_page = self.syj_get(url, "S4_Ganancias_Page", timeout=15)
        if not r_page or r_page.status_code != 200:
            return
        if "wwtempresaabm" in r_page.url:
            logger.error("❌ S4: Redirect to wwtempresaabm")
            return
        # NOTE: POST untuk S4 belum di-implement dengan AJAX_SECURITY_TOKEN
        logger.info(f"✅ S4 GET only (POST chain not yet implemented)")


class StressTester(HttpUser):
    wait_time = between(1, 4)
    tasks     = [HolistorWorkflows]
    host      = EnvConfig.BASE_PLATFORM

    def on_start(self):
        logger.info(f"🟢 on_start CALLED")
        
        self.bearer_token   = ""
        self.is_logged_in   = False
        self.syj_url_access = ""
        self.syj_session    = requests.Session()
        self.account_info   = {}
        self.empresa_id     = "132313"
        self.empresa_syj_id = "6"          # ← UBAH dari "36" ke "6" (sesuai sample browser)
        self.liquidacion_id = 212          # ← liquidacion_id default

        try:
            self.account_info = USER_DATA_QUEUE.get_nowait()
            USER_DATA_QUEUE.put(self.account_info)
            logger.info(f"🟢 Got account: {self.account_info.get('username')} / {self.account_info.get('tenancy')}")
        except Empty:
            logger.error("⚠️ No accounts available.")
            self.environment.runner.quit()
            return

        logger.info(f"🟢 Calling login() for {self.account_info.get('username')}")
        self.login(self.account_info)
        logger.info(f"🟢 login() returned for {self.account_info.get('username')}")

    def login(self, data):
        tenant_id = str(data.get('tenant_id', '849'))
        syj_host  = EnvConfig.get_syj_domain()

        headers_api = {
            "Content-Type": "application/json-patch+json",
            "X-Requested-With": "XMLHttpRequest",
            "Abp.TenantId": tenant_id,
            "Origin": EnvConfig.PORTAL_URL,
            "Referer": EnvConfig.LOGIN_URL
        }

        # STEP 1 — Cek tenant
        logger.info(f"   Step1: Check_Tenant for {data['tenancy']}")
        with self.client.post(
            "/api/services/app/Account/IsTenantAvailable",
            json={"tenancyName": data['tenancy']},
            headers=headers_api,
            name="Check_Tenant",
            catch_response=True
        ) as r:
            if r.status_code == 200:
                r.success()
                logger.info(f"✅ Tenant OK: {data['tenancy']}")
            else:
                r.failure(f"Tenant failed ({r.status_code})")
                return

        # STEP 2 — Authenticate
        logger.info(f"   Step2: Authenticate {data['username']}")
        token = ""
        with self.client.post(
            "/api/TokenAuth/Authenticate",
            json={"userNameOrEmailAddress": data['username'],
                  "password": data['password'],
                  "rememberClient": False},
            headers=headers_api,
            name="Authenticate",
            catch_response=True
        ) as r:
            if r.status_code == 200:
                token = r.json().get("result", {}).get("accessToken", "")
                if token:
                    self.bearer_token = f"Bearer {token}"
                    self.client.headers.update({
                        "Authorization": self.bearer_token,
                        "Abp.TenantId": tenant_id
                    })
                    r.success()
                    logger.info(f"🔑 Token OK: {data['username']}")
                else:
                    r.failure("Token does not exist")
                    return
            else:
                r.failure(f"Auth failed ({r.status_code})")
                return

        # STEP 3 — SOAP PInitSYJ
        logger.info(f"   Step3: SOAP PInitSYJ")
        soap_session = requests.Session()
        soap_session.headers.update({
            "User-Agent":    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Authorization": self.bearer_token
        })

        url_access = soap_call(
            session=soap_session,
            syj_host=syj_host,
            token=token,
            environment=self.environment
        )
        if not url_access:
            logger.error(f"❌ SOAP failed: {data['username']}")
            return

        # STEP 4 — GET Urlaccess
        if "?" in url_access:
            path_with_token = url_access.split(syj_host + "/")[-1]
            base_path       = path_with_token.split("?")[0]
            token_param     = path_with_token.split("?")[1]
            login_url       = f"{syj_host}/{base_path}?{token_param}"
        else:
            login_url = url_access

        logger.info(f"   Step4 start — login_url: {login_url[:100]}")
        start = time.time()

        try:
            r4 = soap_session.get(login_url, timeout=60, allow_redirects=False)
            elapsed = int((time.time() - start) * 1000)
            logger.info(f"   Step4 finish in {elapsed}ms — status: {r4.status_code}")

            redirect_location = r4.headers.get("Location", "")

            if "logout" in redirect_location:
                fire_event(self.environment, "SYJ_Session_Activate",
                           "GET", elapsed, 0,
                           Exception(f"Redirect logout: {redirect_location}"),
                           url=login_url)
                logger.error(f"❌ Login SYJ failed — redirect to logout")
                return

            for c in r4.cookies:
                domain = c.domain if c.domain else syj_host.replace("https://", "")
                soap_session.cookies.set(c.name, c.value, domain=domain)

            gx_session = soap_session.cookies.get("GX_SESSION_ID")
            if not gx_session:
                logger.error(f"❌ GX_SESSION_ID does not exist")
                return

            logger.info(f"   GX_SESSION_ID obtained: {gx_session[:40]}")

            # STEP 5 — DILEWATI
            logger.info("   Step5 skipped")

            # STEP 6 
            logger.info("   Step6 skipped — use empresa from CSV")
            empresa_id = clean_empresa_id(
                self.account_info.get('empresa', '132313'),
                default="132313"
            )
            self.empresa_id = empresa_id
            # empresa_syj_id sudah di-set ke "6" di on_start, tidak perlu override
            # Code akan auto-detect dari response GET nanti

            # STEP 7 — Set cookie EmpresaId & LiquidacionId
            # NOTE: untuk syj_post_ajax (S2/S3/S4) pakai cookie raw saja
            # Untuk S1 (libro_sueldo_digital), syj_post.py akan override dengan padding GeneXus
            domain = syj_host.replace("https://", "")
            soap_session.cookies.set("EmpresaId", empresa_id, domain=domain)
            soap_session.cookies.set("LiquidacionId", "1", domain=domain)
            logger.info(f"   Step7: EmpresaId cookie set = {empresa_id}")

            fire_event(self.environment, "SYJ_Session_Activate",
                       "GET", elapsed, len(r4.content), url=login_url)
            self.syj_session    = soap_session
            self.syj_url_access = f"{syj_host}/home.aspx"
            self.is_logged_in   = True
            logger.info(f"✅ SYJ session active: {data['username']}")

        except requests.exceptions.Timeout:
            elapsed = int((time.time() - start) * 1000)
            logger.error(f"❌ Step4 TIMEOUT after {elapsed}ms")
            fire_event(self.environment, "SYJ_Session_Activate", "GET", elapsed, 0,
                       Exception("Timeout"), url=login_url)
        except Exception as e:
            elapsed = int((time.time() - start) * 1000)
            logger.error(f"❌ Step4 error ({type(e).__name__}): {e}")
            fire_event(self.environment, "SYJ_Session_Activate", "GET", elapsed, 0, e,
                       url=login_url)
