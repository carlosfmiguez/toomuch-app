"""Conector MCP: OAuth com PKCE e as ferramentas, de ponta a ponta."""

import base64
import hashlib
import json
from decimal import Decimal
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from apps.catalog.models import Product

REDIRECT = "https://claude.ai/api/mcp/auth_callback"


class ConnectorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)
        cls.user = get_user_model().objects.create_superuser("carlos", "c@example.com", "senha-forte-123")

    # --- helpers --------------------------------------------------------------
    def _token(self):
        meta = self.client.get("/.well-known/oauth-authorization-server").json()
        reg = self.client.post(meta["registration_endpoint"].replace("http://testserver", ""),
                               json.dumps({"client_name": "Claude", "redirect_uris": [REDIRECT]}), content_type="application/json")
        self.assertEqual(reg.status_code, 201)
        client_id = reg.json()["client_id"]
        verifier = "v" * 64
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        q = {"response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT, "code_challenge": challenge,
             "code_challenge_method": "S256", "state": "xyz", "scope": "toomuch"}
        # sem login → manda para o login do sistema
        r = self.client.get("/oauth/authorize", q)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/admin/login/", r["Location"])
        self.client.force_login(self.user)
        r = self.client.get("/oauth/authorize", q)
        self.assertContains(r, "Permitir que o Claude acesse o sistema?")
        r = self.client.post("/oauth/authorize", {**q, "decision": "allow"})
        self.assertEqual(r.status_code, 302)
        loc = urlparse(r["Location"])
        self.assertEqual(f"{loc.scheme}://{loc.netloc}{loc.path}", REDIRECT)
        params = parse_qs(loc.query)
        self.assertEqual(params["state"], ["xyz"])
        self.client.logout()
        # PKCE errado falha; certo funciona; o código só vale uma vez
        bad = self.client.post("/oauth/token", {"grant_type": "authorization_code", "code": params["code"][0], "client_id": client_id,
                                                "redirect_uri": REDIRECT, "code_verifier": "x" * 64})
        self.assertEqual(bad.json()["error"], "invalid_grant")
        tok = self.client.post("/oauth/token", {"grant_type": "authorization_code", "code": params["code"][0], "client_id": client_id,
                                                "redirect_uri": REDIRECT, "code_verifier": verifier})
        self.assertEqual(tok.status_code, 200, tok.content)
        data = tok.json()
        again = self.client.post("/oauth/token", {"grant_type": "authorization_code", "code": params["code"][0], "client_id": client_id,
                                                  "redirect_uri": REDIRECT, "code_verifier": verifier})
        self.assertEqual(again.json()["error"], "invalid_grant")
        return client_id, data

    def _rpc(self, token, method, params=None, msg_id=1):
        r = self.client.post("/mcp", json.dumps({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}),
                             content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def _call(self, token, name, **args):
        res = self._rpc(token, "tools/call", {"name": name, "arguments": args})["result"]
        text = res["content"][0]["text"]
        if res["isError"]:
            return {"_erro": text}
        return json.loads(text)

    # --- testes ------------------------------------------------------------------
    def test_unauthenticated_points_to_metadata(self):
        r = self.client.post("/mcp", "{}", content_type="application/json")
        self.assertEqual(r.status_code, 401)
        self.assertIn("/.well-known/oauth-protected-resource", r["WWW-Authenticate"])
        meta = self.client.get("/.well-known/oauth-protected-resource").json()
        self.assertTrue(meta["resource"].endswith("/mcp"))

    def test_register_rejects_foreign_redirect(self):
        r = self.client.post("/oauth/register", json.dumps({"redirect_uris": ["https://evil.example.com/cb"]}), content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_full_flow_and_tools(self):
        client_id, tok = self._token()
        access = tok["access_token"]
        init = self._rpc(access, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}})
        self.assertEqual(init["result"]["protocolVersion"], "2025-06-18")
        r = self.client.post("/mcp", json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                             content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {access}")
        self.assertEqual(r.status_code, 202)
        names = {t["name"] for t in self._rpc(access, "tools/list")["result"]["tools"]}
        self.assertTrue({"ver_produto", "criar_produto", "criar_variante", "aprovar_precos", "registrar_compra", "importar_pacote_url"} <= names)

        sol = self._call(access, "ver_produto", produto="TM-LUM-SOL")
        self.assertEqual(sol["nome"], "Luminária Solaris")
        self.assertEqual(sol["variantes"][0]["custo"], "72.19")

        novo = self._call(access, "criar_produto", nome="Vaso Teste", categoria="VAS", origem="autoral", descricao_curta="Um vaso.")
        sku = novo["sku_base"]
        self.assertTrue(sku.startswith("TM-VAS-"))
        fil = self._call(access, "criar_filamento", marca="Bambu Lab", material="PLA Matte", cor="Terracota")
        self.assertIn("_erro", self._call(access, "criar_variante", produto=sku, filamento_principal="inexistente xyz"))
        compra = self._call(access, "registrar_compra", filamento=str(fil["id"]), gramas_total=1000, valor_itens=100)
        self.assertEqual(compra["custo_kg"], "100.00")
        var = self._call(access, "criar_variante", produto=sku, tamanho="G", cor_principal="BRC", filamento_principal=str(fil["id"]))
        self.assertTrue(var["sku"].startswith(f"{sku}-G-BRC"))
        self.assertTrue(var["precos"])

        ok = self._call(access, "aprovar_precos", variante=var["sku"], precos={"Site": "49,90"})
        self.assertIn("Site R$ 49.90", ok["aprovados"][0])
        ok = self._call(access, "aprovar_precos", variante=var["sku"])
        self.assertTrue(all(p["aprovado"] for p in ok["precos"]))

        up = self._call(access, "atualizar_variante", variante=var["sku"], minutos_pos=30)
        self.assertEqual(up["minutos"]["pos_processamento"], 30)
        self.assertEqual(self._call(access, "mudar_status", produto=sku, status="active")["status"], "Ativo")
        self.assertEqual(Product.objects.get(sku_base=sku).status, "active")

        # refresh troca o par e invalida o antigo
        r = self.client.post("/oauth/token", {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": client_id})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.post("/mcp", "{}", content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {access}").status_code, 401)
        self._rpc(r.json()["access_token"], "ping")

    def test_import_package_from_url(self):
        import io
        import zipfile

        from apps.catalog.tests import jpeg_bytes
        from apps.files3d.tests import make_3mf

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("pacote/NAT-SIN Sino/Sino.3mf", make_3mf())
            z.writestr("pacote/NAT-SIN Sino/foto.jpg", jpeg_bytes())
            z.writestr("pacote/produtos.csv", "Pasta;Nome;Categoria;SKU-base;Origem;Autor\nNAT-SIN Sino;Sino de Natal;NAT;TM-NAT-SIN;unlicensed;Andy_E\n")

        def fake_download(url, dest):
            dest.write_bytes(buf.getvalue())
            return dest

        _, tok = self._token()
        import tempfile

        from django.test import override_settings

        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp), \
                mock.patch("apps.connector.tools._download", side_effect=fake_download):
            res = self._call(tok["access_token"], "importar_pacote_url", url="https://github.com/x/y/blob/main/pacote.zip")
        self.assertEqual(res["produtos_criados"], 1, res)
        p = Product.objects.get(sku_base="TM-NAT-SIN")
        self.assertEqual(p.origin, "unlicensed")
        self.assertTrue(p.parts.get().production_assets().exists())
