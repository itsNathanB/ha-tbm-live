"""Vrai Home Assistant + vrai navigateur : la carte s'enregistre, s'affiche et figure dans le sélecteur."""
import asyncio, json, time, urllib.request, urllib.parse
import aiohttp
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8123"
CLIENT = BASE + "/"

def post(path, data, token=None, form=False):
    body = urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()
    req = urllib.request.Request(BASE + path, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded" if form else "application/json")
    if token: req.add_header("Authorization", "Bearer " + token)
    return json.loads(urllib.request.urlopen(req).read() or b"{}")

def get(path, token):
    req = urllib.request.Request(BASE + path); req.add_header("Authorization", "Bearer " + token)
    return json.loads(urllib.request.urlopen(req).read())

code = post("/api/onboarding/users", {"client_id": CLIENT, "name": "Test", "username": "test", "password": "test-pass-123", "language": "fr"})["auth_code"]
tok = post("/auth/token", {"grant_type": "authorization_code", "code": code, "client_id": CLIENT}, form=True)
access = tok["access_token"]
for path, data in (("/api/onboarding/core_config", {}), ("/api/onboarding/analytics", {}),
                   ("/api/onboarding/integration", {"client_id": CLIENT, "redirect_uri": CLIENT})):
    try: post(path, data, access)
    except Exception as e: print("onboarding", path, e)

states = get("/api/states", access)
main = [s["entity_id"] for s in states if isinstance(s["attributes"].get("departures"), list)]
print("Capteurs principaux :", main)
for s in states:
    if s["entity_id"].startswith("sensor."):
        print("  ", s["entity_id"], "=", s["state"])

async def save_dashboard():
    async with aiohttp.ClientSession() as sess:
        async with sess.ws_connect(BASE + "/api/websocket") as ws:
            await ws.receive_json(); await ws.send_json({"type": "auth", "access_token": access}); print("ws", (await ws.receive_json())["type"])
            cfg = {"views": [{"title": "Test", "path": "test", "type": "sections", "sections": [{"type": "grid", "cards": [
                {"type": "custom:tbm-live-card", "title": "Départs", "entities": main},
                {"type": "custom:tbm-live-card", "compact": True, "entities": [{"entity": main[0], "name": "Vers le travail", "color": "#7E1A3B"}]},
            ]}]}]}
            await ws.send_json({"id": 1, "type": "lovelace/dashboards/create", "url_path": "tbm-test", "title": "TBM", "mode": "storage", "require_admin": False, "show_in_sidebar": True})
            print("create", await ws.receive_json())
            await ws.send_json({"id": 2, "type": "lovelace/config/save", "url_path": "tbm-test", "config": cfg})
            print("save", await ws.receive_json())

asyncio.run(save_dashboard())

async def open_page(b, scheme, block_card=False):
    ctx = await b.new_context(viewport={"width": 1200, "height": 900}, locale="fr-FR", color_scheme=scheme, device_scale_factor=2)
    page = await ctx.new_page()
    logs = []
    page.on("console", lambda m: logs.append(f"console[{m.type}] {m.text}") if m.type in ("error", "warning") or "tbm" in m.text.lower() else None)
    page.on("pageerror", lambda e: logs.append(f"PAGEERROR {e.message} | {(e.stack or '')[:600]}"))
    await page.add_init_script("""window.addEventListener('unhandledrejection', e => {
      try { console.error('REJECTION ' + JSON.stringify(e.reason)); } catch (x) { console.error('REJECTION ' + e.reason); }
    });""")
    if block_card:
        await page.route("**/tbm_live/**", lambda route: route.abort())
    await page.goto(BASE + "/auth/authorize")
    tokens = {**tok, "hassUrl": BASE, "clientId": CLIENT, "expires": int(time.time() * 1000) + 1800000}
    await page.evaluate("t => localStorage.setItem('hassTokens', JSON.stringify(t))", tokens)
    await page.goto(BASE + "/tbm-test/test")
    await page.wait_for_timeout(15000)
    return ctx, page, logs

async def main_browser():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        # A/B : la page charge-t-elle sans la carte ?
        ctx, page, logs = await open_page(b, "light", block_card=True)
        await page.screenshot(path="samples/sans-carte.png")
        print("SANS CARTE : hui-root présent =", await page.locator("hui-root").count(), "| erreurs :", logs[:8])
        await ctx.close()
        for scheme in ("light", "dark"):
            ctx, page, logs = await open_page(b, scheme)
            info = await page.evaluate("""() => ({
              defined: !!customElements.get('tbm-live-card'),
              customCards: (window.customCards || []).map(c => c.type),
            })""")
            print(scheme, "AVEC CARTE", json.dumps(info), "| hui-root =", await page.locator("hui-root").count(), "| cartes =", await page.locator("tbm-live-card").count())
            print("  erreurs :", "\n   ".join(logs[:10]))
            await page.screenshot(path=f"samples/dashboard-{scheme}.png")
            cards = page.locator("tbm-live-card")
            for i in range(await cards.count()):
                try:
                    await cards.nth(i).screenshot(path=f"samples/card-{scheme}-{i}.png")
                except Exception as e:
                    print("shot", e)
            if scheme == "light" and await cards.count():
                try:
                    await page.goto(BASE + "/tbm-test/test?edit=1")
                    await page.wait_for_timeout(6000)
                    await page.screenshot(path="samples/edit.png")
                    await page.mouse.click(346, 626)
                    await page.wait_for_timeout(3000)
                    await page.screenshot(path="samples/picker-0.png")
                    await page.keyboard.type("Quinconces")
                    await page.wait_for_timeout(2500)
                    await page.screenshot(path="samples/picker-1.png")
                    # Premier résultat de la liste d'entités
                    await page.get_by_text("Next departure").first.click()
                    await page.wait_for_timeout(3000)
                    await page.screenshot(path="samples/picker-entity.png")
                    print("Par entité : suggestion TBM Live =", await page.get_by_text("TBM Live").count())
                    await page.get_by_text("Par carte").first.click()
                    await page.wait_for_timeout(2000)
                    await page.get_by_placeholder("Rechercher parmi les cartes").first.click()
                    await page.keyboard.type("TBM")
                    await page.wait_for_timeout(2500)
                    await page.screenshot(path="samples/picker-card.png")
                    print("Par carte : TBM Live =", await page.get_by_text("TBM Live").count())
                except Exception as e:
                    print("picker", repr(e)[:300])
            await ctx.close()
        await b.close()

asyncio.run(main_browser())
